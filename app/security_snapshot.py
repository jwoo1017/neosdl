"""Security Snapshot 물질화 — scan(=커밋 1개) 하나의 taint-finding 상태 롤업.

`ensure_security_findings` 와 같은 패턴: 최초 1회 계산 + `scan.stats` 플래그
(`snapshot_built`)로 멱등. `worker/handlers/risk_build.py` 가 파이프라인에서
호출하고, 타임라인/디프 엔드포인트가 과거 스캔에 대해 lazy 하게도 호출한다.

실패해도 스캔 파이프라인에 영향 없다 — 호출부가 try/except 로 감싼다.

`NeoSDL_Security_History_Plan.md` §2.2, §6.
"""

from __future__ import annotations

import logging

from app.branches import resolve_scan_branch
from app.scan_queries import ensure_security_findings
from app.security_fingerprint import compute_fingerprint
from app.security_score import synthetic_score

log = logging.getLogger("app.security_snapshot")


def backfill_fingerprints(cur, scan_id: str) -> None:
    """fingerprint 가 비어 있는 finding 에 지문을 채운다(sql/022 이전에 생긴 행)."""
    cur.execute(
        """
        SELECT id, kind, category, sink_symbol_key, source_var, path
          FROM security_finding
         WHERE scan_id = %s AND fingerprint IS NULL
        """,
        (str(scan_id),),
    )
    for r in cur.fetchall():
        try:
            fp = compute_fingerprint(r)
        except Exception:
            log.warning("fingerprint 계산 실패 finding=%s — 건너뜀", r["id"], exc_info=True)
            continue
        cur.execute("UPDATE security_finding SET fingerprint = %s WHERE id = %s", (fp, r["id"]))


def ensure_security_snapshot(cur, scan_id: str) -> None:
    """이 스캔의 Security Snapshot 이 없으면 계산해서 채운다(있으면 아무것도 안 함)."""
    cur.execute(
        "SELECT repository_id, commit_sha, ref, created_at, stats FROM scan WHERE id = %s",
        (str(scan_id),),
    )
    s = cur.fetchone()
    if s is None or (s["stats"] or {}).get("snapshot_built"):
        return

    ensure_security_findings(cur, scan_id)      # 멱등 — 이미 됐으면 no-op
    backfill_fingerprints(cur, scan_id)

    repository_id = s["repository_id"]
    branch = resolve_scan_branch(cur, repository_id, s["ref"], s["commit_sha"])

    cur.execute(
        "SELECT committed_at FROM git_commit WHERE repository_id = %s AND commit_sha = %s",
        (repository_id, s["commit_sha"]),
    )
    gc = cur.fetchone()
    committed_at = gc["committed_at"] if gc else s["created_at"]

    cur.execute(
        """
        SELECT severity, count(*) AS n,
               COALESCE(SUM(COALESCE(array_length(evidence_kinds, 1), 0)), 0) AS ev
          FROM security_finding sf
         WHERE sf.scan_id = %s AND sf.status = 'open'
           AND NOT EXISTS (SELECT 1 FROM finding_whitelist fw
                            WHERE fw.repository_id = sf.repository_id AND fw.category = sf.category
                              AND (fw.sink_symbol_key IS NULL OR fw.sink_symbol_key = sf.sink_symbol_key))
         GROUP BY severity
        """,
        (str(scan_id),),
    )
    counts: dict[str, int] = {}
    n_evidence = 0
    for r in cur.fetchall():
        counts[r["severity"]] = r["n"]
        n_evidence += int(r["ev"])
    n_findings = sum(counts.values())
    score = synthetic_score(counts)

    cur.execute(
        """
        INSERT INTO security_snapshot (scan_id, repository_id, commit_sha, branch, committed_at,
                                       score, n_critical, n_high, n_medium, n_low,
                                       n_findings, n_evidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (scan_id) DO UPDATE SET
            branch = EXCLUDED.branch, committed_at = EXCLUDED.committed_at, score = EXCLUDED.score,
            n_critical = EXCLUDED.n_critical, n_high = EXCLUDED.n_high,
            n_medium = EXCLUDED.n_medium, n_low = EXCLUDED.n_low,
            n_findings = EXCLUDED.n_findings, n_evidence = EXCLUDED.n_evidence,
            generated_at = now()
        """,
        (str(scan_id), repository_id, s["commit_sha"], branch, committed_at, score,
         counts.get("critical", 0), counts.get("high", 0), counts.get("medium", 0),
         counts.get("low", 0), n_findings, n_evidence),
    )
    cur.execute(
        "UPDATE scan SET stats = stats || '{\"snapshot_built\": true}'::jsonb WHERE id = %s",
        (str(scan_id),),
    )
    log.info("scan %s: security snapshot (score %s, findings %d, branch %s)",
             scan_id, score, n_findings, branch)
