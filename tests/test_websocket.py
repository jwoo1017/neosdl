"""WebSocket 운영 보완 — Origin 검사(CSWSH), 열린 소켓의 세션 재검증, 실시간 버스 자동 재연결.

실제 Postgres 가 필요하다(없으면 skip). 앱 lifespan(버스 시작)이 돌도록 `with TestClient(app)` 로 연다.
"""

from __future__ import annotations

import time

import pytest
from starlette.websockets import WebSocketDisconnect

from app import auth, chat, chat_bus
from app.config import settings


def _team_session(owner):
    c = owner.client
    ws = c.post("/api/workspaces", json={"name": f"{owner.nickname}-ws", "type": "company"}).json()
    ts = c.post(f"/api/workspaces/{ws['id']}/teamspaces", json={"name": "실시간팀"}).json()
    proj = c.post(f"/api/teamspaces/{ts['id']}/projects", json={"name": "WS검증"}).json()
    session = c.post(f"/api/projects/{proj['id']}/chat/sessions", json={}).json()
    return proj["id"], session["id"]


def _origin() -> str:
    return auth._origin_of(settings().frontend_url)


@pytest.fixture
def live_client(api_users):
    """lifespan(실시간 버스 시작)이 도는 클라이언트 — 로그인 쿠키는 alice 것을 옮겨 쓴다."""
    from fastapi.testclient import TestClient

    from app.main import app

    alice = api_users.new("alice")
    with TestClient(app) as c:
        c.cookies = alice.client.cookies
        yield alice, c


def _ws_path(project_id, session_id):
    return f"/api/ws/projects/{project_id}/chat/sessions/{session_id}"


@pytest.mark.integration
def test_cross_site_origin_is_rejected_and_same_origin_connects(live_client):
    alice, c = live_client
    project_id, session_id = _team_session(alice)

    with pytest.raises(WebSocketDisconnect) as exc:
        with c.websocket_connect(_ws_path(project_id, session_id), headers={"origin": "https://evil.example"}):
            pass
    assert exc.value.code == 4403

    with c.websocket_connect(_ws_path(project_id, session_id), headers={"origin": _origin()}) as ws:
        ws.send_json({"type": "ping"})   # 무시되는 프레임 — 연결이 열려 있는지만 확인


@pytest.mark.integration
def test_open_socket_is_closed_after_session_revoked(live_client, db, monkeypatch):
    alice, c = live_client
    project_id, session_id = _team_session(alice)
    monkeypatch.setattr(auth, "WS_REVALIDATE_SECONDS", 0.2)

    with c.websocket_connect(_ws_path(project_id, session_id), headers={"origin": _origin()}) as ws:
        with db() as cur:   # 비밀번호 변경/탈퇴와 같은 효과
            cur.execute("UPDATE member SET token_version = token_version + 1 WHERE id=%s", (alice.id,))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


@pytest.mark.integration
def test_chat_bus_reconnects_after_db_connection_drop(live_client):
    alice, c = live_client
    project_id, session_id = _team_session(alice)

    with c.websocket_connect(_ws_path(project_id, session_id), headers={"origin": _origin()}) as ws:
        before = chat_bus.bus.connect_count
        chat_bus.bus.drop_connection()          # Postgres 재시작과 같은 효과
        deadline = time.monotonic() + 15
        while chat_bus.bus.connect_count == before and time.monotonic() < deadline:
            time.sleep(0.1)
        assert chat_bus.bus.connect_count > before, "리스너가 재연결하지 않았다"

        # 재연결 이후의 새 메시지가 열린 소켓으로 다시 전달된다.
        chat._insert_user_message(session_id, project_id, "재연결 후 질문", alice.id, [], None)
        event = ws.receive_json()
        assert event["type"] == "message" and event["message"]["content"] == "재연결 후 질문"


@pytest.mark.integration
def test_unauthenticated_socket_gets_4401_after_accept(live_client):
    """수락 후 4401 로 닫아야 브라우저가 '로그인 만료'로 구분해 재시도를 멈추고 로그인으로 보낼 수 있다."""
    from fastapi.testclient import TestClient

    from app.main import app

    alice, c = live_client
    project_id, session_id = _team_session(alice)
    anon = TestClient(app)
    with anon.websocket_connect(_ws_path(project_id, session_id), headers={"origin": _origin()}) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401
