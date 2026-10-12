"""Neo4j Community 구현 — Bolt 프로토콜, 별도 프로세스.

라이선스 원칙이 이 파일의 형태를 정한다.

  · 임베디드 API 를 쓰지 않는다. 공식 Python 드라이버(Apache 2.0)로
    별도 프로세스에 Bolt 로 접속만 한다. NeoSDL 코드는 GPL 영향권 밖이다.
  · Neo4j 바이너리는 제품에 동봉하지 않는다. 접속 정보만 설정으로 받는다.
  · 여기에 담기는 것은 구조 그래프뿐이다. 코드 원문과 민감정보 값은
    올리지 않는다 — Community 에는 세분화 접근제어(RBAC)가 없으므로,
    통제할 수 없는 곳에는 통제가 필요한 데이터를 두지 않는다.

Community 제약을 우회한 자리 두 곳:

  · 멀티 DB 가 없다 → 스캔 격리를 데이터베이스가 아니라 scan_id 속성으로 한다.
  · 복합 유니크 제약의 CE 지원 여부가 버전마다 갈린다 → uid 단일 속성
    (scan_id|symbol_key) 로 유일성을 건다. 5.x CE 어디서나 동작한다.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from neo4j import GraphDatabase, basic_auth
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from app.db import tx
from app.graph import (
    EDGE_TYPE_TO_REL,
    CallerPath,
    ProjectionResult,
    check_depth,
)

log = logging.getLogger("graph.neo4j")

BATCH = 5_000


def _uid(scan_id: str, symbol_key: str) -> str:
    return f"{scan_id}|{symbol_key}"


class Neo4jGraphStore:
    backend = "neo4j"

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver = GraphDatabase.driver(
            uri, auth=basic_auth(user, password), max_connection_lifetime=3600
        )

    def close(self) -> None:
        self._driver.close()

    # ------------------------------------------------------------------ health
    def health(self) -> bool:
        try:
            self._driver.verify_connectivity()
            return True
        except (ServiceUnavailable, Neo4jError, OSError) as exc:
            log.warning("Neo4j 응답 없음: %s", exc)
            return False

    # ------------------------------------------------------------------ schema
    def ensure_schema(self) -> None:
        stmts = [
            "CREATE CONSTRAINT symbol_uid IF NOT EXISTS "
            "FOR (s:Symbol) REQUIRE s.uid IS UNIQUE",
            "CREATE INDEX symbol_scan IF NOT EXISTS "
            "FOR (s:Symbol) ON (s.scan_id)",
            "CREATE INDEX symbol_key IF NOT EXISTS "
            "FOR (s:Symbol) ON (s.symbol_key)",
        ]
        with self._driver.session() as sess:
            for s in stmts:
                sess.run(s).consume()

    # -------------------------------------------------------------- projection
    def project(self, scan_id: str) -> ProjectionResult:
        """정본에서 읽어 그래프를 다시 만든다.

        멱등하다. 같은 scan_id 로 몇 번을 돌려도 결과가 같다 —
        스캔은 불변 스냅샷이므로 재투영이 언제나 안전해야 한다.
        """
        self.ensure_schema()
        self._delete_nodes(scan_id)

        nodes = edges = 0
        with self._driver.session() as sess:
            for batch in _chunk(self._read_symbols(scan_id), BATCH):
                res = sess.run(
                    """
                    UNWIND $rows AS r
                    MERGE (s:Symbol {uid: r.uid})
                    SET s.scan_id        = r.scan_id,
                        s.repository_id  = r.repository_id,
                        s.symbol_key     = r.symbol_key,
                        s.lang           = r.lang,
                        s.path           = r.path,
                        s.qualified_name = r.qualified_name,
                        s.kind           = r.kind
                    """,
                    rows=batch,
                ).consume()
                nodes += res.counters.nodes_created

            # 관계 타입은 파라미터로 바인딩되지 않는다. 화이트리스트로만 보간한다.
            for edge_type, rel in EDGE_TYPE_TO_REL.items():
                rows = list(self._read_edges(scan_id, edge_type))
                if not rows:
                    continue
                for batch in _chunk(iter(rows), BATCH):
                    res = sess.run(
                        f"""
                        UNWIND $rows AS r
                        MATCH (a:Symbol {{uid: r.src_uid}})
                        MATCH (b:Symbol {{uid: r.dst_uid}})
                        MERGE (a)-[e:{rel}]->(b)
                        SET e.scan_id    = r.scan_id,
                            e.confidence = r.confidence,
                            e.call_line  = r.call_line
                        """,
                        rows=batch,
                    ).consume()
                    edges += res.counters.relationships_created

        self._record(scan_id, nodes, edges, error=None)
        log.info("scan %s 투영 완료 — 노드 %d, 엣지 %d", scan_id, nodes, edges)
        return ProjectionResult(backend=self.backend, node_count=nodes, edge_count=edges)

    def drop(self, scan_id: str) -> ProjectionResult:
        self._delete_nodes(scan_id)
        with tx() as cur:
            cur.execute("DELETE FROM graph_projection WHERE scan_id = %s", (scan_id,))
        return ProjectionResult(backend=self.backend)

    # ------------------------------------------------------------------ query
    def callers_within(
        self, scan_id: str, target_symbol_key: str, depth: int = 3
    ) -> list[CallerPath]:
        """PostgreSQL callers_within() 과 같은 계약.

        ORDER BY 를 collect 앞에 두어 심볼당 최단 경로 하나만 남긴다.
        path 는 nodes(p) 순서 그대로 [caller, ..., target] 이다.
        """
        depth = check_depth(depth)  # 보간 전 반드시 통과
        cypher = f"""
            MATCH p = (caller:Symbol {{scan_id: $scan_id}})
                      -[:CALLS*1..{depth}]->
                      (target:Symbol {{uid: $target_uid}})
            WHERE caller.uid <> $target_uid
            WITH caller, p, length(p) AS hops
            ORDER BY hops
            WITH caller,
                 collect({{hops: hops, path: [n IN nodes(p) | n.symbol_key]}})[0] AS best
            RETURN caller.symbol_key AS symbol_key,
                   best.hops         AS hops,
                   best.path         AS path
            ORDER BY hops, symbol_key
        """
        with self._driver.session() as sess:
            rows = sess.run(
                cypher,
                scan_id=scan_id,
                target_uid=_uid(scan_id, target_symbol_key),
            ).data()
        return [
            CallerPath(symbol_key=r["symbol_key"], hops=r["hops"], path=tuple(r["path"]))
            for r in rows
        ]

    # ---------------------------------------------------------------- internal
    def _delete_nodes(self, scan_id: str) -> None:
        """관계까지 함께 지운다. 큰 스냅샷에서 트랜잭션이 터지지 않게 나눠서."""
        with self._driver.session() as sess:
            while True:
                res = sess.run(
                    "MATCH (s:Symbol {scan_id: $scan_id}) "
                    "WITH s LIMIT $limit DETACH DELETE s",
                    scan_id=scan_id,
                    limit=BATCH,
                ).consume()
                if res.counters.nodes_deleted == 0:
                    break

    def _read_symbols(self, scan_id: str):
        with tx() as cur:
            cur.execute(
                """
                SELECT symbol_key, lang, path, qualified_name, kind::text AS kind,
                       repository_id::text AS repository_id
                  FROM symbol WHERE scan_id = %s
                """,
                (scan_id,),
            )
            for r in cur:
                yield {
                    "uid": _uid(scan_id, r["symbol_key"]),
                    "scan_id": scan_id,
                    "repository_id": r["repository_id"],
                    "symbol_key": r["symbol_key"],
                    "lang": r["lang"],
                    "path": r["path"],
                    "qualified_name": r["qualified_name"],
                    "kind": r["kind"],
                }

    def _read_edges(self, scan_id: str, edge_type: str):
        # resolved=false 엣지는 투영하지 않는다. 정본에는 남아 있고,
        # 해석 정확도를 올린 뒤 재투영하면 그래프에 들어온다.
        with tx() as cur:
            cur.execute(
                """
                SELECT src_symbol_key, dst_symbol_key, confidence, call_line
                  FROM symbol_edge
                 WHERE scan_id = %s AND edge_type = %s::edge_type AND resolved
                """,
                (scan_id, edge_type),
            )
            for r in cur:
                yield {
                    "scan_id": scan_id,
                    "src_uid": _uid(scan_id, r["src_symbol_key"]),
                    "dst_uid": _uid(scan_id, r["dst_symbol_key"]),
                    "confidence": r["confidence"],
                    "call_line": r["call_line"],
                }

    def _record(self, scan_id: str, nodes: int, edges: int, error: str | None) -> None:
        with tx() as cur:
            cur.execute(
                """
                INSERT INTO graph_projection (scan_id, backend, node_count, edge_count,
                                              projected_at, stale, error)
                VALUES (%s, 'neo4j', %s, %s, now(), false, %s)
                ON CONFLICT (scan_id) DO UPDATE
                    SET backend = 'neo4j', node_count = EXCLUDED.node_count,
                        edge_count = EXCLUDED.edge_count,
                        projected_at = now(), stale = false, error = EXCLUDED.error
                """,
                (scan_id, nodes, edges, error),
            )


def _chunk(it: Iterable[dict], size: int):
    batch: list[dict[str, Any]] = []
    for item in it:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
