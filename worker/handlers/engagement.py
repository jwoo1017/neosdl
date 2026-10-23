"""engagement.run 핸들러.

phase-1 정적 흐름: StaticAnalyst(보안 finding + 엔트리포인트 해석) → Reporter. 샌드박스 무의존이라
빠르므로 여기서 동기로 돈다. 긴 Pentester/Verifier(샌드박스 앱 기동)는 phase-2 에서 subtask.run
잡으로 분리한다(그때 queue.heartbeat 필요).

멱등·재개형: 이미 종료 상태면 아무것도 안 한다(job_reap 로 재큐돼도 이중 실행 방지).
scan_id 가 없으면 검증할 정적 스냅샷이 없으므로 failed 로 끝낸다.
"""

from __future__ import annotations

import logging

from app import agents, audit
from app.db import tx
from app.engagements import set_status

log = logging.getLogger("worker.engagement")

_TERMINAL = ("succeeded", "failed", "cancelled")


def handle(payload: dict) -> None:
    engagement_id = payload["engagement_id"]
    with tx() as cur:
        cur.execute(
            "SELECT id, status, project_id, scan_id, goal FROM engagement WHERE id=%s FOR UPDATE",
            (engagement_id,))
        row = cur.fetchone()
        if row is None:
            log.warning("engagement %s 없음 — 건너뜀", engagement_id)
            return
        if row["status"] in _TERMINAL:
            log.info("engagement %s 이미 %s — 건너뜀", engagement_id, row["status"])
            return
        if not row["scan_id"]:
            set_status(cur, engagement_id, "failed", error="검증 대상 스캔이 없습니다")
            return
        engagement = dict(row)
        set_status(cur, engagement_id, "running")

    try:
        with tx() as cur:
            cur.execute("SELECT status FROM engagement WHERE id=%s", (engagement_id,))
            if cur.fetchone()["status"] == "cancelled":   # 협조적 취소 지점
                return
            static_summary = agents.static_analyst(cur, engagement)
            agents.reporter(cur, engagement, static_summary)
            set_status(cur, engagement_id, "succeeded",
                       result={"phase": 1, "findings": static_summary["findings_total"],
                               "entrypoint_resolved": static_summary["entrypoint_resolved"]})
            audit.record(cur, actor="system:worker", action="engagement.finished",
                         subject={"engagement_id": engagement_id},
                         payload={"status": "succeeded", "phase": 1,
                                  "findings": static_summary["findings_total"]})
        log.info("engagement %s 완료(phase-1): finding %s건", engagement_id,
                 static_summary["findings_total"])
    except Exception as exc:
        log.exception("engagement %s 실패", engagement_id)
        with tx() as cur:
            set_status(cur, engagement_id, "failed", error=str(exc)[:2000])
