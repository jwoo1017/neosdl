"""app/context_budget.py — 근거 슬림화·예산 절단 단위 테스트(DB/LLM 없음)."""

from __future__ import annotations

import json

from app import context_budget as cb


def test_slim_item_drops_internal_keys_and_shortens():
    item = {"symbol_key": "javascript:repo:a.js#f()", "qualified_name": "f", "path": "a.js",
            "summary": "s" * 1000, "similarity": 0.123456, "empty": "", "none": None}
    slim = cb.slim_item(item)
    assert "symbol_key" not in slim and "empty" not in slim and "none" not in slim
    assert slim["summary"].endswith("…") and len(slim["summary"]) <= 301
    assert slim["similarity"] == 0.123


def test_slim_item_compresses_long_chains_but_keeps_code():
    chain = [{"path": f"f{i}.js", "line": i} for i in range(10)]
    slim = cb.slim_item({"path": chain, "code": "x" * 3000})
    assert len(slim["path"]) == 5 and "생략" in slim["path"][2]
    assert slim["code"] == "x" * 3000  # 코드는 4000자까지 그대로


def test_pack_evidence_respects_budget_and_priority():
    results = {
        "codebase_structure": [{"domain": f"d{i}", "file_count": i} for i in range(50)],
        "read_code": [{"path": "a.js", "code": "c" * 400}],
        "semantic_search": [{"qualified_name": f"f{i}", "path": f"p{i}.js"} for i in range(30)],
    }
    packed, truncated = cb.pack_evidence(results, budget_tokens=600)
    assert cb.estimate_tokens(json.dumps(packed, ensure_ascii=False)) <= 600
    assert list(packed)[0] == "read_code"  # 실제 코드가 최우선
    assert truncated.get("codebase_structure")  # 낮은 우선순위가 먼저 잘린다
    assert "생략" in cb.truncation_note(truncated)


def test_pack_evidence_skips_oversized_item_but_keeps_smaller_ones():
    results = {"read_code": [{"code": "c" * 3900}], "semantic_search": [{"qualified_name": "f"}]}
    packed, truncated = cb.pack_evidence(results, budget_tokens=300)
    assert "read_code" not in packed and truncated == {"read_code": 1}
    assert packed["semantic_search"] == [{"qualified_name": "f"}]


def test_pack_evidence_keeps_non_list_values():
    packed, _ = cb.pack_evidence({"project_info": {"name": "p"}}, budget_tokens=100)
    assert packed == {"project_info": {"name": "p"}}
