"""Workspace API — member(서비스 계정)의 최상위 소속 단위.

Workspace는 팀 모델과 달리 가입 시 자동 생성되지 않는다 — 로그인한 회원이
직접 만든다. 역할은 owner/admin/member 3단계 고정이라 커스텀 역할 편집 API는
없다(정책은 sql/039, 권한 판정은 app.auth.assert_can_manage_member).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import audit, profiles
from app import signup_policy
from app.signup_policy import OptionalEmail
from app.auth import (
    assert_can_manage_member,
    assert_not_self_invite,
    assert_workspace_role,
    get_current_member,
    get_current_member_optional,
)
from app.config import settings
from app.db import tx
from app.mailer import invite_email_html, send_mail

router = APIRouter(tags=["workspaces"])


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # 생성 전용 — update_workspace는 이 필드를 읽지 않으므로 이름만 바꿀 때
    # type이 실수로 되돌아가는 일은 없다. 순수 표시/배지용이라 권한·초대
    # 흐름에는 영향이 없다.
    type: str = Field(pattern="^(personal|company)$", default="company")


class WorkspaceInviteIn(BaseModel):
    role: str = Field(pattern="^(admin|member)$", default="member")
    target_email: OptionalEmail = None   # 비우면 링크 초대(외부인은 링크로 가입할 수 없다)


class WorkspaceMemberRoleIn(BaseModel):
    role: str = Field(pattern="^(owner|admin|member)$")   # owner 지정은 owner만(초대로는 줄 수 없다)


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceIn, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    # 게스트(회사 도메인 밖, 초대로 가입)는 초대받은 공간만 쓴다 — Slack/Notion 게스트 관례.
    if signup_policy.is_guest(member.get("email")):
        raise HTTPException(403, "guest_cannot_create_workspace")
    with tx() as cur:
        cur.execute(
            "INSERT INTO workspace (name, created_by, type) VALUES (%s, %s, %s) "
            "RETURNING id, name, type, created_at",
            (body.name, member["id"], body.type),
        )
        workspace = cur.fetchone()
        cur.execute(
            "INSERT INTO workspace_member (workspace_id, member_id, role) VALUES (%s, %s, 'owner')",
            (workspace["id"], member["id"]),
        )
        audit.record(cur, actor=actor, action="workspace.create",
                     subject={"workspace_id": str(workspace["id"])}, payload={"name": body.name})
    return {**workspace, "role": "owner"}


@router.get("/workspaces")
def list_my_workspaces(member: dict = Depends(get_current_member)):
    with tx() as cur:
        cur.execute(
            """
            SELECT w.id, w.name, w.type, w.created_at, wm.role
              FROM workspace_member wm
              JOIN workspace w ON w.id = wm.workspace_id AND w.deleted_at IS NULL
             WHERE wm.member_id = %s
             ORDER BY w.created_at
            """,
            (member["id"],),
        )
        return cur.fetchall()


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_workspace_role(cur, workspace_id, member["id"])
        cur.execute("SELECT id, name, type, created_at FROM workspace WHERE id=%s AND deleted_at IS NULL",
                     (str(workspace_id),))
        workspace = cur.fetchone()
        cur.execute(
            "SELECT " + profiles.PROFILE_COLUMNS + """, wm.role, wm.joined_at
              FROM workspace_member wm
              JOIN member m ON m.id = wm.member_id
             WHERE wm.workspace_id = %s
             ORDER BY """ + ROLE_ORDER_SQL.format(col="wm.role") + """, m.nickname
            """,
            (str(workspace_id),),
        )
        members = [profiles.with_profile(r) for r in cur.fetchall()]
    return {**workspace, "members": members}


@router.patch("/workspaces/{workspace_id}")
def update_workspace(workspace_id: UUID, body: WorkspaceIn, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_workspace_role(cur, workspace_id, member["id"], "admin")
        cur.execute(
            "UPDATE workspace SET name=%s, updated_at=now() WHERE id=%s AND deleted_at IS NULL RETURNING id, name",
            (body.name, str(workspace_id)),
        )
        workspace = cur.fetchone()
        if workspace is None:
            raise HTTPException(404, "workspace not found")
        audit.record(cur, actor=actor, action="workspace.update",
                     subject={"workspace_id": str(workspace_id)}, payload={"name": body.name})
    return workspace


@router.delete("/workspaces/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: UUID, member: dict = Depends(get_current_member)):
    """소프트 삭제 — 소속 teamspace/project도 함께 소프트 삭제한다.

    teamspace/project는 workspace.deleted_at이 아니라 자신의 deleted_at으로만
    가시성이 판정되므로(assert_teamspace_role/assert_project_role), 여기서
    같이 내려주지 않으면 workspace는 사라졌는데 그 id를 아는 사람에겐 여전히
    보이는(심지어 teamspace admin이면 project를 새로 만들 수도 있는) 불일치가
    생긴다. project는 teamspace 삭제만으로는 안 지워지므로(각자 자기
    deleted_at으로 판정) 둘 다 명시적으로 내린다.
    """
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_workspace_role(cur, workspace_id, member["id"], "owner")
        cur.execute(
            "UPDATE workspace SET deleted_at=now(), updated_at=now() WHERE id=%s AND deleted_at IS NULL",
            (str(workspace_id),),
        )
        cur.execute(
            "UPDATE teamspace SET deleted_at=now(), updated_at=now() WHERE workspace_id=%s AND deleted_at IS NULL",
            (str(workspace_id),),
        )
        cur.execute(
            "UPDATE project SET deleted_at=now(), updated_at=now() WHERE workspace_id=%s AND deleted_at IS NULL",
            (str(workspace_id),),
        )
        audit.record(cur, actor=actor, action="workspace.delete", subject={"workspace_id": str(workspace_id)})


# 목록 정렬: owner → admin → member (enum 선언 순서가 admin, member, owner 라 role DESC 로는 안 된다).
ROLE_ORDER_SQL = "CASE {col} WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END"


def _guard_last_owner(cur, workspace_id: UUID, target_member_id, current_role: str) -> None:
    """owner 가 빠지거나 강등되면 남는 owner 가 있어야 한다(sql/039 트리거와 같은 규칙 — 여기서
    먼저 막아 500 대신 409 로 돌려준다)."""
    if current_role != "owner":
        return
    cur.execute(
        "SELECT count(*) AS n FROM workspace_member WHERE workspace_id=%s AND role='owner' AND member_id<>%s",
        (str(workspace_id), str(target_member_id)),
    )
    if cur.fetchone()["n"] == 0:
        raise HTTPException(409, "last_owner")


@router.patch("/workspaces/{workspace_id}/members/{target_member_id}")
def update_workspace_member_role(workspace_id: UUID, target_member_id: UUID, body: WorkspaceMemberRoleIn,
                                  member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        actor_role = assert_workspace_role(cur, workspace_id, member["id"], "admin")
        cur.execute(
            "SELECT role FROM workspace_member WHERE workspace_id=%s AND member_id=%s",
            (str(workspace_id), str(target_member_id)),
        )
        current = cur.fetchone()
        if current is None:
            raise HTTPException(404, "workspace member not found")
        if current["role"] != body.role:
            assert_can_manage_member(actor_role, current["role"], body.role)
            _guard_last_owner(cur, workspace_id, target_member_id, current["role"])
        cur.execute(
            "UPDATE workspace_member SET role=%s WHERE workspace_id=%s AND member_id=%s",
            (body.role, str(workspace_id), str(target_member_id)),
        )
        audit.record(cur, actor=actor, action="workspace.member_role_change",
                     subject={"workspace_id": str(workspace_id), "member_id": str(target_member_id)},
                     payload={"role": body.role})
    return {"workspace_id": str(workspace_id), "member_id": str(target_member_id), "role": body.role}


@router.delete("/workspaces/{workspace_id}/members/{target_member_id}", status_code=204)
def remove_workspace_member(workspace_id: UUID, target_member_id: UUID, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    if str(target_member_id) == str(member["id"]):
        raise HTTPException(400, "use_leave_endpoint")   # 본인은 '나가기'(POST /leave)로
    with tx() as cur:
        actor_role = assert_workspace_role(cur, workspace_id, member["id"], "admin")
        cur.execute(
            "SELECT role FROM workspace_member WHERE workspace_id=%s AND member_id=%s",
            (str(workspace_id), str(target_member_id)),
        )
        current = cur.fetchone()
        if current is None:
            raise HTTPException(404, "workspace member not found")
        assert_can_manage_member(actor_role, current["role"])
        _guard_last_owner(cur, workspace_id, target_member_id, current["role"])
        cur.execute("DELETE FROM workspace_member WHERE workspace_id=%s AND member_id=%s",
                     (str(workspace_id), str(target_member_id)))
        audit.record(cur, actor=actor, action="workspace.member_remove",
                     subject={"workspace_id": str(workspace_id), "member_id": str(target_member_id)})


@router.post("/workspaces/{workspace_id}/leave", status_code=204)
def leave_workspace(workspace_id: UUID, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        cur.execute(
            "SELECT role FROM workspace_member WHERE workspace_id=%s AND member_id=%s",
            (str(workspace_id), member["id"]),
        )
        current = cur.fetchone()
        if current is None:
            raise HTTPException(404, "not a member of this workspace")
        _guard_last_owner(cur, workspace_id, member["id"], current["role"])
        cur.execute("DELETE FROM workspace_member WHERE workspace_id=%s AND member_id=%s",
                     (str(workspace_id), member["id"]))
        audit.record(cur, actor=actor, action="workspace.member_leave", subject={"workspace_id": str(workspace_id)})


def _send_workspace_invite_email(target_email: str, inviter_nickname: str, workspace_name: str,
                                  role: str, invite_url: str) -> bool:
    subject = f"{inviter_nickname}님이 '{workspace_name}' 워크스페이스에 초대했습니다"
    body_line = f"{inviter_nickname}님이 '{workspace_name}' 워크스페이스에 {role} 역할로 초대했습니다."
    expires_note = f"이 링크는 {settings().invite_ttl_days}일 후 만료됩니다."
    return send_mail(
        target_email,
        subject,
        f"{body_line}\n\n아래 링크를 열어 초대를 수락하세요:\n{invite_url}\n\n{expires_note}",
        html_body=invite_email_html(heading=subject, body_line=body_line, invite_url=invite_url,
                                     expires_note=expires_note),
    )


def _create_workspace_invite(cur, workspace_id: UUID, role: str, target_email: str | None,
                              created_by: str) -> tuple[dict, str]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    cur.execute(
        """
        INSERT INTO workspace_invite (workspace_id, role, token_hash, target_email, created_by, expires_at)
        VALUES (%s, %s, %s, %s, %s, now() + %s::interval)
        RETURNING id, created_at, expires_at
        """,
        (str(workspace_id), role, token_hash, target_email.lower() if target_email else None, created_by,
         timedelta(days=settings().invite_ttl_days)),
    )
    return cur.fetchone(), raw_token


@router.post("/workspaces/{workspace_id}/invites", status_code=201)
def create_workspace_invite(workspace_id: UUID, body: WorkspaceInviteIn, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    assert_not_self_invite(member, body.target_email)
    with tx() as cur:
        assert_workspace_role(cur, workspace_id, member["id"], "admin")
        cur.execute("SELECT name FROM workspace WHERE id=%s AND deleted_at IS NULL", (str(workspace_id),))
        workspace = cur.fetchone()
        if workspace is None:
            raise HTTPException(404, "workspace not found")
        invite, raw_token = _create_workspace_invite(cur, workspace_id, body.role, body.target_email, member["id"])
        audit.record(cur, actor=actor, action="workspace.invite_create",
                     subject={"workspace_id": str(workspace_id), "invite_id": str(invite["id"])},
                     payload={"role": body.role, "target_email": body.target_email})

    invite_url = f"{settings().frontend_url}/workspace-invites/{raw_token}"
    email_sent = False
    if body.target_email:
        email_sent = _send_workspace_invite_email(body.target_email, member["nickname"], workspace["name"],
                                                    body.role, invite_url)
        with tx() as cur:
            cur.execute("UPDATE workspace_invite SET email_sent=%s WHERE id=%s", (email_sent, str(invite["id"])))
    return {**invite, "token": raw_token, "invite_url": invite_url, "email_sent": email_sent}


@router.post("/workspaces/{workspace_id}/invites/{invite_id}/resend", status_code=201)
def resend_workspace_invite(workspace_id: UUID, invite_id: UUID, member: dict = Depends(get_current_member)):
    """토큰은 해시로만 저장돼 있어 원래 링크를 그대로 다시 보낼 수 없다 —

    기존 초대를 취소하고 같은 이메일/역할로 새 초대를 발급해 다시 발송한다.
    받는 사람 입장에서는 "다시 초대 메일이 온 것"과 동일하다.
    """
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_workspace_role(cur, workspace_id, member["id"], "admin")
        cur.execute(
            "SELECT role, target_email, accepted_at, revoked_at FROM workspace_invite "
            "WHERE id=%s AND workspace_id=%s",
            (str(invite_id), str(workspace_id)),
        )
        old = cur.fetchone()
        if old is None:
            raise HTTPException(404, "invite not found")
        if old["accepted_at"] is not None or old["revoked_at"] is not None:
            raise HTTPException(410, "invite already resolved")
        if not old["target_email"]:
            raise HTTPException(400, "cannot resend a link-only invite (no target email)")

        cur.execute("SELECT name FROM workspace WHERE id=%s AND deleted_at IS NULL", (str(workspace_id),))
        workspace = cur.fetchone()

        cur.execute("UPDATE workspace_invite SET revoked_at=now() WHERE id=%s", (str(invite_id),))
        invite, raw_token = _create_workspace_invite(cur, workspace_id, old["role"], old["target_email"],
                                                        member["id"])
        audit.record(cur, actor=actor, action="workspace.invite_resend",
                     subject={"workspace_id": str(workspace_id), "invite_id": str(invite["id"]),
                              "old_invite_id": str(invite_id)},
                     payload={"role": old["role"], "target_email": old["target_email"]})

    invite_url = f"{settings().frontend_url}/workspace-invites/{raw_token}"
    email_sent = _send_workspace_invite_email(old["target_email"], member["nickname"], workspace["name"],
                                                old["role"], invite_url)
    with tx() as cur:
        cur.execute("UPDATE workspace_invite SET email_sent=%s WHERE id=%s", (email_sent, str(invite["id"])))
    return {**invite, "token": raw_token, "invite_url": invite_url, "email_sent": email_sent}


@router.get("/workspaces/{workspace_id}/invites")
def list_workspace_invites(workspace_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_workspace_role(cur, workspace_id, member["id"], "admin")
        cur.execute(
            """
            SELECT wi.id, wi.role, wi.target_email, wi.created_at, wi.expires_at, wi.email_sent,
                   wi.accepted_at, wi.revoked_at, m.nickname AS created_by_nickname
              FROM workspace_invite wi
              JOIN member m ON m.id = wi.created_by
             WHERE wi.workspace_id = %s
             ORDER BY wi.created_at DESC
            """,
            (str(workspace_id),),
        )
        return cur.fetchall()


@router.delete("/workspaces/{workspace_id}/invites/{invite_id}", status_code=204)
def revoke_workspace_invite(workspace_id: UUID, invite_id: UUID, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_workspace_role(cur, workspace_id, member["id"], "admin")
        cur.execute(
            """
            UPDATE workspace_invite SET revoked_at=now()
             WHERE id=%s AND workspace_id=%s AND accepted_at IS NULL AND revoked_at IS NULL
            RETURNING id
            """,
            (str(invite_id), str(workspace_id)),
        )
        if cur.fetchone() is None:
            raise HTTPException(404, "invite not found or already resolved")
        audit.record(cur, actor=actor, action="workspace.invite_revoke",
                     subject={"workspace_id": str(workspace_id), "invite_id": str(invite_id)})


def _fetch_pending_workspace_invite(cur, *, token: str | None = None, invite_id=None) -> dict:
    """토큰(공개 링크) 또는 invite_id(수신함처럼 이미 로그인된 회원 본인

    이메일로만 조회할 때) 둘 중 하나로 조회한다 — 원문 토큰은 저장하지
    않으므로 수신함 쪽은 id로만 찾을 수 있다.
    """
    if token is not None:
        where, param = "wi.token_hash = %s", hashlib.sha256(token.encode()).hexdigest()
    else:
        where, param = "wi.id = %s", str(invite_id)
    cur.execute(
        f"""
        SELECT wi.id, wi.workspace_id, wi.role, wi.target_email, wi.expires_at,
               wi.accepted_at, wi.revoked_at, wi.created_by, w.name AS workspace_name,
               m.nickname AS invited_by_nickname
          FROM workspace_invite wi
          JOIN workspace w ON w.id = wi.workspace_id AND w.deleted_at IS NULL
          JOIN member m ON m.id = wi.created_by
         WHERE {where}
        """,
        (param,),
    )
    invite = cur.fetchone()
    if invite is None:
        raise HTTPException(404, "invite not found")
    if invite["accepted_at"] is not None or invite["revoked_at"] is not None:
        raise HTTPException(410, "invite already resolved")
    if invite["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(410, "invite expired")
    return invite


def _workspace_invite_viewer_status(invite: dict, member: dict | None) -> str:
    if member is None:
        return "anonymous"
    if invite["target_email"] and invite["target_email"] != (member.get("email") or "").lower():
        return "email_mismatch"
    return "ok"


@router.get("/workspace-invites/{token}")
def preview_workspace_invite(token: str, member: dict | None = Depends(get_current_member_optional)):
    with tx() as cur:
        invite = _fetch_pending_workspace_invite(cur, token=token)
    viewer_status = _workspace_invite_viewer_status(invite, member)
    return {"workspace_id": str(invite["workspace_id"]), "workspace_name": invite["workspace_name"],
            "role": invite["role"], "invited_by_nickname": invite["invited_by_nickname"],
            "target_email": invite["target_email"], "viewer_status": viewer_status}


# 이미 소속된 사람이 초대를 수락해도 역할을 낮추지 않는다(owner 가 member 초대를 수락해 강등되는 일 방지).
KEEP_HIGHER_ROLE_SQL = ("CASE WHEN " + ROLE_ORDER_SQL.format(col="{tbl}.role") + " <= "
                        + ROLE_ORDER_SQL.format(col="EXCLUDED.role") + " THEN {tbl}.role ELSE EXCLUDED.role END")


def apply_workspace_invite_accept(cur, invite: dict, member: dict) -> dict:
    """수락 로직 본체 — 토큰 경로(accept_workspace_invite)와 수신함 경로

    (app/github_oauth.py의 GET /me/invites 대응 accept)가 공유한다.
    """
    actor = f"user:{member['nickname']}"
    if _workspace_invite_viewer_status(invite, member) == "email_mismatch":
        raise HTTPException(403, "this invite was issued to a different email address")
    cur.execute(
        """
        INSERT INTO workspace_member (workspace_id, member_id, role, invited_by)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (workspace_id, member_id) DO UPDATE SET role = """ + KEEP_HIGHER_ROLE_SQL.format(
            tbl="workspace_member") + """
        """,
        (str(invite["workspace_id"]), member["id"], invite["role"], invite["created_by"]),
    )
    cur.execute(
        "UPDATE workspace_invite SET accepted_at=now(), accepted_by=%s WHERE id=%s",
        (member["id"], str(invite["id"])),
    )
    audit.record(cur, actor=actor, action="workspace.invite_accept",
                 subject={"workspace_id": str(invite["workspace_id"]), "invite_id": str(invite["id"])})
    return {"workspace_id": str(invite["workspace_id"]), "workspace_name": invite["workspace_name"]}


@router.post("/workspace-invites/{token}/accept", status_code=201)
def accept_workspace_invite(token: str, member: dict = Depends(get_current_member)):
    with tx() as cur:
        invite = _fetch_pending_workspace_invite(cur, token=token)
        return apply_workspace_invite_accept(cur, invite, member)
