"""스캔 생성 로직 — API 라우트(app/main.py)와 웹훅 핸들러(app/installations.py)

둘 다 여기서 가져다 쓴다. app.main 을 installations.py 가 직접 import하면
순환 참조(app.main -> installations.router, installations -> app.main)라
공용 로직을 별도 모듈로 뺐다.
"""

from __future__ import annotations

from app import audit, queue, repo_activity_bus
from app.db import tx
from app.github_app import get_latest_commit_sha

PARSER_VERSION = "tree-sitter-0.26/java+python+typescript+javascript"


def create_scan(cur, *, repository_id: str, commit_sha: str, ref: str,
                 trigger: str, actor: str) -> dict:
    """스캔 1회 = 커밋 1개 = 불변 스냅샷. 같은 커밋이 다시 오면 새로 만들지

    않고 기존 스냅샷을 돌려준다(수동 재요청과 웹훅 재전송 둘 다 안전).
    """
    cur.execute(
        """
        INSERT INTO scan (repository_id, commit_sha, ref, trigger, parser_version)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (repository_id, commit_sha) DO NOTHING
        RETURNING id, status
        """,
        (repository_id, commit_sha, ref, trigger, PARSER_VERSION),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "SELECT id, status FROM scan WHERE repository_id=%s AND commit_sha=%s",
            (repository_id, commit_sha),
        )
        return {**cur.fetchone(), "reused": True}

    audit.record(cur, actor=actor, action="scan.request",
                 subject={"scan_id": str(row["id"]), "repository_id": repository_id},
                 payload={"commit_sha": commit_sha, "ref": ref, "trigger": trigger})
    # scan.fetch: 스캔 커밋으로 worktree 체크아웃 + parent 대비 diff 계산 후
    # scan.parse 를 이어서 enqueue 한다. parent 해석/GitHub compare 같은 네트워크
    # 작업을 이 HTTP 요청 트랜잭션이 아니라 워커에서 하기 위해 한 단계 앞에 둔다.
    queue.enqueue(cur, "scan.fetch",
                  {"scan_id": str(row["id"]), "repository_id": repository_id,
                   "commit_sha": commit_sha, "ref": ref},
                  dedupe_key=f"fetch:{row['id']}")
    repo_activity_bus.notify_repo_activity(
        cur, repository_id,
        {"type": "scan_started", "scan_id": str(row["id"]), "trigger": trigger},
    )
    # 이 정확한 커밋을 가리키는 release가 이미 동기화돼 있었다면(스캔이
    # 그보다 나중에 만들어진 경우) 역방향으로 연결한다 — release가 먼저
    # 동기화되고 나중에 스캔되는 순서도 자동으로 이어지게.
    cur.execute(
        "UPDATE release SET scan_id=%s WHERE repository_id=%s AND commit_sha=%s AND scan_id IS NULL",
        (str(row["id"]), repository_id, commit_sha),
    )
    return {**row, "reused": False}


def start_repository_scan(repository_id: str, *, actor: str, commit_sha: str | None = None,
                          ref: str | None = None, trigger: str = "manual") -> dict:
    """저장소 분석 시작 — 커밋을 안 주면 기본 브랜치 최신 커밋을 GitHub 에서 조회한다.

    수동 스캔 버튼(POST /repositories/{id}/scans)과 저장소 연결 직후 자동 분석
    (app/projects.py attach_project_repository)이 같이 쓴다. GitHub 조회는 네트워크라
    DB 트랜잭션 밖에서 하고, 스캔 생성만 짧은 트랜잭션으로 묶는다.
    없는/비활성 저장소면 LookupError.
    """
    with tx() as cur:
        cur.execute(
            """
            SELECT r.namespace, r.name, r.default_branch, i.github_installation_id
              FROM repository r JOIN installation i ON i.id = r.installation_id
             WHERE r.id=%s AND r.is_active
            """,
            (str(repository_id),),
        )
        repo = cur.fetchone()
    if repo is None:
        raise LookupError("repository not found")

    ref = ref or f"refs/heads/{repo['default_branch']}"
    if commit_sha is None:
        commit_sha = get_latest_commit_sha(repo["github_installation_id"], repo["namespace"],
                                           repo["name"], repo["default_branch"])
    with tx() as cur:
        return create_scan(cur, repository_id=str(repository_id), commit_sha=commit_sha,
                           ref=ref, trigger=trigger, actor=actor)

