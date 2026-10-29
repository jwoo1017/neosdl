"""코드 지도 어댑터 — graphify 없이도 도는 순수 로직(폴백 묶음, 흡수, 링크, 최단 경로)."""

from __future__ import annotations

import builtins

import pytest

from app import graphify_adapter as ga


def _sym(key, path, qn=None, kind="function", line=1, domain=None):
    return {"symbol_key": key, "kind": kind, "qualified_name": qn or key, "path": path,
            "start_line": line, "domain": domain}


def _edge(a, b, t="calls", conf=1.0):
    return {"src_symbol_key": a, "dst_symbol_key": b, "edge_type": t, "resolved": True, "confidence": conf}


SYMBOLS = [
    _sym("o1", "src/order/service.js", "src.order.service.cancelOrder", domain="주문"),
    _sym("o2", "src/order/service.js", "src.order.service.placeOrder", domain="주문"),
    _sym("o3", "src/order/repo.js", "src.order.repo.update", domain="주문"),
    _sym("b1", "src/bot/main.js", "src.bot.main.bootstrap"),
    _sym("b2", "src/bot/main.js", "src.bot.main.run"),
    _sym("b3", "src/bot/fill.js", "src.bot.fill.setFillBotEnabled"),
    _sym("lone", "src/util/x.js", "src.util.x.helper"),
]
EDGES = [_edge("o1", "o3"), _edge("o2", "o3"), _edge("b1", "b2"), _edge("b2", "b3"), _edge("b3", "o2")]


@pytest.fixture
def no_graphify(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "graphify" or name.startswith("graphify."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_short_label():
    assert ga.short_label("src.order.service.cancelOrder") == "cancelOrder"
    assert ga.short_label("Foo::bar") == "bar"
    assert ga.short_label("plain") == "plain"


def test_fallback_groups_by_directory_and_absorbs_small(no_graphify):
    cm = ga.compute_code_map(SYMBOLS, EDGES)
    assert cm["engine"] == "fallback:directory"
    names = {c["name"] for c in cm["communities"]}
    # 주문(3)·봇(3)은 모듈, src/util 의 고립 심볼 1개는 같은 디렉터리 모듈이 없으니 '기타'
    assert ga.OTHER_NAME in names
    assert cm["membership"]["lone"] == ga.OTHER_ID
    assert cm["membership"]["o1"] == cm["membership"]["o3"]
    order = next(c for c in cm["communities"] if c["domain"] == "주문")
    assert order["name"].startswith("주문")
    assert order["size"] == 3


def test_links_count_cross_module_edges_only(no_graphify):
    cm = ga.compute_code_map(SYMBOLS, EDGES)
    m = cm["membership"]
    for link in cm["links"]:
        assert link["source"] != link["target"]
    cross = [e for e in EDGES if m[e["src_symbol_key"]] != m[e["dst_symbol_key"]]]
    assert sum(link["weight"] for link in cm["links"]) == len(cross)


def test_compute_is_deterministic(no_graphify):
    assert ga.compute_code_map(SYMBOLS, EDGES) == ga.compute_code_map(list(reversed(SYMBOLS)), EDGES)


def test_shortest_path_follows_call_direction():
    assert ga.shortest_path(SYMBOLS, EDGES, "b1", "o3") == ["b1", "b2", "b3", "o2", "o3"]


def test_shortest_path_falls_back_to_undirected():
    # o3 → b1 방향 호출은 없지만 연결은 돼 있다
    assert ga.shortest_path(SYMBOLS, EDGES, "o3", "b1") == ["o3", "o2", "b3", "b2", "b1"]


def test_shortest_path_none_when_unknown_or_disconnected():
    assert ga.shortest_path(SYMBOLS, EDGES, "o1", "missing") is None
    assert ga.shortest_path(SYMBOLS, EDGES, "o1", "lone") is None


# ------------------------------------------------------------- 실제 graphify 경로

def _clustered_fixture():
    syms, edges = [], []
    for g in range(3):
        for i in range(8):
            syms.append(_sym(f"g{g}f{i}", f"src/m{g}/f{i % 2}.js", f"src.m{g}.fn{g}_{i}"))
        for i in range(8):  # 모듈 안은 촘촘한 고리, 모듈 사이는 엣지 1개
            edges += [_edge(f"g{g}f{i}", f"g{g}f{(i + 1) % 8}"), _edge(f"g{g}f{i}", f"g{g}f{(i + 3) % 8}")]
    edges.append(_edge("g0f0", "g2f4", conf=0.6))
    return syms, edges


def test_graphify_clusters_modules_and_reports():
    pytest.importorskip("graphify")
    syms, edges = _clustered_fixture()
    cm = ga.compute_code_map(syms, edges)
    assert cm["engine"].startswith("graphify")
    assert sum(c["size"] for c in cm["communities"]) == len(syms)
    # 촘촘한 세 덩어리는 각각 한 모듈로 묶인다
    for g in range(3):
        assert len({cm["membership"][f"g{g}f{i}"] for i in range(8)}) == 1
    assert all(link["source"] != link["target"] for link in cm["links"])
    assert cm["report"]["key_symbols"], "god_nodes 결과가 요약 카드에 실려야 한다"


def test_graphify_result_is_deterministic():
    pytest.importorskip("graphify")
    syms, edges = _clustered_fixture()
    assert ga.compute_code_map(syms, edges) == ga.compute_code_map(syms, edges)


# ------------------------------------------------------------- 근거 서브그래프

def test_query_subgraph_seeds_first_then_neighbors():
    kept = ga.query_subgraph(SYMBOLS, EDGES, ["o1"], depth=1)
    assert kept[0] == "o1"
    assert set(kept) == {"o1", "o3"}


def test_query_subgraph_depth0_keeps_only_seeds_and_ignores_unknown():
    assert ga.query_subgraph(SYMBOLS, EDGES, ["b1", "o3", "missing"], depth=0) == ["b1", "o3"]


def test_query_subgraph_respects_max_nodes():
    assert len(ga.query_subgraph(SYMBOLS, EDGES, ["b2"], depth=2, max_nodes=3)) == 3


def test_query_subgraph_does_not_expand_through_hubs():
    # 허브 하나에 200개가 매달린 그래프 — 허브는 거쳐 가지 않는다
    syms = [_sym("hub", "u.js"), _sym("seed", "a.js"), _sym("near", "a.js")]
    syms += [_sym(f"leaf{i}", "l.js") for i in range(200)]
    edges = [_edge("seed", "hub"), _edge("seed", "near")] + [_edge(f"leaf{i}", "hub") for i in range(200)]
    kept = ga.query_subgraph(syms, edges, ["seed"], depth=2)
    assert "near" in kept and "hub" not in kept
    assert not any(k.startswith("leaf") for k in kept)


# ------------------------------------------------------------- 한국어 추천 질문 / 모듈 이름

def test_korean_questions_from_signals():
    communities = [
        {"id": 0, "name": "주문", "size": 20, "cohesion": 0.05, "hub": "x", "top_dir": "src/order"},
        {"id": 1, "name": "봇", "size": 9, "cohesion": 0.5, "hub": "y", "top_dir": "src/bot"},
        {"id": ga.OTHER_ID, "name": ga.OTHER_NAME, "size": 3, "cohesion": 0.0, "hub": None, "top_dir": None},
    ]
    links = [{"source": 1, "target": 0, "weight": 7}, {"source": 0, "target": ga.OTHER_ID, "weight": 50}]
    report = {"key_symbols": [{"symbol_key": "k", "label": "cancelOrder", "degree": 9}],
              "surprising": [{"source": "placeOrder", "target": "sleep"}]}
    qs = ga.korean_questions(report, communities, links)
    assert qs == [
        "cancelOrder 함수는 어떤 역할을 하고 어디서 호출돼?",
        "봇 모듈은 주문 모듈을 어떻게 사용해?",  # '기타' 로 가는 연결은 질문으로 안 만든다
        "placeOrder 함수가 sleep 함수를 호출하는 이유는 뭐야?",
        "주문 모듈에는 어떤 기능들이 섞여 있어?",
    ]
    assert all("`" not in q for q in qs)


def test_korean_questions_empty_signals():
    assert ga.korean_questions({"key_symbols": [], "surprising": []}, [], []) == []


def test_module_names_prefer_domain_and_disambiguate(no_graphify):
    syms = [_sym(f"a{i}", "src/order/a.js", domain="주문") for i in range(3)]
    syms += [_sym(f"b{i}", "src/order2/b.js", domain="주문") for i in range(3)]
    cm = ga.compute_code_map(syms, [])
    names = sorted(c["name"] for c in cm["communities"])
    assert len(set(names)) == 2 and all(n.startswith("주문 · ") for n in names)


def test_graphify_report_questions_are_korean():
    pytest.importorskip("graphify")
    syms, edges = _clustered_fixture()
    cm = ga.compute_code_map(syms, edges)
    assert cm["report"]["questions"]
    assert all(not q.isascii() for q in cm["report"]["questions"])
    assert cm["engine"].endswith(f"/{ga.REPORT_VERSION}")
