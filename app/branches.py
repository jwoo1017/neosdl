"""브랜치 생애주기 — 웹훅 push/create/delete 로 관측한 것만 기록.

라이브 GitHub 브랜치 조회(`repo_queries.list_repo_branches`)와 별개다. 여기는
"우리가 스캔한 시점의 브랜치 상태"의 시간축 기록이고, 브랜치가 삭제돼도
`deleted_at` 만 채우고 행을 남긴다 — Security History 가 과거 스냅샷의 브랜치
소속을 잃지 않게 하기 위함(`NeoSDL_Security_History_Plan.md` §2.1, §4).

`_upsert_pull_request` 와 같은 원칙: 등록된 레포면 CI 옵트인 여부와 무관하게
"이 브랜치가 있었다"는 사실 자체를 기록한다.
"""

from __future__ import annotations

_BRANCH_PREFIX = "refs/heads/"


def parse_branch_name(ref: str | None) -> str | None:
    """refs/heads/feature/x -> feature/x. PR ref / tag ref 는 None."""
    if ref and ref.startswith(_BRANCH_PREFIX):
        return ref[len(_BRANCH_PREFIX):] or None
    return None


def record_branch_event(cur, repository_id: str, ref: str | None, head_sha: str | None,
                         *, default_branch: str | None = None, deleted: bool = False) -> None:
    """웹훅 push/create/delete 로 브랜치 상태를 upsert.

    deleted=True 면 `deleted_at` 만 채우고 행은 유지. 이후 같은 브랜치로 push 가
    오면 `deleted_at` 을 NULL 로 되돌린다(브랜치 재생성).
    """
    name = parse_branch_name(ref)
    if name is None:
        return

    if deleted:
        cur.execute(
            "UPDATE branch SET deleted_at = now(), last_seen_at = now() "
            "WHERE repository_id = %s AND name = %s",
            (repository_id, name),
        )
        return

    is_default = bool(default_branch) and name == default_branch
    cur.execute(
        """
        INSERT INTO branch (repository_id, name, head_sha, is_default)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (repository_id, name) DO UPDATE
            SET head_sha     = COALESCE(EXCLUDED.head_sha, branch.head_sha),
                last_seen_at  = now(),
                deleted_at    = NULL,
                is_default    = branch.is_default OR EXCLUDED.is_default
        """,
        (repository_id, name, head_sha, is_default),
    )


def link_branch_head_scan(cur, repository_id: str, ref: str | None, scan_id: str) -> None:
    """이 브랜치의 최신 스캔을 연결(Branch Security Map 에서 head 스냅샷을 잡을 때)."""
    name = parse_branch_name(ref)
    if name is None:
        return
    cur.execute(
        "UPDATE branch SET head_scan_id = %s, last_seen_at = now() "
        "WHERE repository_id = %s AND name = %s",
        (scan_id, repository_id, name),
    )


def resolve_scan_branch(cur, repository_id: str, ref: str | None, commit_sha: str) -> str | None:
    """스냅샷에 기록할 branch 값. refs/heads/<b> → <b>; PR ref → pull_request.head_ref 폴백."""
    name = parse_branch_name(ref)
    if name:
        return name
    cur.execute(
        "SELECT head_ref FROM pull_request WHERE repository_id = %s AND head_sha = %s "
        "ORDER BY updated_at DESC LIMIT 1",
        (repository_id, commit_sha),
    )
    row = cur.fetchone()
    return row["head_ref"] if row else None
