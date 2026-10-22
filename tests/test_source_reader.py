"""app/source_reader.py — 스캔 커밋 기준 소스 읽기(실제 git 저장소, DB 없음)."""

from __future__ import annotations

import subprocess

import pytest

from app import source_reader


class _Cur:
    """scan/symbol 조회만 흉내 내는 가짜 커서."""

    def __init__(self, scan_row, symbol_row=None):
        self._rows = {"scan": scan_row, "symbol": symbol_row}
        self._last = None

    def execute(self, sql, params=None):
        self._last = "symbol" if "FROM symbol" in sql else "scan"

    def fetchone(self):
        return self._rows[self._last]


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repos" / "r1"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "a.js").write_text("\n".join(f"line{i}" for i in range(1, 201)) + "\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "c1")
    sha = _git(root, "rev-parse", "HEAD")
    # 스캔 이후 기본 브랜치가 바뀐 상황 — 작업 트리는 다른 내용이다.
    (root / "a.js").write_text("changed\n" * 200, encoding="utf-8")
    monkeypatch.setattr(source_reader, "workdir_for", lambda repo_id: tmp_path / "repos" / repo_id)
    source_reader._read_at_commit.cache_clear()
    yield sha
    source_reader._read_at_commit.cache_clear()


def test_reads_scan_commit_not_working_tree(repo):
    cur = _Cur({"repository_id": "r1", "commit_sha": repo})
    got = source_reader.read_lines(cur, "s1", "a.js", 10, 12)
    assert got == {"path": "a.js", "start_line": 10, "end_line": 12,
                   "code": "10| line10\n11| line11\n12| line12"}


def test_long_range_is_cut_around_focus_line(repo):
    cur = _Cur({"repository_id": "r1", "commit_sha": repo})
    got = source_reader.read_lines(cur, "s1", "a.js", 1, 200, focus_line=150, max_lines=20)
    assert (got["start_line"], got["end_line"]) == (140, 159)
    assert "150| line150" in got["code"]


def test_falls_back_to_working_tree_and_marks_stale(repo):
    cur = _Cur({"repository_id": "r1", "commit_sha": "0" * 40})
    got = source_reader.read_lines(cur, "s1", "a.js", 1, 1)
    assert got["stale"] is True and got["code"] == "1| changed"


def test_read_symbol_and_missing_file(repo):
    cur = _Cur({"repository_id": "r1", "commit_sha": repo},
               {"path": "a.js", "qualified_name": "f", "kind": "function", "start_line": 3, "end_line": 4})
    got = source_reader.read_symbol(cur, "s1", "k")
    assert got["qualified_name"] == "f" and got["code"] == "3| line3\n4| line4"
    assert source_reader.read_lines(cur, "s1", "nope.js", 1, 2) is None


def test_file_reader_returns_whole_file_at_commit(repo):
    reader = source_reader.file_reader(_Cur({"repository_id": "r1", "commit_sha": repo}), "s1")
    assert reader("a.js").startswith("line1\nline2")
