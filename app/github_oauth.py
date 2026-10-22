"""회원 인증 — 이메일/비밀번호 가입·로그인 + GitHub OAuth 병행.

서비스 계정(member)과 GitHub 신원(github_account)은 분리돼 있다. GitHub
OAuth 콜백은 이중 역할을 한다: 로그인 세션이 없으면 로그인/가입(그 GitHub
신원에 연결된 member를 찾거나 새로 만듦), 이미 로그인돼 있으면 계정
연동(현재 member에 새 GitHub 신원을 추가)이다. 이렇게 해야 한 사람이
개인 계정과 회사 계정처럼 여러 GitHub 신원을 한 member에 모을 수 있다.

GitHub App 자체에 내장된 "user-to-server" OAuth 를 쓴다(별도 OAuth App
불필요). App 설정의 "Identifying and authorizing users" 섹션에서 발급되는
client_id/secret 을 쓴다 — worker/handlers/clone.py 가 쓰는 App private key
(RS256, server-to-server) 와는 완전히 다른 자격증명이다.
"""

from __future__ import annotations

import hmac
import secrets
import time
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

import httpx
import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from app import audit, email_verification, profiles, signup_policy
from app.auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    get_current_member,
    get_current_member_allow_unverified,
    get_current_member_optional,
    issue_session_token,
)
from app.config import settings
from app.db import tx
from app.projects import _fetch_pending_project_invite, apply_project_invite_accept
from app.security import hash_password, verify_password
from app.teamspaces import _fetch_pending_teamspace_invite, apply_teamspace_invite_accept
from app.workspaces import _fetch_pending_workspace_invite, apply_workspace_invite_accept

router = APIRouter(tags=["auth"])

STATE_COOKIE = "neosdl_oauth_state"
STATE_TTL_SECONDS = 600

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_USER_URL = "https://api.github.com/user"
_GITHUB_USER_EMAILS_URL = "https://api.github.com/user/emails"


class SignupIn(BaseModel):
    email: EmailStr
    # 길이·조합·이름 규칙은 app/signup_policy.py 가 검사한다(공백 제거 후 판단, 위반 코드를 돌려줌).
    password: str = Field(max_length=200)
    name: str = Field(max_length=200)        # 실명 — 프로필/멤버 목록에 표시
    nickname: str = Field(max_length=200)    # 화면 표시용 별칭
    privacy_agreed: bool = False             # 개인정보 수집·이용 동의(필수)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetConfirmIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=1, max_length=12)
    new_password: str = Field(max_length=200)


class VerifyEmailIn(BaseModel):
    code: str = Field(min_length=1, max_length=12)


def _is_verified(row: dict) -> bool:
    """이메일이 없으면(GitHub 전용 가입) 확인할 대상이 없으므로 인증된 것으로 본다."""
    return row.get("email") is None or row.get("email_verified_at") is not None


def _set_session_cookie(resp: Response, member: dict) -> None:
    session_token = issue_session_token(member)
    resp.set_cookie(SESSION_COOKIE, session_token, max_age=SESSION_TTL_SECONDS,
                     httponly=True, secure=settings().cookie_secure, samesite="lax", path="/")


@router.post("/auth/signup", status_code=201)
def signup(body: SignupIn):
    email = body.email.lower()
    name = signup_policy.clean_display_text(body.name, field="name", max_len=50)
    nickname = signup_policy.clean_display_text(body.nickname, field="nickname", max_len=30)
    if not body.privacy_agreed:
        raise HTTPException(400, "privacy_agreement_required")
    signup_policy.validate_password(body.password, email)
    with tx() as cur:
        cur.execute("SELECT 1 FROM member WHERE lower(email)=%s", (email,))
        if cur.fetchone() is not None:
            raise HTTPException(409, "email already registered")
        # 회사 도메인이거나, 그 주소로 온 초대가 있어야 가입할 수 있다(게스트).
        if not signup_policy.can_sign_up(cur, email):
            raise HTTPException(403, "signup_not_allowed")

        cur.execute(
            """
            INSERT INTO member (email, password_hash, name, nickname, last_login_at, privacy_agreed_at)
            VALUES (%s, %s, %s, %s, now(), now())
            RETURNING id, email, nickname, token_version
            """,
            (email, hash_password(body.password), name, nickname),
        )
        member = {**cur.fetchone(), "email_verified": False}
        audit.record(cur, actor=f"user:{member['nickname']}", action="member.signup",
                     subject={"member_id": str(member["id"])}, payload={"method": "email"})
        # 가입과 동시에 첫 인증 코드를 보낸다(쿨다운 대상 아님 — 첫 발송).
        sent = email_verification.issue_code(cur, member, enforce_rate_limit=False)

    # 세션은 미인증(ev=false)으로 발급 — 인증 전엔 /me·인증 API 외엔 403.
    resp = JSONResponse({"email_verification_required": True, **sent}, status_code=201)
    _set_session_cookie(resp, member)
    return resp


_LOGIN_ERRORS = {"invalid": (401, "invalid email or password"), "suspended": (403, "account_suspended")}


@router.post("/auth/login")
def login(body: LoginIn):
    """연속 5회 실패하면 15분 잠근다(429 `account_locked:<남은 초>`). 비활성화 계정은 403.

    실패 횟수 증가는 커밋돼야 하므로 실패도 예외 대신 값으로 받아 트랜잭션 밖에서 응답한다.
    """
    email = body.email.lower()
    error = None
    with tx() as cur:
        cur.execute(
            "SELECT id, email, nickname, password_hash, email_verified_at, token_version, suspended_at, "
            "       failed_login_count, ceil(extract(epoch FROM locked_until - now()))::int AS lock_wait "
            "FROM member WHERE lower(email)=%s AND deleted_at IS NULL FOR UPDATE",
            (email,),
        )
        member = cur.fetchone()
        # 계정 존재 여부가 401 사유로 새지 않도록 "없음"과 "비밀번호 틀림"을 구분하지 않는다.
        if member is None:
            error = "invalid"
        elif (member["lock_wait"] or 0) > 0:
            error = f"locked:{member['lock_wait']}"
        elif not verify_password(body.password, member["password_hash"]):
            failed = member["failed_login_count"] + 1
            locked = failed >= signup_policy.MAX_FAILED_LOGINS
            cur.execute(
                "UPDATE member SET failed_login_count=%s, "
                "       locked_until = CASE WHEN %s THEN now() + make_interval(secs => %s) ELSE NULL END "
                "WHERE id=%s",
                (0 if locked else failed, locked, signup_policy.LOCK_SECONDS, member["id"]),
            )
            audit.record(cur, actor=f"user:{member['nickname']}", action="member.login_failed",
                         subject={"member_id": str(member["id"])}, payload={"locked": locked})
            error = f"locked:{signup_policy.LOCK_SECONDS}" if locked else "invalid"
        elif member["suspended_at"] is not None:
            # 비밀번호가 맞을 때만 비활성 사실을 알려준다(남의 계정 상태를 떠보지 못하게).
            error = "suspended"
        else:
            cur.execute("UPDATE member SET last_login_at=now(), failed_login_count=0, locked_until=NULL "
                        "WHERE id=%s", (member["id"],))
            audit.record(cur, actor=f"user:{member['nickname']}", action="member.login",
                         subject={"member_id": str(member["id"])}, payload={"method": "email"})
            member = {**member, "email_verified": _is_verified(member)}

    if error is not None:
        if error.startswith("locked:"):
            raise HTTPException(429, "account_locked:" + error.split(":", 1)[1])
        raise HTTPException(*_LOGIN_ERRORS[error])
    resp = Response(status_code=204)
    _set_session_cookie(resp, member)
    return resp


@router.post("/auth/password-reset/request", status_code=202)
def request_password_reset(body: PasswordResetRequestIn):
    """비밀번호 찾기 1단계 — 계정이 있으면 재설정 코드를 메일로 보낸다.

    가입 여부가 드러나지 않게 계정이 없어도, 재발송 제한에 걸려도 같은 응답을 준다.
    """
    with tx() as cur:
        cur.execute(
            "SELECT id FROM member WHERE lower(email)=%s AND deleted_at IS NULL AND suspended_at IS NULL",
            (body.email.lower(),),
        )
        row = cur.fetchone()
        if row is not None:
            try:
                email_verification.issue_password_reset_code(cur, str(row["id"]))
            except HTTPException as exc:
                if exc.status_code != 429:   # 쿨다운/시간당 한도는 조용히 넘긴다
                    raise
    return {"expires_in": email_verification.CODE_TTL_SECONDS,
            "resend_after": email_verification.RESEND_COOLDOWN_SECONDS}


@router.post("/auth/password-reset/confirm", status_code=204)
def confirm_password_reset(body: PasswordResetConfirmIn):
    """비밀번호 찾기 2단계 — 코드가 맞으면 새 비밀번호로 바꾸고 모든 세션을 끊는다(잠금도 해제).

    코드 오입력 횟수는 커밋돼야 하므로 실패는 트랜잭션 밖에서 400 으로 바꾼다.
    """
    email = body.email.lower()
    signup_policy.validate_password(body.new_password, email)
    with tx() as cur:
        cur.execute(
            "SELECT id, nickname FROM member WHERE lower(email)=%s AND deleted_at IS NULL AND suspended_at IS NULL",
            (email,),
        )
        row = cur.fetchone()
        if row is None:
            result = {"error": "code_expired"}   # 계정이 없을 때도 "코드 없음"과 같은 응답
        else:
            result = email_verification.verify_code(cur, str(row["id"]), body.code, purpose="password_reset")
            if "error" not in result:
                cur.execute(
                    """
                    UPDATE member SET password_hash=%s, token_version=token_version+1,
                           failed_login_count=0, locked_until=NULL,
                           email_verified_at=coalesce(email_verified_at, now()), updated_at=now()
                     WHERE id=%s
                    """,
                    (hash_password(body.new_password), row["id"]),
                )
                audit.record(cur, actor=f"user:{row['nickname']}", action="member.password.reset",
                             subject={"member_id": str(row["id"])}, payload={})
    if "error" in result:
        raise HTTPException(400, result["error"])
    return Response(status_code=204)


def _make_state(next_path: str | None) -> str:
    now = int(time.time())
    claims = {
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(16),
        "next": next_path,
    }
    return jwt.encode(claims, settings().session_secret, algorithm="HS256")


def _fetch_verified_emails(client: httpx.Client, access_token: str, public_email: str | None) -> list[str]:
    """GitHub 가 인증한 이메일 목록(primary 먼저). 프로필 공개 이메일도 GitHub 에선 인증된 주소다.

    회사 메일이 primary 가 아니라 보조 주소로 등록된 경우가 흔해서(개인 메일이 primary),
    가입 자격 판단(_pick_signup_email)을 위해 primary 하나가 아니라 전체를 본다.
    GitHub App 의 "Account permissions > Email addresses" 가 꺼져 있으면 /user/emails 가
    403 이나 빈 배열이라 공개 이메일만 남는다.
    """
    emails: list[str] = []
    try:
        resp = client.get(_GITHUB_USER_EMAILS_URL, headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        })
        if resp.status_code == 200:
            verified = [e for e in resp.json() if e.get("verified") and e.get("email")]
            verified.sort(key=lambda e: not e.get("primary"))
            emails = [e["email"] for e in verified]
    except (httpx.HTTPError, ValueError):
        pass
    if public_email and public_email not in emails:
        emails.append(public_email)
    return emails


def _pick_signup_email(cur, emails: list[str]) -> str | None:
    """GitHub 신규 가입에 쓸 이메일 — 회사 도메인 우선, 없으면 초대가 와 있는 주소. 없으면 가입 불가."""
    for e in emails:
        if signup_policy.is_company_email(e):
            return e
    for e in emails:
        if signup_policy.has_pending_invite(cur, e):
            return e
    return None


def _apply_email_if_available(cur, member_id: str, candidate_email: str | None) -> None:
    """이미 이메일이 있는 member는 덮어쓰지 않고, 다른 계정이 이미 쓰는

    이메일이면 자동 병합하지 않고 건너뛴다 — 신규 가입 때와 같은 계정 탈취
    방지 원칙을 기존 회원 백필에도 동일하게 적용한다.
    """
    if not candidate_email:
        return
    cur.execute("SELECT email FROM member WHERE id=%s", (member_id,))
    if cur.fetchone()["email"] is not None:
        return
    cur.execute("SELECT 1 FROM member WHERE lower(email)=%s AND id<>%s", (candidate_email.lower(), member_id))
    if cur.fetchone() is not None:
        return
    # GitHub 가 인증한 주소라 우리 쪽 인증도 끝난 것으로 본다.
    cur.execute("UPDATE member SET email=%s, email_verified_at=now(), updated_at=now() WHERE id=%s",
                (candidate_email, member_id))


def _safe_redirect_target(next_path: str | None) -> str:
    """오픈 리다이렉트 방지 — next 는 허용목록을 통과해야만 그대로 쓴다."""
    if next_path and next_path.startswith("/api/auth/github/setup"):
        return f"{settings().backend_base_url}{next_path}"
    if (next_path and next_path.startswith("/") and not next_path.startswith("//")
            and "://" not in next_path):
        return f"{settings().frontend_url}{next_path}"
    return settings().frontend_url


@router.get("/auth/github/login")
def github_login(next: str | None = None):
    state = _make_state(next)
    authorize_url = f"{_GITHUB_AUTHORIZE_URL}?" + urlencode({
        "client_id": settings().github_oauth_client_id,
        "redirect_uri": settings().github_oauth_callback_url,
        "state": state,
    })
    resp = RedirectResponse(authorize_url, status_code=302)
    # 이 라우터는 app/main.py에서 prefix="/api"로 마운트되므로 실제 요청
    # 경로는 /api/auth/github/... 다 — 쿠키 path가 그와 안 맞으면 브라우저가
    # 콜백 요청에 이 쿠키를 아예 안 실어서 state 검증이 매번 실패한다.
    resp.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL_SECONDS,
                     httponly=True, secure=settings().cookie_secure, samesite="lax", path="/api/auth/github")
    return resp


@router.get("/auth/github/callback")
def github_callback(
    code: str | None = None,
    state: str | None = None,
    oauth_state: str | None = Cookie(default=None, alias=STATE_COOKIE),
    current_member: dict | None = Depends(get_current_member_optional),
):
    if not state or not oauth_state:
        raise HTTPException(400, "missing_state")
    if not hmac.compare_digest(state, oauth_state):
        raise HTTPException(400, "invalid_state")
    try:
        claims = jwt.decode(state, settings().session_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(400, "state_expired") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(400, "invalid_state") from exc

    if not code:
        raise HTTPException(400, "missing_code")

    with httpx.Client(timeout=10) as client:
        token_resp = client.post(
            _GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings().github_oauth_client_id,
                "client_secret": settings().github_oauth_client_secret,
                "code": code,
                "redirect_uri": settings().github_oauth_callback_url,
            },
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise HTTPException(400, "github_token_exchange_failed")

        user_resp = client.get(_GITHUB_USER_URL, headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        })
        user_resp.raise_for_status()
        gh_user = user_resp.json()
        verified_emails = _fetch_verified_emails(client, access_token, gh_user.get("email"))
    # 기존 계정 이메일 백필용 — 회사 메일이 있으면 그것을, 없으면 primary.
    verified_email = next((e for e in verified_emails if signup_policy.is_company_email(e)),
                          verified_emails[0] if verified_emails else None)

    redirect_error: str | None = None
    member = None
    with tx() as cur:
        cur.execute("SELECT id, member_id FROM github_account WHERE github_user_id=%s", (gh_user["id"],))
        existing = cur.fetchone()

        if current_member is not None:
            # 이미 로그인된 세션 — 계정 연동 모드. 다른 member에 이미 연결된
            # GitHub 계정이면 병합하지 않고 충돌로만 알린다.
            if existing is not None and existing["member_id"] != current_member["id"]:
                redirect_error = "github_already_linked_elsewhere"
            elif existing is None:
                cur.execute(
                    """
                    INSERT INTO github_account (member_id, github_user_id, github_login, avatar_url)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (current_member["id"], gh_user["id"], gh_user["login"], gh_user.get("avatar_url")),
                )
                audit.record(cur, actor=f"user:{current_member['nickname']}", action="github_account.link",
                             subject={"member_id": current_member["id"]},
                             payload={"github_login": gh_user["login"]})
            else:
                cur.execute(
                    "UPDATE github_account SET github_login=%s, avatar_url=%s, updated_at=now() WHERE id=%s",
                    (gh_user["login"], gh_user.get("avatar_url"), existing["id"]),
                )
            if redirect_error is None:
                # 이 member가 이메일 없이 가입(GitHub 전용)했을 수 있으니,
                # 방금 연동한 GitHub 계정에 이메일이 있으면 이 기회에 채운다
                # — 초대는 이메일 기준이라 이게 없으면 타겟 초대를 못 받는다.
                _apply_email_if_available(cur, current_member["id"], verified_email)
            member = current_member
        else:
            # 로그인 세션 없음 — 로그인/가입 모드.
            suspended = False
            if existing is not None:
                cur.execute("SELECT suspended_at FROM member WHERE id=%s", (existing["member_id"],))
                suspended = cur.fetchone()["suspended_at"] is not None
            if suspended:
                redirect_error = "account_suspended"
            elif existing is not None:
                cur.execute(
                    "UPDATE github_account SET github_login=%s, avatar_url=%s, updated_at=now() WHERE id=%s",
                    (gh_user["login"], gh_user.get("avatar_url"), existing["id"]),
                )
                cur.execute("UPDATE member SET last_login_at=now(), failed_login_count=0, locked_until=NULL "
                            "WHERE id=%s", (existing["member_id"],))
                # 예전에 이메일 없이 가입한 계정이면 이번 로그인에 딸려온
                # 인증 이메일로 백필한다 — _apply_email_if_available이 이미
                # 이메일이 있으면 건드리지 않는다.
                _apply_email_if_available(cur, existing["member_id"], verified_email)
                cur.execute("SELECT id, nickname, email, email_verified_at, token_version FROM member WHERE id=%s",
                            (existing["member_id"],))
                member = cur.fetchone()
                member = {**member, "email_verified": _is_verified(member)}
                audit.record(cur, actor=f"user:{member['nickname']}", action="member.login",
                             subject={"member_id": str(member["id"])}, payload={"method": "github"})
            else:
                # 신규 가입은 GitHub 가 인증한 회사 메일(또는 초대받은 주소)이 있을 때만.
                email = _pick_signup_email(cur, verified_emails)
                if email is None:
                    redirect_error = "github_signup_not_allowed"
                else:
                    cur.execute("SELECT 1 FROM member WHERE lower(email)=%s", (email.lower(),))
                    if cur.fetchone() is not None:
                        # 이미 그 메일로 가입한 계정이 있다 — 자동 병합하지 않는다(계정 탈취 방지).
                        # 그 계정으로 로그인한 뒤 마이페이지에서 GitHub 를 연결하게 안내한다.
                        redirect_error = "github_email_in_use"
            if redirect_error is None and existing is None:
                # GitHub 가 인증한 이메일이라 가입 즉시 인증 완료. 개인정보 동의는 첫 화면에서 받는다
                # (privacy_agreed_at NULL → 프론트 동의 화면 → POST /me/privacy-agreement).
                cur.execute(
                    "INSERT INTO member (email, name, nickname, last_login_at, email_verified_at, avatar_source) "
                    "VALUES (%s, %s, %s, now(), now(), CASE WHEN %s::text IS NULL THEN 'initials' ELSE 'github' END) "
                    "RETURNING id, nickname, email, token_version",
                    # GitHub 프로필 이름이 없으면 login 을 이름으로 쓴다(마이페이지에서 수정 가능).
                    # GitHub 사진이 있으면 그걸 기본 아바타로(마이페이지에서 바꿀 수 있음).
                    (email.lower(), (gh_user.get("name") or gh_user["login"])[:50], gh_user["login"][:30],
                     gh_user.get("avatar_url")),
                )
                member = {**cur.fetchone(), "email_verified": True}
                cur.execute(
                    """
                    INSERT INTO github_account (member_id, github_user_id, github_login, avatar_url)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (member["id"], gh_user["id"], gh_user["login"], gh_user.get("avatar_url")),
                )
                audit.record(cur, actor=f"user:{member['nickname']}", action="member.signup",
                             subject={"member_id": str(member["id"])}, payload={"method": "github"})

    if redirect_error and current_member is None:
        # 로그인/가입 거절 — 세션을 만들지 않고 로그인 화면에서 사유를 보여준다.
        redirect_to = f"{settings().frontend_url}/login?error={redirect_error}"
    elif redirect_error:
        # next가 다시 /auth/github/setup 같은 백엔드 경로를 가리키는 경우,
        # 그걸 그대로 따라가면 같은 충돌이 반복돼 리다이렉트 루프가 될 수
        # 있다 — 충돌 시엔 항상 프론트 대시보드로 보낸다.
        redirect_to = f"{settings().frontend_url}/dashboard?error={redirect_error}"
    else:
        redirect_to = _safe_redirect_target(claims.get("next"))

    resp = RedirectResponse(redirect_to, status_code=302)
    if current_member is None and member is not None:
        _set_session_cookie(resp, member)
    resp.delete_cookie(STATE_COOKIE, path="/api/auth/github")
    return resp


@router.post("/auth/logout", status_code=204)
def logout():
    resp = Response(status_code=204)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/me")
def me(member: dict = Depends(get_current_member_allow_unverified)):
    # 미인증 상태에서도 호출된다 — 프론트가 email_verified 를 보고 인증 화면으로 보낸다.
    # 이름·아바타는 세션 클레임이 아니라 DB 에서 — 마이페이지에서 바꾸면 바로 반영되게.
    with tx() as cur:
        profile = profiles.load_profile(cur, member["id"])
        cur.execute("SELECT is_system_admin, privacy_agreed_at FROM member WHERE id=%s", (member["id"],))
        flags = cur.fetchone()
    return {**profile, "email_verified": member["email_verified"],
            "is_system_admin": flags["is_system_admin"],
            "is_guest": signup_policy.is_guest(profile.get("email")),
            "privacy_agreed": flags["privacy_agreed_at"] is not None}


@router.post("/me/privacy-agreement", status_code=204)
def agree_privacy(member: dict = Depends(get_current_member_allow_unverified)):
    """개인정보 수집·이용 동의 — GitHub 로 가입했거나 정책 도입 전에 가입한 계정이 첫 화면에서 한다."""
    with tx() as cur:
        cur.execute("UPDATE member SET privacy_agreed_at=coalesce(privacy_agreed_at, now()) WHERE id=%s",
                    (member["id"],))
        audit.record(cur, actor=f"user:{member['nickname']}", action="member.privacy_agreed",
                     subject={"member_id": member["id"]}, payload={})


@router.post("/auth/email-verification/send")
def send_email_verification(member: dict = Depends(get_current_member_allow_unverified)):
    """인증 코드 (재)발송. 쿨다운/시간당 횟수 초과면 429(detail: resend_cooldown:<초> | too_many_requests)."""
    with tx() as cur:
        return email_verification.issue_code(cur, member)


@router.post("/auth/email-verification/verify", status_code=204)
def verify_email(body: VerifyEmailIn, member: dict = Depends(get_current_member_allow_unverified)):
    """맞으면 ev=true 세션 쿠키를 다시 발급한다. 틀리면 400(detail: invalid_code:<남은 횟수> 등)."""
    if member["email_verified"]:
        return Response(status_code=204)
    with tx() as cur:
        result = email_verification.verify_code(cur, member["id"], body.code)
    # 실패는 트랜잭션 커밋 뒤에 응답한다 — 오입력 횟수 증가가 롤백되면 안 되므로.
    if "error" in result:
        raise HTTPException(400, result["error"])
    resp = Response(status_code=204)
    _set_session_cookie(resp, result["member"])
    return resp


@router.get("/me/github-accounts")
def list_my_github_accounts(member: dict = Depends(get_current_member)):
    with tx() as cur:
        cur.execute(
            "SELECT id, github_login, avatar_url, linked_at FROM github_account "
            "WHERE member_id=%s ORDER BY linked_at",
            (member["id"],),
        )
        return cur.fetchall()


@router.delete("/me/github-accounts/{github_account_id}", status_code=204)
def unlink_github_account(github_account_id: str, member: dict = Depends(get_current_member)):
    with tx() as cur:
        cur.execute("SELECT password_hash FROM member WHERE id=%s", (member["id"],))
        has_password = cur.fetchone()["password_hash"] is not None
        cur.execute("SELECT count(*) AS n FROM github_account WHERE member_id=%s", (member["id"],))
        remaining = cur.fetchone()["n"]
        if not has_password and remaining <= 1:
            raise HTTPException(409, "cannot unlink your only login method — set a password first")

        cur.execute(
            "DELETE FROM github_account WHERE id=%s AND member_id=%s RETURNING id",
            (github_account_id, member["id"]),
        )
        if cur.fetchone() is None:
            raise HTTPException(404, "github account not found")
        audit.record(cur, actor=f"user:{member['nickname']}", action="github_account.unlink",
                     subject={"member_id": member["id"], "github_account_id": github_account_id})


# ---------------------------------------------------------------- 초대 수신함
# "내 이메일로 온 초대"를 workspace/teamspace/project 세 테이블에서 한 번에
# 모아 보여준다 — 지금까지는 토큰(링크)이 있어야만 초대를 볼 수 있었는데,
# 초대장을 안 열어본 사람은 자기한테 뭐가 와 있는지 알 방법이 없었다.
# member.email이 없으면(GitHub 전용이라 이메일 미확보) 애초에 target_email
# 매칭이 안 되므로 빈 목록을 돌려준다 — 에러가 아니라 정상 케이스.
@router.get("/me/invites")
def list_my_invites(member: dict = Depends(get_current_member)):
    email = (member.get("email") or "").lower()
    if not email:
        return []
    with tx() as cur:
        cur.execute(
            """
            SELECT 'workspace' AS kind, wi.id, wi.role::text AS role, w.id AS scope_id, w.name AS scope_name,
                   wi.created_at, wi.expires_at, m.nickname AS invited_by_nickname
              FROM workspace_invite wi
              JOIN workspace w ON w.id = wi.workspace_id AND w.deleted_at IS NULL
              JOIN member m ON m.id = wi.created_by
             WHERE wi.target_email = %s AND wi.accepted_at IS NULL AND wi.revoked_at IS NULL
               AND wi.expires_at > now()
            UNION ALL
            SELECT 'teamspace' AS kind, ti.id, ti.role::text, t.id, t.name,
                   ti.created_at, ti.expires_at, m.nickname
              FROM teamspace_invite ti
              JOIN teamspace t ON t.id = ti.teamspace_id AND t.deleted_at IS NULL
              JOIN member m ON m.id = ti.created_by
             WHERE ti.target_email = %s AND ti.accepted_at IS NULL AND ti.revoked_at IS NULL
               AND ti.expires_at > now()
            UNION ALL
            SELECT 'project' AS kind, pi.id, pi.role::text, p.id, p.name,
                   pi.created_at, pi.expires_at, m.nickname
              FROM project_invite pi
              JOIN project p ON p.id = pi.project_id AND p.deleted_at IS NULL
              JOIN member m ON m.id = pi.created_by
             WHERE pi.target_email = %s AND pi.accepted_at IS NULL AND pi.revoked_at IS NULL
               AND pi.expires_at > now()
            ORDER BY created_at DESC
            """,
            (email, email, email),
        )
        return cur.fetchall()


@router.post("/me/invites/{kind}/{invite_id}/accept", status_code=201)
def accept_my_invite(kind: Literal["workspace", "teamspace", "project"], invite_id: UUID,
                      member: dict = Depends(get_current_member)):
    """수신함에서 직접 수락 — 원문 토큰은 저장돼 있지 않으므로(해시만 저장)

    토큰 기반 accept 엔드포인트를 재사용할 수 없다. 대신 invite_id로 조회하고,
    로그인된 본인 이메일이 target_email과 일치하는지는 각 모듈의
    apply_*_invite_accept가 기존과 동일하게 다시 확인한다 — 이 목록 자체가
    이미 그 이메일로 필터링돼 있어도, 응답을 신뢰하지 않고 서버에서 한 번 더
    검증한다.
    """
    with tx() as cur:
        if kind == "workspace":
            invite = _fetch_pending_workspace_invite(cur, invite_id=invite_id)
            return apply_workspace_invite_accept(cur, invite, member)
        if kind == "teamspace":
            invite = _fetch_pending_teamspace_invite(cur, invite_id=invite_id)
            return apply_teamspace_invite_accept(cur, invite, member)
        invite = _fetch_pending_project_invite(cur, invite_id=invite_id)
        return apply_project_invite_accept(cur, invite, member)
