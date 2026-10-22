"""사내 협업 사이트용 가입·계정 정책(app/signup_policy.py, app/admin.py, sql/033).

실제 Postgres 가 필요하다(없으면 skip). 테스트 환경은 conftest 가 example.com 을 회사 도메인으로
넣어 두었으므로, 여기서는 "회사 밖" 주소로 외부 도메인(partner-corp.com)을 쓴다.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import email_verification, github_oauth, signup_policy
from app.config import settings

PW = "pw-12345678"
OUTSIDE = "partner-corp.com"


@pytest.fixture
def env(db, monkeypatch):
    from app.main import app

    codes: list[tuple[str, str, str]] = []   # (email, code, purpose)
    monkeypatch.setattr(email_verification, "_send_code_email",
                        lambda email, nickname, code, purpose="signup": codes.append((email, code, purpose)) or True)
    emails: list[str] = []

    class Env:
        pass

    e = Env()
    e.codes = codes
    e.client = lambda: TestClient(app)

    def signup(client, email=None, *, verify=True, **over):
        email = email or f"pol-{uuid.uuid4().hex[:10]}@example.com"
        emails.append(email.lower())
        body = {"email": email, "password": PW, "name": "홍길동", "nickname": "길동", "privacy_agreed": True, **over}
        r = client.post("/api/auth/signup", json=body)
        if r.status_code == 201 and verify:
            assert client.post("/api/auth/email-verification/verify",
                               json={"code": codes[-1][1]}).status_code == 204
        return r

    e.signup = signup
    e.track = emails.append
    yield e
    with db() as cur:
        cur.execute("SELECT id FROM member WHERE lower(email) = ANY(%s)", (emails,))
        ids = [r["id"] for r in cur.fetchall()]
        from tests.conftest import disable_last_admin_triggers
        disable_last_admin_triggers(cur)
        for mid in ids:
            cur.execute("DELETE FROM workspace WHERE created_by=%s", (mid,))
        for mid in ids:
            cur.execute("DELETE FROM member WHERE id=%s", (mid,))


def _make_admin(db, client):
    me = client.get("/api/me").json()
    with db() as cur:
        cur.execute("UPDATE member SET is_system_admin=true WHERE id=%s", (me["id"],))
    return me


# --------------------------------------------------------------------- 가입 자격

@pytest.mark.integration
def test_company_domain_signs_up_and_outside_domain_is_blocked(env):
    c = env.client()
    assert env.signup(c).status_code == 201
    me = c.get("/api/me").json()
    assert me["is_guest"] is False and me["is_system_admin"] is False and me["privacy_agreed"] is True

    r = env.signup(env.client(), f"x-{uuid.uuid4().hex[:6]}@{OUTSIDE}")
    assert r.status_code == 403 and r.json()["detail"] == "signup_not_allowed"
    # 하위 도메인은 허용하지 않는다(정확히 일치).
    r = env.signup(env.client(), f"x-{uuid.uuid4().hex[:6]}@mail.example.com")
    assert r.status_code == 403
    # 대소문자는 구분하지 않는다.
    assert env.signup(env.client(), f"Up-{uuid.uuid4().hex[:6]}@EXAMPLE.com").status_code == 201


@pytest.mark.integration
def test_outside_email_can_sign_up_only_with_pending_invite(env, db):
    owner = env.client()
    env.signup(owner)
    ws = owner.post("/api/workspaces", json={"name": "정책검증", "type": "company"}).json()
    guest_email = f"guest-{uuid.uuid4().hex[:6]}@{OUTSIDE}"

    # 링크 초대(대상 없음)로는 외부인이 가입할 수 없다.
    assert owner.post(f"/api/workspaces/{ws['id']}/invites", json={"target_email": ""}).status_code == 201
    assert env.signup(env.client(), guest_email).status_code == 403

    r = owner.post(f"/api/workspaces/{ws['id']}/invites", json={"target_email": guest_email})
    assert r.status_code == 201, r.text
    g = env.client()
    assert env.signup(g, guest_email).status_code == 201
    me = g.get("/api/me").json()
    assert me["is_guest"] is True
    # 게스트는 워크스페이스를 만들 수 없고, 초대는 받을 수 있다.
    r = g.post("/api/workspaces", json={"name": "게스트 공간"})
    assert r.status_code == 403 and r.json()["detail"] == "guest_cannot_create_workspace"
    invites = g.get("/api/me/invites").json()
    assert len(invites) == 1
    assert g.post(f"/api/me/invites/workspace/{invites[0]['id']}/accept").status_code == 201

    # 수락했거나 만료된 초대는 가입 자격이 아니다.
    other = f"late-{uuid.uuid4().hex[:6]}@{OUTSIDE}"
    owner.post(f"/api/workspaces/{ws['id']}/invites", json={"target_email": other})
    with db() as cur:
        cur.execute("UPDATE workspace_invite SET expires_at=now() - interval '1 minute' WHERE target_email=%s",
                    (other,))
    assert env.signup(env.client(), other).status_code == 403


@pytest.mark.integration
def test_invite_email_must_be_valid(env):
    owner = env.client()
    env.signup(owner)
    ws = owner.post("/api/workspaces", json={"name": "형식검증"}).json()
    assert owner.post(f"/api/workspaces/{ws['id']}/invites", json={"target_email": "not-an-email"}).status_code == 422


@pytest.mark.integration
def test_signup_validates_consent_names_and_password(env):
    c = env.client()
    r = env.signup(c, privacy_agreed=False)
    assert r.status_code == 400 and r.json()["detail"] == "privacy_agreement_required"
    r = env.signup(c, name="   ")
    assert r.status_code == 400 and r.json()["detail"] == "invalid_name"
    r = env.signup(c, nickname="x" * 31)
    assert r.status_code == 400 and r.json()["detail"] == "invalid_nickname"
    r = env.signup(c, password="short1!")
    assert r.status_code == 400 and "too_short" in r.json()["detail"]
    r = env.signup(c, password="onlyletterspassword")
    assert r.status_code == 400 and "needs_two_kinds" in r.json()["detail"]


@pytest.mark.parametrize("pw,email,expected", [
    ("pw-12345678", "a@example.com", []),
    ("abcdefghij1", None, []),
    ("abcdefghi", None, ["too_short", "needs_two_kinds"]),
    ("1234567890", None, ["needs_two_kinds"]),
    ("가나다라마바사아자차", None, ["needs_two_kinds"]),
    ("Password123", None, ["too_common"]),
    ("gildong-2026!", "gildong@zerokleek.com", ["contains_email"]),
    ("a1" + "가" * 30, None, ["too_long"]),
])
def test_password_rules(pw, email, expected):
    assert signup_policy.password_problems(pw, email) == expected


def test_domain_helpers():
    assert signup_policy.is_company_email("A@ZEROKLEEK.COM")
    assert not signup_policy.is_company_email("a@sub.zerokleek.com")
    assert signup_policy.is_guest(None)
    assert {"aeonlab.kr", "finble.io", "zerokleek.com"} <= signup_policy.allowed_domains()


# --------------------------------------------------------------------- 로그인 잠금

@pytest.mark.integration
def test_five_failures_lock_the_account_for_15_minutes(env, db):
    email = f"lock-{uuid.uuid4().hex[:6]}@example.com"
    env.signup(env.client(), email)
    c = env.client()
    for _ in range(4):
        assert c.post("/api/auth/login", json={"email": email, "password": "wrong-pass-1"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": email, "password": "wrong-pass-1"})
    assert r.status_code == 429 and r.json()["detail"].startswith("account_locked:")
    # 잠긴 동안엔 맞는 비밀번호도 거절한다.
    r = c.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 429
    # 15분이 지난 것처럼
    with db() as cur:
        cur.execute("UPDATE member SET locked_until=now() - interval '1 second' WHERE lower(email)=%s", (email,))
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 204
    # 성공하면 실패 횟수가 초기화된다 — 한 번 틀려도 다시 잠기지 않는다.
    assert c.post("/api/auth/login", json={"email": email, "password": "wrong-pass-1"}).status_code == 401
    with db() as cur:
        cur.execute("SELECT failed_login_count FROM member WHERE lower(email)=%s", (email,))
        assert cur.fetchone()["failed_login_count"] == 1


@pytest.mark.integration
def test_unknown_email_and_wrong_password_look_the_same(env):
    c = env.client()
    r1 = c.post("/api/auth/login", json={"email": f"nobody-{uuid.uuid4().hex[:6]}@example.com", "password": PW})
    email = f"same-{uuid.uuid4().hex[:6]}@example.com"
    env.signup(env.client(), email)
    r2 = c.post("/api/auth/login", json={"email": email, "password": "wrong-pass-1"})
    assert r1.status_code == r2.status_code == 401 and r1.json() == r2.json()


# --------------------------------------------------------------------- 비밀번호 찾기

@pytest.mark.integration
def test_password_reset_flow(env):
    email = f"reset-{uuid.uuid4().hex[:6]}@example.com"
    other_device = env.client()
    env.signup(other_device, email)

    c = env.client()
    unknown = c.post("/api/auth/password-reset/request", json={"email": f"no-{uuid.uuid4().hex[:6]}@example.com"})
    known = c.post("/api/auth/password-reset/request", json={"email": email})
    assert unknown.status_code == known.status_code == 202 and unknown.json() == known.json()
    assert env.codes[-1][0] == email and env.codes[-1][2] == "password_reset"
    code = env.codes[-1][1]

    # 재발송 제한에 걸려도 같은 응답(가입 여부·상태를 떠볼 수 없게)
    assert c.post("/api/auth/password-reset/request", json={"email": email}).status_code == 202

    r = c.post("/api/auth/password-reset/confirm", json={"email": email, "code": code, "new_password": "short"})
    assert r.status_code == 400 and r.json()["detail"].startswith("weak_password:")
    r = c.post("/api/auth/password-reset/confirm",
               json={"email": email, "code": "000000" if code != "000000" else "111111", "new_password": "new-pw-2026"})
    assert r.status_code == 400 and r.json()["detail"].startswith("invalid_code:")
    r = c.post("/api/auth/password-reset/confirm", json={"email": email, "code": code, "new_password": "new-pw-2026"})
    assert r.status_code == 204

    assert other_device.get("/api/me").status_code == 401        # 다른 기기 세션 종료
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 401
    assert c.post("/api/auth/login", json={"email": email, "password": "new-pw-2026"}).status_code == 204
    # 같은 코드는 다시 못 쓴다.
    r = c.post("/api/auth/password-reset/confirm", json={"email": email, "code": code, "new_password": "new-pw-2027"})
    assert r.status_code == 400


@pytest.mark.integration
def test_password_change_uses_same_rules(env):
    c = env.client()
    env.signup(c)
    r = c.post("/api/me/password", json={"current_password": PW, "new_password": "abcdefgh"})
    assert r.status_code == 400 and r.json()["detail"].startswith("weak_password:")


# --------------------------------------------------------------------- 관리자 비활성화

@pytest.mark.integration
def test_admin_suspends_and_reactivates_member(env, db):
    admin = env.client()
    env.signup(admin)
    target_email = f"leaver-{uuid.uuid4().hex[:6]}@example.com"
    target = env.client()
    env.signup(target, target_email)
    target_id = target.get("/api/me").json()["id"]

    # 일반 회원은 관리자 API 를 못 쓴다.
    assert admin.get("/api/admin/members").status_code == 403
    me = _make_admin(db, admin)
    assert admin.get("/api/me").json()["is_system_admin"] is True

    rows = admin.get("/api/admin/members", params={"q": "leaver-"}).json()
    assert [r["id"] for r in rows] == [target_id] and rows[0]["suspended_at"] is None

    assert admin.post(f"/api/admin/members/{me['id']}/suspend").status_code == 400   # 자기 자신 불가
    r = admin.post(f"/api/admin/members/{target_id}/suspend")
    assert r.status_code == 200 and r.json() == {"id": target_id, "suspended": True}
    assert target.get("/api/me").status_code == 401                                      # 기존 세션 즉시 무효
    r = env.client().post("/api/auth/login", json={"email": target_email, "password": PW})
    assert r.status_code == 403 and r.json()["detail"] == "account_suspended"
    # 비밀번호가 틀리면 비활성 여부를 알려주지 않는다.
    r = env.client().post("/api/auth/login", json={"email": target_email, "password": "wrong-pass-1"})
    assert r.status_code == 401
    # 비활성 계정은 비밀번호 찾기도 안 된다(코드 발송 없음).
    before = len(env.codes)
    env.client().post("/api/auth/password-reset/request", json={"email": target_email})
    assert len(env.codes) == before

    assert admin.post(f"/api/admin/members/{target_id}/reactivate").json()["suspended"] is False
    assert env.client().post("/api/auth/login", json={"email": target_email, "password": PW}).status_code == 204


@pytest.mark.integration
def test_suspension_closes_open_websocket(env, db, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    from app import auth
    from app.main import app

    admin = env.client()
    env.signup(admin)
    _make_admin(db, admin)
    target = env.client()
    env.signup(target)
    target_id = target.get("/api/me").json()["id"]
    ws_ = target.post("/api/workspaces", json={"name": "소켓"}).json()
    ts = target.post(f"/api/workspaces/{ws_['id']}/teamspaces", json={"name": "팀"}).json()
    proj = target.post(f"/api/teamspaces/{ts['id']}/projects", json={"name": "P"}).json()
    session = target.post(f"/api/projects/{proj['id']}/chat/sessions", json={}).json()
    monkeypatch.setattr(auth, "WS_REVALIDATE_SECONDS", 0.2)

    with TestClient(app) as live:
        live.cookies = target.cookies
        path = f"/api/ws/projects/{proj['id']}/chat/sessions/{session['id']}"
        with live.websocket_connect(path, headers={"origin": auth._origin_of(settings().frontend_url)}) as sock:
            admin.post(f"/api/admin/members/{target_id}/suspend")
            with pytest.raises(WebSocketDisconnect) as exc:
                sock.receive_json()
            assert exc.value.code == 4401


# --------------------------------------------------------------------- GitHub 첫 로그인

class _FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


def _fake_github(monkeypatch, *, user_id, login, public_email=None, emails=()):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **k):
            return _FakeResp({"access_token": "t"})

        def get(self, url, **k):
            if url.endswith("/user/emails"):
                return _FakeResp([{"email": e, "verified": True, "primary": i == 0} for i, e in enumerate(emails)])
            return _FakeResp({"id": user_id, "login": login, "name": login, "email": public_email})

    monkeypatch.setattr(github_oauth.httpx, "Client", FakeClient)


def _github_callback(client):
    state = github_oauth._make_state("/workspace")
    return client.get("/api/auth/github/callback", params={"code": "c", "state": state},
                      headers={"cookie": f"{github_oauth.STATE_COOKIE}={state}"}, follow_redirects=False)


@pytest.mark.integration
def test_github_signup_requires_company_or_invited_email(env, db, monkeypatch):
    gid = int(uuid.uuid4().int % 10**9)
    personal = f"me-{uuid.uuid4().hex[:6]}@{OUTSIDE}"
    _fake_github(monkeypatch, user_id=gid, login=f"gh{gid}", emails=[personal])
    r = _github_callback(env.client())
    assert r.status_code == 302 and r.headers["location"].endswith("/login?error=github_signup_not_allowed")
    assert "neosdl_session" not in r.headers.get("set-cookie", "")

    # 회사 메일이 보조 주소로만 있어도(개인 메일이 primary) 그걸로 가입한다.
    company = f"gh-{uuid.uuid4().hex[:6]}@example.com"
    env.track(company)
    _fake_github(monkeypatch, user_id=gid, login=f"gh{gid}", emails=[personal, company])
    c = env.client()
    r = _github_callback(c)
    assert r.status_code == 302 and r.headers["location"].endswith("/workspace")
    me = c.get("/api/me").json()
    assert me["email"] == company and me["privacy_agreed"] is False   # 첫 화면에서 동의
    assert c.post("/api/me/privacy-agreement").status_code == 204
    assert c.get("/api/me").json()["privacy_agreed"] is True

    # 이미 그 회사 메일로 가입한 계정이 있으면 자동 병합하지 않는다.
    taken = f"taken-{uuid.uuid4().hex[:6]}@example.com"
    env.signup(env.client(), taken)
    gid2 = gid + 1
    _fake_github(monkeypatch, user_id=gid2, login=f"gh{gid2}", emails=[taken])
    r = _github_callback(env.client())
    assert r.headers["location"].endswith("/login?error=github_email_in_use")


@pytest.mark.integration
def test_github_login_blocked_when_suspended(env, db, monkeypatch):
    gid = int(uuid.uuid4().int % 10**9)
    company = f"ghs-{uuid.uuid4().hex[:6]}@example.com"
    env.track(company)
    _fake_github(monkeypatch, user_id=gid, login=f"gh{gid}", emails=[company])
    assert _github_callback(env.client()).status_code == 302
    with db() as cur:
        cur.execute("UPDATE member SET suspended_at=now() WHERE lower(email)=%s", (company,))
    r = _github_callback(env.client())
    assert r.headers["location"].endswith("/login?error=account_suspended")
