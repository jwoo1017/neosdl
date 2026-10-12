"""Security Diff DB 통합 (app/security_history.compute_diff).

두 스냅샷의 fingerprint 단위 델타: new/resolved/unchanged + severity/score 델타.
"""

from __future__ import annotations

import json

import pytest

from app.security_history import compute_diff

pytestmark = pytest.mark.integration


def _finding(cur, scan_id, repo_id, *, fp, category, severity, sink):
    cur.execute(
        """
        INSERT INTO security_finding (scan_id, repository_id, kind, category, severity,
                                      sink_symbol_key, sink_line, source_var, path,
                                      evidence_kinds, fingerprint)
        VALUES (%s, %s, 'security', %s, %s, %s, 1, 'req.body.x', %s::jsonb, '{}', %s)
        """,
        (str(scan_id), repo_id, category, severity, sink,
         json.dumps([{"symbol_key": sink, "var": "q", "line": 1}]), fp),
    )


def test_diff_new_resolved_unchanged(db, scan_factory):
    base = scan_factory("sh-diff", ref="refs/heads/main", stats={"security_analyzed": True})
    head = scan_factory("sh-diff", ref="refs/heads/main", stats={"security_analyzed": True})
    with db() as cur:
        cur.execute("SELECT repository_id FROM scan WHERE id=%s", (base,))
        repo_id = str(cur.fetchone()["repository_id"])

        # base: A(critical), B(medium)
        _finding(cur, base, repo_id, fp="fpA", category="sql_injection", severity="critical",
                 sink="py:r:app/a.py#run(any)")
        _finding(cur, base, repo_id, fp="fpB", category="xss", severity="medium",
                 sink="js:r:web/b.js#tpl(any)")
        # head: B(medium), C(high)
        _finding(cur, head, repo_id, fp="fpB", category="xss", severity="medium",
                 sink="js:r:web/b.js#tpl(any)")
        _finding(cur, head, repo_id, fp="fpC", category="command_injection", severity="high",
                 sink="py:r:jobs/c.py#exec(any)")

        diff = compute_diff(cur, base, head)

    assert [f["fingerprint"] for f in diff["new"]] == ["fpC"]
    assert [f["fingerprint"] for f in diff["resolved"]] == ["fpA"]
    assert [f["fingerprint"] for f in diff["unchanged"]] == ["fpB"]

    # base counts: crit 1, med 1 → score 12 ; head: med 1, high 1 → score 7
    assert diff["severity_delta"] == {"critical": -1, "high": 1, "medium": 0, "low": 0}
    assert diff["score_delta"] == 7 - 12

    # 영향 파일: 새로 생긴 C, 사라진 A 의 경로
    assert "jobs/c.py" in diff["affected_paths"]
    assert "app/a.py" in diff["affected_paths"]
    assert "web/b.js" not in diff["affected_paths"]   # 안 바뀐 건 제외


def test_diff_materializes_snapshots(db, scan_factory):
    base = scan_factory("sh-diff2", ref="refs/heads/main", stats={"security_analyzed": True})
    head = scan_factory("sh-diff2", ref="refs/heads/main", stats={"security_analyzed": True})
    with db() as cur:
        cur.execute("SELECT repository_id FROM scan WHERE id=%s", (base,))
        repo_id = str(cur.fetchone()["repository_id"])
        _finding(cur, head, repo_id, fp="fpZ", category="xss", severity="low",
                 sink="js:r:z.js#f(any)")
        compute_diff(cur, base, head)
        cur.execute("SELECT count(*) n FROM security_snapshot WHERE scan_id = ANY(%s)", ([base, head],))
        assert cur.fetchone()["n"] == 2   # 양쪽 다 물질화됨
