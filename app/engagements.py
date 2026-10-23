"""Engagement(자율 보안 검증 run) 서비스 + REST.

Automation 모드의 진입점. `scans.py::create_scan`·`chat.py` 패턴을 따른다: 권한 확인 →
engagement 행 insert → `engagement.run` job enqueue → audit. 상세 실행(Planner→StaticAnalyst→
…)은 worker/handlers/engagement.py 가 맡는다.

phase-0 범위: 생성/조회 REST + 생명주기 전이 헬퍼. 역할 체인(Planner 등)은 phase-1.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import audit, engagement_bus, queue
from app.auth import assert_project_role, get_current_member
from app.chat import _resolve_chat_ctx  # scan_id/repository_id 해석 재사용(단일 소스)
from app.db import tx

router = APIRouter(tags=["engagement"])

_VALID_MODES = ("assistant", "automation")
_VALID_PERMISSION = ("plan", "ask", "auto")


class CreateEngagementIn(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    mode: str = "automation"
    permission_mode: str = "ask"
    scan_id: str | None = None


def set_status(cur, engagement_id: str, status: str, *, error: str | None = None,
               result: dict | None = None, notify: bool = True) -> None:
    """engagement 상태 전이 + 버스 알림(같은 tx). started_at/finished_at 을 상태에 맞춰 채운다."""
    import json
    cur.execute(
        """
        UPDATE engagement SET
            status = %s,
            error = COALESCE(%s, error),
            result = COALESCE(%s::jsonb, result),
            started_at = CASE WHEN %s = 'running' AND started_at IS NULL THEN now() ELSE started_at END,
            finished_at = CASE WHEN %s IN ('succeeded','failed','cancelled') THEN now() ELSE finished_at END,
            updated_at = now()
        WHERE id = %s
        """,
        (status, error, json.dumps(result) if result is not None else None, status, status, engagement_id),
    )
    if notify:
        engagement_bus.notify_engagement_event(cur, engagement_id, {"kind": "status", "ref_id": status})


@router.post("/projects/{project_id}/engagements")
def create_engagement(project_id: UUID, body: CreateEngagementIn,
                      member: dict = Depends(get_current_member)):
    if body.mode not in _VALID_MODES:
        raise HTTPException(422, "invalid mode")
    if body.permission_mode not in _VALID_PERMISSION:
        raise HTTPException(422, "invalid permission_mode")
    with tx() as cur:
        # 검증은 코드를 실제로 때리는 행위라 manager 이상만 시작할 수 있다.
        assert_project_role(cur, project_id, member["id"], "manager")
        ctx = _resolve_chat_ctx(cur, project_id)
        scan_id = body.scan_id or ctx.get("scan_id")
        if not scan_id:
            raise HTTPException(409, "no completed scan for this project yet")
        cur.execute(
            """
            INSERT INTO engagement (project_id, repository_id, scan_id, mode, permission_mode, goal, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (str(project_id), ctx.get("repository_id"), scan_id, body.mode, body.permission_mode,
             body.goal, member["id"]),
        )
        engagement_id = str(cur.fetchone()["id"])
        queue.enqueue(cur, "engagement.run", {"engagement_id": engagement_id},
                      dedupe_key=f"engagement:{engagement_id}")
        audit.record(cur, actor=f"user:{member['id']}", action="engagement.create",
                     subject={"engagement_id": engagement_id, "project_id": str(project_id)},
                     payload={"mode": body.mode, "permission_mode": body.permission_mode, "scan_id": scan_id})
    return {"id": engagement_id, "status": "queued"}


@router.get("/projects/{project_id}/engagements")
def list_engagements(project_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"], "viewer")
        cur.execute(
            "SELECT id, scan_id, mode, permission_mode, goal, status, created_at, finished_at "
            "FROM engagement WHERE project_id=%s ORDER BY created_at DESC LIMIT 50",
            (str(project_id),),
        )
        return cur.fetchall()


@router.get("/engagements/{engagement_id}")
def get_engagement(engagement_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        cur.execute("SELECT * FROM engagement WHERE id=%s", (str(engagement_id),))
        eng = cur.fetchone()
        if eng is None:
            raise HTTPException(404, "engagement not found")
        assert_project_role(cur, eng["project_id"], member["id"], "viewer")
        cur.execute(
            "SELECT id, seq, title, assigned_role, status FROM task WHERE engagement_id=%s ORDER BY seq",
            (str(engagement_id),))
        tasks = cur.fetchall()
        cur.execute(
            "SELECT kind, title, created_at FROM artifact WHERE engagement_id=%s ORDER BY created_at",
            (str(engagement_id),))
        artifacts = cur.fetchall()
    return {"engagement": eng, "tasks": tasks, "artifacts": artifacts}
