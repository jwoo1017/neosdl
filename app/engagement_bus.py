"""Engagement 실시간 버스 — 자율 검증 run 의 진행(task/subtask/action/상태/승인요청)을
engagement 를 보고 있는 WebSocket 연결들에 fan-out.

채팅과의 결정적 차이: 채팅 파이프라인은 웹 프로세스(threadpool)에서 돌아 소켓에 바로 밀 수
있지만, engagement 는 **워커 프로세스**에서 돈다 — 워커는 웹 소켓을 못 만진다. 그래서
`chat_bus`와 똑같이 Postgres NOTIFY 로 넘기고 웹이 fan-out 한다. payload 는 참조만 싣고
내용은 조회해서 채운다(NOTIFY 8KB 제한).
"""

from __future__ import annotations

import json

from starlette.concurrency import run_in_threadpool

from app.db import tx
from app.pg_listener import PgListenerBus

_CHANNEL = "neosdl_engagement"


def notify_engagement_event(cur, engagement_id, event: dict) -> None:
    """engagement 상태/행동 쓰기와 같은 트랜잭션에서 호출 — 커밋 시에만 발화(경합 없음).

    event 는 작은 참조 dict: {"kind": "status|task|subtask|action|approval_request", "ref_id": ...}.
    """
    cur.execute(
        "SELECT pg_notify(%s, %s)",
        (_CHANNEL, json.dumps({"engagement_id": str(engagement_id), **event}, default=str)),
    )


def notify_now(engagement_id, event: dict) -> None:
    """메인 run tx 와 **별개의 짧은 tx**로 즉시 pg_notify — 스텝 진행을 실시간으로 민다.

    엔게이지먼트는 단일 tx 에서 돌아 notify_engagement_event(같은 tx)는 커밋 시(=끝)에만 발화한다.
    라이브(채팅/EngageTab 타임라인)를 위해선 중간에 발화해야 하므로, 풀에서 다른 커넥션을 받아
    즉시 커밋하는 이 함수를 쓴다. 실패는 무시(best-effort — 라이브 못 밀어도 최종 결과는 영속됨).
    NOTIFY 8KB 제한이라 큰 출력은 잘라 싣는다(전체는 최종 리포트에).
    """
    def _clip(v):
        return v[:600] + "…" if isinstance(v, str) and len(v) > 600 else v

    payload = json.dumps({"engagement_id": str(engagement_id), **event}, default=str)
    if len(payload.encode("utf-8")) > 7500:
        payload = json.dumps({"engagement_id": str(engagement_id),
                              **{k: _clip(v) for k, v in event.items()}}, default=str)
    try:
        with tx() as cur:
            cur.execute("SELECT pg_notify(%s, %s)", (_CHANNEL, payload))
    except Exception:
        import logging
        logging.getLogger("app.engagement_bus").debug("notify_now 발화 실패", exc_info=True)


def _fetch_engagement(engagement_id: str) -> dict | None:
    with tx() as cur:
        cur.execute(
            "SELECT id, project_id, status, mode, permission_mode, goal, updated_at "
            "FROM engagement WHERE id=%s",
            (engagement_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": str(row["id"]), "project_id": str(row["project_id"]), "status": row["status"],
        "mode": row["mode"], "permission_mode": row["permission_mode"], "goal": row["goal"],
        "updated_at": row["updated_at"].isoformat(),
    }


class EngagementBus(PgListenerBus):
    """engagement_id 별 fan-out. 연결/재연결·스레드 관리는 app/pg_listener.py 공용 뼈대."""

    channel = _CHANNEL
    name = "engagement_bus"

    async def _dispatch(self, payload: str) -> None:
        try:
            data = json.loads(payload)
            engagement_id = data["engagement_id"]
        except Exception:
            self._log.exception("engagement_bus: payload 파싱 실패: %r", payload)
            return

        subs = self.subscribers(engagement_id)
        if not subs:
            return

        engagement = await run_in_threadpool(_fetch_engagement, engagement_id)
        if engagement is None:
            return
        event = {"type": "engagement", "event": {k: v for k, v in data.items() if k != "engagement_id"},
                 "engagement": engagement}
        for q in subs:
            q.put_nowait(event)


bus = EngagementBus()
