"""마이페이지 — 내 계정 관리(프로필·비밀번호·이메일 변경·회원 탈퇴).

GET /api/me 와 GitHub 계정 연동/해제는 app/github_oauth.py, 아바타는 app/avatars.py 에 있다.

세션 처리:
- 닉네임·이메일은 세션 클레임에도 들어 있어서, 바꾸면 쿠키를 다시 발급한다.
- 비밀번호 변경은 token_version 을 올려 **다른 기기 세션을 끊고**, 지금 기기엔 새 쿠키를 준다.
- 탈퇴도 token_version 을 올리고 쿠키를 지운다 — 다른 기기 세션도 즉시 401(app/auth.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from app import audit, email_verification, profiles
from app.auth import SESSION_COOKIE, get_current_member
from app.db import tx
from app.github_oauth import _set_session_cookie
from app.security import hash_password, verify_password
from app.storage import storage

router = APIRouter(tags=["me"])

WITHDRAWN_NAME = "탈퇴한 사용자"


class ProfileIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    nickname: str | None = Field(default=None, min_length=1, max_length=100)


class PasswordIn(BaseModel):
    current_password: str | None = None          # 비밀번호가 없는 GitHub 전용 계정의 최초 설정이면 생략
    new_password: str = Field(min_length=8, max_length=200)


class EmailChangeIn(BaseModel):
    new_email: EmailStr
    password: str | None = None                  # 비밀번호가 있는 계정은 필수(세션 탈취 대비 재확인)


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=12)


class WithdrawIn(BaseModel):
    password: str | None = None                  # 비밀번호 계정은 필수
    confirm: str = Field(min_length=1)            # 실수 방지 — 정확히 "탈퇴합니다"


def _session_member(cur, member_id: str, email_verified: bool) -> dict:
    cur.execute("SELECT id, email, nickname, token_version FROM member WHERE id=%s", (member_id,))
    return {**cur.fetchone(), "email_verified": email_verified}


def _require_password(cur, member_id: str, password: str | None) -> None:
    """비밀번호가 설정된 계정이면 현재 비밀번호를 확인한다(GitHub 전용 계정은 통과)."""
    cur.execute("SELECT password_hash FROM member WHERE id=%s", (member_id,))
    pw_hash = cur.fetchone()["password_hash"]
    if pw_hash is not None and not (password and verify_password(password, pw_hash)):
        raise HTTPException(403, "invalid_password")


@router.patch("/me")
def update_profile(body: ProfileIn, member: dict = Depends(get_current_member)):
    sets, args = [], []
    if body.name is not None:
        sets.append("name=%s")
        args.append(body.name.strip())
    if body.nickname is not None:
        sets.append("nickname=%s")
        args.append(body.nickname.strip())
    if not sets or not all(a for a in args):
        raise HTTPException(400, "nothing_to_update")
    with tx() as cur:
        cur.execute(f"UPDATE member SET {', '.join(sets)}, updated_at=now() WHERE id=%s", (*args, member["id"]))
        audit.record(cur, actor=f"user:{member['nickname']}", action="member.profile.update",
                     subject={"member_id": member["id"]}, payload=body.model_dump(exclude_none=True))
        profile = profiles.load_profile(cur, member["id"])
        session_member = _session_member(cur, member["id"], member["email_verified"])
    resp = JSONResponse({**profile, "email_verified": member["email_verified"]})
    _set_session_cookie(resp, session_member)     # 닉네임이 클레임에 있으므로 재발급
    return resp


@router.post("/me/password", status_code=204)
def change_password(body: PasswordIn, member: dict = Depends(get_current_member)):
    with tx() as cur:
        _require_password(cur, member["id"], body.current_password)
        cur.execute(
            "UPDATE member SET password_hash=%s, token_version=token_version+1, updated_at=now() WHERE id=%s",
            (hash_password(body.new_password), member["id"]),
        )
        audit.record(cur, actor=f"user:{member['nickname']}", action="member.password.change",
                     subject={"member_id": member["id"]}, payload={})
        session_member = _session_member(cur, member["id"], member["email_verified"])
    resp = Response(status_code=204)
    _set_session_cookie(resp, session_member)     # 다른 기기는 tv 불일치로 401, 이 기기만 유지
    return resp


@router.post("/me/email")
def request_email_change(body: EmailChangeIn, member: dict = Depends(get_current_member)):
    with tx() as cur:
        _require_password(cur, member["id"], body.password)
        return email_verification.issue_email_change_code(cur, member["id"], body.new_email)


@router.post("/me/email/verify")
def confirm_email_change(body: CodeIn, member: dict = Depends(get_current_member)):
    with tx() as cur:
        result = email_verification.verify_code(cur, member["id"], body.code, purpose="email_change")
    # 실패 응답은 커밋 뒤에 — 오입력 횟수 증가가 롤백되지 않게(app/email_verification.py 참고).
    if "error" in result:
        status = 409 if result["error"] == "email_already_registered" else 400
        raise HTTPException(status, result["error"])
    with tx() as cur:
        profile = profiles.load_profile(cur, member["id"])
    resp = JSONResponse({**profile, "email_verified": True})
    _set_session_cookie(resp, result["member"])   # 이메일이 클레임에 있으므로 재발급
    return resp


# --------------------------------------------------------------------- 회원 탈퇴

def _withdraw_blockers(cur, member_id: str) -> list[dict]:
    """혼자 관리자인데 다른 사람이 남아 있는 곳 — 넘기지 않고 나가면 관리자 없는 공간이 된다."""
    cur.execute(
        """
        SELECT 'workspace' AS kind, w.id, w.name
          FROM workspace_member wm JOIN workspace w ON w.id = wm.workspace_id AND w.deleted_at IS NULL
         WHERE wm.member_id = %(m)s AND wm.role = 'admin'
           AND NOT EXISTS (SELECT 1 FROM workspace_member o
                            WHERE o.workspace_id = w.id AND o.member_id <> %(m)s AND o.role = 'admin')
           AND EXISTS (SELECT 1 FROM workspace_member o WHERE o.workspace_id = w.id AND o.member_id <> %(m)s)
        UNION ALL
        SELECT 'teamspace', t.id, t.name
          FROM teamspace_member tm JOIN teamspace t ON t.id = tm.teamspace_id AND t.deleted_at IS NULL
         WHERE tm.member_id = %(m)s AND tm.role = 'admin'
           AND NOT EXISTS (SELECT 1 FROM teamspace_member o
                            WHERE o.teamspace_id = t.id AND o.member_id <> %(m)s AND o.role = 'admin')
           AND EXISTS (SELECT 1 FROM teamspace_member o WHERE o.teamspace_id = t.id AND o.member_id <> %(m)s)
        UNION ALL
        SELECT 'project', p.id, p.name
          FROM project_member pm JOIN project p ON p.id = pm.project_id AND p.deleted_at IS NULL
         WHERE pm.member_id = %(m)s AND pm.role = 'owner'
           AND NOT EXISTS (SELECT 1 FROM project_member o
                            WHERE o.project_id = p.id AND o.member_id <> %(m)s AND o.role = 'owner')
           AND (EXISTS (SELECT 1 FROM project_member o WHERE o.project_id = p.id AND o.member_id <> %(m)s)
                OR EXISTS (SELECT 1 FROM teamspace_member o WHERE o.teamspace_id = p.teamspace_id
                                                              AND o.member_id <> %(m)s))
        UNION ALL
        -- 내 GitHub 계정으로 설치한 App 이 다른 사람도 쓰는 프로젝트의 저장소를 잇고 있으면 막는다:
        -- github_account 삭제 → installation CASCADE 삭제 → repository.installation_id NULL 로
        -- 팀 저장소의 clone/스캔이 끊기기 때문(sql/002, 008).
        SELECT 'installation', i.id, i.account_login
          FROM installation i JOIN github_account ga ON ga.id = i.github_account_id
         WHERE ga.member_id = %(m)s AND i.status = 'active'
           AND EXISTS (
               SELECT 1 FROM repository r
                 JOIN project_repository pr ON pr.repository_id = r.id
                 JOIN project p ON p.id = pr.project_id AND p.deleted_at IS NULL
                WHERE r.installation_id = i.id
                  AND (EXISTS (SELECT 1 FROM project_member o WHERE o.project_id = p.id AND o.member_id <> %(m)s)
                       OR EXISTS (SELECT 1 FROM teamspace_member o WHERE o.teamspace_id = p.teamspace_id
                                                                     AND o.member_id <> %(m)s)))
        """,
        {"m": member_id},
    )
    return [{"kind": r["kind"], "id": str(r["id"]), "name": r["name"]} for r in cur.fetchall()]


@router.delete("/me", status_code=204)
def withdraw(body: WithdrawIn, member: dict = Depends(get_current_member)):
    """회원 탈퇴 — 행은 남기고(created_by 등 이력 FK 보존) 개인정보를 지운다(soft delete).

    혼자 관리자인 곳이 남아 있으면 409 + 목록. 나 혼자뿐인 공간(개인 페이지 포함)은 같이 soft delete.
    """
    if body.confirm.strip() != "탈퇴합니다":
        raise HTTPException(400, "confirm_mismatch")
    m = member["id"]
    with tx() as cur:
        _require_password(cur, m, body.password)
        blockers = _withdraw_blockers(cur, m)
        if blockers:
            raise HTTPException(409, {"code": "transfer_required", "items": blockers})

        # 나 혼자뿐인 공간은 주인 없는 채로 남기지 않고 함께 정리(soft delete).
        cur.execute(
            """
            UPDATE project p SET deleted_at = now() WHERE p.deleted_at IS NULL
               AND EXISTS (SELECT 1 FROM project_member pm WHERE pm.project_id = p.id AND pm.member_id = %(m)s)
               AND NOT EXISTS (SELECT 1 FROM project_member o WHERE o.project_id = p.id AND o.member_id <> %(m)s)
               AND NOT EXISTS (SELECT 1 FROM teamspace_member o WHERE o.teamspace_id = p.teamspace_id
                                                               AND o.member_id <> %(m)s)
            """,
            {"m": m},
        )
        cur.execute(
            """
            UPDATE teamspace t SET deleted_at = now() WHERE t.deleted_at IS NULL
               AND EXISTS (SELECT 1 FROM teamspace_member tm WHERE tm.teamspace_id = t.id AND tm.member_id = %(m)s)
               AND NOT EXISTS (SELECT 1 FROM teamspace_member o WHERE o.teamspace_id = t.id AND o.member_id <> %(m)s)
            """,
            {"m": m},
        )
        cur.execute(
            """
            UPDATE workspace w SET deleted_at = now() WHERE w.deleted_at IS NULL
               AND EXISTS (SELECT 1 FROM workspace_member wm WHERE wm.workspace_id = w.id AND wm.member_id = %(m)s)
               AND NOT EXISTS (SELECT 1 FROM workspace_member o WHERE o.workspace_id = w.id AND o.member_id <> %(m)s)
            """,
            {"m": m},
        )

        # 소속 해제 — 살아 있는 공간에서만. 방금 같이 soft delete 한 "나 혼자뿐인" 공간의 소속 행은
        # 남겨 둔다: DB 트리거(*_protect_last_admin/owner, sql/008·011)가 마지막 관리자 행 삭제를
        # 공간 삭제 여부와 무관하게 막고, 지워진 공간의 소속 행은 어디에도 노출되지 않는다.
        # 살아 있는 공간은 _withdraw_blockers 를 통과했으므로 다른 관리자가 반드시 있다.
        cur.execute(
            "DELETE FROM project_member pm USING project p WHERE pm.project_id = p.id "
            "AND pm.member_id = %s AND p.deleted_at IS NULL",
            (m,),
        )
        cur.execute(
            "DELETE FROM teamspace_member tm USING teamspace t WHERE tm.teamspace_id = t.id "
            "AND tm.member_id = %s AND t.deleted_at IS NULL",
            (m,),
        )
        cur.execute(
            "DELETE FROM workspace_member wm USING workspace w WHERE wm.workspace_id = w.id "
            "AND wm.member_id = %s AND w.deleted_at IS NULL",
            (m,),
        )
        # GitHub 연동 해제(설치가 팀 저장소를 잇는 경우는 _withdraw_blockers 가 이미 막았다).
        cur.execute("DELETE FROM github_account WHERE member_id = %s", (m,))
        # 내가 보낸 대기 중 초대 취소.
        for table in ("project_invite", "teamspace_invite", "workspace_invite"):
            cur.execute(
                f"UPDATE {table} SET revoked_at = now() "
                "WHERE created_by = %s AND accepted_at IS NULL AND revoked_at IS NULL",
                (m,),
            )
        cur.execute("DELETE FROM email_verification WHERE member_id = %s", (m,))

        # 개인정보 제거 + 세션 무효화. email 을 비워 같은 주소로 재가입할 수 있게 한다.
        cur.execute("SELECT avatar_key FROM member WHERE id = %s FOR UPDATE", (m,))
        avatar_key = cur.fetchone()["avatar_key"]
        cur.execute(
            """
            UPDATE member SET email = NULL, password_hash = NULL, email_verified_at = NULL,
                   name = %s, nickname = %s, avatar_key = NULL, avatar_source = 'initials',
                   avatar_updated_at = NULL, deleted_at = now(), token_version = token_version + 1,
                   updated_at = now()
             WHERE id = %s
            """,
            (WITHDRAWN_NAME, WITHDRAWN_NAME, m),
        )
        audit.record(cur, actor=f"user:{member['nickname']}", action="member.withdraw",
                     subject={"member_id": m}, payload={})

    if avatar_key:
        storage().delete(avatar_key)
    resp = Response(status_code=204)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp
