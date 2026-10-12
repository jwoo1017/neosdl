"""WBS 1.3 — 호출/의존 관계 추출 단위 테스트.

DB 없이 순수 로직만 검증한다. build_scan_index() 가 요구하는 커서 인터페이스
(execute/fetchall) 만 흉내 낸 가짜 커서로 symbol 테이블 조회를 대체한다.
"""

from __future__ import annotations

from pathlib import Path

from worker.handlers.ast_extract import extract_symbols
from worker.handlers.edge_extract import build_scan_index, extract_edges

REPO_ID = "test-repo"
LANG_BY_SUFFIX = {".js": "javascript", ".py": "python", ".java": "java"}


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows


def _symbol_key(lang: str, path: str, qname: str, params: list[str]) -> str:
    """symbol_key_of() 와 같은 규격 — 테스트 전용 재현. 프로덕션 코드는 절대
    이렇게 재구현하지 않는다 (DB generated column 이 유일한 정의)."""
    return f"{lang}:{REPO_ID}:{path}#{qname}({','.join(params)})"


def _build_index(files: dict[str, bytes]):
    sources, rows = {}, []
    for path, src in files.items():
        lang = LANG_BY_SUFFIX[Path(path).suffix]
        sources[path] = src
        for sym in extract_symbols(lang, Path(path).suffix, path, src):
            key = _symbol_key(lang, path, sym["qualified_name"], sym["param_types"])
            rows.append({"path": path, "lang": lang, "kind": sym["kind"],
                         "qualified_name": sym["qualified_name"],
                         "param_types": sym["param_types"], "symbol_key": key})
    index = build_scan_index(_FakeCursor(rows), "scan-1", sources)
    return index, sources


def _edges_by_kind(edges, kind_label):
    return [e for e in edges if e["evidence"].get("kind") == kind_label]


# ----------------------------------------------------------------- fixtures
JS_FILES = {
    "src/util.js": b"""
export function helper(x) { return x + 1; }
export class Tool {
    run() { return this.step(); }
    step() { return 1; }
}
""",
    "src/main.js": b"""
import { helper, Tool } from './util';
function localFn() { return 1; }
function main() {
    localFn();
    helper(5);
    unknownExternal.doThing();
}
""",
}

PY_FILES = {
    "app/mathutils.py": b"def add(a, b):\n    return a + b\n",
    "app/main.py": b"""
from .mathutils import add

class Runner:
    def run(self):
        return self.helper()
    def helper(self):
        return add(1, 2)
""",
}


# --------------------------------------------------------------------- tests
def test_same_file_call_resolved():
    index, sources = _build_index(JS_FILES)
    edges = extract_edges("javascript", ".js", "src/main.js", sources["src/main.js"], index)
    same_file = [e for e in edges if e["evidence"].get("kind") == "same_file"]
    assert len(same_file) == 1
    assert same_file[0]["resolved"] is True
    assert same_file[0]["confidence"] == 1.0
    assert "localFn" in same_file[0]["dst_symbol_key"]


def test_cross_file_import_call_resolved():
    index, sources = _build_index(JS_FILES)
    edges = extract_edges("javascript", ".js", "src/main.js", sources["src/main.js"], index)
    import_calls = _edges_by_kind(edges, "import_bare")
    assert len(import_calls) == 1
    assert import_calls[0]["resolved"] is True
    assert "helper" in import_calls[0]["dst_symbol_key"]

    module_imports = [e for e in edges if e["edge_type"] == "imports" and e["resolved"]]
    assert len(module_imports) == 1
    assert "util.js" in module_imports[0]["dst_symbol_key"]


def test_unresolved_external_call_preserved():
    index, sources = _build_index(JS_FILES)
    edges = extract_edges("javascript", ".js", "src/main.js", sources["src/main.js"], index)
    unresolved = [e for e in edges if not e["resolved"] and e["evidence"].get("kind") == "unknown_receiver"]
    assert len(unresolved) == 1
    assert unresolved[0]["dst_symbol_key"] is None
    assert unresolved[0]["dst_hint"] == "unknownExternal.doThing"
    assert 0.0 <= unresolved[0]["confidence"] <= 1.0


def test_self_call_resolved():
    index, sources = _build_index(JS_FILES)
    edges = extract_edges("javascript", ".js", "src/util.js", sources["src/util.js"], index)
    self_calls = _edges_by_kind(edges, "self_call")
    assert len(self_calls) == 1
    assert self_calls[0]["resolved"] is True
    assert self_calls[0]["confidence"] == 0.95
    assert "step" in self_calls[0]["dst_symbol_key"]


def test_python_cross_file_relative_import_resolved():
    index, sources = _build_index(PY_FILES)
    edges = extract_edges("python", ".py", "app/main.py", sources["app/main.py"], index)

    module_import = [e for e in edges if e["edge_type"] == "imports" and e["resolved"]]
    assert len(module_import) == 1
    assert "mathutils.py" in module_import[0]["dst_symbol_key"]

    self_call = _edges_by_kind(edges, "self_call")
    assert len(self_call) == 1 and self_call[0]["resolved"] is True

    import_call = _edges_by_kind(edges, "import_bare")
    assert len(import_call) == 1
    assert "add" in import_call[0]["dst_symbol_key"]


def test_scope_tracking_matches_ast_extract_symbol_keys():
    """edge_extract 의 스코프 추적이 ast_extract 가 만든 심볼과 정확히 같은
    지점을 가리키는지 — 어긋나면 src_symbol_key 조회가 전부 실패한다."""
    index, sources = _build_index(JS_FILES)
    for path, src in sources.items():
        lang = LANG_BY_SUFFIX[Path(path).suffix]
        for sym in extract_symbols(lang, Path(path).suffix, path, src):
            if sym["kind"] in ("function", "method"):
                key = (path, sym["qualified_name"], tuple(sym["param_types"]))
                assert key in index.symbol_key_exact, f"{key} missing from index"
