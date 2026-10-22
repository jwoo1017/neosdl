"""시스템 관리자 — 회원 목록·비활성화(퇴사자 처리)·재활성화.

관리자 권한(member.is_system_admin)은 웹에서 부여할 수 없고 서버에서 deploy/admin.py 로만 준다.
비활성화하면 suspended_at 을 기록하고 token_version 을 올린다 — 모든 기기의 세션이 바로 401,
열린 WebSocket 도 재검증 주기(app/auth.py WS_REVALIDATE_SECONDS) 안에 끊긴다. 이후 이메일·GitHub
로그인 모두 403 account_suspended. 계정·소속·작성 기록은 그대로 남는다(탈퇴와 다름).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app import audit, profiles, signup_policy
from app.auth import get_current_member
from app.db import tx

router = APIRouter(tags=["admin"])


def require_system_admin(member: dict = Depends(get_current_member)) -> dict:
    with tx() as cur:
        cur.execute("SELECT is_system_admin FROM member WHERE id=%s", (member["id"],))
        row = cur.fetchone()
    if row is None or not row["is_system_admin"]:
        raise HTTPException(403, "system_admin_required")
    return member


@router.get("/admin/members")
def list_members(q: str | None = None, admin: dict = Depends(require_system_admin)):
    """탈퇴하지 않은 회원 전체(최대 500명). q 는 이메일·이름·닉네임 부분 일치."""
    pattern = f"%{q.strip()}%" if q and q.strip() else None
    with tx() as cur:
        cur.execute(
            f"""
            SELECT {profiles.PROFILE_COLUMNS}, m.email, m.created_at, m.last_login_at, m.email_verified_at,
                   m.suspended_at, m.is_system_admin, m.locked_until > now() AS locked
              FROM member m
             WHERE m.deleted_at IS NULL
               AND (%(p)s::text IS NULL OR m.email ILIKE %(p)s OR m.name ILIKE %(p)s OR m.nickname ILIKE %(p)s)
             ORDER BY m.suspended_at IS NOT NULL, m.created_at DESC
             LIMIT 500
            """,
            {"p": pattern},
        )
        rows = cur.fetchall()
    return [
        {**profiles.with_profile(r), "id": str(r["member_id"]), "is_guest": signup_policy.is_guest(r["email"]),
         "locked": bool(r["locked"])}
        for r in rows
    ]


def _set_suspended(member_id: UUID, admin: dict, suspend: bool) -> dict:
    if str(member_id) == str(admin["id"]):
        raise HTTPException(400, "cannot_suspend_self")
    with tx() as cur:
        if suspend:
            cur.execute(
                """
                UPDATE member SET suspended_at=now(), suspended_by=%s, token_version=token_version+1, updated_at=now()
                 WHERE id=%s AND deleted_at IS NULL AND suspended_at IS NULL
                RETURNING id, nickname
                """,
                (admin["id"], str(member_id)),
            )
        else:
            cur.execute(
                """
                UPDATE member SET suspended_at=NULL, suspended_by=NULL, failed_login_count=0, locked_until=NULL,
                       updated_at=now()
                 WHERE id=%s AND deleted_at IS NULL AND suspended_at IS NOT NULL
                RETURNING id, nickname
                """,
                (str(member_id),),
            )
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT suspended_at FROM member WHERE id=%s AND deleted_at IS NULL", (str(member_id),))
            current = cur.fetchone()
            if current is None:
                raise HTTPException(404, "member not found")
            # 이미 그 상태 — 같은 요청을 두 번 보내도 결과는 같다.
            return {"id": str(member_id), "suspended": current["suspended_at"] is not None}
        audit.record(cur, actor=f"user:{admin['nickname']}",
                     action="member.suspend" if suspend else "member.reactivate",
                     subject={"member_id": str(member_id)}, payload={"nickname": row["nickname"]})
    return {"id": str(member_id), "suspended": suspend}


@router.post("/admin/members/{member_id}/suspend")
def suspend_member(member_id: UUID, admin: dict = Depends(require_system_admin)):
    return _set_suspended(member_id, admin, True)


@router.post("/admin/members/{member_id}/reactivate")
def reactivate_member(member_id: UUID, admin: dict = Depends(require_system_admin)):
    return _set_suspended(member_id, admin, False)
