"""PostgreSQL 테이블 큐.

Redis / Celery 를 두지 않는다. 폐쇄망 납품에서 컴포넌트 하나를 줄이는 것이
운영 부담을 줄이고, 잡 상태가 정본 DB 안에 있어야 감사 로그와 같은
트랜잭션으로 묶인다.
"""

import json
from typing import Any

from psycopg import Cursor


def enqueue(
    cur: Cursor,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    priority: int = 100,
    dedupe_key: str | None = None,
) -> int | None:
    """작업을 큐에 넣는다. dedupe_key 가 이미 대기/실행 중이면 None 을 반환한다."""
    cur.execute(
        """
        INSERT INTO job (kind, payload, priority, dedupe_key)
        VALUES (%s, %s::jsonb, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        (kind, json.dumps(payload or {}), priority, dedupe_key),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def dequeue(cur: Cursor, worker: str, kinds: list[str] | None = None) -> dict | None:
    """FOR UPDATE SKIP LOCKED. 워커를 몇 개 띄우든 한 작업은 한 번만 나간다."""
    cur.execute("SELECT * FROM job_dequeue(%s, %s)", (worker, kinds))
    return cur.fetchone()


def finish(cur: Cursor, job_id: int) -> None:
    cur.execute(
        "UPDATE job SET status='done', updated_at=now() WHERE id=%s", (job_id,)
    )


def fail(cur: Cursor, job_id: int, error: str, *, backoff_seconds: int = 30) -> str | None:
    """재시도 한도를 넘으면 dead 로 내린다. 조용히 사라지는 작업이 없어야 한다.

    바뀐 상태('queued' 재시도 대기 | 'dead' 최종 실패)를 돌려준다.
    """
    cur.execute(
        """
        UPDATE job SET
            status = CASE WHEN attempts >= max_attempts THEN 'dead'::job_status
                          ELSE 'queued'::job_status END,
            run_after  = now() + (%s * interval '1 second' * attempts),
            last_error = %s,
            locked_by  = NULL,
            locked_at  = NULL,
            updated_at = now()
        WHERE id = %s
        RETURNING status
        """,
        (backoff_seconds, error[:4000], job_id),
    )
    row = cur.fetchone()
    return row["status"] if row else None
