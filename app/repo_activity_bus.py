"""Postgres LISTEN/NOTIFY 기반 저장소 활동(푸시/스캔/수정/PR) 실시간 버스.

`app/chat_bus.py`와 같은 이유로 Redis 없이 이걸 쓴다(동기 psycopg 연결 + 전용
백그라운드 스레드 — Windows ProactorEventLoop에서 async LISTEN/NOTIFY가 안
되는 사정도 동일 — 연결·재연결·스레드는 app/pg_listener.py 공용 뼈대). 다만 채팅과
달리 fan-out 처리(_dispatch)는 별도 클래스로 둔다 — 결정적 차이는 payload 설계다.

채팅은 NOTIFY에 `{session_id, message_id}`만 싣고 실제 내용은 `_fetch_message`로
1회 재조회한다(메시지 본문이 8000바이트 NOTIFY 제한을 넘을 수 있어서). 여기
이벤트들(`push_received`, `scan_started` 등)은 작고, 무엇보다 `push_received`/
`scan_started`처럼 "다시 읽어올 단일 행"이 없는 이벤트가 있다 — 그래서 payload
자체에 프론트가 필요로 하는 정보를 전부 담아 보낸다(fetch-back 단계 없음).

재연결 시 과거 이벤트 재생(replay)은 하지 않는다 — NOTIFY는 그 순간 LISTEN 중인
연결에만 가고, 구독 이전 이벤트는 유실된다. 재연결한 클라이언트는
`GET /repositories/{id}/activity/feed`(app/repo_activity.py)로 현재 상태를
복원한 뒤 이 버스로 실시간 델타만 받는다.
"""

from __future__ import annotations

import json

from app.pg_listener import PgListenerBus

_CHANNEL = "neosdl_repo_activity"


def notify_repo_activity(cur, repository_id, event: dict) -> None:
    """repository 상태 변화와 같은 트랜잭션 안에서 호출한다(chat_bus.notify_chat_event와 동일 계약).

    호출부는 전부 fail-open으로 감싸져 있어야 한다 — 알림 실패가 웹훅/스캔/
    수정 생성 파이프라인을 막으면 안 된다.
    """
    cur.execute(
        "SELECT pg_notify(%s, %s)",
        (_CHANNEL, json.dumps({"repository_id": str(repository_id), **event}, default=str)),
    )


class RepoActivityBus(PgListenerBus):
    """저장소별 fan-out. 연결/재연결·스레드 관리는 app/pg_listener.py 공용 뼈대."""

    channel = _CHANNEL
    name = "repo_activity_bus"

    async def _dispatch(self, payload: str) -> None:
        try:
            event = json.loads(payload)
            repository_id = event["repository_id"]
        except Exception:
            self._log.exception("repo_activity_bus: 알림 payload 파싱 실패: %r", payload)
            return

        for q in self.subscribers(repository_id):  # 아무도 안 보고 있으면 빈 목록
            q.put_nowait(event)



bus = RepoActivityBus()
