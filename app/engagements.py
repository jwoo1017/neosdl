"""Engagement(자율 보안 검증 run) 서비스 + REST.

Automation 모드의 진입점. `scans.py::create_scan`·`chat.py` 패턴을 따른다: 권한 확인 →
engagement 행 insert → `engagement.run` job enqueue → audit. 상세 실행(Planner→StaticAnalyst→
…)은 worker/handlers/engagement.py 가 맡는다.

phase-0 범위: 생성/조회 REST + 생명주기 전이 헬퍼. 역할 체인(Planner 등)은 phase-1.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import audit, engagement_bus, queue
from app.auth import (
    assert_project_role, get_current_member, get_current_member_ws, watch_ws_session, ws_origin_allowed,
)
from app.chat import _resolve_chat_ctx  # scan_id/repository_id 해석 재사용(단일 소스)
from app.db import tx

log = logging.getLogger("app.engagements")

router = APIRouter(tags=["engagement"])

_VALID_MODES = ("assistant", "automation")
_VALID_PERMISSION = ("plan", "ask", "auto")


class CreateEngagementIn(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    mode: str = "automation"
    permission_mode: str = "ask"
    scan_id: str | None = None
    # 블랙박스 외부표적 모드: target(인가된 host/URL)을 주면 스캔 없이 샌드박스 도구로 점검한다.
    # 없으면 기존 화이트박스(스캔 기반 정적→동적 검증) 경로.
    target: str | None = None
    # 레포 부팅 블랙박스: 워크스페이스 레포(Dockerfile/compose)를 샌드박스에 띄워 그 앱을 표적으로
    # 점검한다. 표적 IP·allowlist 가 자동이라 운영자 수고가 없다. scan_id(소스 스냅샷) 필요.
    boot_repo: bool = False


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
    import json

    from app import toolbox

    blackbox = bool(body.target)
    repo_boot = bool(body.boot_repo) and not blackbox
    if blackbox:
        # 표적 형식만 여기서 검증(인가 allowlist 는 실행 직전 worker 에서 assert_target_allowed).
        try:
            toolbox.parse_target(body.target)
        except toolbox.CommandRejected:
            raise HTTPException(422, "invalid target")

    with tx() as cur:
        # 검증은 코드/표적을 실제로 때리는 행위라 manager 이상만 시작할 수 있다.
        assert_project_role(cur, project_id, member["id"], "manager")
        ctx = _resolve_chat_ctx(cur, project_id)
        scan_id = body.scan_id or ctx.get("scan_id")
        if not scan_id and not blackbox:
            # 화이트박스·레포부팅은 소스/정적 스냅샷(스캔)이 반드시 필요. 외부표적 블랙박스만 예외.
            raise HTTPException(409, "no completed scan for this project yet")
        if repo_boot and not ctx.get("repository_id"):
            raise HTTPException(409, "no repository for this project")
        if blackbox:
            params = {"target": body.target, "engagement_kind": "blackbox"}
            scan_val, kind = None, "blackbox"
        elif repo_boot:
            params = {"engagement_kind": "repo_blackbox"}
            scan_val, kind = scan_id, "repo_blackbox"
        else:
            params = {}
            scan_val, kind = scan_id, "whitebox"
        cur.execute(
            """
            INSERT INTO engagement (project_id, repository_id, scan_id, mode, permission_mode, goal,
                                    params, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING id
            """,
            (str(project_id), ctx.get("repository_id"), scan_val,
             body.mode, body.permission_mode, body.goal, json.dumps(params), member["id"]),
        )
        engagement_id = str(cur.fetchone()["id"])
        queue.enqueue(cur, "engagement.run", {"engagement_id": engagement_id},
                      dedupe_key=f"engagement:{engagement_id}")
        audit.record(cur, actor=f"user:{member['id']}", action="engagement.create",
                     subject={"engagement_id": engagement_id, "project_id": str(project_id)},
                     payload={"mode": body.mode, "permission_mode": body.permission_mode,
                              "scan_id": scan_val, "kind": kind})
    return {"id": engagement_id, "status": "queued"}


@router.post("/engagements/{engagement_id}/approve")
def approve_engagement(engagement_id: UUID, member: dict = Depends(get_current_member)):
    """awaiting_approval 로 멈춘 engagement 의 pending 명령을 승인하고 재개한다(HITL).

    승인된 명령을 params.approved_commands 에 쌓고 engagement.run 을 재enqueue → 그 명령은
    permission 게이트를 통과한다. 재실행 시 비파괴 정찰(nmap/nikto)은 다시 돌지만 멱등하다.
    """
    import json

    with tx() as cur:
        cur.execute("SELECT project_id, status, result, params FROM engagement WHERE id=%s FOR UPDATE",
                    (str(engagement_id),))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "engagement not found")
        assert_project_role(cur, row["project_id"], member["id"], "manager")
        if row["status"] != "awaiting_approval":
            raise HTTPException(409, "engagement is not awaiting approval")
        pending = (row["result"] or {}).get("pending_command")
        if not pending:
            raise HTTPException(409, "no pending command to approve")
        params = dict(row["params"] or {})
        approved = list(params.get("approved_commands") or [])
        if pending not in approved:
            approved.append(pending)
        params["approved_commands"] = approved
        cur.execute("UPDATE engagement SET params=%s::jsonb, status='queued', error=NULL, updated_at=now() "
                    "WHERE id=%s", (json.dumps(params), str(engagement_id)))
        # 원래 dedupe_key(engagement:{id}) 와 겹치지 않게 재개마다 다른 키.
        queue.enqueue(cur, "engagement.run", {"engagement_id": str(engagement_id)},
                      dedupe_key=f"engagement:{engagement_id}:resume:{len(approved)}")
        engagement_bus.notify_engagement_event(cur, str(engagement_id), {"kind": "status", "ref_id": "queued"})
        audit.record(cur, actor=f"user:{member['id']}", action="engagement.approve",
                     subject={"engagement_id": str(engagement_id)}, payload={"command": pending[:500]})
    return {"status": "queued", "approved": pending}


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
        # 리포트 아티팩트 본문 — StaticAnalyst 가 모은 finding+엔트리포인트 후보(candidates)와 markdown.
        cur.execute(
            "SELECT body FROM artifact WHERE engagement_id=%s AND kind='report' ORDER BY created_at DESC LIMIT 1",
            (str(engagement_id),))
        rrow = cur.fetchone()
        report = rrow["body"] if rrow else None
        # finding 별 동적 판정 + 그 finding 의 정적 메타 + 최신 수정 PR(있으면). 상세 페이지가 한 번에 그린다.
        cur.execute(
            """
            SELECT v.finding_id, v.verdict, v.confidence, v.oracle, v.entry_route, v.reason,
                   f.category, f.severity, f.sink_symbol_key, f.sink_line, f.source_var,
                   ff.pr_url, ff.pr_number, ff.status AS fix_status
            FROM finding_verification v
            JOIN security_finding f ON f.id = v.finding_id
            LEFT JOIN LATERAL (
                SELECT pr_url, pr_number, status FROM finding_fix
                WHERE finding_id = v.finding_id ORDER BY created_at DESC LIMIT 1
            ) ff ON true
            WHERE v.engagement_id = %s
            ORDER BY v.finding_id
            """,
            (str(engagement_id),))
        verifications = cur.fetchall()
    return {"engagement": eng, "tasks": tasks, "artifacts": artifacts,
            "report": report, "verifications": verifications}


def _assert_engagement_access(engagement_id: UUID, member_id: str) -> str | None:
    """engagement 가 있고 멤버가 그 프로젝트 viewer 이상이면 project_id 반환, 아니면 예외."""
    with tx() as cur:
        cur.execute("SELECT project_id FROM engagement WHERE id=%s", (str(engagement_id),))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "engagement not found")
        assert_project_role(cur, row["project_id"], member_id, "viewer")
        return str(row["project_id"])


@router.websocket("/ws/engagements/{engagement_id}")
async def engagement_socket(websocket: WebSocket, engagement_id: UUID):
    """engagement 진행(status 전이 등)을 실시간 수신 — 읽기 전용(클라이언트→서버 메시지 없음).

    chat_socket 을 미러하되 수신 명령이 없어 forward 루프만 둔다. 상태 이벤트를 받으면
    프런트가 상세를 refetch 한다(engagement_bus 모듈 docstring 참고 — 워커가 쏜 NOTIFY 를 웹이 fan-out).
    """
    if not ws_origin_allowed(websocket):   # CSWSH 차단
        await websocket.close(code=4403)
        return
    member = get_current_member_ws(websocket)
    if member is None:
        await websocket.accept()
        await websocket.close(code=4401)
        return
    try:
        await run_in_threadpool(_assert_engagement_access, engagement_id, member["id"])
    except Exception:
        await websocket.accept()
        await websocket.close(code=4403)
        return

    await websocket.accept()
    eid = str(engagement_id)
    sub = engagement_bus.bus.subscribe(eid)

    async def forward() -> None:
        while True:
            event = await sub.get()
            try:
                await websocket.send_json(event)
            except Exception:
                pass

    forwarder = asyncio.create_task(forward())
    watcher = asyncio.create_task(watch_ws_session(websocket, member))
    try:
        while True:
            try:
                await websocket.receive_json()   # 수신은 무시(keepalive/ping 용) — 끊기면 종료
            except WebSocketDisconnect:
                break
            except Exception:
                continue
    finally:
        forwarder.cancel()
        watcher.cancel()
        engagement_bus.bus.unsubscribe(eid, sub)
