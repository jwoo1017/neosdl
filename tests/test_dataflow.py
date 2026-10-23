"""app/dataflow.py — source→sink 탐색의 인자 위치 판정(DB 없음)."""

from __future__ import annotations

from app.dataflow import find_security_paths_interprocedural


def _call(args, texts, line=10, target="pool.execute"):
    return {"symbol_key": "f", "line": line, "target_text": target, "arg_vars": args,
            "arg_texts": texts, "arg_lines": [line] * len(args)}


def _tainted_param_edges(var="reason"):
    # `const { reason } = req.body` 다음 줄에서 sink 호출 인자로 사용
    return [{"symbol_key": "f", "var": var, "def_line": 5, "def_block": 0, "use_line": 10,
             "evidence_kind": "ASSIGNMENT", "def_text": "const { reason } = req.body"}]


def test_tainted_value_in_bind_parameters_is_not_sql_injection():
    """`pool.execute(\`UPDATE ... SET reason = ?\`, [reason, id])` — 바인딩된 값은 안전하다."""
    calls = [_call(["HISTORY_TABLE", "reason"], ["`UPDATE ${HISTORY_TABLE} SET r = ?`", "[reason, id]"])]
    assert find_security_paths_interprocedural(_tainted_param_edges(), calls, [], {}) == []


def test_tainted_value_in_query_text_is_sql_injection():
    calls = [_call(["reason"], ["`UPDATE t SET r = '${reason}'`"])]
    found = find_security_paths_interprocedural(_tainted_param_edges(), calls, [], {})
    assert [f["category"] for f in found] == ["sql_injection"]


def test_direct_source_argument_only_counts_in_query_position():
    safe = [_call(["sql", "?"], ["sql", "[req.body.id]"])]
    assert find_security_paths_interprocedural([], safe, [], {}) == []
    unsafe = [_call(["?"], ["'SELECT ' + req.query.q"])]
    assert [f["category"] for f in find_security_paths_interprocedural([], unsafe, [], {})] == ["sql_injection"]


def test_other_sinks_still_check_every_argument():
    calls = [_call(["opts", "reason"], ["opts", "reason"], target="child_process.exec")]
    found = find_security_paths_interprocedural(_tainted_param_edges(), calls, [], {})
    assert [f["category"] for f in found] == ["command_injection"]
