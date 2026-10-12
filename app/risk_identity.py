"""Risk Identity 생애주기 — Security History Phase 2.

지문(`security_finding.fingerprint`) 하나가 프로젝트 시간축에서 거치는 상태를
추적한다: 어느 브랜치·커밋에서 처음 생겨(Introduced), 어떤 PR 로 전파돼
(Propagated/Merged), 언제 기본 브랜치에서 사라졌나(Fixed/Resolved).

`ensure_risk_identities(cur, scan_id)` 가 스냅샷마다 재계산한다 —
`ensure_security_snapshot` 이후 `worker/handlers/risk_build.py` 가 호출한다.
`ensure_security_findings` / `ensure_security_snapshot` 과 같은 멱등·fail-safe.

`NeoSDL_Security_History_Design.md` §2.3, §2.4, §4.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("app.risk_identity")

_SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}
_RESOLVED_AFTER_N_SNAPSHOTS = 3   # 기본 브랜치에서 이만큼 연속 부재 확인되면 resolved


def _worse(a: str, b: str) -> str:
    return a if _SEVERITY_RANK.get(a, -1) >= _SEVERITY_RANK.get(b, -1) else b


def ensure_risk_identities(cur, scan_id: str, *, force: bool = False) -> None:
    """이 스캔이 관측한 지문들의 risk_identity + lifecycle 을 갱신한다.

    `scan.stats->>'identities_built'` 플래그로 멱등(force=True 면 무시). 지문
    관측은 order-independent 하고 fixed/resolved 는 최신 기본 브랜치 스캔이
    처리될 때 갱신되므로, 스캔당 1회 처리로 충분하다.
    """
    cur.execute("SELECT repository_id, stats FROM scan WHERE id = %s", (str(scan_id),))
    srow = cur.fetchone()
    if srow is None:
        return
    if not force and (srow["stats"] or {}).get("identities_built"):
        return
    repository_id = str(srow["repository_id"])

    cur.execute("SELECT branch, commit_sha, committed_at FROM security_snapshot WHERE scan_id = %s",
                (str(scan_id),))
    snap = cur.fetchone()
    if snap is None:
        return  # 스냅샷이 먼저 물질화돼야 한다

    cur.execute("SELECT default_branch FROM repository WHERE id = %s", (repository_id,))
    default_branch = (cur.fetchone() or {}).get("default_branch")
    is_default = bool(snap["branch"]) and snap["branch"] == default_branch

    cur.execute(
        "SELECT fingerprint, kind, category, severity FROM risk_observation "
        "WHERE repository_id = %s AND scan_id = %s",
        (repository_id, str(scan_id)),
    )
    observed = cur.fetchall()
    observed_fps = {r["fingerprint"] for r in observed}

    touched: set[str] = set(observed_fps)

    for r in observed:
        _upsert_identity(cur, repository_id, r, scan_id, snap)

    # fixed/resolved 판정은 "이 스캔이 기본 브랜치의 최신 커밋일 때"만 — 옛 커밋을
    # 나중에 백필해도 최신 상태를 뒤집지 않게(스캔 처리 순서에 안전).
    if is_default and _is_latest_default(cur, repository_id, snap["committed_at"]):
        touched |= _detect_fixed(cur, repository_id, snap, observed_fps)
        touched |= _detect_resolved(cur, repository_id)

    for fp in touched:
        _recompute_lifecycle(cur, repository_id, fp, default_branch)

    cur.execute(
        "UPDATE scan SET stats = stats || '{\"identities_built\": true}'::jsonb WHERE id = %s",
        (str(scan_id),),
    )
    log.info("scan %s: risk identity %d개 관측 (branch=%s, default=%s)",
             scan_id, len(observed_fps), snap["branch"], is_default)


def _upsert_identity(cur, repository_id: str, obs: dict, scan_id: str, snap: dict) -> None:
    fp = obs["fingerprint"]
    cur.execute("SELECT sink_symbol_key, sink_line, source_symbol_key, source_var, path "
                "FROM security_finding WHERE repository_id = %s AND fingerprint = %s LIMIT 1",
                (repository_id, fp))
    sf = cur.fetchone() or {}
    sample = json.dumps({
        "sink_symbol_key": sf.get("sink_symbol_key"),
        "sink_line": sf.get("sink_line"),
        "source_symbol_key": sf.get("source_symbol_key"),
        "source_var": sf.get("source_var"),
        "path": sf.get("path"),
    }, default=str)

    cur.execute(
        """
        INSERT INTO risk_identity
            (repository_id, fingerprint, kind, category, worst_severity,
             first_seen_scan_id, first_seen_commit, first_seen_branch, first_seen_at,
             last_seen_scan_id, last_seen_at, status, sample)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s::jsonb)
        ON CONFLICT (repository_id, fingerprint) DO UPDATE SET
            worst_severity = CASE
                WHEN %s = 'critical' OR risk_identity.worst_severity = 'critical' THEN 'critical'
                WHEN %s = 'high'     OR risk_identity.worst_severity = 'high'     THEN 'high'
                WHEN %s = 'medium'   OR risk_identity.worst_severity = 'medium'   THEN 'medium'
                ELSE 'low' END,
            -- 처음 관측이 실제로 더 이르면 first_seen 갱신(백필/역순 처리 대비)
            first_seen_scan_id = CASE WHEN EXCLUDED.first_seen_at < risk_identity.first_seen_at
                                      THEN EXCLUDED.first_seen_scan_id ELSE risk_identity.first_seen_scan_id END,
            first_seen_commit  = CASE WHEN EXCLUDED.first_seen_at < risk_identity.first_seen_at
                                      THEN EXCLUDED.first_seen_commit ELSE risk_identity.first_seen_commit END,
            first_seen_branch  = CASE WHEN EXCLUDED.first_seen_at < risk_identity.first_seen_at
                                      THEN EXCLUDED.first_seen_branch ELSE risk_identity.first_seen_branch END,
            first_seen_at      = LEAST(risk_identity.first_seen_at, EXCLUDED.first_seen_at),
            last_seen_scan_id  = CASE WHEN EXCLUDED.last_seen_at >= risk_identity.last_seen_at
                                      THEN EXCLUDED.last_seen_scan_id ELSE risk_identity.last_seen_scan_id END,
            last_seen_at       = GREATEST(risk_identity.last_seen_at, EXCLUDED.last_seen_at),
            -- 사라졌다가 다시 관측되면 active 로 되돌리고 fixed 표시 해제
            status   = 'active',
            fixed_at = NULL, resolved_at = NULL,
            sample   = EXCLUDED.sample,
            updated_at = now()
        """,
        (repository_id, fp, obs["kind"], obs["category"], obs["severity"],
         str(scan_id), snap["commit_sha"], snap["branch"], snap["committed_at"],
         str(scan_id), snap["committed_at"], sample,
         obs["severity"], obs["severity"], obs["severity"]),
    )


def _is_latest_default(cur, repository_id: str, committed_at) -> bool:
    cur.execute(
        """
        SELECT max(ss.committed_at) AS m
          FROM security_snapshot ss JOIN repository r ON r.id = ss.repository_id
         WHERE ss.repository_id = %s AND ss.branch = r.default_branch
        """,
        (repository_id,),
    )
    row = cur.fetchone()
    return row is None or row["m"] is None or committed_at >= row["m"]


def _detect_fixed(cur, repository_id: str, snap: dict, observed_fps: set[str]) -> set[str]:
    """기본 브랜치 스캔인데 예전 기본 브랜치엔 있던 지문이 이번엔 없다 → fixed."""
    cur.execute(
        """
        UPDATE risk_identity ri
           SET status = 'fixed', fixed_at = %s, updated_at = now()
         WHERE ri.repository_id = %s
           AND ri.status = 'active'
           AND NOT (ri.fingerprint = ANY(%s))
           AND EXISTS (SELECT 1 FROM risk_observation ro
                        WHERE ro.repository_id = ri.repository_id
                          AND ro.fingerprint = ri.fingerprint
                          AND ro.branch = %s
                          AND ro.committed_at < %s)
        RETURNING ri.fingerprint
        """,
        (snap["committed_at"], repository_id, list(observed_fps),
         snap["branch"], snap["committed_at"]),
    )
    return {r["fingerprint"] for r in cur.fetchall()}


def _detect_resolved(cur, repository_id: str) -> set[str]:
    """fixed 상태로 기본 브랜치 스냅샷 N개 지나면 resolved."""
    cur.execute(
        """
        UPDATE risk_identity ri
           SET status = 'resolved', resolved_at = now(), updated_at = now()
         WHERE ri.repository_id = %s AND ri.status = 'fixed'
           AND (SELECT count(*) FROM security_snapshot ss
                 JOIN repository r ON r.id = ss.repository_id AND ss.branch = r.default_branch
                WHERE ss.repository_id = ri.repository_id
                  AND ss.committed_at > ri.fixed_at) >= %s
        RETURNING ri.fingerprint
        """,
        (repository_id, _RESOLVED_AFTER_N_SNAPSHOTS),
    )
    return {r["fingerprint"] for r in cur.fetchall()}


def _recompute_lifecycle(cur, repository_id: str, fingerprint: str, default_branch: str | None) -> None:
    """이 지문의 lifecycle 이벤트를 전부 다시 도출한다(DELETE 후 재삽입 — 멱등)."""
    cur.execute("DELETE FROM risk_lifecycle_event WHERE repository_id = %s AND fingerprint = %s",
                (repository_id, fingerprint))

    cur.execute(
        "SELECT scan_id, branch, commit_sha, committed_at FROM risk_observation "
        "WHERE repository_id = %s AND fingerprint = %s ORDER BY committed_at, scan_id",
        (repository_id, fingerprint),
    )
    obs = cur.fetchall()
    cur.execute("SELECT status, fixed_at, resolved_at FROM risk_identity "
                "WHERE repository_id = %s AND fingerprint = %s", (repository_id, fingerprint))
    ident = cur.fetchone() or {}

    events: list[tuple] = []

    if obs:
        first = obs[0]
        events.append(("detected", first["scan_id"], None, first["branch"],
                       {"commit": first["commit_sha"]}, first["committed_at"]))
        events.append(("introduced", first["scan_id"], None, first["branch"],
                       {"commit": first["commit_sha"], "branch": first["branch"]}, first["committed_at"]))

        # PR-merge 전파: 이 지문이 head_ref 브랜치에서 관측됐고, base_ref 브랜치에서도 관측됐으면
        obs_branches = {o["branch"] for o in obs if o["branch"]}
        cur.execute(
            """
            SELECT number, base_ref, head_ref, merged_at
              FROM pull_request
             WHERE repository_id = %s AND merged AND merged_at IS NOT NULL
             ORDER BY merged_at
            """,
            (repository_id,),
        )
        for pr in cur.fetchall():
            if pr["head_ref"] in obs_branches and pr["base_ref"] in obs_branches:
                head_seen_before = any(
                    o["branch"] == pr["head_ref"] and o["committed_at"] <= pr["merged_at"] for o in obs
                )
                if not head_seen_before:
                    continue
                kind = "merged" if pr["base_ref"] == default_branch else "propagated"
                events.append((kind, None, pr["number"], pr["base_ref"],
                               {"from": pr["head_ref"], "to": pr["base_ref"]}, pr["merged_at"]))

    if ident.get("fixed_at"):
        events.append(("fixed", None, None, default_branch, {}, ident["fixed_at"]))
    if ident.get("resolved_at"):
        events.append(("resolved", None, None, default_branch, {}, ident["resolved_at"]))

    for kind, scan_id, pr_number, branch, detail, at in events:
        cur.execute(
            """
            INSERT INTO risk_lifecycle_event
                (repository_id, fingerprint, kind, scan_id, pr_number, branch, detail, at)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (repository_id, fingerprint, kind, scan_id, pr_number, branch,
             json.dumps(detail, default=str), at),
        )
