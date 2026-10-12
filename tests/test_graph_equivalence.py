"""두 GraphStore 구현이 같은 답을 내는지 대조한다.

이 테스트가 이 프로젝트에서 가장 중요한 테스트다.

Neo4j Community 를 쓰면서 GPLv3 에 묶이지 않는 논리 전체가
"Neo4j 없이도 같은 판정이 나온다"는 한 문장 위에 서 있다.
그 문장이 참인지 검사하는 것은 여기뿐이다. 깨지면 폴백은 폴백이 아니라
다른 제품이고, 라이선스 안전성 주장도 같이 무너진다.

실행:
    docker compose -f docker-compose.dev.yml up -d
    NEO4J_URI=bolt://localhost:7687 pytest tests/test_graph_equivalence.py -v

NEO4J_URI 가 없으면 Neo4j 비교 부분은 skip 된다 (폴백 단독 검사만 수행).
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.config import settings
from app.db import tx
from app.graph import CallerPath
from app.graph.postgres import PostgresGraphStore

REPO_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PARSER = "test"


# --------------------------------------------------------------------- fixture
@pytest.fixture(scope="module")
def scan_id() -> str:
    """호출 사슬을 하나 심는다.

        Router.route → Handler.handle → Scorer.score   (핵심 IP)
        Batch.run    → Scorer.score
        Orphan.noop  → (없음)
        Cycle.a ⇄ Cycle.b → Handler.handle             (순환 포함)
    """
    sid = str(uuid.uuid4())
    with tx() as cur:
        cur.execute(
            """
            INSERT INTO repository (id, provider, remote_id, namespace, name, clone_url)
            VALUES (%s,'gitlab','test-4021','finble','equiv','file:///dev/null')
            ON CONFLICT (provider, remote_id) DO NOTHING
            """,
            (str(REPO_ID),),
        )
        cur.execute(
            """
            INSERT INTO scan (id, repository_id, commit_sha, ref, parser_version, status)
            VALUES (%s, %s, %s, 'refs/heads/main', %s, 'succeeded')
            """,
            (sid, str(REPO_ID), uuid.uuid4().hex + uuid.uuid4().hex[:8], PARSER),
        )
        cur.execute(
            "INSERT INTO code_file (scan_id, path, lang, size_bytes, content_hash)"
            " VALUES (%s,'src/All.java','java',1,%s) RETURNING id",
            (sid, "0" * 64),
        )
        file_id = cur.fetchone()["id"]

        names = ["Router.route", "Handler.handle", "Scorer.score",
                 "Batch.run", "Orphan.noop", "Cycle.a", "Cycle.b"]
        keys: dict[str, str] = {}
        for n in names:
            cur.execute(
                """
                INSERT INTO symbol (scan_id, file_id, repository_id, lang, path,
                                    qualified_name, param_types, kind,
                                    start_line, end_line)
                VALUES (%s,%s,%s,'java','src/All.java',%s,'{}','method',1,2)
                RETURNING symbol_key
                """,
                (sid, file_id, str(REPO_ID), f"com.finble.{n}"),
            )
            keys[n] = cur.fetchone()["symbol_key"]

        chain = [
            ("Router.route", "Handler.handle"),
            ("Handler.handle", "Scorer.score"),
            ("Batch.run", "Scorer.score"),
            ("Cycle.a", "Cycle.b"),
            ("Cycle.b", "Cycle.a"),      # 순환
            ("Cycle.b", "Handler.handle"),
        ]
        for src, dst in chain:
            cur.execute(
                """
                INSERT INTO symbol_edge (scan_id, src_symbol_key, dst_symbol_key,
                                         edge_type, resolved, confidence)
                VALUES (%s,%s,%s,'calls',true,1.0)
                """,
                (sid, keys[src], keys[dst]),
            )
        # 미해석 엣지 — 투영되지 않아야 한다
        cur.execute(
            """
            INSERT INTO symbol_edge (scan_id, src_symbol_key, dst_hint,
                                     edge_type, resolved, confidence)
            VALUES (%s,%s,'reflect(x)','calls',false,0.2)
            """,
            (sid, keys["Orphan.noop"]),
        )

    pytest.KEYS = keys          # type: ignore[attr-defined]
    yield sid

    with tx() as cur:
        cur.execute("DELETE FROM scan WHERE id=%s", (sid,))


@pytest.fixture(scope="module")
def neo(scan_id):
    if not os.environ.get("NEO4J_URI") and not settings().neo4j_uri:
        pytest.skip("NEO4J_URI 없음 — Neo4j 비교 생략")
    from app.graph.neo4j_store import Neo4jGraphStore

    cfg = settings()
    store = Neo4jGraphStore(cfg.neo4j_uri, cfg.neo4j_user or "neo4j",
                            cfg.neo4j_password or "")
    if not store.health():
        pytest.skip("Neo4j 응답 없음")
    store.project(scan_id)
    yield store
    store.drop(scan_id)
    store.close()


def _norm(paths: list[CallerPath]) -> list[tuple]:
    return sorted((p.symbol_key, p.hops, p.path) for p in paths)


# ----------------------------------------------------------------------- tests
def test_postgres_path_order_is_caller_first(scan_id):
    """계약: path 는 [호출자 → ... → 대상]. DB 함수 순서와 반대다."""
    pg = PostgresGraphStore()
    target = pytest.KEYS["Scorer.score"]           # type: ignore[attr-defined]
    got = {p.symbol_key: p for p in pg.callers_within(scan_id, target, 3)}

    router = got[pytest.KEYS["Router.route"]]      # type: ignore[attr-defined]
    assert router.hops == 2
    assert router.path[0] == pytest.KEYS["Router.route"]   # type: ignore[attr-defined]
    assert router.path[-1] == target


@pytest.mark.parametrize("depth", [1, 2, 3, 4])
def test_same_result_both_backends(scan_id, neo, depth):
    """같은 데이터, 같은 질문 → 같은 답."""
    pg = PostgresGraphStore()
    target = pytest.KEYS["Scorer.score"]           # type: ignore[attr-defined]
    assert _norm(pg.callers_within(scan_id, target, depth)) == \
           _norm(neo.callers_within(scan_id, target, depth))


def test_cycle_terminates(scan_id, neo):
    """Cycle.a ⇄ Cycle.b 가 있어도 무한 순회하지 않고 같은 답을 낸다."""
    pg = PostgresGraphStore()
    target = pytest.KEYS["Handler.handle"]         # type: ignore[attr-defined]
    assert _norm(pg.callers_within(scan_id, target, 4)) == \
           _norm(neo.callers_within(scan_id, target, 4))


def test_unresolved_edges_not_projected(scan_id, neo):
    """미해석 엣지는 그래프에 올라가지 않는다. 정본에는 남아 있다."""
    orphan = pytest.KEYS["Orphan.noop"]            # type: ignore[attr-defined]
    for target in (pytest.KEYS["Scorer.score"],    # type: ignore[attr-defined]
                   pytest.KEYS["Handler.handle"]): # type: ignore[attr-defined]
        assert orphan not in {p.symbol_key for p in neo.callers_within(scan_id, target, 4)}

    with tx() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM symbol_edge WHERE scan_id=%s AND NOT resolved",
            (scan_id,),
        )
        assert cur.fetchone()["n"] == 1


def test_projection_is_idempotent(scan_id, neo):
    """스캔은 불변 스냅샷이므로 재투영이 언제나 안전해야 한다."""
    target = pytest.KEYS["Scorer.score"]           # type: ignore[attr-defined]
    before = _norm(neo.callers_within(scan_id, target, 3))
    neo.project(scan_id)
    neo.project(scan_id)
    assert _norm(neo.callers_within(scan_id, target, 3)) == before


def test_depth_is_not_injectable(scan_id, neo):
    """depth 는 Cypher 에 문자열 보간되는 유일한 자리다."""
    import pytest as _pytest

    for bad in ["3; MATCH (n) DETACH DELETE n", 0, 99, None, 2.5, True]:
        with _pytest.raises((ValueError, TypeError)):
            neo.callers_within(scan_id, "x", bad)   # type: ignore[arg-type]
