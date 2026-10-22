"""이메일 인증 코드(app/email_verification.py) + 미인증 계정 기본 차단(app/auth.py).

순수 로직(세션 클레임/게이트)은 DB 없이, 가입→코드→인증 전체 흐름은 실제 Postgres
(sql/027 적용)로 검증한다 — DB 가 없으면 integration 은 skip.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app import auth, email_verification


# --------------------------------------------------------------------- 순수 로직

def _token(**extra) -> str:
    return auth.issue_session_token({"id": str(uuid.uuid4()), "nickname": "n", "email": "a@b.c", **extra})


@pytest.fixture(autouse=True)
def _no_db_session_check(request, monkeypatch):
    # 순수 로직 테스트는 DB 없이 돈다 — 세션 유효성(token_version/탈퇴) DB 확인은 통과시킨다.
    if "client" not in request.fixturenames:
        monkeypatch.setattr(auth, "_still_valid", lambda member: True)


def test_unverified_session_is_blocked_by_default():
    token = _token(email_verified=False)
    with pytest.raises(HTTPException) as exc:
        auth.get_current_member(token)
    assert exc.value.status_code == 403 and exc.value.detail == "email_not_verified"
    # /me·인증 API 용 의존성은 통과한다.
    assert auth.get_current_member_allow_unverified(token)["email_verified"] is False
    # optional/WS 경로에서는 로그인 안 한 것과 같게 취급한다.
    assert auth.get_current_member_optional(token) is None


def test_verified_session_passes():
    assert auth.get_current_member(_token(email_verified=True))["email_verified"] is True


def test_legacy_session_without_ev_claim_is_treated_as_verified():
    import jwt

    from app.config import settings

    legacy = jwt.encode({"sub": str(uuid.uuid4()), "nickname": "old", "exp": 4102444800},
                        settings().session_secret, algorithm="HS256")
    assert auth.get_current_member(legacy)["email_verified"] is True


def test_code_is_six_digits_and_hash_is_member_bound():
    codes = {email_verification._generate_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    assert email_verification._hash_code(a, "123456") != email_verification._hash_code(b, "123456")


# --------------------------------------------------------------------- 통합(가입 → 인증)

def _schema_ready(db) -> bool:
    with db() as cur:
        cur.execute("SELECT to_regclass('public.email_verification') AS t")
        return cur.fetchone()["t"] is not None


@pytest.fixture
def client(db, monkeypatch):
    if not _schema_ready(db):
        pytest.skip("sql/027_email_verification.sql 미적용")
    from fastapi.testclient import TestClient

    from app.main import app

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(email_verification, "_send_code_email",
                        lambda email, nickname, code, purpose="signup": sent.append((email, code)) or True)
    email = f"verify-{uuid.uuid4().hex[:10]}@example.com"
    with TestClient(app) as c:
        c.sent, c.email = sent, email
        yield c
    with db() as cur:
        cur.execute("DELETE FROM member WHERE lower(email)=%s", (email,))


def _signup(client):
    r = client.post("/api/auth/signup", json={"email": client.email, "password": "pw-12345678", "name": "검증", "nickname": "검증",
                                              "privacy_agreed": True})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.integration
def test_signup_sends_code_and_blocks_until_verified(client):
    body = _signup(client)
    assert body["email_verification_required"] is True and body["sent"] is True
    assert len(client.sent) == 1 and client.sent[0][0] == client.email

    me = client.get("/api/me").json()
    assert me["email_verified"] is False
    blocked = client.get("/api/workspaces")
    assert blocked.status_code == 403 and blocked.json()["detail"] == "email_not_verified"

    r = client.post("/api/auth/email-verification/verify", json={"code": client.sent[0][1]})
    assert r.status_code == 204
    # 쿠키가 ev=true 로 재발급돼 바로 기능이 열린다.
    assert client.get("/api/me").json()["email_verified"] is True
    assert client.get("/api/workspaces").status_code == 200


@pytest.mark.integration
def test_wrong_code_counts_attempts_and_locks_after_max(client, db):
    _signup(client)
    good = client.sent[0][1]
    wrong = "000000" if good != "000000" else "111111"

    r = client.post("/api/auth/email-verification/verify", json={"code": wrong})
    assert r.status_code == 400 and r.json()["detail"] == f"invalid_code:{email_verification.MAX_ATTEMPTS - 1}"
    # 실패 응답 뒤에도 횟수 증가는 커밋돼 있어야 한다(롤백되면 제한이 무력화).
    with db() as cur:
        cur.execute(
            "SELECT ev.attempts FROM email_verification ev JOIN member m ON m.id = ev.member_id "
            "WHERE lower(m.email)=%s", (client.email,))
        assert cur.fetchone()["attempts"] == 1

    for _ in range(email_verification.MAX_ATTEMPTS - 1):
        r = client.post("/api/auth/email-verification/verify", json={"code": wrong})
    assert r.json()["detail"] == "too_many_attempts"
    # 잠긴 코드는 맞는 값을 넣어도 통과하지 않는다.
    r = client.post("/api/auth/email-verification/verify", json={"code": good})
    assert r.status_code == 400 and r.json()["detail"] == "too_many_attempts"


@pytest.mark.integration
def test_resend_cooldown_and_old_code_invalidated(client, db):
    _signup(client)
    first = client.sent[0][1]

    r = client.post("/api/auth/email-verification/send")
    assert r.status_code == 429 and r.json()["detail"].startswith("resend_cooldown:")

    # 쿨다운을 지난 것으로 만들고 재발송 → 새 코드만 유효.
    with db() as cur:
        cur.execute(
            "UPDATE email_verification SET created_at = now() - interval '2 minutes' "
            "WHERE member_id = (SELECT id FROM member WHERE lower(email)=%s)", (client.email,))
    r = client.post("/api/auth/email-verification/send")
    assert r.status_code == 200, r.text
    second = client.sent[-1][1]

    if first != second:
        # 이전 코드는 최신 코드와 대조돼 틀린 코드로 처리된다(이전 행은 이미 만료).
        r = client.post("/api/auth/email-verification/verify", json={"code": first})
        assert r.status_code == 400 and r.json()["detail"].startswith("invalid_code:")
    r = client.post("/api/auth/email-verification/verify", json={"code": second})
    assert r.status_code == 204


@pytest.mark.integration
def test_login_of_unverified_account_stays_unverified(client):
    _signup(client)
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": client.email, "password": "pw-12345678"})
    assert r.status_code == 204
    assert client.get("/api/me").json()["email_verified"] is False
    assert client.get("/api/workspaces").status_code == 403
