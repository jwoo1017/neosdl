"""Security Snapshot 물질화 DB 통합 (app/security_snapshot.py).

scan(=커밋 1개) 하나의 taint-finding 롤업이 실제 스키마에서 계산·멱등·fingerprint
backfill 되는지. Postgres(sql/001..022) 필요.
"""

from __future__ import annotations

import json

import pytest

from app.security_snapshot import ensure_security_snapshot

pytestmark = pytest.mark.integration


def _insert_finding(cur, scan_id, repo_id, *, category, severity, sink, source_var,
                    path, evidence_kinds, fingerprint=None):
    cur.execute(
        """
        INSERT INTO security_finding (scan_id, repository_id, kind, category, severity,
                                      sink_symbol_key, sink_line, source_symbol_key, source_var,
                                      path, evidence_kinds, fingerprint)
        VALUES (%s, %s, 'security', %s, %s, %s, 1, NULL, %s, %s::jsonb, %s, %s)
        """,
        (str(scan_id), repo_id, category, severity, sink, source_var,
         json.dumps(path), evidence_kinds, fingerprint),
    )


def test_snapshot_counts_score_branch(db, scan_factory):
    sid = scan_factory("sh-snap", ref="refs/heads/main", stats={"security_analyzed": True})
    with db() as cur:
        cur.execute("SELECT repository_id FROM scan WHERE id=%s", (sid,))
        repo_id = str(cur.fetchone()["repository_id"])
        _insert_finding(cur, sid, repo_id, category="sql_injection", severity="critical",
                        sink="py:r:app/db.py#run(any)", source_var="req.body.id",
                        path=[{"symbol_key": "py:r:app/db.py#run(any)", "var": "q", "line": 5}],
                        evidence_kinds=["PARAMETER", "DIRECT_ASSIGNMENT"])
        _insert_finding(cur, sid, repo_id, category="xss", severity="high",
                        sink="js:r:web/t.js#tpl(any)", source_var="req.query.q",
                        path=[], evidence_kinds=["DIRECT_ARGUMENT"])
        _insert_finding(cur, sid, repo_id, category="command_injection", severity="low",
                        sink="py:r:jobs/x.py#run(any)", source_var="input()",
                        path=[], evidence_kinds=[])

        ensure_security_snapshot(cur, sid)

        cur.execute("SELECT * FROM security_snapshot WHERE scan_id=%s", (sid,))
        snap = cur.fetchone()
        cur.execute("SELECT stats FROM scan WHERE id=%s", (sid,))
        stats = cur.fetchone()["stats"]
        cur.execute("SELECT count(*) n, count(fingerprint) fp, count(DISTINCT fingerprint) d "
                    "FROM security_finding WHERE scan_id=%s", (sid,))
        fp = cur.fetchone()

    assert snap["n_critical"] == 1 and snap["n_high"] == 1 and snap["n_low"] == 1
    assert snap["n_medium"] == 0
    assert snap["n_findings"] == 3
    assert float(snap["score"]) == 10 + 5 + 1        # synthetic_score
    assert snap["branch"] == "main"
    assert snap["n_evidence"] == 3                    # 2 + 1 + 0
    assert stats.get("snapshot_built") is True
    # fingerprint 3개 모두 backfill, 서로 다름
    assert fp["n"] == 3 and fp["fp"] == 3 and fp["d"] == 3


def test_snapshot_excludes_whitelisted(db, scan_factory):
    sid = scan_factory("sh-wl", ref="refs/heads/main", stats={"security_analyzed": True})
    with db() as cur:
        cur.execute("SELECT repository_id FROM scan WHERE id=%s", (sid,))
        repo_id = str(cur.fetchone()["repository_id"])
        _insert_finding(cur, sid, repo_id, category="sql_injection", severity="critical",
                        sink="py:r:app/db.py#run(any)", source_var="req.body.id", path=[],
                        evidence_kinds=[])
        _insert_finding(cur, sid, repo_id, category="xss", severity="high",
                        sink="js:r:web/t.js#tpl(any)", source_var="req.query.q", path=[],
                        evidence_kinds=[])
        # xss 카테고리 전체를 whitelist (project_id 는 FK 상 필요할 뿐 — 매칭은 repo+category)
        cur.execute("SELECT id FROM project LIMIT 1")
        prow = cur.fetchone()
        if prow is None:
            pytest.skip("project 없음 — whitelist FK 만족 불가")
        cur.execute(
            "INSERT INTO finding_whitelist (project_id, repository_id, category, reason) VALUES (%s, %s, 'xss', 'test')",
            (prow["id"], repo_id),
        )
        ensure_security_snapshot(cur, sid)
        cur.execute("SELECT n_critical, n_high, score FROM security_snapshot WHERE scan_id=%s", (sid,))
        snap = cur.fetchone()
        cur.execute("DELETE FROM finding_whitelist WHERE repository_id=%s", (repo_id,))
    assert snap["n_critical"] == 1
    assert snap["n_high"] == 0            # whitelisted 제외
    assert float(snap["score"]) == 10     # critical 만


def test_snapshot_idempotent(db, scan_factory):
    sid = scan_factory("sh-snap2", ref="refs/heads/develop", stats={"security_analyzed": True})
    with db() as cur:
        cur.execute("SELECT repository_id FROM scan WHERE id=%s", (sid,))
        repo_id = str(cur.fetchone()["repository_id"])
        _insert_finding(cur, sid, repo_id, category="xss", severity="medium",
                        sink="js:r:a.js#f(any)", source_var="req.body.x", path=[],
                        evidence_kinds=[])
        ensure_security_snapshot(cur, sid)
        ensure_security_snapshot(cur, sid)   # 두 번째 — no-op
        cur.execute("SELECT count(*) n FROM security_snapshot WHERE scan_id=%s", (sid,))
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT branch, n_medium FROM security_snapshot WHERE scan_id=%s", (sid,))
        r = cur.fetchone()
        assert r["branch"] == "develop" and r["n_medium"] == 1
