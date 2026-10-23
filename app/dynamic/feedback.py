"""검증 판정 반영 — finding_verification 기록 + 증거 마스킹 + finding 상태 갱신 + 감사 + fix PR.

전부 한 tx(호출자 커서) 안에서 — 판정·감사·증거가 따로 커밋되는 구간이 없게(audit 계약).
핵심 효과: false_positive 는 finding_whitelist 로 억제(이번 오탐 9건 자동 제거), confirmed 는
fix.generate 를 enqueue(증거기반 PR, 오탐 PR 없음).
"""

from __future__ import annotations

import json
import logging

from app import audit, queue
from app.risk import pii_scan, secret_scan

log = logging.getLogger("app.dynamic.feedback")


def _mask(text: str | None) -> tuple[str, int]:
    """저장 전 비밀값·PII 마스킹. 스캐너는 매칭 '위치(line)'만 주므로(원문 토큰 미반환),
    플래그된 '줄 전체'를 치환한다(보수적). (마스킹된 텍스트, 치환된 줄 수) 반환.
    """
    if not text:
        return "", 0
    lines = text[:20000].split("\n")
    flagged: set[int] = set()
    for scanner, label in ((secret_scan, "secret"), (pii_scan, "pii")):
        fn = getattr(scanner, "scan_text", None)
        if fn is None:
            continue
        try:
            for f in fn("\n".join(lines)):
                ln = (f.get("location") or {}).get("line") if isinstance(f, dict) else None
                if isinstance(ln, int) and 1 <= ln <= len(lines):
                    flagged.add(ln - 1)
        except Exception:
            log.debug("마스킹 스캐너 %s 실패 — 무시", label, exc_info=True)
    for i in sorted(flagged):
        lines[i] = "***REDACTED***"
    return "\n".join(lines), len(flagged)


def write_verdict(cur, *, engagement: dict, finding: dict, verdict, entry_route: dict,
                  oracle: str, artifacts: list[dict], actor: str = "system:verifier") -> int:
    """verdict(oracle.Verdict), entry_route, 증거 아티팩트를 반영한다. finding_verification.id 반환.

    artifacts: [{"kind": request|response|app_log|iast_event, "content": str}] — 저장 전 마스킹.
    """
    engagement_id = str(engagement["id"])
    cur.execute(
        """
        INSERT INTO finding_verification (finding_id, engagement_id, verdict, confidence, oracle,
                                          entry_route, signals, reason)
        VALUES (%s,%s,%s,%s,%s::verification_oracle,%s::jsonb,%s::jsonb,%s)
        ON CONFLICT (finding_id, engagement_id) DO UPDATE SET
            verdict=EXCLUDED.verdict, confidence=EXCLUDED.confidence, oracle=EXCLUDED.oracle,
            entry_route=EXCLUDED.entry_route, signals=EXCLUDED.signals, reason=EXCLUDED.reason
        RETURNING id
        """,
        (finding["id"], engagement_id, verdict.verdict, float(verdict.confidence), oracle,
         json.dumps(entry_route, default=str), json.dumps(verdict.signals, default=str),
         getattr(verdict, "reason", None)),
    )
    verification_id = cur.fetchone()["id"]

    for a in artifacts:
        content, redactions = _mask(a.get("content"))
        cur.execute(
            "INSERT INTO verification_artifact (verification_id, kind, content, redactions) "
            "VALUES (%s,%s::verification_artifact_kind,%s,%s)",
            (verification_id, a["kind"], content, redactions),
        )

    # finding 상태 반영: false_positive → whitelist 억제(다음 스캔도 상속), confirmed → fix PR enqueue.
    if verdict.verdict == "false_positive":
        cur.execute(
            """
            INSERT INTO finding_whitelist (project_id, repository_id, category, sink_symbol_key, reason, created_by)
            SELECT %s, repository_id, category, sink_symbol_key, %s, NULL
              FROM security_finding WHERE id=%s
            ON CONFLICT DO NOTHING
            """,
            (str(engagement["project_id"]), f"dynamic false_positive (conf {verdict.confidence:.2f})", finding["id"]),
        )
    elif verdict.verdict == "confirmed" and verdict.confidence >= 0.6:
        _enqueue_fix(cur, finding)

    audit.record(cur, actor=actor, action="finding.verified",
                 subject={"finding_id": finding["id"], "engagement_id": engagement_id},
                 payload={"verdict": verdict.verdict, "confidence": round(float(verdict.confidence), 3),
                          "oracle": oracle})
    return verification_id


def _enqueue_fix(cur, finding: dict) -> None:
    """confirmed finding 에 대해 기존 fix 흐름을 그대로 재사용(중복 방지)."""
    cur.execute("SELECT id, status FROM finding_fix WHERE finding_id=%s ORDER BY created_at DESC LIMIT 1",
                (finding["id"],))
    row = cur.fetchone()
    if row and row["status"] in ("pending", "ready", "pr_created"):
        return
    cur.execute(
        "SELECT repository_id, scan_id FROM security_finding WHERE id=%s", (finding["id"],))
    f = cur.fetchone()
    cur.execute(
        "INSERT INTO finding_fix (finding_id, repository_id, scan_id, status) VALUES (%s,%s,%s,'pending') RETURNING id",
        (finding["id"], str(f["repository_id"]), str(f["scan_id"]) if f["scan_id"] else None),
    )
    queue.enqueue(cur, "fix.generate", {"finding_fix_id": cur.fetchone()["id"]})
