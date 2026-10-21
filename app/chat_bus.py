"""Postgres LISTEN/NOTIFY 기반 프로젝트 채팅 실시간 버스.

Redis를 새로 들이지 않는다 — `app/queue.py`가 이미 명시한 이유(폐쇄망 납품
환경에서 컴포넌트 하나를 줄이는 것)와 같다. 앱 전체에 전용 연결 하나만 열어
`LISTEN`하고, 알림이 오면 session_id로 구독 중인 WebSocket 연결들에 fan-out한다
(sql/018로 프로젝트 채팅이 세션 단위로 쪼개졌으므로, 브로드캐스트도 프로젝트
전체가 아니라 그 세션을 보고 있는 연결에만 간다).

리스너는 **동기** psycopg 연결 + 전용 백그라운드 스레드로 돈다(async가 아님) —
psycopg의 async LISTEN/NOTIFY는 Windows 기본 이벤트 루프(ProactorEventLoop)에서
동작하지 않고, uvicorn이 그 정책을 앱 시작 시점에 다시 강제로 세팅해버려서
import 시점에 selector 루프로 바꿔치기하는 것도 소용없었다(uvicorn
Server.run()이 나중에 다시 덮어씀). 동기 연결의 `notifies()`는 이런 루프
제약이 전혀 없어 더 단순하고 견고하다 — 백그라운드 스레드에서 블로킹
대기하다가 알림이 오면 `asyncio.run_coroutine_threadsafe`로 메인 이벤트
루프(WebSocket 연결들이 사는 곳)에 안전하게 넘긴다.

토큰 단위 스트리밍은 이 버스를 거치지 않는다(app/chat.py 참고 — 그 질문을
보낸 연결에만 로컬로 직접 전송한다). 이 버스로는 완성된 메시지(사용자 메시지는
즉시, AI 답변은 생성 완료 시)만 브로드캐스트된다 — Postgres NOTIFY로 토큰마다
방송하면 호출이 폭주하므로 의도적으로 뺐다.

NOTIFY payload는 작게 유지한다(Postgres 제한 8000바이트 — 보안 findings 같은
evidence는 그보다 클 수 있다) — 알림에는 `{project_id, message_id}` 참조만
싣고, 실제 내용은 `_fetch_message`로 1회 조회해서 채운 뒤 구독자들에게 전달한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

import psycopg
from starlette.concurrency import run_in_threadpool

from app import profiles
from app.config import settings
from app.db import tx

log = logging.getLogger("app.chat_bus")

_CHANNEL = "neosdl_chat"


def _fetch_message(message_id: int) -> dict | None:
    """websocket.send_json은 표준 json.dumps라 datetime/UUID를 못 그대로 실어보낸다 —
    여기서 문자열로 변환해 둔다(프론트는 다시 Date로 안 만들고 그대로 표시만 함)."""
    with tx() as cur:
        cur.execute(
            "SELECT id, project_id, session_id, role, content, evidence, tools_used, created_at, author_member_id "
            "FROM chat_message WHERE id=%s",
            (message_id,),
        )
        row = cur.fetchone()
        author = None
        if row is not None and row["author_member_id"]:
            # 다른 사람이 보낸 질문이 실시간으로 도착할 때도 말풍선 옆 프로필·역할을 그릴 수 있게.
            author = profiles.load_authors(cur, [row["author_member_id"]], str(row["project_id"])).get(
                str(row["author_member_id"]))
    if row is None:
        return None
    return {
        "id": row["id"],
        "project_id": str(row["project_id"]),
        "session_id": str(row["session_id"]),
        "role": row["role"],
        "content": row["content"],
        "evidence": row["evidence"],
        "tools_used": row["tools_used"],
        "created_at": row["created_at"].isoformat(),
        "author": author,
    }


def notify_chat_event(cur, session_id, message_id: int) -> None:
    """chat_message INSERT와 같은 트랜잭션 안에서 호출한다 — 커밋 시점에만
    실제 전달되므로("행이 아직 안 보이는데 알림만 먼저 옴" 경합 없음) 안전하다.
    """
    cur.execute(
        "SELECT pg_notify(%s, %s)",
        (_CHANNEL, json.dumps({"session_id": str(session_id), "message_id": message_id})),
    )


class ChatBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._conn: psycopg.Connection | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(session_id, set()).add(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(session_id)
        if not subs:
            return
        subs.discard(q)
        if not subs:
            self._subs.pop(session_id, None)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._conn = psycopg.connect(settings().database_url, autocommit=True)
        self._conn.execute(f"LISTEN {_CHANNEL}")
        self._thread = threading.Thread(target=self._listen_thread, daemon=True, name="chat-bus-listener")
        self._thread.start()
        log.info("chat_bus: LISTEN %s 시작(백그라운드 스레드)", _CHANNEL)

    async def stop(self) -> None:
        self._stopping.set()
        if self._conn:
            self._conn.close()  # notifies() 제너레이터가 대기 중이던 소켓이 끊기며 스레드가 빠져나온다
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
                log.exception("chat_bus: 리스너 스레드 중단됨")

    async def _dispatch(self, payload: str) -> None:
        try:
            data = json.loads(payload)
            session_id = data["session_id"]
            message_id = data["message_id"]
        except Exception:
            log.exception("chat_bus: 알림 payload 파싱 실패: %r", payload)
            return

        subs = self._subs.get(session_id)
        if not subs:
            return  # 아무도 이 세션을 보고 있지 않음 — DB 조회도 아낀다

        message = await run_in_threadpool(_fetch_message, message_id)
        if message is None:
            return
        event = {"type": "message", "message": message}
        for q in list(subs):
            q.put_nowait(event)


bus = ChatBus()
