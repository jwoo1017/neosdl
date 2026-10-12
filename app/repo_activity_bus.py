"""Postgres LISTEN/NOTIFY 기반 저장소 활동(푸시/스캔/수정/PR) 실시간 버스.

`app/chat_bus.py`와 같은 이유로 Redis 없이 이걸 쓴다(동기 psycopg 연결 + 전용
백그라운드 스레드 — Windows ProactorEventLoop에서 async LISTEN/NOTIFY가 안
되는 사정도 동일). 다만 채팅과 달리 이 채널은 `ChatBus`를 재사용하지 않고
별도 클래스로 둔다 — 결정적 차이는 payload 설계다.

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

import asyncio
import json
import logging
import threading

import psycopg
from starlette.concurrency import run_in_threadpool

from app.config import settings

log = logging.getLogger("app.repo_activity_bus")

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


class RepoActivityBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._conn: psycopg.Connection | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def subscribe(self, repository_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(repository_id, set()).add(q)
        return q

    def unsubscribe(self, repository_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(repository_id)
        if not subs:
            return
        subs.discard(q)
        if not subs:
            self._subs.pop(repository_id, None)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._conn = psycopg.connect(settings().database_url, autocommit=True)
        self._conn.execute(f"LISTEN {_CHANNEL}")
        self._thread = threading.Thread(target=self._listen_thread, daemon=True,
                                         name="repo-activity-bus-listener")
        self._thread.start()
        log.info("repo_activity_bus: LISTEN %s 시작(백그라운드 스레드)", _CHANNEL)

    async def stop(self) -> None:
        self._stopping.set()
        if self._conn:
            self._conn.close()
        if self._thread:
            self._thread.join(timeout=2)

    def _listen_thread(self) -> None:
        assert self._conn is not None
        try:
            for notify in self._conn.notifies():
                if self._loop is None or self._loop.is_closed():
                    continue
                asyncio.run_coroutine_threadsafe(self._dispatch(notify.payload), self._loop)
        except Exception:
            if not self._stopping.is_set():
                log.exception("repo_activity_bus: 리스너 스레드 중단됨")

    async def _dispatch(self, payload: str) -> None:
        try:
            event = json.loads(payload)
            repository_id = event["repository_id"]
        except Exception:
            log.exception("repo_activity_bus: 알림 payload 파싱 실패: %r", payload)
            return

        subs = self._subs.get(repository_id)
        if not subs:
            return  # 아무도 이 저장소를 보고 있지 않음
        for q in list(subs):
            q.put_nowait(event)


bus = RepoActivityBus()
