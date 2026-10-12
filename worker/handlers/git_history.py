"""git.sync_history — WBS 1.4. 레포의 최근 커밋 이력 + 건드린 파일 목록을 가져온다.

scan 과 무관하게 repository 등록 시점에 바로 돈다(repo.clone 과 나란히
큐를 탐) — git 히스토리는 특정 스캔(불변 스냅샷)이 아니라 레포의 시간축
전체에 속하는 정보라서다. "이 스캔의 이 파일이 최근 몇 번 바뀌었나"는
API 조회 시점에 path 로 symbol(module)과 JOIN한다(commit_file 자체엔
scan_id 가 없다).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db import tx
from app.github_app import get_commit_files, list_recent_commits

log = logging.getLogger("worker.git_history")

HISTORY_LIMIT = 100


def handle(payload: dict) -> None:
    repository_id = payload["repository_id"]

    with tx() as cur:
        cur.execute(
            """
            SELECT r.namespace, r.name, i.github_installation_id
              FROM repository r
              JOIN installation i ON i.id = r.installation_id
             WHERE r.id=%s
            """,
            (repository_id,),
        )
        repo = cur.fetchone()
    if repo is None:
        log.warning("repository %s: installation 연결 없음 — 건너뜀", repository_id)
        return

    token, commits = list_recent_commits(
        repo["github_installation_id"], repo["namespace"], repo["name"], limit=HISTORY_LIMIT
    )

    new_commits = 0
    for c in commits:
        with tx() as cur:
            cur.execute(
                """
                INSERT INTO git_commit (repository_id, commit_sha, author_name, author_email,
                                         committed_at, message)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (repository_id, commit_sha) DO NOTHING
                RETURNING id
                """,
                (repository_id, c["sha"],
                 c.get("commit", {}).get("author", {}).get("name"),
                 c.get("commit", {}).get("author", {}).get("email"),
                 _parse_date(c.get("commit", {}).get("author", {}).get("date")),
                 c.get("commit", {}).get("message", "")),
            )
            row = cur.fetchone()

        if row is None:
            continue  # 이미 있는 커밋 — 상세(파일 목록) 재조회 안 함

        new_commits += 1
        try:
            files = get_commit_files(token, repo["namespace"], repo["name"], c["sha"])
        except Exception:
            log.exception("commit %s 파일 목록 조회 실패 — 건너뜀", c["sha"])
            continue

        with tx() as cur:
            for f in files:
                cur.execute(
                    """
                    INSERT INTO commit_file (commit_id, path, change_type, additions, deletions)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (commit_id, path) DO NOTHING
                    """,
                    (row["id"], f["filename"], f["status"],
                     f.get("additions", 0), f.get("deletions", 0)),
                )

    log.info("repository %s: 커밋 %d개 조회, 신규 %d개 저장", repository_id, len(commits), new_commits)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
