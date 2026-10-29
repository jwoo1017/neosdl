"""채팅 근거 → 흐름 탭 데이터(app/code_flow.py)."""

from app.code_flow import MAX_FLOWS, flows_from_evidence


def _h(key, qn, var=None, line=1):
    return {"symbol_key": key, "qualified_name": qn, "path": f"src/{key}.js", "line": line, "var": var}


def test_taint_flow_collapses_same_function_hops():
    ev = {"dataflow_security": [{
        "category": "sql_injection", "sink_call": "db.query", "sink_line": 42,
        "path": [_h("a", "routes.order.cancel", "req", 3), _h("a", "routes.order.cancel", "id", 5),
                 _h("b", "service.order.cancelOrder", "id", 10), _h("c", "repo.order.update", "sql", 40)],
    }]}
    [flow] = flows_from_evidence(ev)
    assert flow["kind"] == "taint"
    assert [h["label"] for h in flow["hops"]] == ["cancel", "cancelOrder", "update"]
    assert flow["hops"][0]["var"] == "req"
    assert flow["sink"] == "db.query"


def test_call_graph_flows_longest_first_and_deduped():
    short = {"relation": "caller", "symbol_key": "b", "chain_hops": [_h("b", "x.b"), _h("c", "x.c")]}
    long = {"relation": "caller", "symbol_key": "a",
            "chain_hops": [_h("a", "x.a"), _h("b", "x.b"), _h("c", "x.c")]}
    flows = flows_from_evidence({"call_graph": [short, long, dict(long)]})
    assert [len(f["hops"]) for f in flows] == [3, 2]
    assert flows[0]["title"].endswith("a → c")


def test_single_function_and_old_items_are_skipped():
    ev = {"call_graph": [{"relation": "target", "symbol_key": "a"},  # chain_hops 없는 예전 항목
                         {"relation": "caller", "chain_hops": [_h("a", "x.a")]}],
          "dataflow_security": [{"path": [_h("a", "x.a", "v"), _h("a", "x.a", "w")]}]}
    assert flows_from_evidence(ev) == []
    assert flows_from_evidence(None) == []


def test_max_flows():
    items = [{"relation": "caller", "chain_hops": [_h(f"a{i}", f"x.a{i}"), _h("z", "x.z")]} for i in range(6)]
    assert len(flows_from_evidence({"call_graph": items})) == MAX_FLOWS
