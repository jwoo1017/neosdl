"""저장소 활동 실시간 스트림 + 초기 히스토리 피드 — 마스코트 사이드바가 쓴다.

WebSocket(`/ws/repositories/{id}/activity`)은 `app/repo_activity_bus.py`의
델타(push_received/scan_started/scan_completed/fix_ready/pr_created)만
전달한다 — 과거 이벤트는 재생되지 않으므로, 클라이언트는 연결 직후
`GET .../activity/feed`로 현재 상태를 한 번 채우고 그 다음부터 WS로 받는다
(app/chat.py의 chat_socket과 같은 인증/구독 구조, 채널만 저장소 단위).

`activity/feed`는 git_commit/pull_request/scan/security_snapshot/finding_fix
각각을 조회해 애플리케이션 레벨에서 정규화 후 시간순 병합한다 —
`app/repo_queries.py`가 이미 쓰는 "테이블마다 따로 조회, 라우트에서 병합"
관례와 같다(타입이 서로 다른 테이블을 SQL UNION으로 억지로 합치지 않는다).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app import repo_activity_bus
from app.auth import (
    assert_repository_access, get_current_member, get_current_member_ws, watch_ws_session, ws_origin_allowed,
)
from app.db import tx

router = APIRouter(tags=["repo_activity"])


def _check_ws_access(repository_id: UUID, member_id: str) -> None:
    with tx() as cur:
        assert_repository_access(cur, repository_id, member_id, "viewer")


@router.websocket("/ws/repositories/{repository_id}/activity")
async def repo_activity_socket(websocket: WebSocket, repository_id: UUID):
    if not ws_origin_allowed(websocket):   # 다른 사이트가 우리 쿠키로 여는 소켓(CSWSH) 차단
        await websocket.close(code=4403)
        return
    member = get_current_member_ws(websocket)
    if member is None:
        # 인증·권한 실패는 수락한 뒤 이유 코드로 닫는다 — 수락 전에 닫으면 브라우저엔 HTTP 403/1006 으로만
        # 보여 "로그인 만료(4401) → 로그인 화면" / "권한 없음(4403) → 재시도 중단" 을 구분할 수 없다.
        await websocket.accept()
        await websocket.close(code=4401)
        return
    try:
        await run_in_threadpool(_check_ws_access, repository_id, member["id"])
    except Exception:
        await websocket.accept()
        await websocket.close(code=4403)
        return

    await websocket.accept()
    rid = str(repository_id)
    queue = repo_activity_bus.bus.subscribe(rid)

    async def forward() -> None:
        while True:
            event = await queue.get()
            try:
                await websocket.send_json(event)
            except Exception:
                pass

    forwarder = asyncio.create_task(forward())
    # 비밀번호 변경·탈퇴 후에도 열린 소켓이 남지 않게 주기적으로 세션 재검증(무효면 4401 로 닫힘).
    watcher = asyncio.create_task(watch_ws_session(websocket, member))
    try:
        while True:
            try:
                # 이 소켓은 서버 -> 클라이언트 단방향이다 — 클라이언트가 보내는
                # 프레임은 없지만, receive_json()으로 연결 끊김(WebSocketDisconnect)을
                # 감지해야 forward 태스크를 정리할 수 있다(chat_socket과 동일 패턴).
                await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:
                continue
    finally:
        forwarder.cancel()
        watcher.cancel()
        repo_activity_bus.bus.unsubscribe(rid, queue)


def _normalize_commit(row: dict) -> dict:
    return {"type": "push_received", "at": row["committed_at"], "title": row["message"] or "(no message)",
            "ref": row["commit_sha"][:8], "url": None,
            "meta": {"commit_sha": row["commit_sha"], "author_name": row["author_name"]}}


def _normalize_pr(row: dict) -> dict:
    return {"type": "pull_request", "at": row["opened_at"], "title": row["title"],
            "ref": f"#{row['number']}", "url": row["html_url"],
            "meta": {"state": row["state"], "merged": row["merged"], "author_login": row["author_login"]}}


def _normalize_scan(row: dict) -> dict:
    event_type = "scan_started" if row["status"] in ("queued", "running") else "scan_completed"
    return {"type": event_type, "at": row["created_at"], "title": f"스캔 {row['status']}",
            "ref": row["commit_sha"][:8], "url": None,
            "meta": {"scan_id": str(row["id"]), "status": row["status"], "trigger": row["trigger"]}}


def _normalize_snapshot(row: dict) -> dict:
    return {"type": "security_snapshot", "at": row["generated_at"],
            "title": f"취약점 {row['n_findings']}건 (critical {row['n_critical']}, high {row['n_high']})",
            "ref": (row["commit_sha"] or "")[:8], "url": None,
            "meta": {"scan_id": str(row["scan_id"]), "n_findings": row["n_findings"],
                     "n_critical": row["n_critical"], "n_high": row["n_high"]}}


def _normalize_fix(row: dict) -> dict:
    event_type = "pr_created" if row["status"] == "pr_created" else "fix_ready"
    return {"type": event_type, "at": row["updated_at"],
            "title": row["summary"] or f"{row['file_path']} 수정 제안",
            "ref": row["file_path"], "url": row["pr_url"],
            "meta": {"finding_id": row["finding_id"], "finding_fix_id": row["id"], "status": row["status"]}}


@router.get("/repositories/{repository_id}/activity/feed")
def repository_activity_feed(repository_id: UUID, limit: int = Query(default=50, ge=1, le=200),
                              member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")

        cur.execute(
            "SELECT commit_sha, author_name, committed_at, message FROM git_commit "
            "WHERE repository_id=%s ORDER BY committed_at DESC LIMIT %s",
            (str(repository_id), limit),
        )
        commits = cur.fetchall()

        cur.execute(
            "SELECT number, title, state, author_login, html_url, merged, opened_at FROM pull_request "
            "WHERE repository_id=%s ORDER BY opened_at DESC NULLS LAST LIMIT %s",
            (str(repository_id), limit),
        )
        prs = cur.fetchall()

        cur.execute(
            "SELECT id, commit_sha, status, trigger, created_at FROM scan "
            "WHERE repository_id=%s ORDER BY created_at DESC LIMIT %s",
            (str(repository_id), limit),
        )
        scans = cur.fetchall()

        cur.execute(
            "SELECT scan_id, commit_sha, n_findings, n_critical, n_high, generated_at FROM security_snapshot "
            "WHERE repository_id=%s ORDER BY generated_at DESC LIMIT %s",
            (str(repository_id), limit),
        )
        snapshots = cur.fetchall()

        cur.execute(
            "SELECT id, finding_id, file_path, summary, status, pr_url, updated_at FROM finding_fix "
            "WHERE repository_id=%s AND status IN ('ready', 'pr_created') "
            "ORDER BY updated_at DESC LIMIT %s",
            (str(repository_id), limit),
        )
        fixes = cur.fetchall()

    events = (
        [_normalize_commit(r) for r in commits if r["committed_at"] is not None]
        + [_normalize_pr(r) for r in prs if r["opened_at"] is not None]
        + [_normalize_scan(r) for r in scans]
        + [_normalize_snapshot(r) for r in snapshots]
        + [_normalize_fix(r) for r in fixes]
    )
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]
