"""엔게이지먼트 UI 백엔드 보강 — GET /engagements/{id} 확장 형상 + WS 접근 제어.

실제 Postgres 가 필요하다(없으면 skip). WS 는 lifespan(engagement_bus 시작)이 돌도록
`with TestClient(app)` 로 연다 — test_websocket.py 패턴.
"""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from app import auth
from app.config import settings


def _project(owner):
    c = owner.client
    ws = c.post("/api/workspaces", json={"name": f"{owner.nickname}-ws", "type": "company"}).json()
    ts = c.post(f"/api/workspaces/{ws['id']}/teamspaces", json={"name": "검증팀"}).json()
    return c.post(f"/api/teamspaces/{ts['id']}/projects", json={"name": "ENG검증"}).json()["id"]


def _insert_engagement(db, project_id, created_by):
    with db() as cur:
        cur.execute(
            "INSERT INTO engagement (project_id, goal, created_by) VALUES (%s,%s,%s) RETURNING id",
            (project_id, "테스트 검증 run", created_by))
        return str(cur.fetchone()["id"])


def _origin() -> str:
    return auth._origin_of(settings().frontend_url)


@pytest.fixture
def live_client(api_users):
    from fastapi.testclient import TestClient

    from app.main import app

    alice = api_users.new("alice")
    with TestClient(app) as c:
        c.cookies = alice.client.cookies
        yield alice, c


@pytest.mark.integration
def test_get_engagement_has_report_and_verifications_keys(api_users, db):
    alice = api_users.new("alice")
    project_id = _project(alice)
    eid = _insert_engagement(db, project_id, alice.id)

    res = alice.client.get(f"/api/engagements/{eid}")
    assert res.status_code == 200
    body = res.json()
    # 확장으로 추가된 키들 — 정적만 돈 engagement 라 비어 있어도 키는 있어야 상세가 그릴 수 있다.
    assert "report" in body and "verifications" in body
    assert body["verifications"] == []
    assert body["engagement"]["id"] == eid


@pytest.mark.integration
def test_engagement_ws_rejects_cross_site_and_accepts_same_origin(live_client, db):
    alice, c = live_client
    project_id = _project(alice)
    eid = _insert_engagement(db, project_id, alice.id)
    path = f"/api/ws/engagements/{eid}"

    with pytest.raises(WebSocketDisconnect) as exc:
        with c.websocket_connect(path, headers={"origin": "https://evil.example"}):
            pass
    assert exc.value.code == 4403

    with c.websocket_connect(path, headers={"origin": _origin()}) as ws:
        ws.send_json({"type": "ping"})   # 무시되는 프레임 — 연결이 열려 있는지만 확인


@pytest.mark.integration
def test_engagement_ws_unauthenticated_gets_4401(live_client, db):
    from fastapi.testclient import TestClient

    from app.main import app

    alice, c = live_client
    project_id = _project(alice)
    eid = _insert_engagement(db, project_id, alice.id)

    anon = TestClient(app)
    with anon.websocket_connect(f"/api/ws/engagements/{eid}", headers={"origin": _origin()}) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


@pytest.mark.integration
def test_engagement_ws_forbidden_for_non_member(live_client, api_users, db):
    alice, c = live_client
    project_id = _project(alice)
    eid = _insert_engagement(db, project_id, alice.id)

    bob = api_users.new("bob")   # alice 프로젝트에 속하지 않은 사용자
    from fastapi.testclient import TestClient

    from app.main import app

    other = TestClient(app)
    other.cookies = bob.client.cookies
    with other.websocket_connect(f"/api/ws/engagements/{eid}", headers={"origin": _origin()}) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4403
