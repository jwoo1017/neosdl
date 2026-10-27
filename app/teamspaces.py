"""Teamspace API — workspace와 project 사이의 협업 단위(Notion의 teamspace

참고). project는 반드시 teamspace 하나에 속한다. visibility 3단계로
"누가 이 teamspace와 그 안의 project를 볼 수 있고, 누가 스스로 가입할 수
있는가"를 정한다:
  open    — workspace 멤버 누구나 열람 + 자율 가입
  closed  — workspace 멤버 누구나 열람 가능하지만 가입은 초대로만("참가 제한")
  private — teamspace_member가 아니면 존재 자체가 안 보임

teamspace_member 역할은 workspace처럼 owner/admin/member 3단계 고정이다(sql/039) —
project 단위 세부 권한은 지금처럼 project_member가 계속 담당하고,
teamspace 역할은 "teamspace 자체(이름·설명·공개범위·멤버)를
관리할 수 있는지"만 가른다(삭제·admin 관리는 owner, 나머지 관리는 admin 이상). 실제 project 접근 판정은
app.auth._effective_project_role이 담당한다.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import audit, profiles
from app.signup_policy import OptionalEmail
from app.auth import (
    assert_can_manage_member,
    assert_not_self_invite,
    assert_teamspace_role,
    assert_workspace_role,
    get_current_member,
    get_current_member_optional,
)
from app.config import settings
from app.db import tx
from app.mailer import invite_email_html, send_mail
from app.workspaces import KEEP_HIGHER_ROLE_SQL, ROLE_ORDER_SQL

router = APIRouter(tags=["teamspaces"])

_VISIBILITY_PATTERN = "^(open|closed|private)$"
_ROLE_PATTERN = "^(admin|member)$"              # 초대로 줄 수 있는 역할
_ASSIGNABLE_ROLE_PATTERN = "^(owner|admin|member)$"   # 역할 변경(owner 지정은 owner만)


class TeamspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    visibility: str = Field(pattern=_VISIBILITY_PATTERN, default="open")


class TeamspaceMemberRoleIn(BaseModel):
    role: str = Field(pattern=_ASSIGNABLE_ROLE_PATTERN)


_DISCIPLINE_PATTERN = "^(pm|frontend|backend)$"


class TeamspaceMemberDisciplineIn(BaseModel):
    discipline: str | None = Field(pattern=_DISCIPLINE_PATTERN, default=None)


class TeamspaceInviteIn(BaseModel):
    role: str = Field(pattern=_ROLE_PATTERN, default="member")
    target_email: OptionalEmail = None   # 비우면 링크 초대(외부인은 링크로 가입할 수 없다)
    # PM/frontend/backend — project 생성 마법사의 "담당자" 단계가 이 값을
    # 보여줘서, 어떤 teamspace 멤버가 어떤 직무인지 구분할 수 있게 한다.
    discipline: str | None = Field(pattern=_DISCIPLINE_PATTERN, default=None)


@router.post("/workspaces/{workspace_id}/teamspaces", status_code=201)
def create_teamspace(workspace_id: UUID, body: TeamspaceIn, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_workspace_role(cur, workspace_id, member["id"])  # workspace 멤버라면 누구나
        cur.execute(
            """
            INSERT INTO teamspace (workspace_id, name, description, visibility, created_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, name, description, visibility, created_at
            """,
            (str(workspace_id), body.name, body.description, body.visibility, member["id"]),
        )
        teamspace = cur.fetchone()
        cur.execute(
            "INSERT INTO teamspace_member (teamspace_id, member_id, role) VALUES (%s, %s, 'owner')",
            (teamspace["id"], member["id"]),
        )
        audit.record(cur, actor=actor, action="teamspace.create",
                     subject={"workspace_id": str(workspace_id), "teamspace_id": str(teamspace["id"])},
                     payload={"name": body.name, "visibility": body.visibility})
    return {**teamspace, "role": "owner"}


def _assert_not_personal(cur, teamspace_id) -> None:
    """개인 페이지(sql/031)는 초대·가입·멤버 관리·이름 변경·삭제를 받지 않는다 — 항상 주인 혼자인 공간."""
    cur.execute("SELECT personal_owner_id FROM teamspace WHERE id=%s", (str(teamspace_id),))
    row = cur.fetchone()
    if row is not None and row["personal_owner_id"] is not None:
        raise HTTPException(400, "personal_space")


PERSONAL_SPACE_NAME = "개인 페이지"


@router.get("/workspaces/{workspace_id}/personal-space")
def get_personal_space(workspace_id: UUID, member: dict = Depends(get_current_member)):
    """이 워크스페이스에서의 내 개인 페이지 — 없으면 만든다(get-or-create, DML 만).

    항상 private + 멤버는 나 혼자(admin)라, 기존 접근 규칙이 그대로 "나만 보임"을 보장한다.
    여기 프로젝트를 팀스페이스로 드래그(POST /projects/{id}/move)하면 그 팀과 공유된다.
    """
    with tx() as cur:
        assert_workspace_role(cur, workspace_id, member["id"])
        cur.execute(
            """
            INSERT INTO teamspace (workspace_id, name, visibility, created_by, personal_owner_id)
            VALUES (%s, %s, 'private', %s, %s)
            ON CONFLICT (workspace_id, personal_owner_id)
               WHERE personal_owner_id IS NOT NULL AND deleted_at IS NULL
            DO NOTHING
            RETURNING id
            """,
            (str(workspace_id), PERSONAL_SPACE_NAME, member["id"], member["id"]),
        )
        created = cur.fetchone()
        if created is not None:
            cur.execute("INSERT INTO teamspace_member (teamspace_id, member_id, role) VALUES (%s, %s, 'owner')",
                        (str(created["id"]), member["id"]))
        cur.execute(
            """
            SELECT ts.id, ts.name, ts.description, ts.visibility, ts.created_at, 'owner' AS my_role,
                   true AS personal,
                   (SELECT count(*) FROM project p WHERE p.teamspace_id = ts.id AND p.deleted_at IS NULL)
                     AS project_count
              FROM teamspace ts
             WHERE ts.workspace_id = %s AND ts.personal_owner_id = %s AND ts.deleted_at IS NULL
            """,
            (str(workspace_id), member["id"]),
        )
        return cur.fetchone()


@router.get("/workspaces/{workspace_id}/teamspaces")
def list_workspace_teamspaces(workspace_id: UUID, member: dict = Depends(get_current_member)):
    """private teamspace는 teamspace_member가 아니면 목록에서 아예 빠진다 —

    존재 자체가 workspace 멤버 전체에게 보이면 안 되기 때문. open/closed는
    가입 여부와 무관하게 다 보여주고, my_role로 가입 여부를 알려준다.
    """
    with tx() as cur:
        assert_workspace_role(cur, workspace_id, member["id"])
        cur.execute(
            """
            SELECT ts.id, ts.name, ts.description, ts.visibility, ts.created_at, tm.role AS my_role,
                   (SELECT count(*) FROM project p WHERE p.teamspace_id = ts.id AND p.deleted_at IS NULL) AS project_count
              FROM teamspace ts
              LEFT JOIN teamspace_member tm ON tm.teamspace_id = ts.id AND tm.member_id = %s
             WHERE ts.workspace_id = %s AND ts.deleted_at IS NULL
               AND ts.personal_owner_id IS NULL   -- 개인 페이지는 별도 API(personal-space)로
               AND (ts.visibility <> 'private' OR tm.member_id IS NOT NULL)
             ORDER BY ts.created_at
            """,
            (member["id"], str(workspace_id)),
        )
        return cur.fetchall()


def _load_teamspace_for_view(cur, teamspace_id: UUID, member_id: str) -> dict:
    cur.execute(
        """
        SELECT ts.id, ts.workspace_id, ts.name, ts.description, ts.visibility, ts.created_at,
               wm.role AS workspace_role, tm.role AS my_role
          FROM teamspace ts
          JOIN workspace w ON w.id = ts.workspace_id AND w.deleted_at IS NULL
          LEFT JOIN workspace_member wm ON wm.workspace_id = ts.workspace_id AND wm.member_id = %s
          LEFT JOIN teamspace_member tm ON tm.teamspace_id = ts.id AND tm.member_id = %s
         WHERE ts.id = %s AND ts.deleted_at IS NULL
        """,
        (member_id, member_id, str(teamspace_id)),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "teamspace not found")
    if row["visibility"] == "private" and row["my_role"] is None:
        raise HTTPException(404, "teamspace not found")
    if row["visibility"] != "private" and row["workspace_role"] is None and row["my_role"] is None:
        # open/closed라도 이 workspace 소속도 아니고 개별 초대도 안 받았으면 접근 불가.
        raise HTTPException(404, "teamspace not found")
    return row


@router.get("/teamspaces/{teamspace_id}")
def get_teamspace(teamspace_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        teamspace = _load_teamspace_for_view(cur, teamspace_id, member["id"])
        cur.execute(
            "SELECT " + profiles.PROFILE_COLUMNS + """, tm.role, tm.discipline, tm.joined_at
              FROM teamspace_member tm
              JOIN member m ON m.id = tm.member_id
             WHERE tm.teamspace_id = %s
             ORDER BY """ + ROLE_ORDER_SQL.format(col="tm.role") + """, m.nickname
            """,
            (str(teamspace_id),),
        )
        members = [profiles.with_profile(r) for r in cur.fetchall()]
    return {**teamspace, "members": members}


@router.patch("/teamspaces/{teamspace_id}")
def update_teamspace(teamspace_id: UUID, body: TeamspaceIn, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        _assert_not_personal(cur, teamspace_id)
        assert_teamspace_role(cur, teamspace_id, member["id"], "admin")
        cur.execute(
            """
            UPDATE teamspace SET name=%s, description=%s, visibility=%s, updated_at=now()
             WHERE id=%s AND deleted_at IS NULL
            RETURNING id, name, description, visibility
            """,
            (body.name, body.description, body.visibility, str(teamspace_id)),
        )
        teamspace = cur.fetchone()
        if teamspace is None:
            raise HTTPException(404, "teamspace not found")
        audit.record(cur, actor=actor, action="teamspace.update",
                     subject={"teamspace_id": str(teamspace_id)},
                     payload={"name": body.name, "visibility": body.visibility})
    return teamspace


@router.delete("/teamspaces/{teamspace_id}", status_code=204)
def delete_teamspace(teamspace_id: UUID, member: dict = Depends(get_current_member)):
    """소프트 삭제 — 소속 project도 함께 소프트 삭제한다(workspace.delete와 동일 패턴)."""
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        _assert_not_personal(cur, teamspace_id)
        assert_teamspace_role(cur, teamspace_id, member["id"], "owner")
        cur.execute(
            "UPDATE teamspace SET deleted_at=now(), updated_at=now() WHERE id=%s AND deleted_at IS NULL",
            (str(teamspace_id),),
        )
        cur.execute(
            "UPDATE project SET deleted_at=now(), updated_at=now() WHERE teamspace_id=%s AND deleted_at IS NULL",
            (str(teamspace_id),),
        )
        audit.record(cur, actor=actor, action="teamspace.delete", subject={"teamspace_id": str(teamspace_id)})


def _guard_last_teamspace_owner(cur, teamspace_id: UUID, target_member_id, current_role: str) -> None:
    if current_role != "owner":
        return
    cur.execute(
        "SELECT count(*) AS n FROM teamspace_member WHERE teamspace_id=%s AND role='owner' AND member_id<>%s",
        (str(teamspace_id), str(target_member_id)),
    )
    if cur.fetchone()["n"] == 0:
        raise HTTPException(409, "last_owner")


@router.patch("/teamspaces/{teamspace_id}/members/{target_member_id}")
def update_teamspace_member_role(teamspace_id: UUID, target_member_id: UUID, body: TeamspaceMemberRoleIn,
                                  member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        _assert_not_personal(cur, teamspace_id)
        actor_role = assert_teamspace_role(cur, teamspace_id, member["id"], "admin")
        cur.execute(
            "SELECT role FROM teamspace_member WHERE teamspace_id=%s AND member_id=%s",
            (str(teamspace_id), str(target_member_id)),
        )
        current = cur.fetchone()
        if current is None:
            raise HTTPException(404, "teamspace member not found")
        if current["role"] != body.role:
            assert_can_manage_member(actor_role, current["role"], body.role)
            _guard_last_teamspace_owner(cur, teamspace_id, target_member_id, current["role"])
        cur.execute(
            "UPDATE teamspace_member SET role=%s WHERE teamspace_id=%s AND member_id=%s",
            (body.role, str(teamspace_id), str(target_member_id)),
        )
        audit.record(cur, actor=actor, action="teamspace.member_role_change",
                     subject={"teamspace_id": str(teamspace_id), "member_id": str(target_member_id)},
                     payload={"role": body.role})
    return {"teamspace_id": str(teamspace_id), "member_id": str(target_member_id), "role": body.role}


@router.patch("/teamspaces/{teamspace_id}/members/{target_member_id}/discipline")
def update_teamspace_member_discipline(teamspace_id: UUID, target_member_id: UUID, body: TeamspaceMemberDisciplineIn,
                                        member: dict = Depends(get_current_member)):
    """role(admin/member, teamspace 관리 권한)과 별개 축 — PM/frontend/backend

    같은 실제 직무. project 생성 마법사의 "담당자" 단계가 이 값을 참고해
    teamspace 멤버 중 누가 어떤 역할인지 보여준다.
    """
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        _assert_not_personal(cur, teamspace_id)
        assert_teamspace_role(cur, teamspace_id, member["id"], "admin")
        cur.execute(
            "UPDATE teamspace_member SET discipline=%s WHERE teamspace_id=%s AND member_id=%s RETURNING member_id",
            (body.discipline, str(teamspace_id), str(target_member_id)),
        )
        if cur.fetchone() is None:
            raise HTTPException(404, "teamspace member not found")
        audit.record(cur, actor=actor, action="teamspace.member_discipline_change",
                     subject={"teamspace_id": str(teamspace_id), "member_id": str(target_member_id)},
                     payload={"discipline": body.discipline})
    return {"teamspace_id": str(teamspace_id), "member_id": str(target_member_id), "discipline": body.discipline}


@router.delete("/teamspaces/{teamspace_id}/members/{target_member_id}", status_code=204)
def remove_teamspace_member(teamspace_id: UUID, target_member_id: UUID, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    if str(target_member_id) == str(member["id"]):
        raise HTTPException(400, "use_leave_endpoint")   # 본인은 '나가기'(POST /leave)로
    with tx() as cur:
        _assert_not_personal(cur, teamspace_id)
        actor_role = assert_teamspace_role(cur, teamspace_id, member["id"], "admin")
        cur.execute(
            "SELECT role FROM teamspace_member WHERE teamspace_id=%s AND member_id=%s",
            (str(teamspace_id), str(target_member_id)),
        )
        current = cur.fetchone()
        if current is None:
            raise HTTPException(404, "teamspace member not found")
        assert_can_manage_member(actor_role, current["role"])
        _guard_last_teamspace_owner(cur, teamspace_id, target_member_id, current["role"])
        cur.execute("DELETE FROM teamspace_member WHERE teamspace_id=%s AND member_id=%s",
                     (str(teamspace_id), str(target_member_id)))
        audit.record(cur, actor=actor, action="teamspace.member_remove",
                     subject={"teamspace_id": str(teamspace_id), "member_id": str(target_member_id)})


@router.post("/teamspaces/{teamspace_id}/leave", status_code=204)
def leave_teamspace(teamspace_id: UUID, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        _assert_not_personal(cur, teamspace_id)
        cur.execute(
            "SELECT role FROM teamspace_member WHERE teamspace_id=%s AND member_id=%s",
            (str(teamspace_id), member["id"]),
        )
        current = cur.fetchone()
        if current is None:
            raise HTTPException(404, "not a member of this teamspace")
        _guard_last_teamspace_owner(cur, teamspace_id, member["id"], current["role"])
        cur.execute("DELETE FROM teamspace_member WHERE teamspace_id=%s AND member_id=%s",
                     (str(teamspace_id), member["id"]))
        audit.record(cur, actor=actor, action="teamspace.member_leave", subject={"teamspace_id": str(teamspace_id)})


@router.post("/teamspaces/{teamspace_id}/join", status_code=201)
def join_teamspace(teamspace_id: UUID, member: dict = Depends(get_current_member)):
    """visibility='open'인 teamspace만 초대 없이 자율 가입할 수 있다 —

    'closed'는 존재는 보이되 admin의 초대가 있어야 하고, 'private'은애초에
    목록에도 안 뜬다(둘 다 여기서 막는다).
    """
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        _assert_not_personal(cur, teamspace_id)
        cur.execute(
            "SELECT workspace_id, visibility FROM teamspace WHERE id=%s AND deleted_at IS NULL",
            (str(teamspace_id),),
        )
        teamspace = cur.fetchone()
        if teamspace is None:
            raise HTTPException(404, "teamspace not found")
        assert_workspace_role(cur, teamspace["workspace_id"], member["id"])
        if teamspace["visibility"] != "open":
            raise HTTPException(403, "this teamspace requires an invite to join")
        cur.execute(
            """
            INSERT INTO teamspace_member (teamspace_id, member_id, role, invited_by)
            VALUES (%s, %s, 'member', %s)
            ON CONFLICT (teamspace_id, member_id) DO NOTHING
            """,
            (str(teamspace_id), member["id"], member["id"]),
        )
        audit.record(cur, actor=actor, action="teamspace.join", subject={"teamspace_id": str(teamspace_id)})
    return {"teamspace_id": str(teamspace_id), "role": "member"}


def _send_teamspace_invite_email(target_email: str, inviter_nickname: str, teamspace_name: str,
                                  role: str, invite_url: str) -> bool:
    subject = f"{inviter_nickname}님이 '{teamspace_name}' 팀스페이스에 초대했습니다"
    body_line = f"{inviter_nickname}님이 '{teamspace_name}' 팀스페이스에 {role} 역할로 초대했습니다."
    expires_note = f"이 링크는 {settings().invite_ttl_days}일 후 만료됩니다."
    return send_mail(
        target_email,
        subject,
        f"{body_line}\n\n아래 링크를 열어 초대를 수락하세요:\n{invite_url}\n\n{expires_note}",
        html_body=invite_email_html(heading=subject, body_line=body_line, invite_url=invite_url,
                                     expires_note=expires_note),
    )


def _create_teamspace_invite(cur, teamspace_id: UUID, role: str, target_email: str | None,
                              created_by: str, discipline: str | None = None) -> tuple[dict, str]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    cur.execute(
        """
        INSERT INTO teamspace_invite (teamspace_id, role, token_hash, target_email, created_by, expires_at, discipline)
        VALUES (%s, %s, %s, %s, %s, now() + %s::interval, %s)
        RETURNING id, created_at, expires_at
        """,
        (str(teamspace_id), role, token_hash, target_email.lower() if target_email else None, created_by,
         timedelta(days=settings().invite_ttl_days), discipline),
    )
    return cur.fetchone(), raw_token


@router.post("/teamspaces/{teamspace_id}/invites", status_code=201)
def create_teamspace_invite(teamspace_id: UUID, body: TeamspaceInviteIn, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    assert_not_self_invite(member, body.target_email)
    with tx() as cur:
        _assert_not_personal(cur, teamspace_id)
        assert_teamspace_role(cur, teamspace_id, member["id"], "admin")
        cur.execute("SELECT name FROM teamspace WHERE id=%s AND deleted_at IS NULL", (str(teamspace_id),))
        teamspace = cur.fetchone()
        if teamspace is None:
            raise HTTPException(404, "teamspace not found")
        invite, raw_token = _create_teamspace_invite(cur, teamspace_id, body.role, body.target_email, member["id"],
                                                       body.discipline)
        audit.record(cur, actor=actor, action="teamspace.invite_create",
                     subject={"teamspace_id": str(teamspace_id), "invite_id": str(invite["id"])},
                     payload={"role": body.role, "target_email": body.target_email, "discipline": body.discipline})

    invite_url = f"{settings().frontend_url}/teamspace-invites/{raw_token}"
    email_sent = False
    if body.target_email:
        email_sent = _send_teamspace_invite_email(body.target_email, member["nickname"], teamspace["name"],
                                                    body.role, invite_url)
        with tx() as cur:
            cur.execute("UPDATE teamspace_invite SET email_sent=%s WHERE id=%s", (email_sent, str(invite["id"])))
    return {**invite, "token": raw_token, "invite_url": invite_url, "email_sent": email_sent}


@router.post("/teamspaces/{teamspace_id}/invites/{invite_id}/resend", status_code=201)
def resend_teamspace_invite(teamspace_id: UUID, invite_id: UUID, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        _assert_not_personal(cur, teamspace_id)
        assert_teamspace_role(cur, teamspace_id, member["id"], "admin")
        cur.execute(
            "SELECT role, target_email, discipline, accepted_at, revoked_at FROM teamspace_invite "
            "WHERE id=%s AND teamspace_id=%s",
            (str(invite_id), str(teamspace_id)),
        )
        old = cur.fetchone()
        if old is None:
            raise HTTPException(404, "invite not found")
        if old["accepted_at"] is not None or old["revoked_at"] is not None:
            raise HTTPException(410, "invite already resolved")
        if not old["target_email"]:
            raise HTTPException(400, "cannot resend a link-only invite (no target email)")

        cur.execute("SELECT name FROM teamspace WHERE id=%s AND deleted_at IS NULL", (str(teamspace_id),))
        teamspace = cur.fetchone()

        cur.execute("UPDATE teamspace_invite SET revoked_at=now() WHERE id=%s", (str(invite_id),))
        invite, raw_token = _create_teamspace_invite(cur, teamspace_id, old["role"], old["target_email"],
                                                        member["id"], old["discipline"])
        audit.record(cur, actor=actor, action="teamspace.invite_resend",
                     subject={"teamspace_id": str(teamspace_id), "invite_id": str(invite["id"]),
                              "old_invite_id": str(invite_id)},
                     payload={"role": old["role"], "target_email": old["target_email"]})

    invite_url = f"{settings().frontend_url}/teamspace-invites/{raw_token}"
    email_sent = _send_teamspace_invite_email(old["target_email"], member["nickname"], teamspace["name"],
                                                old["role"], invite_url)
    with tx() as cur:
        cur.execute("UPDATE teamspace_invite SET email_sent=%s WHERE id=%s", (email_sent, str(invite["id"])))
    return {**invite, "token": raw_token, "invite_url": invite_url, "email_sent": email_sent}


@router.get("/teamspaces/{teamspace_id}/invites")
def list_teamspace_invites(teamspace_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_teamspace_role(cur, teamspace_id, member["id"], "admin")
        cur.execute(
            """
            SELECT ti.id, ti.role, ti.target_email, ti.discipline, ti.created_at, ti.expires_at, ti.email_sent,
                   ti.accepted_at, ti.revoked_at, m.nickname AS created_by_nickname
              FROM teamspace_invite ti
              JOIN member m ON m.id = ti.created_by
             WHERE ti.teamspace_id = %s
             ORDER BY ti.created_at DESC
            """,
            (str(teamspace_id),),
        )
        return cur.fetchall()


@router.delete("/teamspaces/{teamspace_id}/invites/{invite_id}", status_code=204)
def revoke_teamspace_invite(teamspace_id: UUID, invite_id: UUID, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_teamspace_role(cur, teamspace_id, member["id"], "admin")
        cur.execute(
            """
            UPDATE teamspace_invite SET revoked_at=now()
             WHERE id=%s AND teamspace_id=%s AND accepted_at IS NULL AND revoked_at IS NULL
            RETURNING id
            """,
            (str(invite_id), str(teamspace_id)),
        )
        if cur.fetchone() is None:
            raise HTTPException(404, "invite not found or already resolved")
        audit.record(cur, actor=actor, action="teamspace.invite_revoke",
                     subject={"teamspace_id": str(teamspace_id), "invite_id": str(invite_id)})


def _fetch_pending_teamspace_invite(cur, *, token: str | None = None, invite_id=None) -> dict:
    """토큰(공개 링크) 또는 invite_id(수신함) 둘 중 하나로 조회한다."""
    if token is not None:
        where, param = "ti.token_hash = %s", hashlib.sha256(token.encode()).hexdigest()
    else:
        where, param = "ti.id = %s", str(invite_id)
    cur.execute(
        f"""
        SELECT ti.id, ti.teamspace_id, ti.role, ti.target_email, ti.discipline, ti.expires_at,
               ti.accepted_at, ti.revoked_at, ti.created_by, t.name AS teamspace_name,
               t.workspace_id, m.nickname AS invited_by_nickname
          FROM teamspace_invite ti
          JOIN teamspace t ON t.id = ti.teamspace_id AND t.deleted_at IS NULL
          JOIN member m ON m.id = ti.created_by
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


def _teamspace_invite_viewer_status(invite: dict, member: dict | None) -> str:
    if member is None:
        return "anonymous"
    if invite["target_email"] and invite["target_email"] != (member.get("email") or "").lower():
        return "email_mismatch"
    return "ok"


@router.get("/teamspace-invites/{token}")
def preview_teamspace_invite(token: str, member: dict | None = Depends(get_current_member_optional)):
    with tx() as cur:
        invite = _fetch_pending_teamspace_invite(cur, token=token)
    viewer_status = _teamspace_invite_viewer_status(invite, member)
    return {"teamspace_id": str(invite["teamspace_id"]), "teamspace_name": invite["teamspace_name"],
            "workspace_id": str(invite["workspace_id"]),
            "role": invite["role"], "invited_by_nickname": invite["invited_by_nickname"],
            "target_email": invite["target_email"], "viewer_status": viewer_status}


def apply_teamspace_invite_accept(cur, invite: dict, member: dict) -> dict:
    """teamspace 초대 수락은 workspace 합류를 의미하지 않는다 — project

    초대와 같은 원칙(독립적인 소속). 초대받은 teamspace가 workspace 소속과
    무관하게 그 teamspace(와 그 안의 project)에는 접근할 수 있게 하는 건
    [[_effective_project_role]]의 teamspace_member 우선 규칙이 담당한다.
    토큰 경로(accept_teamspace_invite)와 수신함 경로가 이 함수를 공유한다.
    """
    actor = f"user:{member['nickname']}"
    if _teamspace_invite_viewer_status(invite, member) == "email_mismatch":
        raise HTTPException(403, "this invite was issued to a different email address")
    cur.execute(
        """
        INSERT INTO teamspace_member (teamspace_id, member_id, role, invited_by, discipline)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (teamspace_id, member_id) DO UPDATE SET role = """ + KEEP_HIGHER_ROLE_SQL.format(
            tbl="teamspace_member") + """, discipline = EXCLUDED.discipline
        """,
        (str(invite["teamspace_id"]), member["id"], invite["role"], invite["created_by"], invite["discipline"]),
    )
    cur.execute(
        "UPDATE teamspace_invite SET accepted_at=now(), accepted_by=%s WHERE id=%s",
        (member["id"], str(invite["id"])),
    )
    audit.record(cur, actor=actor, action="teamspace.invite_accept",
                 subject={"teamspace_id": str(invite["teamspace_id"]), "invite_id": str(invite["id"])})
    return {"teamspace_id": str(invite["teamspace_id"]), "teamspace_name": invite["teamspace_name"],
            "workspace_id": str(invite["workspace_id"])}


@router.post("/teamspace-invites/{token}/accept", status_code=201)
def accept_teamspace_invite(token: str, member: dict = Depends(get_current_member)):
    with tx() as cur:
        invite = _fetch_pending_teamspace_invite(cur, token=token)
        return apply_teamspace_invite_accept(cur, invite, member)
