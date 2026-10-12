"""graph.project — 정본(PostgreSQL) → 파생(Neo4j) 투영. WBS 1.3.

이 작업이 실패해도 스캔은 실패가 아니다. 그래프는 성능 최적화이지
동작 조건이 아니므로, 실패는 graph_projection.stale=true 로 남기고
판정은 postgres_cte 로 계속 돈다.
"""

import logging

from app.db import tx
from app.graph import make_graph_store

log = logging.getLogger("worker.project")


def handle(payload: dict) -> None:
    scan_id = payload["scan_id"]
    store = make_graph_store()

    if not store.health():
        # Neo4j 가 꺼져 있다. 오류로 올리지 않는다 — 정상적인 배포 형태다.
        log.warning("graph backend(%s) 응답 없음 — 투영 건너뜀", store.backend)
        _mark_stale(scan_id, store.backend, "backend unreachable")
        return

    try:
        result = store.project(scan_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("scan %s 투영 실패", scan_id)
        _mark_stale(scan_id, store.backend, f"{type(exc).__name__}: {exc}")
        return                                    # 워커 재시도로 넘기지 않는다
    finally:
        close = getattr(store, "close", None)
        if close:
            close()

    log.info(
        "scan %s 투영 — backend=%s nodes=%d edges=%d%s",
        scan_id, result.backend, result.node_count, result.edge_count,
        f" ({result.detail})" if result.skipped else "",
    )


def _mark_stale(scan_id: str, backend: str, error: str) -> None:
    with tx() as cur:
        cur.execute(
            """
            INSERT INTO graph_projection (scan_id, backend, stale, error)
            VALUES (%s, %s::projection_backend, true, %s)
            ON CONFLICT (scan_id) DO UPDATE
                SET stale = true, error = EXCLUDED.error
            """,
            (scan_id, backend, error[:2000]),
        )
