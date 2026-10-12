"""취약점 수정 코드 생성 + GitHub PR 자동 생성 — 마스코트 사이드바의 Fix 카드가 쓴다.

생성(`POST .../generate-fix`)은 git worktree 체크아웃 + LLM 호출이 블로킹이라
워커 job(`fix.generate`, app/fix_generation.py)으로 넘기고 202를 돌려준다.
PR 생성(`POST .../create-pr`)은 GitHub Contents/Pulls API 호출 몇 번이라
HTTP 핸들러에서 동기로 처리한다(risk_build 같은 무거운 단계가 아니다).

안전장치: `repository.pr_fix_enabled`(저장소별 opt-in, 기본 꺼짐) + PR 생성
시점에 대상 파일이 수정 생성 당시와 여전히 같은지 확인하는 동시성 체크 —
그 사이 파일이 바뀌었으면 덮어쓰지 않고 409로 재생성을 요구한다.
"""

from __future__ import annotations

import base64
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app import audit, queue, repo_activity_bus
from app.auth import assert_repository_access, get_current_member
from app.db import tx
from app.github_app import (
    create_branch,
    create_pull_request,
    get_branch_sha,
    get_file_with_sha,
    update_file_contents,
)
from app.repo_queries import repo_github_info

log = logging.getLogger("app.finding_fix_api")

router = APIRouter(tags=["finding_fix"])


def _finding_repo(cur, finding_id: int) -> dict:
    cur.execute("SELECT id, repository_id, scan_id, category FROM security_finding WHERE id=%s", (finding_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "finding not found")
    return row


@router.post("/findings/{finding_id}/generate-fix", status_code=202)
def request_generate_fix(finding_id: int, member: dict = Depends(get_current_member)):
    with tx() as cur:
        finding = _finding_repo(cur, finding_id)
        assert_repository_access(cur, finding["repository_id"], member["id"], "developer")

        # 이미 대기/완료된 시도가 있으면 그걸 돌려준다(중복 job 방지) — 실패한
        # 시도만 재생성을 허용한다.
        cur.execute(
            "SELECT id, status FROM finding_fix WHERE finding_id=%s ORDER BY created_at DESC LIMIT 1",
            (finding_id,),
        )
        existing = cur.fetchone()
        if existing is not None and existing["status"] in ("pending", "ready"):
            return {"finding_fix_id": existing["id"], "status": existing["status"]}

        cur.execute(
            "INSERT INTO finding_fix (finding_id, repository_id, scan_id, status) "
            "VALUES (%s, %s, %s, 'pending') RETURNING id",
            (finding_id, str(finding["repository_id"]), str(finding["scan_id"])),
        )
        fix_id = cur.fetchone()["id"]
        queue.enqueue(cur, "fix.generate", {"finding_fix_id": fix_id})
    return {"finding_fix_id": fix_id, "status": "pending"}


@router.get("/findings/{finding_id}/fix")
def get_fix(finding_id: int, member: dict = Depends(get_current_member)):
    with tx() as cur:
        finding = _finding_repo(cur, finding_id)
        assert_repository_access(cur, finding["repository_id"], member["id"], "viewer")
        cur.execute(
            "SELECT * FROM finding_fix WHERE finding_id=%s ORDER BY created_at DESC LIMIT 1",
            (finding_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "no fix requested for this finding yet")
    return row


def _splice(original_content: str, start_line: int, end_line: int, replacement: str) -> str:
    trailing_newline = original_content.endswith("\n")
    lines = original_content.splitlines()
    new_lines = lines[:start_line - 1] + replacement.splitlines() + lines[end_line:]
    text = "\n".join(new_lines)
    return text + "\n" if trailing_newline else text


@router.post("/findings/{finding_id}/create-pr")
def create_fix_pr(finding_id: int, member: dict = Depends(get_current_member)):
    with tx() as cur:
        finding = _finding_repo(cur, finding_id)
        assert_repository_access(cur, finding["repository_id"], member["id"], "manager")

        cur.execute("SELECT pr_fix_enabled FROM repository WHERE id=%s", (str(finding["repository_id"]),))
        repo_flag = cur.fetchone()
        if repo_flag is None or not repo_flag["pr_fix_enabled"]:
            raise HTTPException(403, "이 저장소는 자동 PR 생성이 꺼져 있습니다 — 저장소 설정에서 켜세요")

        cur.execute(
            "SELECT * FROM finding_fix WHERE finding_id=%s ORDER BY created_at DESC LIMIT 1",
            (finding_id,),
        )
        fix = cur.fetchone()
        if fix is None or fix["status"] != "ready":
            raise HTTPException(409, "먼저 수정 코드를 생성하세요(generate-fix)")

        repo_info = repo_github_info(cur, finding["repository_id"])

    installation_id = repo_info["github_installation_id"]
    owner, name = repo_info["namespace"], repo_info["name"]
    default_branch = repo_info["default_branch"]
    branch_name = fix.get("branch_name") or f"neosdl/fix-{finding_id}"

    try:
        current = get_file_with_sha(installation_id, owner, name, fix["file_path"], ref=default_branch)
        current_content = base64.b64decode(current["content_b64"]).decode("utf-8", errors="replace")
        current_lines = current_content.splitlines()
        current_snippet = "\n".join(current_lines[fix["start_line"] - 1:fix["end_line"]])
        if current_snippet != fix["original_snippet"]:
            raise HTTPException(409, "수정 생성 이후 파일이 변경됐습니다 — 수정을 다시 생성하세요")

        new_content = _splice(current_content, fix["start_line"], fix["end_line"], fix["replacement_code"])
        base_sha = get_branch_sha(installation_id, owner, name, default_branch)
        create_branch(installation_id, owner, name, branch_name, base_sha)
        update_file_contents(
            installation_id, owner, name, fix["file_path"],
            base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
            message=f"[NeoSDL] Fix {finding['category']} in {fix['file_path']}",
            branch=branch_name, sha=current["sha"],
        )
        pr = create_pull_request(
            installation_id, owner, name,
            title=f"[NeoSDL] Fix {finding['category']} in {fix['file_path']}",
            body=(fix["summary"] or "") + f"\n\n_NeoSDL이 finding #{finding_id}에 대해 자동 생성한 수정입니다._",
            head=branch_name, base=default_branch,
        )
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise HTTPException(
                502, "GitHub App에 코드 쓰기 권한이 없습니다 — 조직 관리자가 앱 권한을 재승인해야 합니다"
            ) from exc
        log.warning("create-pr GitHub API 실패: %s", exc.response.text[:500])
        raise HTTPException(502, f"GitHub API 오류: {exc.response.status_code}") from exc

    with tx() as cur:
        cur.execute(
            "UPDATE finding_fix SET status='pr_created', pr_url=%s, pr_number=%s, branch_name=%s, "
            "updated_at=now() WHERE id=%s",
            (pr["html_url"], pr["number"], branch_name, fix["id"]),
        )
        audit.record(cur, actor=f"user:{member['nickname']}", action="finding_fix.create_pr",
                     subject={"finding_id": str(finding_id), "finding_fix_id": str(fix["id"])},
                     payload={"pr_url": pr["html_url"], "branch": branch_name})
        repo_activity_bus.notify_repo_activity(
            cur, finding["repository_id"],
            {"type": "pr_created", "finding_id": finding_id, "pr_url": pr["html_url"]},
        )
    return {"pr_url": pr["html_url"], "pr_number": pr["number"], "branch_name": branch_name}
