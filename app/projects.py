"""Project API — workspace 안에 사는 협업 단위. 역할은 owner/manager/developer/viewer

고정 4단계라 커스텀 역할 편집 API는 없다(007의 team_role 방식 폐기).
repository는 installation 전체가 아니라 개별 단위로 project에 붙는다
(project_repository) — 그 attach/detach/list도 여기서 다룬다.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import audit, profiles
from app.auth import (
    assert_not_self_invite,
    assert_project_role,
    assert_teamspace_role,
    effective_project_role,
    get_current_member,
    get_current_member_optional,
)
from app.config import settings
from app.db import tx
from app.mailer import invite_email_html, send_mail
from app.project_spec import generate_project_spec

router = APIRouter(tags=["projects"])

_PROJECT_ROLES = "^(owner|manager|developer|viewer)$"


_LEAD_ROLE_PATTERN = "^(manager|developer|viewer)$"


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # 생성 전용 — update_project는 이 필드들을 읽지 않는다.
    description: str | None = Field(default=None, max_length=2000)
    lead_member_id: UUID | None = None  # 이 teamspace의 기존 멤버여야 한다(선택 사항).
    lead_role: str = Field(pattern=_LEAD_ROLE_PATTERN, default="manager")  # lead_member_id를 넣을 때만 쓰인다.


class ProjectMoveIn(BaseModel):
    teamspace_id: UUID


class ProjectSpecIn(BaseModel):
    repo_language: str | None = None
    repo_description: str | None = None
    repo_topics: list[str] = []


class ProjectInviteIn(BaseModel):
    role: str = Field(pattern=_PROJECT_ROLES, default="developer")
    target_email: str | None = None


class ProjectMemberRoleIn(BaseModel):
    role: str = Field(pattern=_PROJECT_ROLES)


class AttachRepositoryIn(BaseModel):
    repository_id: UUID


@router.post("/teamspaces/{teamspace_id}/projects", status_code=201)
def create_project(teamspace_id: UUID, body: ProjectIn, member: dict = Depends(get_current_member)):
    """project는 반드시 teamspace 하나에 속한다 — teamspace 멤버라면 누구나

    (admin/member 무관) 그 안에 project를 만들 수 있다. 'open' teamspace는
    누구나 스스로 가입할 수 있으니 사실상 워크스페이스 멤버 누구나로
    이어지고, 'closed'/'private'은 미리 가입/초대된 사람만 만들 수 있다.
    """
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_teamspace_role(cur, teamspace_id, member["id"])
        cur.execute("SELECT workspace_id FROM teamspace WHERE id=%s AND deleted_at IS NULL", (str(teamspace_id),))
        teamspace = cur.fetchone()
        if teamspace is None:
            raise HTTPException(404, "teamspace not found")
        workspace_id = teamspace["workspace_id"]
        cur.execute(
            "INSERT INTO project (name, workspace_id, teamspace_id, created_by, description) "
            "VALUES (%s, %s, %s, %s, %s) "
            "RETURNING id, name, workspace_id, teamspace_id, description, created_at",
            (body.name, str(workspace_id), str(teamspace_id), member["id"], body.description),
        )
        project = cur.fetchone()
        cur.execute(
            "INSERT INTO project_member (project_id, member_id, role) VALUES (%s, %s, 'owner')",
            (project["id"], member["id"]),
        )
        if body.lead_member_id and str(body.lead_member_id) != member["id"]:
            # 담당자로 고른 사람이 실제 이 teamspace 멤버일 때만, 초대/수락 없이
            # 바로 골라준 역할로 넣는다 — teamspace 합류 시 이미 직무(PM/frontend/
            # backend)까지 정해서 한 번 검증된 사람이라, project 단위로 또 accept를
            # 받게 하는 건 불필요한 마찰이다.
            cur.execute(
                "SELECT 1 FROM teamspace_member WHERE teamspace_id=%s AND member_id=%s",
                (str(teamspace_id), str(body.lead_member_id)),
            )
            if cur.fetchone() is not None:
                cur.execute(
                    "INSERT INTO project_member (project_id, member_id, role) VALUES (%s, %s, %s) "
                    "ON CONFLICT (project_id, member_id) DO NOTHING",
                    (project["id"], str(body.lead_member_id), body.lead_role),
                )
        audit.record(cur, actor=actor, action="project.create",
                     subject={"project_id": str(project["id"])},
                     payload={"name": body.name, "teamspace_id": str(teamspace_id)})
    return {**project, "role": "owner"}


@router.post("/projects/{project_id}/generate-spec")
def generate_project_spec_endpoint(project_id: UUID, body: ProjectSpecIn, member: dict = Depends(get_current_member)):
    """생성 마법사의 마지막(자동) 단계 — 레포 스캔 없이, 이미 입력/선택된

    프로젝트 소개(project.description) + 레포지토리 선택 단계에서 이미
    받아온 GitHub 메타데이터(언어/설명/토픽)만으로 LLM에게 태그/요약을
    맡긴다. 실패해도 예외를 던지지 않고 빈 결과로 응답한다 — 자동 정리는
    편의 기능이지 프로젝트 생성을 막을 이유가 아니다.
    """
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"], "manager")
        cur.execute("SELECT description FROM project WHERE id=%s AND deleted_at IS NULL", (str(project_id),))
        project = cur.fetchone()
        if project is None:
            raise HTTPException(404, "project not found")

    spec = generate_project_spec(
        intro=project["description"] or "",
        repo_language=body.repo_language,
        repo_description=body.repo_description,
        repo_topics=body.repo_topics,
    )

    with tx() as cur:
        cur.execute(
            "UPDATE project SET spec_summary=%s, spec_tags=%s, updated_at=now() WHERE id=%s AND deleted_at IS NULL",
            (spec["summary"], spec["tags"], str(project_id)),
        )
    return spec


def _list_projects_with_effective_role(cur, extra_where: str, params: tuple, *, q: str | None = None) -> list[dict]:
    """project 목록을 teamspace 가시성까지 감안한 role과 함께 돌려준다 —

    실제 판정은 [[effective_project_role]] 하나로 통일해서 assert_project_role
    (단건 접근 체크)과 여기(다건 목록)가 같은 규칙을 쓰게 한다. private
    teamspace의 project는 teamspace_member가 아니면 role이 None이 되므로
    목록에서 걸러낸다.

    `q`가 있으면 이름 부분일치(ILIKE)로 좁힌다 — 채팅 입력창의 "@ 프로젝트
    멘션" 자동완성이 쓴다.
    """
    where = extra_where
    if q:
        where += " AND p.name ILIKE %s"
        params = params + (f"%{q}%",)
    cur.execute(
        f"""
        SELECT p.id, p.name, p.spec_tags, p.created_at, p.teamspace_id,
               pm.role AS project_role, wm.role AS workspace_role,
               t.visibility AS teamspace_visibility, (tm.member_id IS NOT NULL) AS is_teamspace_member
          FROM project p
          JOIN teamspace t ON t.id = p.teamspace_id AND t.deleted_at IS NULL
          LEFT JOIN project_member pm ON pm.project_id = p.id AND pm.member_id = %s
          LEFT JOIN workspace_member wm ON wm.workspace_id = p.workspace_id AND wm.member_id = %s
          LEFT JOIN teamspace_member tm ON tm.teamspace_id = t.id AND tm.member_id = %s
         WHERE p.deleted_at IS NULL AND {where}
         ORDER BY p.created_at
        """,
        params,
    )
    rows = []
    for row in cur.fetchall():
        role = effective_project_role(row["project_role"], row["workspace_role"],
                                       row["teamspace_visibility"], row["is_teamspace_member"])
        if role is None:
            continue
        rows.append({"id": row["id"], "name": row["name"], "spec_tags": row["spec_tags"],
                     "created_at": row["created_at"], "teamspace_id": row["teamspace_id"],
                     "role": role})
    return rows


@router.get("/workspaces/{workspace_id}/projects")
def list_workspace_projects(workspace_id: UUID, member: dict = Depends(get_current_member)):
    """이 workspace 안에서 caller가 볼 수 있는 모든 project를, 소속

    teamspace와 무관하게 평평한 목록으로 돌려준다(사이드바의 project
    스위처처럼 teamspace 구분 없이 하나의 목록이 필요한 곳에서 사용).
    teamspace별로 묶은 트리가 필요하면 [[list_teamspace_projects]]를 쓴다.
    """
    with tx() as cur:
        cur.execute("SELECT 1 FROM workspace WHERE id=%s AND deleted_at IS NULL", (str(workspace_id),))
        if cur.fetchone() is None:
            raise HTTPException(404, "workspace not found")

        rows = _list_projects_with_effective_role(
            cur, "p.workspace_id = %s", (member["id"], member["id"], member["id"], str(workspace_id))
        )

        cur.execute(
            "SELECT 1 FROM workspace_member WHERE workspace_id=%s AND member_id=%s",
            (str(workspace_id), member["id"]),
        )
        is_workspace_member = cur.fetchone() is not None
        if not is_workspace_member and not rows:
            # 워크스페이스 멤버도 아니고 그 안에 소속된 project도 없으면
            # 다른 테넌트의 워크스페이스 존재만 확인해주는 셈이라 404.
            raise HTTPException(404, "workspace not found")
        return rows


@router.get("/teamspaces/{teamspace_id}/projects")
def list_teamspace_projects(teamspace_id: UUID, q: str | None = Query(None),
                             member: dict = Depends(get_current_member)):
    """사이드바의 teamspace > project 트리용 — 한 teamspace 안의 project만.

    `q`를 주면 채팅 입력창의 "@ 프로젝트 멘션" 자동완성이 쓰는 이름 검색이 된다.
    """
    with tx() as cur:
        cur.execute("SELECT visibility FROM teamspace WHERE id=%s AND deleted_at IS NULL", (str(teamspace_id),))
        teamspace = cur.fetchone()
        if teamspace is None:
            raise HTTPException(404, "teamspace not found")
        if teamspace["visibility"] == "private":
            # private teamspace는 project가 하나도 없어도(=조건 없이 그냥
            # 빈 목록) 존재 자체를 들키면 안 되므로, 멤버가 아니면 project
            # 조회 전에 바로 404로 막는다.
            cur.execute(
                "SELECT 1 FROM teamspace_member WHERE teamspace_id=%s AND member_id=%s",
                (str(teamspace_id), member["id"]),
            )
            if cur.fetchone() is None:
                raise HTTPException(404, "teamspace not found")
        rows = _list_projects_with_effective_role(
            cur, "p.teamspace_id = %s", (member["id"], member["id"], member["id"], str(teamspace_id)), q=q
        )
        return rows


@router.get("/projects/{project_id}")
def get_project(project_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"])
        cur.execute(
            "SELECT id, name, workspace_id, teamspace_id, description, spec_summary, spec_tags, "
            "created_at FROM project WHERE id=%s AND deleted_at IS NULL",
            (str(project_id),),
        )
        project = cur.fetchone()
        cur.execute(
            "SELECT " + profiles.PROFILE_COLUMNS + """, pm.role, pm.joined_at
              FROM project_member pm
              JOIN member m ON m.id = pm.member_id
             WHERE pm.project_id = %s
             ORDER BY pm.role DESC, m.nickname
            """,
            (str(project_id),),
        )
        members = [profiles.with_profile(r) for r in cur.fetchall()]
    return {**project, "members": members}


@router.patch("/projects/{project_id}")
def update_project(project_id: UUID, body: ProjectIn, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"], "manager")
        cur.execute(
            "UPDATE project SET name=%s, updated_at=now() WHERE id=%s AND deleted_at IS NULL RETURNING id, name",
            (body.name, str(project_id)),
        )
        project = cur.fetchone()
        if project is None:
            raise HTTPException(404, "project not found")
        audit.record(cur, actor=actor, action="project.update",
                     subject={"project_id": str(project_id)}, payload={"name": body.name})
    return project


@router.post("/projects/{project_id}/move")
def move_project(project_id: UUID, body: ProjectMoveIn, member: dict = Depends(get_current_member)):
    """project를 다른 teamspace로 옮긴다 — 사이드바 드래그 앤 드롭용.

    project.workspace_id는 자기 teamspace.workspace_id와 항상 같아야 하는
    불변식이 있어서(011_teamspaces.sql), 같은 workspace 안의 teamspace로만
    옮길 수 있다. 대상 teamspace는 create_project와 같은 규칙으로 —
    옮기는 사람이 그 teamspace의 멤버여야 한다(admin/member 무관).
    """
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"], "manager")
        cur.execute(
            "SELECT workspace_id, teamspace_id FROM project WHERE id=%s AND deleted_at IS NULL",
            (str(project_id),),
        )
        project = cur.fetchone()
        if project is None:
            raise HTTPException(404, "project not found")

        assert_teamspace_role(cur, body.teamspace_id, member["id"])
        cur.execute("SELECT workspace_id FROM teamspace WHERE id=%s AND deleted_at IS NULL", (str(body.teamspace_id),))
        target_teamspace = cur.fetchone()
        if target_teamspace is None:
            raise HTTPException(404, "teamspace not found")
        if target_teamspace["workspace_id"] != project["workspace_id"]:
            raise HTTPException(400, "cannot move a project to a teamspace in a different workspace")

        cur.execute(
            "UPDATE project SET teamspace_id=%s, updated_at=now() WHERE id=%s AND deleted_at IS NULL "
            "RETURNING id, name, workspace_id, teamspace_id",
            (str(body.teamspace_id), str(project_id)),
        )
        updated = cur.fetchone()
        audit.record(cur, actor=actor, action="project.move", subject={"project_id": str(project_id)},
                     payload={"from_teamspace_id": str(project["teamspace_id"]),
                              "to_teamspace_id": str(body.teamspace_id)})
    return updated


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: UUID, member: dict = Depends(get_current_member)):
    """소프트 삭제 — owner 전용. 소속 repository/scan/audit_log은 보존된다."""
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"], "owner")
        cur.execute(
            "UPDATE project SET deleted_at=now(), updated_at=now() WHERE id=%s AND deleted_at IS NULL",
            (str(project_id),),
        )
        audit.record(cur, actor=actor, action="project.delete", subject={"project_id": str(project_id)})


def _guard_last_owner(cur, project_id: UUID, target_member_id, current_role: str) -> None:
    if current_role != "owner":
        return
    cur.execute(
        "SELECT count(*) AS n FROM project_member WHERE project_id=%s AND role='owner' AND member_id<>%s",
        (str(project_id), str(target_member_id)),
    )
    if cur.fetchone()["n"] == 0:
        raise HTTPException(409, "project must keep at least one owner — promote someone else first")


@router.patch("/projects/{project_id}/members/{target_member_id}")
def update_project_member_role(project_id: UUID, target_member_id: UUID, body: ProjectMemberRoleIn,
                                member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        caller_role = assert_project_role(cur, project_id, member["id"], "manager")
        cur.execute(
            "SELECT role FROM project_member WHERE project_id=%s AND member_id=%s",
            (str(project_id), str(target_member_id)),
        )
        current = cur.fetchone()
        if current is None:
            raise HTTPException(404, "project member not found")
        # Owner 부여/강등(소유권 이전)은 owner만 할 수 있다 — manager는 그 밑까지만.
        if (body.role == "owner" or current["role"] == "owner") and caller_role != "owner":
            raise HTTPException(403, "only an owner can grant or change owner status")
        if current["role"] != body.role:
            _guard_last_owner(cur, project_id, target_member_id, current["role"])
        cur.execute(
            "UPDATE project_member SET role=%s WHERE project_id=%s AND member_id=%s",
            (body.role, str(project_id), str(target_member_id)),
        )
        audit.record(cur, actor=actor, action="project.member_role_change",
                     subject={"project_id": str(project_id), "member_id": str(target_member_id)},
                     payload={"role": body.role})
    return {"project_id": str(project_id), "member_id": str(target_member_id), "role": body.role}


@router.delete("/projects/{project_id}/members/{target_member_id}", status_code=204)
def remove_project_member(project_id: UUID, target_member_id: UUID, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        caller_role = assert_project_role(cur, project_id, member["id"], "manager")
        cur.execute(
            "SELECT role FROM project_member WHERE project_id=%s AND member_id=%s",
            (str(project_id), str(target_member_id)),
        )
        current = cur.fetchone()
        if current is None:
            raise HTTPException(404, "project member not found")
        if current["role"] == "owner" and caller_role != "owner":
            raise HTTPException(403, "only an owner can remove another owner")
        _guard_last_owner(cur, project_id, target_member_id, current["role"])
        cur.execute("DELETE FROM project_member WHERE project_id=%s AND member_id=%s",
                     (str(project_id), str(target_member_id)))
        audit.record(cur, actor=actor, action="project.member_remove",
                     subject={"project_id": str(project_id), "member_id": str(target_member_id)})


@router.post("/projects/{project_id}/leave", status_code=204)
def leave_project(project_id: UUID, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        cur.execute(
            "SELECT role FROM project_member WHERE project_id=%s AND member_id=%s",
            (str(project_id), member["id"]),
        )
        current = cur.fetchone()
        if current is None:
            raise HTTPException(404, "not a member of this project")
        _guard_last_owner(cur, project_id, member["id"], current["role"])
        cur.execute("DELETE FROM project_member WHERE project_id=%s AND member_id=%s",
                     (str(project_id), member["id"]))
        audit.record(cur, actor=actor, action="project.member_leave", subject={"project_id": str(project_id)})


def _send_project_invite_email(target_email: str, inviter_nickname: str, project_name: str,
                                role: str, invite_url: str) -> bool:
    subject = f"{inviter_nickname}님이 '{project_name}' 프로젝트에 초대했습니다"
    body_line = f"{inviter_nickname}님이 '{project_name}' 프로젝트에 {role} 역할로 초대했습니다."
    expires_note = f"이 링크는 {settings().invite_ttl_days}일 후 만료됩니다."
    return send_mail(
        target_email,
        subject,
        f"{body_line}\n\n아래 링크를 열어 초대를 수락하세요:\n{invite_url}\n\n{expires_note}",
        html_body=invite_email_html(heading=subject, body_line=body_line, invite_url=invite_url,
                                     expires_note=expires_note),
    )


def _create_project_invite(cur, project_id: UUID, role: str, target_email: str | None,
                            created_by: str) -> tuple[dict, str]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    cur.execute(
        """
        INSERT INTO project_invite (project_id, role, token_hash, target_email, created_by, expires_at)
        VALUES (%s, %s, %s, %s, %s, now() + %s::interval)
        RETURNING id, created_at, expires_at
        """,
        (str(project_id), role, token_hash, target_email.lower() if target_email else None, created_by,
         timedelta(days=settings().invite_ttl_days)),
    )
    return cur.fetchone(), raw_token


def _assert_can_grant_role(caller_role: str, role: str) -> None:
    # owner 부여는 owner만 — member 역할 변경(update_project_member_role)과 같은
    # 규칙을 초대 발급/재발송에도 그대로 적용한다. 여기 없으면 manager가
    # owner 역할 초대를 만들어 그 규칙을 통째로 우회할 수 있다.
    if role == "owner" and caller_role != "owner":
        raise HTTPException(403, "only an owner can create or resend an owner-role invite")


@router.post("/projects/{project_id}/invites", status_code=201)
def create_project_invite(project_id: UUID, body: ProjectInviteIn, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    assert_not_self_invite(member, body.target_email)
    with tx() as cur:
        caller_role = assert_project_role(cur, project_id, member["id"], "manager")
        _assert_can_grant_role(caller_role, body.role)
        cur.execute("SELECT name FROM project WHERE id=%s AND deleted_at IS NULL", (str(project_id),))
        project = cur.fetchone()
        if project is None:
            raise HTTPException(404, "project not found")
        invite, raw_token = _create_project_invite(cur, project_id, body.role, body.target_email, member["id"])
        audit.record(cur, actor=actor, action="project.invite_create",
                     subject={"project_id": str(project_id), "invite_id": str(invite["id"])},
                     payload={"role": body.role, "target_email": body.target_email})

    invite_url = f"{settings().frontend_url}/project-invites/{raw_token}"
    email_sent = False
    if body.target_email:
        email_sent = _send_project_invite_email(body.target_email, member["nickname"], project["name"],
                                                  body.role, invite_url)
        with tx() as cur:
            cur.execute("UPDATE project_invite SET email_sent=%s WHERE id=%s", (email_sent, str(invite["id"])))
    return {**invite, "token": raw_token, "invite_url": invite_url, "email_sent": email_sent}


@router.post("/projects/{project_id}/invites/{invite_id}/resend", status_code=201)
def resend_project_invite(project_id: UUID, invite_id: UUID, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        caller_role = assert_project_role(cur, project_id, member["id"], "manager")
        cur.execute(
            "SELECT role, target_email, accepted_at, revoked_at FROM project_invite "
            "WHERE id=%s AND project_id=%s",
            (str(invite_id), str(project_id)),
        )
        old = cur.fetchone()
        if old is None:
            raise HTTPException(404, "invite not found")
        _assert_can_grant_role(caller_role, old["role"])
        if old["accepted_at"] is not None or old["revoked_at"] is not None:
            raise HTTPException(410, "invite already resolved")
        if not old["target_email"]:
            raise HTTPException(400, "cannot resend a link-only invite (no target email)")

        cur.execute("SELECT name FROM project WHERE id=%s AND deleted_at IS NULL", (str(project_id),))
        project = cur.fetchone()

        cur.execute("UPDATE project_invite SET revoked_at=now() WHERE id=%s", (str(invite_id),))
        invite, raw_token = _create_project_invite(cur, project_id, old["role"], old["target_email"], member["id"])
        audit.record(cur, actor=actor, action="project.invite_resend",
                     subject={"project_id": str(project_id), "invite_id": str(invite["id"]),
                              "old_invite_id": str(invite_id)},
                     payload={"role": old["role"], "target_email": old["target_email"]})

    invite_url = f"{settings().frontend_url}/project-invites/{raw_token}"
    email_sent = _send_project_invite_email(old["target_email"], member["nickname"], project["name"],
                                             old["role"], invite_url)
    with tx() as cur:
        cur.execute("UPDATE project_invite SET email_sent=%s WHERE id=%s", (email_sent, str(invite["id"])))
    return {**invite, "token": raw_token, "invite_url": invite_url, "email_sent": email_sent}


@router.get("/projects/{project_id}/invites")
def list_project_invites(project_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"], "manager")
        cur.execute(
            """
            SELECT pi.id, pi.role, pi.target_email, pi.created_at, pi.expires_at, pi.email_sent,
                   pi.accepted_at, pi.revoked_at, m.nickname AS created_by_nickname
              FROM project_invite pi
              JOIN member m ON m.id = pi.created_by
             WHERE pi.project_id = %s
             ORDER BY pi.created_at DESC
            """,
            (str(project_id),),
        )
        return cur.fetchall()


@router.delete("/projects/{project_id}/invites/{invite_id}", status_code=204)
def revoke_project_invite(project_id: UUID, invite_id: UUID, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"], "manager")
        cur.execute(
            """
            UPDATE project_invite SET revoked_at=now()
             WHERE id=%s AND project_id=%s AND accepted_at IS NULL AND revoked_at IS NULL
            RETURNING id
            """,
            (str(invite_id), str(project_id)),
        )
        if cur.fetchone() is None:
            raise HTTPException(404, "invite not found or already resolved")
        audit.record(cur, actor=actor, action="project.invite_revoke",
                     subject={"project_id": str(project_id), "invite_id": str(invite_id)})


def _fetch_pending_project_invite(cur, *, token: str | None = None, invite_id=None) -> dict:
    """토큰(공개 링크) 또는 invite_id(수신함) 둘 중 하나로 조회한다."""
    if token is not None:
        where, param = "pi.token_hash = %s", hashlib.sha256(token.encode()).hexdigest()
    else:
        where, param = "pi.id = %s", str(invite_id)
    cur.execute(
        f"""
        SELECT pi.id, pi.project_id, pi.role, pi.target_email, pi.expires_at,
               pi.accepted_at, pi.revoked_at, pi.created_by, p.name AS project_name,
               p.workspace_id, m.nickname AS invited_by_nickname
          FROM project_invite pi
          JOIN project p ON p.id = pi.project_id AND p.deleted_at IS NULL
          JOIN member m ON m.id = pi.created_by
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


def _project_invite_viewer_status(invite: dict, member: dict | None) -> str:
    if member is None:
        return "anonymous"
    if invite["target_email"] and invite["target_email"] != (member.get("email") or "").lower():
        return "email_mismatch"
    return "ok"


@router.get("/project-invites/{token}")
def preview_project_invite(token: str, member: dict | None = Depends(get_current_member_optional)):
    with tx() as cur:
        invite = _fetch_pending_project_invite(cur, token=token)
    viewer_status = _project_invite_viewer_status(invite, member)
    return {"project_id": str(invite["project_id"]), "project_name": invite["project_name"],
            "workspace_id": str(invite["workspace_id"]),
            "role": invite["role"], "invited_by_nickname": invite["invited_by_nickname"],
            "target_email": invite["target_email"], "viewer_status": viewer_status}


def apply_project_invite_accept(cur, invite: dict, member: dict) -> dict:
    """수락 로직 본체 — 토큰 경로(accept_project_invite)와 수신함 경로가 공유한다."""
    actor = f"user:{member['nickname']}"
    if _project_invite_viewer_status(invite, member) == "email_mismatch":
        raise HTTPException(403, "this invite was issued to a different email address")
    cur.execute(
        """
        INSERT INTO project_member (project_id, member_id, role, invited_by)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (project_id, member_id) DO UPDATE SET role = EXCLUDED.role
        """,
        (str(invite["project_id"]), member["id"], invite["role"], invite["created_by"]),
    )
    cur.execute(
        "UPDATE project_invite SET accepted_at=now(), accepted_by=%s WHERE id=%s",
        (member["id"], str(invite["id"])),
    )
    audit.record(cur, actor=actor, action="project.invite_accept",
                 subject={"project_id": str(invite["project_id"]), "invite_id": str(invite["id"])})
    return {"project_id": str(invite["project_id"]), "project_name": invite["project_name"],
            "workspace_id": str(invite["workspace_id"])}


@router.post("/project-invites/{token}/accept", status_code=201)
def accept_project_invite(token: str, member: dict = Depends(get_current_member)):
    with tx() as cur:
        invite = _fetch_pending_project_invite(cur, token=token)
        return apply_project_invite_accept(cur, invite, member)


# ---------------------------------------------------------------- repository attachment
@router.get("/projects/{project_id}/repositories")
def list_project_repositories(project_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"])
        cur.execute(
            """
            SELECT r.id, r.provider, r.remote_id, r.namespace, r.name,
                   r.default_branch, r.webhook_ci_enabled, r.pr_fix_enabled
              FROM project_repository pr
              JOIN repository r ON r.id = pr.repository_id AND r.is_active
             WHERE pr.project_id = %s
             ORDER BY r.namespace, r.name
            """,
            (str(project_id),),
        )
        return cur.fetchall()


@router.post("/projects/{project_id}/repositories", status_code=201)
def attach_project_repository(project_id: UUID, body: AttachRepositoryIn,
                               member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"], "manager")
        cur.execute("SELECT 1 FROM repository WHERE id=%s AND is_active", (str(body.repository_id),))
        if cur.fetchone() is None:
            raise HTTPException(404, "repository not found")
        cur.execute(
            """
            INSERT INTO project_repository (project_id, repository_id, attached_by)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING project_id, repository_id
            """,
            (str(project_id), str(body.repository_id), member["id"]),
        )
        row = cur.fetchone()
        audit.record(cur, actor=actor, action="project_repository.attach",
                     subject={"project_id": str(project_id), "repository_id": str(body.repository_id)})
    return {"project_id": str(project_id), "repository_id": str(body.repository_id),
            "already_attached": row is None}


@router.delete("/projects/{project_id}/repositories/{repository_id}", status_code=204)
def detach_project_repository(project_id: UUID, repository_id: UUID, member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"], "manager")
        cur.execute(
            "DELETE FROM project_repository WHERE project_id=%s AND repository_id=%s RETURNING project_id",
            (str(project_id), str(repository_id)),
        )
        if cur.fetchone() is None:
            raise HTTPException(404, "repository not attached to this project")
        audit.record(cur, actor=actor, action="project_repository.detach",
                     subject={"project_id": str(project_id), "repository_id": str(repository_id)})
