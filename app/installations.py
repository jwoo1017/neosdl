"""GitHub App 설치 연동 — Setup URL 콜백, 웹훅, 설치/레포 목록 조회.

installation은 그것을 연동한 member 개인 소유다(project 권한과 무관) —
project는 project_repository로 이미 등록된 repository를 낱개로 가져다
붙일 뿐, installation 자체를 공유하지 않는다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import audit, branches, repo_activity_bus
from app.auth import assert_installation_owned, get_current_member, get_current_member_optional
from app.config import settings
from app.db import tx
from app.github_app import get_installation, list_installation_repositories
from app.scans import create_scan

router = APIRouter(tags=["installations"])


def _login_redirect(installation_id: int, setup_action: str | None, github_account_id: str | None):
    next_path = f"/api/auth/github/setup?installation_id={installation_id}"
    if setup_action:
        next_path += f"&setup_action={setup_action}"
    if github_account_id:
        next_path += f"&github_account_id={github_account_id}"
    # next 안의 &(setup_action 등)가 로그인 URL 쪽 파라미터로 새지 않게 통째로 인코딩한다.
    return RedirectResponse(f"/api/auth/github/login?next={quote(next_path, safe='/')}", status_code=302)


@router.get("/auth/github/setup")
def github_setup(
    installation_id: int,
    setup_action: str | None = None,
    github_account_id: str | None = None,
    member: dict | None = Depends(get_current_member_optional),
):
    if member is None:
        return _login_redirect(installation_id, setup_action, github_account_id)

    info = get_installation(installation_id)
    account = info["account"]
    actor = f"user:{member['nickname']}"

    with tx() as cur:
        cur.execute("SELECT id FROM github_account WHERE member_id=%s ORDER BY linked_at",
                     (member["id"],))
        accounts = cur.fetchall()

    if not accounts:
        # GitHub 신원이 하나도 연결 안 됨 — 먼저 연동을 유도한다. 로그인은
        # 이미 돼 있으므로 /auth/github/login의 콜백이 "이미 로그인된 세션"
        # 분기(연동 모드)를 타서 이 member에 github_account가 생긴 뒤 이
        # setup URL로 되돌아온다.
        return _login_redirect(installation_id, setup_action, github_account_id)
    if len(accounts) == 1:
        resolved_account_id = str(accounts[0]["id"])
    elif github_account_id and github_account_id in {str(a["id"]) for a in accounts}:
        resolved_account_id = github_account_id
    else:
        raise HTTPException(409, "multiple linked GitHub accounts — specify github_account_id")

    with tx() as cur:
        cur.execute(
            """
            INSERT INTO installation (github_installation_id, github_account_id, account_login, account_type)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (github_installation_id) DO UPDATE
                SET github_account_id = EXCLUDED.github_account_id,
                    account_login = EXCLUDED.account_login,
                    account_type = EXCLUDED.account_type,
                    status = 'active',
                    updated_at = now()
            RETURNING id
            """,
            (installation_id, resolved_account_id, account["login"], account["type"]),
        )
        row = cur.fetchone()
        audit.record(cur, actor=actor, action="installation.setup",
                     subject={"installation_id": str(row["id"])},
                     payload={"github_installation_id": installation_id, "account_login": account["login"]})

    return RedirectResponse(settings().frontend_url + "/dashboard", status_code=302)


@router.post("/webhooks/github")
async def github_webhook(request: Request):
    body = await request.body()
    sig_header = request.headers.get("X-Hub-Signature-256", "")
    secret = (settings().github_app_webhook_secret or "").encode()
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig_header):
        raise HTTPException(401, "invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    payload = json.loads(body)

    if event == "installation":
        action = payload.get("action")
        gh_installation_id = payload["installation"]["id"]
        new_status = {"deleted": "deleted", "suspend": "suspended", "unsuspend": "active"}.get(action)
        if new_status:
            with tx() as cur:
                cur.execute(
                    """
                    UPDATE installation SET status=%s, updated_at=now()
                    WHERE github_installation_id=%s
                    RETURNING id
                    """,
                    (new_status, gh_installation_id),
                )
                row = cur.fetchone()
                if row:
                    audit.record(cur, actor="system:github-webhook",
                                 action=f"installation.{action}",
                                 subject={"installation_id": str(row["id"])},
                                 payload={"github_installation_id": gh_installation_id})

    elif event == "push":
        _handle_push(payload)

    elif event in ("create", "delete") and payload.get("ref_type") == "branch":
        # 브랜치 생성/삭제 — 스캔 트리거 없이 branch 생애주기만 기록(Security History).
        repo = payload["repository"]
        _record_branch_event(repo["id"], f"refs/heads/{payload['ref']}",
                             repo.get("default_branch"), deleted=(event == "delete"))

    elif event == "pull_request":
        pr = payload["pull_request"]
        _upsert_pull_request(payload["repository"]["id"], pr)
        if payload.get("action") in ("opened", "synchronize", "reopened"):
            _trigger_webhook_scan(payload["repository"]["id"], pr["head"]["sha"],
                                   f"refs/pull/{pr['number']}/head")

    # 처리 안 하는 이벤트 타입도 항상 200 — GitHub 은 2xx 아니면 웹훅을 계속 재시도하다 비활성화한다.
    return {"ok": True}


def _handle_push(payload: dict) -> None:
    """push 이벤트 — (등록된 레포면) branch 생애주기 + head_commit 을 기록하고,
    (webhook_ci_enabled 면) 스캔을 트리거한다. 브랜치 무관(어느 브랜치든).

    branch/commit 기록은 CI 옵트인과 무관하다(`_upsert_pull_request` 와 같은
    원칙 — "이 브랜치가 있었다"는 사실 자체를 남긴다). 스캔만 옵트인 필터를 탄다.
    """
    repo = payload["repository"]
    ref = payload["ref"]
    after = payload.get("after") or ""
    deleted = bool(payload.get("deleted")) or after == "0" * 40
    head_commit = payload.get("head_commit")
    default_branch = repo.get("default_branch")

    try:
        with tx() as cur:
            cur.execute(
                "SELECT id FROM repository WHERE provider='github' AND remote_id=%s",
                (str(repo["id"]),),
            )
            row = cur.fetchone()
            if row is not None:
                repository_id = str(row["id"])
                branches.record_branch_event(
                    cur, repository_id, ref, None if deleted else after,
                    default_branch=default_branch, deleted=deleted,
                )
                if not deleted and head_commit:
                    _record_push_commit(cur, repository_id, head_commit)
                    repo_activity_bus.notify_repo_activity(
                        cur, repository_id,
                        {"type": "push_received", "ref": ref, "commit_sha": head_commit["id"]},
                    )
    except Exception:  # noqa: BLE001 — 웹훅은 항상 200 이어야 한다
        pass

    if deleted or not after or after == "0" * 40:
        return

    scan = _trigger_webhook_scan(repo["id"], after, ref)  # webhook_ci_enabled 필터는 여기서
    if scan is not None:
        try:
            with tx() as cur:
                cur.execute(
                    "SELECT id FROM repository WHERE provider='github' AND remote_id=%s",
                    (str(repo["id"]),),
                )
                r = cur.fetchone()
                if r is not None:
                    branches.link_branch_head_scan(cur, str(r["id"]), ref, str(scan["id"]))
        except Exception:  # noqa: BLE001
            pass


def _record_push_commit(cur, repository_id: str, head_commit: dict) -> None:
    """웹훅 push 는 지금 git_commit 에 안 들어간다(git.sync_history 는 등록 시 1회).
    타임라인이 committed_at 로 정렬하려면 커밋 시각이 필요하므로 head_commit 1행만
    최소 upsert 한다. commit_file 은 안 채운다(파일 델타는 GitHub compare API 로 조회)."""
    author = head_commit.get("author") or {}
    cur.execute(
        """
        INSERT INTO git_commit (repository_id, commit_sha, author_name, author_email,
                                committed_at, message)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (repository_id, commit_sha) DO NOTHING
        """,
        (repository_id, head_commit["id"], author.get("name"), author.get("email"),
         head_commit.get("timestamp"), head_commit.get("message", "")),
    )


def _record_branch_event(github_repo_id: int, ref: str, default_branch: str | None,
                          *, deleted: bool) -> None:
    try:
        with tx() as cur:
            cur.execute(
                "SELECT id FROM repository WHERE provider='github' AND remote_id=%s",
                (str(github_repo_id),),
            )
            row = cur.fetchone()
            if row is not None:
                branches.record_branch_event(cur, str(row["id"]), ref, None,
                                              default_branch=default_branch, deleted=deleted)
    except Exception:  # noqa: BLE001
        pass


def _trigger_webhook_scan(github_repo_id: int, commit_sha: str, ref: str) -> dict | None:
    """webhook_ci_enabled 가 켜진, 우리 DB에 등록된 레포에 대해서만 스캔을 만든다.
    생성/재사용된 scan dict(id 포함) 또는 None(옵트인 꺼짐/미등록).

    installation 이벤트와 달리 이건 우리가 추적하지 않는(등록 안 된) 레포에
    대해서도 GitHub App 이 설치된 계정의 모든 레포에 대해 날아오므로, 조용히
    무시하는 게 기본이다 — 없는 레포/꺼진 옵트인은 에러가 아니라 정상 케이스.
    """
    with tx() as cur:
        cur.execute(
            "SELECT id FROM repository WHERE provider='github' AND remote_id=%s AND webhook_ci_enabled",
            (str(github_repo_id),),
        )
        repo = cur.fetchone()
        if repo is None:
            return None
        return create_scan(cur, repository_id=str(repo["id"]), commit_sha=commit_sha, ref=ref,
                           trigger="webhook", actor="system:github-webhook")


def _upsert_pull_request(github_repo_id: int, pr: dict) -> None:
    """PR 메타데이터 영속화 — webhook_ci_enabled 여부와 무관하게, 등록된 레포면 기록한다.

    자동 스캔 옵트인(_trigger_webhook_scan)과 달리 이건 "이 레포에 PR이
    있었다는 사실" 자체를 남기는 것이라 별도 옵트인이 필요 없다.
    """
    with tx() as cur:
        cur.execute(
            "SELECT id FROM repository WHERE provider='github' AND remote_id=%s",
            (str(github_repo_id),),
        )
        repo = cur.fetchone()
        if repo is None:
            return
        cur.execute(
            """
            INSERT INTO pull_request (repository_id, number, title, state, author_login,
                                      base_ref, head_ref, head_sha, merged, html_url,
                                      opened_at, closed_at, merged_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (repository_id, number) DO UPDATE
                SET title = EXCLUDED.title, state = EXCLUDED.state,
                    merged = EXCLUDED.merged, closed_at = EXCLUDED.closed_at,
                    merged_at = EXCLUDED.merged_at, updated_at = now()
            """,
            (str(repo["id"]), pr["number"], pr["title"], pr["state"],
             (pr.get("user") or {}).get("login"), pr["base"]["ref"], pr["head"]["ref"],
             pr["head"]["sha"], bool(pr.get("merged_at")), pr.get("html_url"),
             pr.get("created_at"), pr.get("closed_at"), pr.get("merged_at")),
        )


@router.get("/me/installations")
def list_my_installations(member: dict = Depends(get_current_member)):
    with tx() as cur:
        cur.execute(
            """
            SELECT i.id, i.account_login, i.account_type, i.status, i.created_at
              FROM installation i
              JOIN github_account ga ON ga.id = i.github_account_id
             WHERE ga.member_id=%s AND i.status='active'
             ORDER BY i.created_at DESC
            """,
            (member["id"],),
        )
        return cur.fetchall()


@router.get("/installations/{installation_id}/repositories")
def installation_repositories(installation_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_installation_owned(cur, installation_id, member["id"])
        cur.execute("SELECT github_installation_id FROM installation WHERE id=%s",
                     (str(installation_id),))
        row = cur.fetchone()

        repos = list_installation_repositories(row["github_installation_id"])
        remote_ids = [str(r["id"]) for r in repos]
        cur.execute(
            "SELECT remote_id, id FROM repository WHERE provider='github' AND remote_id = ANY(%s)",
            (remote_ids,),
        )
        already_registered = {r["remote_id"]: str(r["id"]) for r in cur.fetchall()}

    return [
        {
            "remote_id": str(r["id"]),
            "namespace": r["owner"]["login"],
            "name": r["name"],
            "full_name": r["full_name"],
            "private": r["private"],
            "default_branch": r["default_branch"],
            "clone_url": r["clone_url"],
            "already_registered": already_registered.get(str(r["id"])),
            # 프로젝트 생성 마법사의 "자동 스펙 정리" 단계가 레포를 다시
            # 조회하지 않고 이 응답에 실린 값을 그대로 쓴다.
            "language": r.get("language"),
            "description": r.get("description"),
            "topics": r.get("topics") or [],
        }
        for r in repos
    ]
