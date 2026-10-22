"""스캔한 커밋 그대로의 소스 읽기 — 채팅 `read_code` 도구와 risk 즉석 계산이 공유한다.

레포 캐시(repos/<repo_id>, worker/handlers/clone.py)는 기본 브랜치 tip으로 계속
갱신되므로 파일을 그대로 읽으면 "스캔 당시"가 아니라 "지금"의 코드다 — symbol 테이블의
start_line/end_line과 어긋나 엉뚱한 줄을 보여주게 된다. 스캔 worktree(scans/<scan_id>)는
risk_build 후 지워진다. 그래서 캐시 저장소에서 `git show <scan.commit_sha>:<path>`로
그 커밋의 blob을 직접 읽는다(scan.fetch가 그 커밋 객체를 이미 받아 뒀다).

커밋 객체가 없으면(얕은 클론이 갱신되며 사라진 경우 등) 작업 트리 파일로 폴백하고
`stale=True`를 붙인다 — 호출부가 "현재 코드 기준"이라고 밝힐 수 있게.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from worker.handlers.clone import _git, workdir_for

log = logging.getLogger("app.source_reader")

MAX_LINES = 80


@lru_cache(maxsize=256)
def _read_at_commit(repository_id: str, commit_sha: str, path: str) -> tuple[tuple[str, ...], bool] | None:
    root = workdir_for(repository_id)
    if commit_sha:
        try:
            text = _git("-C", str(root), "show", f"{commit_sha}:{path}")
            return tuple(text.splitlines()), False
        except Exception:
            log.debug("git show 실패 — 작업 트리로 폴백: %s@%s", path, commit_sha[:8], exc_info=True)
    try:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return tuple(text.splitlines()), True


def _scan_repo(cur, scan_id: str) -> tuple[str, str] | None:
    cur.execute("SELECT repository_id, commit_sha FROM scan WHERE id=%s", (str(scan_id),))
    row = cur.fetchone()
    if row is None:
        return None
    return str(row["repository_id"]), row["commit_sha"] or ""


def file_reader(cur, scan_id: str):
    """path → 파일 전체 텍스트(없으면 None). risk 엔진의 `source_reader` 계약."""
    repo = _scan_repo(cur, scan_id)
    if repo is None:
        return None

    def reader(path: str) -> str | None:
        got = _read_at_commit(repo[0], repo[1], path)
        return "\n".join(got[0]) if got else None

    return reader


def read_lines(cur, scan_id: str, path: str, start_line: int, end_line: int, *,
               focus_line: int | None = None, max_lines: int = MAX_LINES) -> dict | None:
    """[start_line, end_line] 구간을 줄 번호 접두(`  12| ...`)와 함께 돌려준다.

    구간이 `max_lines`보다 길면 `focus_line`(예: dataflow sink 줄) 중심으로, 없으면
    앞부분을 자른다 — 실제로 보여준 범위를 `start_line/end_line`에 다시 담아 돌려주므로
    답변이 인용하는 줄 번호가 항상 보여준 범위 안에 있다.
    """
    repo = _scan_repo(cur, scan_id)
    if repo is None:
        return None
    got = _read_at_commit(repo[0], repo[1], path)
    if got is None:
        return None
    lines, stale = got
    start = max(1, int(start_line or 1))
    end = min(len(lines), int(end_line or start))
    if end < start:
        return None
    if end - start + 1 > max_lines:
        if focus_line and start <= focus_line <= end:
            start = max(start, focus_line - max_lines // 2)
        end = start + max_lines - 1
    width = len(str(end))
    code = "\n".join(f"{n:>{width}}| {lines[n - 1]}" for n in range(start, end + 1))
    out = {"path": path, "start_line": start, "end_line": end, "code": code}
    if stale:
        out["stale"] = True
    return out


def read_symbol(cur, scan_id: str, symbol_key: str, *, focus_line: int | None = None,
                max_lines: int = MAX_LINES) -> dict | None:
    cur.execute(
        "SELECT path, qualified_name, kind, start_line, end_line FROM symbol WHERE scan_id=%s AND symbol_key=%s",
        (str(scan_id), symbol_key),
    )
    sym = cur.fetchone()
    if sym is None:
        return None
    got = read_lines(cur, scan_id, sym["path"], sym["start_line"], sym["end_line"],
                     focus_line=focus_line, max_lines=max_lines)
    if got is None:
        return None
    return {"qualified_name": sym["qualified_name"], "kind": sym["kind"], "symbol_key": symbol_key, **got}
