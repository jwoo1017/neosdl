"""감사 로그.

해시 체인은 DB 트리거가 계산한다. 여기서는 기록만 한다 —
애플리케이션이 체인을 만들면 애플리케이션 버그가 곧 증거 훼손이 된다.

호출자는 반드시 업무 트랜잭션과 같은 커서를 넘긴다. 판정과 기록이
따로 커밋되면 "판정은 됐는데 로그가 없는" 구간이 생긴다.
"""

import json
from typing import Any

from psycopg import Cursor


def record(
    cur: Cursor,
    *,
    actor: str,
    action: str,
    subject: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    cur.execute(
        """
        INSERT INTO audit_log (actor, action, subject, payload)
        VALUES (%s, %s, %s::jsonb, %s::jsonb)
        RETURNING seq
        """,
        (actor, action, json.dumps(subject or {}), json.dumps(payload or {})),
    )
    return cur.fetchone()["seq"]


def verify(cur: Cursor) -> tuple[bool, int | None]:
    """체인 전체 검증. 감사 대응 시 이 함수의 결과가 증거다."""
    cur.execute("SELECT seq, ok FROM audit_log_verify() WHERE NOT ok ORDER BY seq LIMIT 1")
    row = cur.fetchone()
    return (row is None, row["seq"] if row else None)
