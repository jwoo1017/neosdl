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


def test_return_statement_sink_and_commonjs_export_are_seen_end_to_end():
    """`exports.f = (email) => { const sql = '…' + email; return db.query(sql); }` 가 PDG 에 잡히고
    컨트롤러의 req.body 가 파라미터로 이어져 SQL 인젝션으로 나온다(return 안 호출 누락·exports 심볼 누락 회귀)."""
    import sys
    sys.path.insert(0, "tests")
    from test_edge_extract import _build_index
    from worker.handlers.cfg_extract import analyze, build_function_cfgs
    from worker.handlers.edge_extract import extract_edges
    from app.dataflow import find_security_paths_interprocedural

    files = {
        "src/repo.js": b"exports.findByEmail = async (email) => {\n  const sql = 'SELECT * FROM u WHERE e=' + email;\n  return db.query(sql);\n};\n",
        "src/ctrl.js": b"const repo = require('./repo');\nexports.login = async (req, res) => {\n  return repo.findByEmail(req.body.email);\n};\n",
    }
    index, sources = _build_index(files)
    data_edges, calls, links, functions = [], [], [], {}
    for path, src in sources.items():
        for key, b in build_function_cfgs("javascript", ".js", path, src, index).items():
            functions[key] = b.params
            de, _ce = analyze(b)
            for var, def_line, kind, _blk, use_line, def_text in de:
                data_edges.append({"symbol_key": key, "var": var, "def_line": def_line, "evidence_kind": kind,
                                   "use_line": use_line, "def_text": def_text})
            for _bid, cs in b.calls.items():
                for line, target, av, at, al in cs:
                    calls.append({"symbol_key": key, "line": line, "target_text": target, "arg_vars": av,
                                  "arg_texts": at, "arg_lines": al})
        for e in extract_edges("javascript", ".js", path, src, index):
            if e["resolved"] and e["edge_type"] == "calls":
                links.append({"caller_symbol_key": e["src_symbol_key"], "call_line": e["call_line"],
                              "callee_symbol_key": e["dst_symbol_key"]})
    found = find_security_paths_interprocedural(data_edges, calls, links, functions)
    assert any(f["category"] == "sql_injection" and f["sink_line"] == 3 and "repo.js" in f["sink_symbol_key"]
               for f in found), found
