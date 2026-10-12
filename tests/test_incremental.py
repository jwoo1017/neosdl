"""증분 스캔 순수 로직 — DB/GitHub 없이 (app/incremental.py).

`compute_changed_paths`(GitHub compare 응답 해석)와 `affected_symbols`(재분석 대상
집합) 검증. compare/DB 는 fake 로 대체.
"""

from __future__ import annotations

import pytest

from app import incremental


class _Cur:
    """SQL 텍스트로 응답을 고르는 fake 커서."""

    def __init__(self, responses: dict[str, list]):
        self._responses = responses
        self._last: list = []

    def execute(self, sql, params=None):
        for needle, rows in self._responses.items():
            if needle in sql:
                self._last = rows
                return
        self._last = []

    def fetchall(self):
        return self._last

    def fetchone(self):
        return self._last[0] if self._last else None


# --------------------------------------------------------------------- compute_changed_paths

def _patch_compare(monkeypatch, payload):
    monkeypatch.setattr("app.repo_queries.repo_github_info",
                        lambda cur, rid: {"github_installation_id": 1, "namespace": "o", "name": "r"})
    monkeypatch.setattr("app.github_app.compare_commits",
                        lambda inst, ns, name, base, head: payload)


def test_changed_paths_classifies_status(monkeypatch):
    _patch_compare(monkeypatch, {"status": "ahead", "files": [
        {"filename": "app/a.py", "status": "modified"},
        {"filename": "app/b.py", "status": "added"},
        {"filename": "app/c.py", "status": "removed"},
        {"filename": "app/new.py", "status": "renamed", "previous_filename": "app/old.py"},
        {"filename": "README.md", "status": "modified"},      # 비코드 → 무시
    ]})
    out = incremental.compute_changed_paths(_Cur({}), "repo", "base", "head")
    assert set(out["changed"]) == {"app/a.py", "app/b.py", "app/new.py"}
    assert set(out["removed"]) == {"app/c.py", "app/old.py"}


def test_changed_paths_identical_short_circuits(monkeypatch):
    _patch_compare(monkeypatch, {"status": "identical", "files": []})
    assert incremental.compute_changed_paths(_Cur({}), "r", "base", "head") == {"changed": [], "removed": []}
    # 같은 sha 면 API 호출도 없이 빈 결과
    assert incremental.compute_changed_paths(_Cur({}), "r", "x", "x") == {"changed": [], "removed": []}


def test_changed_paths_diverged_falls_back(monkeypatch):
    _patch_compare(monkeypatch, {"status": "diverged", "files": []})
    assert incremental.compute_changed_paths(_Cur({}), "r", "b", "h") is None


def test_changed_paths_too_many_files_falls_back(monkeypatch):
    _patch_compare(monkeypatch, {"status": "ahead",
                                 "files": [{"filename": f"a/{i}.py", "status": "modified"} for i in range(300)]})
    assert incremental.compute_changed_paths(_Cur({}), "r", "b", "h") is None


def test_changed_paths_api_error_falls_back(monkeypatch):
    monkeypatch.setattr("app.repo_queries.repo_github_info",
                        lambda cur, rid: {"github_installation_id": 1, "namespace": "o", "name": "r"})
    def boom(*a, **k):
        raise RuntimeError("gitlab repo / api down")
    monkeypatch.setattr("app.github_app.compare_commits", boom)
    assert incremental.compute_changed_paths(_Cur({}), "r", "b", "h") is None


def test_changed_paths_noncode_only_yields_empty(monkeypatch):
    _patch_compare(monkeypatch, {"status": "ahead", "files": [
        {"filename": "docs/x.md", "status": "modified"},
        {"filename": "package.json", "status": "modified"},
    ]})
    out = incremental.compute_changed_paths(_Cur({}), "r", "b", "h")
    assert out == {"changed": [], "removed": []}


# --------------------------------------------------------------------- affected_symbols

def test_affected_symbols_union():
    cur = _Cur({
        "path = ANY(%s) AND kind IN": [{"symbol_key": "py:r:app/pricing.py#calc()"}],
        "dst_symbol_key = ANY(%s)": [{"src_symbol_key": "py:r:app/order.py#checkout()"}],
        "FROM security_finding sf": [
            {"sink_symbol_key": "py:r:app/db.py#run()", "source_symbol_key": None,
             "path": [{"symbol_key": "py:r:app/mid.py#pass()"}]},
        ],
    })
    got = incremental.affected_symbols(cur, "scan", ["app/pricing.py"])
    assert got == {
        "py:r:app/pricing.py#calc()",       # 변경 심볼
        "py:r:app/order.py#checkout()",      # 1-hop caller
        "py:r:app/db.py#run()",             # finding sink
        "py:r:app/mid.py#pass()",          # finding hop
    }


def test_affected_symbols_empty_when_no_changes():
    assert incremental.affected_symbols(_Cur({}), "scan", []) == set()


# --------------------------------------------------------------------- read_incremental_context

def test_read_incremental_context():
    cur = _Cur({"SELECT parent_scan_id, stats FROM scan": [
        {"parent_scan_id": "P", "stats": {"incremental": True, "changed_paths": ["a.py"]}},
    ]})
    assert incremental.read_incremental_context(cur, "s") == ("P", ["a.py"])

    cur2 = _Cur({"SELECT parent_scan_id, stats FROM scan": [{"parent_scan_id": None, "stats": {}}]})
    assert incremental.read_incremental_context(cur2, "s") == (None, None)

    # parent 는 있는데 incremental 플래그가 없으면(웹훅 아닌 수동 등) 전체
    cur3 = _Cur({"SELECT parent_scan_id, stats FROM scan": [{"parent_scan_id": "P", "stats": {}}]})
    assert incremental.read_incremental_context(cur3, "s") == (None, None)
