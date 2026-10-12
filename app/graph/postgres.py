"""postgres_cte 구현 — 정본이 곧 그래프인 경우.

무거운 로직은 없다. 순회는 이미 DB 함수 callers_within() 안에 있고
그건 검증됐다. 여기서 하는 일은 그 결과를 GraphStore 계약에 맞추는 것뿐이다.
"""

from app.db import tx
from app.graph import CallerPath, ProjectionResult, check_depth


class PostgresGraphStore:
    backend = "postgres_cte"

    def health(self) -> bool:
        try:
            with tx() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() is not None
        except Exception:  # noqa: BLE001
            return False

    def callers_within(
        self, scan_id: str, target_symbol_key: str, depth: int = 3
    ) -> list[CallerPath]:
        depth = check_depth(depth)
        with tx() as cur:
            cur.execute(
                "SELECT symbol_key, hops, path FROM callers_within(%s, %s, %s)"
                " ORDER BY hops, symbol_key",
                (scan_id, target_symbol_key, depth),
            )
            rows = cur.fetchall()

        # DB 함수는 대상에서 바깥으로 경로를 쌓으므로 [target, ..., caller] 로 나온다.
        # 계약은 [caller, ..., target] 이므로 여기서 뒤집는다.
        # 검증된 SQL 을 건드리지 않고 계약을 맞추는 자리가 여기다.
        return [
            CallerPath(
                symbol_key=r["symbol_key"],
                hops=r["hops"],
                path=tuple(reversed(r["path"])),
            )
            for r in rows
        ]

    def project(self, scan_id: str) -> ProjectionResult:
        """투영할 데가 없다. 정본이 곧 그래프다."""
        with tx() as cur:
            cur.execute(
                """
                INSERT INTO graph_projection (scan_id, backend, stale, projected_at,
                                              node_count, edge_count)
                SELECT %s, 'postgres_cte', false, now(),
                       (SELECT count(*) FROM symbol WHERE scan_id = %s),
                       (SELECT count(*) FROM symbol_edge
                         WHERE scan_id = %s AND resolved)
                ON CONFLICT (scan_id) DO UPDATE
                    SET backend = 'postgres_cte', stale = false, projected_at = now(),
                        node_count = EXCLUDED.node_count,
                        edge_count = EXCLUDED.edge_count, error = NULL
                RETURNING node_count, edge_count
                """,
                (scan_id, scan_id, scan_id),
            )
            row = cur.fetchone()
        return ProjectionResult(
            backend=self.backend,
            node_count=row["node_count"],
            edge_count=row["edge_count"],
            skipped=True,
            detail="정본이 곧 그래프이므로 투영하지 않는다",
        )

    def drop(self, scan_id: str) -> ProjectionResult:
        with tx() as cur:
            cur.execute("DELETE FROM graph_projection WHERE scan_id = %s", (scan_id,))
        return ProjectionResult(backend=self.backend, skipped=True)
