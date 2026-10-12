"""Risk Identity 생애주기 DB 통합 (app/risk_identity.py) — Security History Phase 2.

지문 하나 = 여러 커밋/브랜치의 하나의 위험. Introduced/Propagated/Merged/Fixed/Resolved.
Postgres(sql/001..023) 필요.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.risk_identity import ensure_risk_identities
from app.security_snapshot import ensure_security_snapshot

pytestmark = pytest.mark.integration

_T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _mk_scan(db, scan_factory, *, branch, day, fps, suffix="ident"):
    """scan + git_commit(committed_at 제어) + security_finding + snapshot 을 만든다."""
    sha = f"{day:040x}"
    sid = scan_factory(suffix, ref=f"refs/heads/{branch}", commit_sha=sha,
                       stats={"security_analyzed": True})
    with db() as cur:
        cur.execute("SELECT repository_id, default_branch FROM scan s "
                    "JOIN repository r ON r.id = s.repository_id WHERE s.id = %s", (sid,))
        row = cur.fetchone()
        repo_id = str(row["repository_id"])
        cur.execute(
            "INSERT INTO git_commit (repository_id, commit_sha, committed_at, message) "
            "VALUES (%s, %s, %s, 'x') ON CONFLICT DO NOTHING",
            (repo_id, sha, _T0 + timedelta(days=day)),
        )
        for fp, sev in fps.items():
            cur.execute(
                """
                INSERT INTO security_finding (scan_id, repository_id, kind, category, severity,
                                              sink_symbol_key, sink_line, source_var, path,
                                              evidence_kinds, fingerprint)
                VALUES (%s, %s, 'security', 'sql_injection', %s, %s, 1, 'req.body.x',
                        %s::jsonb, '{}', %s)
                """,
                (sid, repo_id, sev, f"py:r:app/{fp}.py#run(any)",
                 json.dumps([{"symbol_key": f"py:r:app/{fp}.py#run(any)", "var": "q", "line": 1}]), fp),
            )
        ensure_security_snapshot(cur, sid)
    return sid, repo_id, row["default_branch"]


def _identities(db, repo_id):
    with db() as cur:
        cur.execute("SELECT fingerprint, status, worst_severity, first_seen_branch, first_seen_commit, "
                    "fixed_at, resolved_at FROM risk_identity WHERE repository_id = %s", (repo_id,))
        return {r["fingerprint"]: r for r in cur.fetchall()}


def _events(db, repo_id, fp):
    with db() as cur:
        cur.execute("SELECT kind, branch, pr_number, at FROM risk_lifecycle_event "
                    "WHERE repository_id = %s AND fingerprint = %s ORDER BY at, id", (repo_id, fp))
        return cur.fetchall()


def test_identity_first_seen_is_earliest(db, scan_factory):
    # day 3 스캔을 먼저, day 1 스캔을 나중에 처리해도 first_seen 은 day 1
    s3, repo, _ = _mk_scan(db, scan_factory, branch="main", day=3, fps={"aa": "high"}, suffix="id-fs")
    with db() as cur:
        ensure_risk_identities(cur, s3, force=True)
    s1, _, _ = _mk_scan(db, scan_factory, branch="main", day=1, fps={"aa": "critical"}, suffix="id-fs")
    with db() as cur:
        ensure_risk_identities(cur, s1, force=True)

    ident = _identities(db, repo)["aa"]
    assert ident["first_seen_commit"] == f"{1:040x}"
    assert ident["worst_severity"] == "critical"           # 관측된 것 중 최악
    assert ident["status"] == "active"


def test_fixed_and_resolved_on_default_branch(db, scan_factory):
    s1, repo, default = _mk_scan(db, scan_factory, branch="main", day=1,
                                 fps={"bb": "critical", "cc": "low"}, suffix="id-fix")
    with db() as cur:
        ensure_risk_identities(cur, s1, force=True)

    # day 5: bb 사라짐, cc 유지
    s5, _, _ = _mk_scan(db, scan_factory, branch="main", day=5, fps={"cc": "low"}, suffix="id-fix")
    with db() as cur:
        ensure_risk_identities(cur, s5, force=True)

    ids = _identities(db, repo)
    assert ids["bb"]["status"] == "fixed"
    assert ids["bb"]["fixed_at"] is not None
    assert ids["cc"]["status"] == "active"
    assert any(e["kind"] == "fixed" for e in _events(db, repo, "bb"))

    # day 6,7,8: 기본 브랜치 스냅샷 3개 더 (bb 계속 부재) → resolved
    for d in (6, 7, 8):
        sd, _, _ = _mk_scan(db, scan_factory, branch="main", day=d, fps={"cc": "low"}, suffix="id-fix")
        with db() as cur:
            ensure_risk_identities(cur, sd, force=True)

    assert _identities(db, repo)["bb"]["status"] == "resolved"
    assert any(e["kind"] == "resolved" for e in _events(db, repo, "bb"))


def test_reappear_reactivates(db, scan_factory):
    s1, repo, _ = _mk_scan(db, scan_factory, branch="main", day=1, fps={"dd": "high"}, suffix="id-re")
    s2, _, _ = _mk_scan(db, scan_factory, branch="main", day=2, fps={}, suffix="id-re")   # dd 사라짐
    s3, _, _ = _mk_scan(db, scan_factory, branch="main", day=3, fps={"dd": "high"}, suffix="id-re")  # 다시
    with db() as cur:
        for s in (s1, s2, s3):
            ensure_risk_identities(cur, s, force=True)
    ident = _identities(db, repo)["dd"]
    assert ident["status"] == "active"
    assert ident["fixed_at"] is None


def test_pr_merge_propagation(db, scan_factory):
    # feature/pay 에서 관측 → PR #10 merge (feature/pay → main) → main 에서 관측
    sf, repo, default = _mk_scan(db, scan_factory, branch="feature/pay", day=2,
                                 fps={"ee": "critical"}, suffix="id-prop")
    with db() as cur:
        cur.execute(
            """
            INSERT INTO pull_request (repository_id, number, title, state, base_ref, head_ref,
                                      head_sha, merged, merged_at, updated_at)
            VALUES (%s, 10, 'pay', 'closed', %s, 'feature/pay', %s, true, %s, now())
            ON CONFLICT (repository_id, number) DO NOTHING
            """,
            (repo, default, f"{2:040x}", _T0 + timedelta(days=3)),
        )
    sm, _, _ = _mk_scan(db, scan_factory, branch=default, day=4, fps={"ee": "critical"}, suffix="id-prop")
    with db() as cur:
        ensure_risk_identities(cur, sf, force=True)
        ensure_risk_identities(cur, sm, force=True)

    ids = _identities(db, repo)
    assert ids["ee"]["first_seen_branch"] == "feature/pay"
    kinds = [e["kind"] for e in _events(db, repo, "ee")]
    assert "introduced" in kinds
    assert "merged" in kinds        # base_ref == default → merged (아니면 propagated)
    merged_ev = next(e for e in _events(db, repo, "ee") if e["kind"] == "merged")
    assert merged_ev["pr_number"] == 10
