"""scan.fetch — scan.parse 앞단계. 스캔 커밋을 정확히 체크아웃하고, 직전 성공
스캔(parent)과의 diff를 계산해 증분 스캔의 근거를 마련한다.

이 잡이 하는 일:
  1. 레포 캐시(repos/<id>) 확보 + 스캔 커밋 객체 fetch.
  2. scans/<scan_id> git worktree 를 scan.commit_sha 로 체크아웃.
  3. parent 스캔 해석 → GitHub compare 로 변경 파일 계산 → scan.parent_scan_id +
     scan.stats 기록.
  4. scan.parse enqueue.

전부 fail-safe: worktree 실패 → 레포 캐시로 폴백(오늘 동작), parent NULL → 전체
분석. compare 실패 → 전체 분석. 어느 경우든 scan.parse 는 반드시 enqueue 한다.

app/scans.py create_scan 이 (scan.parse 대신) 이 잡을 enqueue 한다 — parent 해석과
GitHub compare 같은 네트워크 작업을 HTTP 요청 트랜잭션 밖(워커)에서 하기 위함.
"""

from __future__ import annotations

import json
import logging

from app import incremental, queue
from app.db import tx
from app.scans import PARSER_VERSION
from worker.handlers.clone import _repo_row, create_scan_worktree

log = logging.getLogger("worker.scan_fetch")


def handle(payload: dict) -> None:
    scan_id = payload["scan_id"]
    repository_id = payload["repository_id"]
    commit_sha = payload["commit_sha"]

    with tx() as cur:
        repo = _repo_row(cur, repository_id)

    # 1~2. worktree (실패해도 계속 — parse 가 레포 캐시로 폴백)
    worktree = create_scan_worktree(repository_id, repo, str(scan_id), commit_sha)

    # 3. 증분 근거 계산 — worktree 가 있어야(정확한 커밋) 의미가 있다
    parent_id = None
    changed = None
    if worktree is not None:
        with tx() as cur:
            parent = incremental.resolve_parent_scan(cur, repository_id, PARSER_VERSION,
                                                      exclude_scan_id=str(scan_id))
            if parent is not None:
                paths = incremental.compute_changed_paths(
                    cur, repository_id, parent["commit_sha"], commit_sha,
                )
                if paths is not None:
                    parent_id = str(parent["id"])
                    changed = paths["changed"]
                    cur.execute(
                        """
                        UPDATE scan
                           SET parent_scan_id = %s,
                               stats = stats || %s::jsonb
                         WHERE id = %s
                        """,
                        (parent_id,
                         json.dumps({
                             "incremental": True,
                             "changed_paths": paths["changed"],
                             "removed_paths": paths["removed"],
                             "parent_commit": parent["commit_sha"],
                         }),
                         str(scan_id)),
                    )

    if parent_id:
        log.info("scan %s: 증분 (parent=%s, 변경 파일 %d개)", scan_id, parent_id, len(changed or []))
    else:
        log.info("scan %s: 전체 스캔 (parent 없음 또는 diff 계산 불가)", scan_id)

    # 4. 항상 parse 로 이어간다
    with tx() as cur:
        queue.enqueue(cur, "scan.parse", {"scan_id": str(scan_id)},
                      dedupe_key=f"parse:{scan_id}")
