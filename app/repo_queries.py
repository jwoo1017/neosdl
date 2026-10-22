"""Repository 단위 조회 — REST 라우트(main.py/releases.py)와 orchestrator 도구가 공유한다.

main.py와 releases.py가 각자 들고 있던 `_repo_github_info` 중복을 여기 하나로
합쳤고, commits/pull-requests/releases 조회 SQL도 여기로 옮겨 REST 라우트와
`app.orchestrator`의 repo_* 도구가 같은 쿼리를 쓰게 했다. branches는 로컬
캐시가 없어(sql/004 참고 — git_commit에 branch 컬럼 없음) GitHub 라이브
조회만 가능하다.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException

from app.github_app import list_branches as _gh_list_branches
from app.github_app import list_pull_requests as _gh_list_pull_requests
from app.github_app import list_recent_commits as _gh_list_recent_commits
from app.github_app import list_releases as _gh_list_releases


def repo_github_info(cur, repository_id: UUID) -> dict:
    """namespace/name/default_branch/github_installation_id — GitHub API 호출에 필요한 최소 정보.

    레포가 없거나 installation 연결이 끊겼으면(예: 소유 installation이
    재설치돼 옛 installation row가 사라진 경우) 404.
    """
    cur.execute(
        """
        SELECT r.namespace, r.name, r.default_branch, i.github_installation_id
          FROM repository r JOIN installation i ON i.id = r.installation_id
         WHERE r.id=%s
        """,
        (str(repository_id),),
    )
    repo = cur.fetchone()
    if repo is None:
        raise HTTPException(404, "repository not found or not linked to a live installation")
    return repo


def list_commits(cur, repository_id: UUID, *, limit: int = 100, query: str | None = None) -> list[dict]:
    """git_commit(로컬 동기화, 기본 브랜치만)에서 최근 커밋을 가져온다.

    `query`가 있으면 커밋 메시지로 좁힌다 — orchestrator가 키워드로 찾을 때 씀.
    """
    sql = """
        SELECT id, commit_sha, author_name, author_email, committed_at, message
          FROM git_commit WHERE repository_id=%s
    """
    params: list = [str(repository_id)]
    if query:
        sql += " AND message ILIKE %s"
        params.append(f"%{query}%")
    sql += " ORDER BY committed_at DESC LIMIT %s"
    params.append(limit)
    cur.execute(sql, params)
    return cur.fetchall()


def list_pull_requests(cur, repository_id: UUID, *, state: str | None = None, limit: int | None = None) -> list[dict]:
    sql = """
        SELECT id, number, title, state, author_login, base_ref, head_ref, head_sha,
               merged, html_url, opened_at, closed_at, merged_at
          FROM pull_request
         WHERE repository_id=%s AND (%s::text IS NULL OR state=%s)
         ORDER BY opened_at DESC NULLS LAST
    """
    params: list = [str(repository_id), state, state]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    cur.execute(sql, params)
    return cur.fetchall()


def list_releases(cur, repository_id: UUID, *, limit: int | None = None) -> list[dict]:
    sql = """
        SELECT id, tag_name, name, is_draft, is_prerelease, commit_sha, scan_id,
               html_url, published_at
          FROM release WHERE repository_id=%s ORDER BY published_at DESC NULLS LAST
    """
    params: list = [str(repository_id)]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    cur.execute(sql, params)
    return cur.fetchall()


def list_repo_branches(repo_info: dict) -> list[dict]:
    """GitHub 라이브 조회 — 로컬 캐시가 없어 매번 API를 부른다(orchestrator에서도 동일)."""
    branches = _gh_list_branches(repo_info["github_installation_id"], repo_info["namespace"], repo_info["name"])
    return [
        {"name": b["name"], "commit_sha": b["commit"]["sha"],
         "is_default": b["name"] == repo_info["default_branch"],
         "protected": b.get("protected", False)}
        for b in branches
    ]


# ---------------------------------------------------------------------------
# 채팅 전용 래퍼 — 로컬에 동기화된 게 없으면 GitHub 라이브로 즉석 조회한다.
#
# REST 라우트가 쓰는 위 list_commits/list_pull_requests/list_releases는 그대로
# 둔다 — Releases 페이지의 "동기화" 버튼 흐름과 release.id 기반 드릴다운(비교/
# 스캔 연결)이 DB 행 존재를 전제하므로, 거기서 말없이 라이브 데이터를 섞으면
# 그 흐름이 깨진다. 채팅은 그런 후속 링크가 필요 없는 "사실 답변"이라 로컬이
# 비어 있으면 라이브로 보충해도 안전하다 — 오히려 "아직 동기화 안 해서 모른다"
# 보다 실제로 물어본 걸 답해주는 쪽이 사용자에게 낫다.
# ---------------------------------------------------------------------------

def _has_rows(cur, table: str, repository_id: UUID) -> bool:
    # table은 아래 호출부의 고정 문자열만 들어온다(사용자 입력 아님).
    cur.execute(f"SELECT 1 FROM {table} WHERE repository_id=%s LIMIT 1", (str(repository_id),))
    return cur.fetchone() is not None


def list_commits_for_chat(cur, repository_id: UUID, *, limit: int = 20, query: str | None = None) -> list[dict]:
    """로컬에 동기화된 커밋이 하나도 없을 때만 GitHub로 폴백한다 — "키워드에 맞는 커밋이
    없다"(정상적인 빈 결과)를 "동기화 안 됨"으로 오인해 라이브 조회하지 않는다."""
    rows = list_commits(cur, repository_id, limit=limit, query=query)
    if rows or _has_rows(cur, "git_commit", repository_id):
        return rows
    repo = repo_github_info(cur, repository_id)
    _, commits = _gh_list_recent_commits(repo["github_installation_id"], repo["namespace"], repo["name"],
                                          limit=limit)
    result = [
        {
            "commit_sha": c["sha"],
            "author_name": (c.get("commit") or {}).get("author", {}).get("name"),
            "author_email": (c.get("commit") or {}).get("author", {}).get("email"),
            "committed_at": (c.get("commit") or {}).get("author", {}).get("date"),
            "message": (c.get("commit") or {}).get("message"),
        }
        for c in commits
    ]
    if query:
        q = query.lower()
        result = [c for c in result if q in (c["message"] or "").lower()]
    return result


def list_pull_requests_for_chat(cur, repository_id: UUID, *, state: str | None = None,
                                 limit: int = 20) -> list[dict]:
    """state: open|closed|merged|None(전체). merged는 GitHub에 별도 state가 없어
    closed 중 merged인 것으로 거른다. 폴백 규칙은 list_commits_for_chat과 같다."""
    merged_only = state == "merged"
    db_state = "closed" if merged_only else state
    rows = list_pull_requests(cur, repository_id, state=db_state, limit=None if merged_only else limit)
    if merged_only:
        rows = [r for r in rows if r.get("merged")][:limit]
    if rows or _has_rows(cur, "pull_request", repository_id):
        return rows
    repo = repo_github_info(cur, repository_id)
    prs = _gh_list_pull_requests(repo["github_installation_id"], repo["namespace"], repo["name"],
                                  state=db_state or "all", limit=limit)
    if merged_only:
        prs = [pr for pr in prs if pr.get("merged_at")]
    return [
        {
            "number": pr["number"], "title": pr["title"], "state": pr["state"],
            "author_login": (pr.get("user") or {}).get("login"),
            "base_ref": pr["base"]["ref"], "head_ref": pr["head"]["ref"], "head_sha": pr["head"]["sha"],
            "merged": bool(pr.get("merged_at")), "html_url": pr.get("html_url"),
            "opened_at": pr.get("created_at"), "closed_at": pr.get("closed_at"),
            "merged_at": pr.get("merged_at"),
        }
        for pr in prs
    ]


def list_releases_for_chat(cur, repository_id: UUID, *, limit: int = 10) -> list[dict]:
    rows = list_releases(cur, repository_id, limit=limit)
    if rows or _has_rows(cur, "release", repository_id):
        return rows
    repo = repo_github_info(cur, repository_id)
    gh_releases = _gh_list_releases(repo["github_installation_id"], repo["namespace"], repo["name"])
    return [
        {
            "tag_name": r["tag_name"], "name": r.get("name"),
            "is_draft": r.get("draft", False), "is_prerelease": r.get("prerelease", False),
            "commit_sha": None, "scan_id": None,
            "html_url": r.get("html_url"), "published_at": r.get("published_at"),
        }
        for r in gh_releases[:limit]
    ]
