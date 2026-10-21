"""Postgres LISTEN/NOTIFY 공용 리스너 — 실시간 버스(chat_bus, repo_activity_bus)의 공통 뼈대.

리스너는 **동기** psycopg 연결 + 전용 백그라운드 스레드로 돈다(자세한 이유는 chat_bus 모듈
docstring — Windows 이벤트 루프 제약). 알림이 오면 `asyncio.run_coroutine_threadsafe` 로 메인
이벤트 루프(WebSocket 연결들이 사는 곳)의 `_dispatch` 에 넘긴다.

자동 재연결: Postgres 재시작(백업·업데이트·컨테이너 재생성)이나 네트워크 단절로 연결이 끊겨도
스레드가 죽지 않고 지수 백오프(1s → 최대 30s)로 다시 연결해 다시 LISTEN 한다. 끊긴 걸 빨리
알아채도록 TCP keepalive 를 켜고, 알림이 없을 때도 30초마다 `SELECT 1` 로 연결을 확인한다.
끊겨 있던 동안의 NOTIFY 는 재생되지 않는다 — 클라이언트는 재접속 시 히스토리를 다시 불러온다.

하위 클래스는 `_dispatch(payload)`(코루틴)만 구현하면 된다.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import psycopg

from app.config import settings

HEALTHCHECK_SECONDS = 30
MAX_BACKOFF_SECONDS = 30


class PgListenerBus:
    """구독 키(session_id, repository_id 등)별 asyncio.Queue 로 fan-out 하는 버스."""

    channel: str = ""
    name: str = "pg-listener"

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._conn: psycopg.Connection | None = None
        self._conn_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._log = logging.getLogger(f"app.{self.name}")
        # 테스트·운영 점검용 — 지금까지 몇 번 (재)연결했는지.
        self.connect_count = 0

    # ------------------------------------------------------------------ 구독

    def subscribe(self, key: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(key, set()).add(q)
        return q

    def unsubscribe(self, key: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(key)
        if not subs:
            return
        subs.discard(q)
        if not subs:
            self._subs.pop(key, None)

    def subscribers(self, key: str) -> list[asyncio.Queue]:
        return list(self._subs.get(key) or ())

    # ------------------------------------------------------------------ 수명

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"{self.name}-listener")
        self._thread.start()

    async def stop(self) -> None:
        self._stopping.set()
        self._close_conn()  # 대기 중인 notifies() 를 깨워 스레드가 빠져나오게
        if self._thread:
            self._thread.join(timeout=2)

    def drop_connection(self) -> None:
        """연결을 강제로 끊는다(테스트용 — 실제 DB 재시작과 같은 효과). 스레드는 재연결한다."""
        self._close_conn()

    # ------------------------------------------------------------------ 내부

    def _close_conn(self) -> None:
        with self._conn_lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(
            settings().database_url,
            autocommit=True,
            # 반쯤 끊긴 연결(서버 재부팅·NAT 테이블 만료)을 OS 수준에서 빨리 감지
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
        )
        conn.execute(f"LISTEN {self.channel}")
        return conn

    def _run(self) -> None:
        delay = 1
        while not self._stopping.is_set():
            try:
                conn = self._connect()
                with self._conn_lock:
                    self._conn = conn
                self.connect_count += 1
                if self.connect_count == 1:
                    self._log.info("%s: LISTEN %s 시작(백그라운드 스레드)", self.name, self.channel)
                else:
                    self._log.info("%s: 재연결 성공 — LISTEN %s 재개", self.name, self.channel)
                delay = 1
                while not self._stopping.is_set():
                    for notify in conn.notifies(timeout=HEALTHCHECK_SECONDS):
                        self._forward(notify.payload)
                    if self._stopping.is_set():
                        break
                    conn.execute("SELECT 1")  # 알림이 없던 동안 연결이 살아 있는지 확인
            except Exception:
                if self._stopping.is_set():
                    break
                self._log.warning("%s: LISTEN 연결 끊김 — %ss 후 재연결", self.name, delay, exc_info=True)
            finally:
                self._close_conn()
            if self._stopping.wait(delay):
                break
            delay = min(delay * 2, MAX_BACKOFF_SECONDS)

    def _forward(self, payload: str) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._dispatch(payload), loop)

    async def _dispatch(self, payload: str) -> None:  # pragma: no cover - 하위 클래스가 구현
        raise NotImplementedError
