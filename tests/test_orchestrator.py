"""app/orchestrator.py의 멀티스텝 에이전틱 루프(run_agentic) 단위 테스트.

DB/LLM 없이 순수 로직만 검증한다. `ai_providers.generate`는 미리 정해둔 JSON
응답 시퀀스를 순서대로 돌려주는 가짜로 대체하고, `_execute_tool`은 호출
기록만 남기는 가짜로 대체한다 — 루프의 종료 조건(정상 finish/max_steps/
중복 호출 방지)과 tool_results 누적만 검증 대상이다.
"""

from __future__ import annotations

import json

import pytest

from app import llm_schemas, orchestrator


def _fake_generate(responses):
    calls = []

    def generate(model, prompt, *, json_mode=False, response_schema=None,
                 project_id=None, purpose="qa", scan_id=None, system=None):
        calls.append(prompt)
        idx = len(calls) - 1
        if idx >= len(responses):
            raise AssertionError("에이전틱 루프가 예상보다 더 많이 LLM을 호출했다")
        return json.dumps(responses[idx])

    return generate, calls


def test_run_agentic_calls_tools_then_finishes(monkeypatch):
    responses = [
        {"action": "call_tool", "tool": "semantic_search", "params": {"q": "로그인"}},
        {"action": "call_tool", "tool": "dataflow_security", "params": {"keywords": ["sql"]}},
        {"action": "finish"},
    ]
    generate, calls = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)

    executed = []

    def fake_execute_tool(cur, ctx, name, params, question):
        executed.append((name, params))
        return [{"dummy": name}]

    monkeypatch.setattr(orchestrator, "_execute_tool", fake_execute_tool)

    used, tool_results = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="로그인 처리하는 함수 어디 있어?",
        model="qwen2.5-coder",
    )

    assert executed == [
        ("semantic_search", {"q": "로그인"}),
        ("dataflow_security", {"keywords": ["sql"]}),
    ]
    assert used == [
        {"name": "semantic_search", "params": {"q": "로그인"}},
        {"name": "dataflow_security", "params": {"keywords": ["sql"]}},
    ]
    assert tool_results["semantic_search"] == [{"dummy": "semantic_search"}]
    assert tool_results["dataflow_security"] == [{"dummy": "dataflow_security"}]
    assert len(calls) == 3  # finish까지 포함해 3번만 LLM 호출


def test_run_agentic_stops_at_max_steps(monkeypatch):
    """LLM이 계속 call_tool만 내놓아도(매번 다른 params라 dedupe에 안 걸려도)
    max_steps에서 멈춘다."""
    call_n = {"n": 0}

    def generate(model, prompt, *, json_mode=False, response_schema=None,
                 project_id=None, purpose="qa", scan_id=None, system=None):
        call_n["n"] += 1
        return json.dumps({"action": "call_tool", "tool": "semantic_search", "params": {"q": f"질의{call_n['n']}"}})

    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    monkeypatch.setattr(orchestrator, "_execute_tool", lambda cur, ctx, name, params, question: [{"ok": True}])

    used, _ = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="아무 질문",
        model="qwen2.5-coder", max_steps=2,
    )

    assert len(used) == 2


def test_run_agentic_stops_on_repeated_identical_call(monkeypatch):
    def generate(model, prompt, *, json_mode=False, response_schema=None,
                 project_id=None, purpose="qa", scan_id=None, system=None):
        return json.dumps({"action": "call_tool", "tool": "semantic_search", "params": {"q": "같은 질의"}})

    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)

    call_count = {"n": 0}

    def fake_execute_tool(cur, ctx, name, params, question):
        call_count["n"] += 1
        return [{"n": call_count["n"]}]

    monkeypatch.setattr(orchestrator, "_execute_tool", fake_execute_tool)

    used, tool_results = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="아무 질문",
        model="qwen2.5-coder", max_steps=5,
    )

    # 같은 (tool, params)를 반복해서 고르는 순간 루프가 멈춘다 — 1번만 실행됨.
    assert used == [{"name": "semantic_search", "params": {"q": "같은 질의"}}]
    assert call_count["n"] == 1
    assert tool_results["semantic_search"] == [{"n": 1}]


def test_run_agentic_fail_open_on_bad_json(monkeypatch):
    def generate(model, prompt, *, json_mode=False, response_schema=None,
                 project_id=None, purpose="qa", scan_id=None, system=None):
        return "이건 JSON이 아니다"

    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)

    used, tool_results = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="아무 질문",
        model="qwen2.5-coder",
    )

    assert used == []
    assert tool_results == {}


def test_run_agentic_seeds_project_info():
    project_info = {"name": "OJT 프로젝트", "description": "테스트"}
    used, tool_results = orchestrator.run_agentic(
        cur=None, ctx={"project_info": project_info}, question="이 프로젝트 이름이 뭐야?",
        model="qwen2.5-coder", max_steps=0,
    )
    assert used == []
    assert tool_results["project_info"] == [project_info]


def test_run_agentic_accepts_flattened_action_shape(monkeypatch):
    """실전에서 재현된 실패: 로컬 7B 모델이 {"action":"call_tool","tool":"X"}
    대신 도구 이름을 action 자리에 바로 써버린다
    ({"action":"dataflow_security","params":{...}}). 이걸 finish로 오인해
    도구 호출을 잃어버리면 안 된다."""
    responses = [
        {"action": "semantic_search", "params": {"q": "인증"}},  # 평평해진 형태
        {"action": "dataflow_security", "params": {"keywords": ["인증", "경로"]}},  # 재현된 실제 실패
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)

    executed = []

    def fake_execute_tool(cur, ctx, name, params, question):
        executed.append((name, params))
        return [{"dummy": name}]

    monkeypatch.setattr(orchestrator, "_execute_tool", fake_execute_tool)

    used, tool_results = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="공용 인증 경로 확인 기능 데이터 흐름 설명해줘",
        model="qwen2.5-coder", max_steps=5,
    )

    assert executed == [
        ("semantic_search", {"q": "인증"}),
        ("dataflow_security", {"keywords": ["인증", "경로"]}),
    ]
    assert tool_results["dataflow_security"] == [{"dummy": "dataflow_security"}]


def test_run_agentic_emits_events_in_order(monkeypatch):
    """on_step이 있으면(=화면에 보여줄 게 있으면) 루프 시작 전 계획 LLM 호출이
    한 번 더 일어난다 — 첫 가짜 응답은 그 계획 호출용이다."""
    responses = [
        {"steps": ["1. 관련 코드 검색"]},
        {"action": "call_tool", "tool": "semantic_search", "params": {"q": "로그인"}},
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    monkeypatch.setattr(orchestrator, "_execute_tool",
                         lambda cur, ctx, name, params, question: [{"a": 1}, {"a": 2}])

    events = []
    orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="로그인 처리하는 함수 어디 있어?",
        model="qwen2.5-coder", on_step=events.append,
    )

    assert [(e["type"], e["status"]) for e in events] == [
        ("plan", "running"),
        ("plan", "done"),
        ("thinking", "running"),
        ("thinking", "done"),
        ("tool_call", "running"),
        ("tool_result", "done"),
        ("thinking", "running"),
        ("thinking", "done"),
    ]
    # plan 이벤트는 running -> done으로 같은 id를 유지하며 finished_at이 채워진다.
    assert events[0]["id"] == events[1]["id"] == "plan-0"
    assert events[1]["finished_at"] is not None
    assert events[1]["plan_steps"] == ["관련 코드 검색"]
    # tool_call -> tool_result는 같은 id를 유지한 채 type/status만 전환된다.
    assert events[4]["id"] == events[5]["id"] == "tool-0"
    assert events[4]["tool"] == "semantic_search"
    assert events[5]["summary"] == "2건을 확인했습니다"


def test_run_agentic_on_step_failure_does_not_break_loop(monkeypatch):
    responses = [
        {"steps": ["1. 관련 코드 검색"]},
        {"action": "call_tool", "tool": "semantic_search", "params": {"q": "로그인"}},
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    monkeypatch.setattr(orchestrator, "_execute_tool",
                         lambda cur, ctx, name, params, question: [{"a": 1}])

    def broken_on_step(event):
        raise RuntimeError("소켓이 끊겼다고 치자")

    used, tool_results = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="아무 질문",
        model="qwen2.5-coder", on_step=broken_on_step,
    )

    assert used == [{"name": "semantic_search", "params": {"q": "로그인"}}]
    assert tool_results["semantic_search"] == [{"a": 1}]


def test_build_plan_fails_open_on_bad_json(monkeypatch):
    def generate(model, prompt, *, json_mode=False, response_schema=None,
                 project_id=None, purpose="qa", scan_id=None, system=None):
        return "이건 JSON이 아니다"

    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)

    assert orchestrator._build_plan("아무 질문", "- semantic_search: ...",
                                     model="qwen2.5-coder",
                                     project_id=None, scan_id="s1") is None


def test_step_prompt_includes_plan(monkeypatch):
    """세운 계획이 실제로 스텝 프롬프트에 들어가야 한다(계획 따로/실행 따로 방지)."""
    responses = [
        {"steps": ["1. dataflow_security로 위험 흐름 탐지", "2. semantic_search로 관련 코드 검색"]},
        {"action": "finish"},
    ]
    generate, calls = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    monkeypatch.setattr(orchestrator, "_execute_tool", lambda *a, **k: None)

    orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="보안 검사해줘",
        model="qwen2.5-coder", on_step=lambda e: None,
    )

    # calls[0] = 계획, calls[1] = 스텝 0 프롬프트
    assert "dataflow_security로 위험 흐름 탐지" in calls[1]
    assert "semantic_search로 관련 코드 검색" in calls[1]


def test_run_agentic_plan_coverage_backstop(monkeypatch):
    """계획이 분석 도구를 지목했는데 루프가 하나도 안 돌리고 finish 하면,
    계획의 첫 번째 미실행 분석 도구를 1회 강제 실행한다."""
    responses = [
        {"steps": ["1. dataflow_security 도구로 위험 데이터 흐름 탐지"]},
        {"action": "call_tool", "tool": "repo_commits", "params": {}},
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)

    executed = []

    def fake_execute_tool(cur, ctx, name, params, question):
        executed.append(name)
        return [{"dummy": name}]

    monkeypatch.setattr(orchestrator, "_execute_tool", fake_execute_tool)

    used, tool_results = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="프로젝트 보안검사 해줘",
        model="qwen2.5-coder", on_step=lambda e: None,
    )

    assert executed == ["repo_commits", "dataflow_security"]  # 백스톱이 뒤에 붙음
    assert [u["name"] for u in used] == ["repo_commits", "dataflow_security"]
    assert tool_results["dataflow_security"] == [{"dummy": "dataflow_security"}]


def test_run_agentic_backstop_skipped_without_scan(monkeypatch):
    """scan_id가 없으면(분석 도구가 실제로 못 도는 상황) 백스톱을 트리거하지 않는다."""
    responses = [
        {"steps": ["1. dataflow_security 도구로 위험 데이터 흐름 탐지"]},
        {"action": "call_tool", "tool": "repo_commits", "params": {}},
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    monkeypatch.setattr(orchestrator, "_execute_tool",
                         lambda cur, ctx, name, params, question: [{"dummy": name}])

    used, _ = orchestrator.run_agentic(
        cur=None, ctx={}, question="프로젝트 보안검사 해줘",
        model="qwen2.5-coder", scan_id=None, on_step=lambda e: None,
    )

    assert [u["name"] for u in used] == ["repo_commits"]


def test_run_agentic_backstop_fills_skipped_cheap_tool_even_after_search(monkeypatch):
    """계획에 있던 dataflow/call_graph(LLM 호출 없는 싼 도구)는 semantic_search가 이미 돌았어도
    채워 넣는다 — 베이스라인에서 보안 질문에 semantic_search만 보고 "위험 없음"이라 답한 거짓 음성."""
    responses = [
        {"steps": ["1. semantic_search로 검색", "2. dataflow_security로 탐지"]},
        {"action": "call_tool", "tool": "semantic_search", "params": {"q": "x"}},
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    monkeypatch.setattr(orchestrator, "_execute_tool",
                         lambda cur, ctx, name, params, question: [{"dummy": name}])

    used, _ = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="보안검사",
        model="qwen2.5-coder", on_step=lambda e: None,
    )

    assert [u["name"] for u in used] == ["semantic_search", "dataflow_security"]


def test_run_agentic_backstop_not_triggered_for_skipped_search_when_analysis_ran(monkeypatch):
    """비싼 도구(semantic_search, 임베딩 호출)는 다른 분석 도구가 이미 돌았으면 채우지 않는다."""
    responses = [
        {"steps": [{"text": "호출 관계", "tool": "call_graph"}, {"text": "검색", "tool": "semantic_search"}]},
        {"action": "call_tool", "tool": "call_graph", "params": {"symbol": "f"}},
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    monkeypatch.setattr(orchestrator, "_execute_tool",
                         lambda cur, ctx, name, params, question: [{"dummy": name}])
    used, _ = orchestrator.run_agentic(cur=None, ctx={"scan_id": "s1"}, question="f 누가 불러?",
                                       model="m", on_step=lambda e: None)
    assert [u["name"] for u in used] == ["call_graph"]


def test_run_agentic_forces_search_when_code_tools_all_empty(monkeypatch):
    """dataflow가 0건(키워드 필터로 전부 걸러짐)인 채 끝나려 하면 semantic_search를 1회 돌린다."""
    responses = [
        {"action": "call_tool", "tool": "dataflow_security", "params": {"keywords": ["pii"]}},
        {"action": "finish"},
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    monkeypatch.setattr(orchestrator, "_execute_tool",
                        lambda cur, ctx, name, params, question: [] if name == "dataflow_security" else [{"d": 1}])
    used, _ = orchestrator.run_agentic(cur=None, ctx={"scan_id": "s1"}, question="개인정보 어디서 탐지해?", model="m")
    assert [u["name"] for u in used] == ["dataflow_security", "semantic_search"]


def test_empty_evidence_after_tools_gets_fixed_answer_not_llm(monkeypatch):
    monkeypatch.setattr(orchestrator.ai_providers, "generate",
                        lambda *a, **k: pytest.fail("근거가 비면 LLM을 부르지 않는다"))
    out = orchestrator.synthesize_answer("개인정보 어디서 탐지해?", {"project_info": [{"name": "p"}]},
                                         model="m", executed_tools=["dataflow_security"], analysis_available=True)
    assert "찾지 못" in out and "스캔이 없어" not in out


def test_run_agentic_forces_semantic_search_after_codebase_structure_only(monkeypatch):
    """실전 재현 버그: codebase_structure만 실행한 채 LLM이 곧바로 finish하면
    (얇은 도메인 개요만 보고 조기 종료 → synthesize_answer가 존재하지 않는 파일
    경로를 환각), semantic_search를 1회 강제 실행해 LLM에게 재판단 기회를 준다."""
    responses = [
        {"action": "call_tool", "tool": "codebase_structure", "params": {}},
        {"action": "finish"},   # 얇은 근거만 보고 조기 종료 시도 — 게이트가 가로챔
        {"action": "finish"},   # 강제 semantic_search 이후 재판단 → 진짜로 끝
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)

    executed = []

    def fake_execute_tool(cur, ctx, name, params, question):
        executed.append(name)
        if name == "codebase_structure":
            return [{"domain": "인증 관리", "role": "auth", "file_count": 12}]
        if name == "semantic_search":
            return [{"file_path": "src/auth/service.py", "symbol": "register_user"}]
        return None

    monkeypatch.setattr(orchestrator, "_execute_tool", fake_execute_tool)

    used, tool_results = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="회원가입 정책 설명해주세요",
        model="qwen2.5-coder", max_steps=5,
    )

    assert executed == ["codebase_structure", "semantic_search"]
    assert [u["name"] for u in used] == ["codebase_structure", "semantic_search"]
    assert tool_results["semantic_search"] == [
        {"file_path": "src/auth/service.py", "symbol": "register_user"}
    ]


def test_run_agentic_forces_semantic_search_on_unparseable_response_too(monkeypatch):
    """finish가 아니라 파싱 불가능한 응답이 와도(별도 except-break 경로) 게이트가
    똑같이 개입해야 한다 — _parse_step_action을 거치지 않는 예외 경로도 커버해야 함."""
    call_n = {"n": 0}

    def generate(model, prompt, *, json_mode=False, response_schema=None,
                 project_id=None, purpose="qa", scan_id=None, system=None):
        call_n["n"] += 1
        if call_n["n"] == 1:
            return json.dumps({"action": "call_tool", "tool": "codebase_structure", "params": {}})
        if call_n["n"] == 2:
            return "이건 JSON이 아니다"
        return json.dumps({"action": "finish"})

    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)

    executed = []
    monkeypatch.setattr(orchestrator, "_execute_tool",
                         lambda cur, ctx, name, params, question: executed.append(name) or [{"dummy": name}])

    used, _ = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="아무 질문",
        model="qwen2.5-coder",
    )

    assert executed == ["codebase_structure", "semantic_search"]
    assert [u["name"] for u in used] == ["codebase_structure", "semantic_search"]


def test_run_agentic_forces_semantic_search_on_repeated_codebase_structure(monkeypatch):
    """세 번째 조기 종료 지점: 같은 도구를 같은 params로 다시 고르면 원래
    중복 방지로 즉시 break하는데, 그게 codebase_structure 반복이면(실전 관측:
    7B가 다음 수를 못 정하고 방금 한 걸 그대로 되풀이) 이것도 게이트를 거쳐야 한다."""
    responses = [
        {"action": "call_tool", "tool": "codebase_structure", "params": {}},
        {"action": "call_tool", "tool": "codebase_structure", "params": {}},  # 중복 — 원래는 즉시 break
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)

    executed = []
    monkeypatch.setattr(
        orchestrator, "_execute_tool",
        lambda cur, ctx, name, params, question: executed.append(name) or [{"dummy": name}],
    )

    used, _ = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="아무 질문",
        model="qwen2.5-coder",
    )
    assert executed == ["codebase_structure", "semantic_search"]
    assert [u["name"] for u in used] == ["codebase_structure", "semantic_search"]


def test_run_agentic_no_force_when_no_tools_used(monkeypatch):
    """도구를 하나도 안 부르고 finish하는 건 정당한 케이스 — 건드리지 않는다."""
    responses = [{"action": "finish"}]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    monkeypatch.setattr(
        orchestrator, "_execute_tool",
        lambda *a, **k: pytest.fail("도구가 호출되면 안 됨"),
    )

    used, tool_results = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="안녕",
        model="qwen2.5-coder",
    )
    assert used == []
    assert tool_results == {}


def test_run_agentic_no_force_when_other_tool_also_used(monkeypatch):
    """codebase_structure 외에 다른 도구도 이미 실행됐으면 게이트를 건너뛴다."""
    responses = [
        {"action": "call_tool", "tool": "codebase_structure", "params": {}},
        {"action": "call_tool", "tool": "repo_commits", "params": {}},
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)

    executed = []
    monkeypatch.setattr(
        orchestrator, "_execute_tool",
        lambda cur, ctx, name, params, question: executed.append(name) or [{"dummy": name}],
    )

    used, _ = orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="아무 질문",
        model="qwen2.5-coder",
    )
    assert executed == ["codebase_structure", "repo_commits"]
    assert [u["name"] for u in used] == ["codebase_structure", "repo_commits"]


def test_run_agentic_no_force_without_scan(monkeypatch):
    """scan_id가 없으면 semantic_search도 결과 없이 끝나므로 게이트를 트리거하지 않는다."""
    responses = [
        {"action": "call_tool", "tool": "codebase_structure", "params": {}},
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)

    executed = []
    monkeypatch.setattr(
        orchestrator, "_execute_tool",
        lambda cur, ctx, name, params, question: executed.append(name) or None,
    )

    used, _ = orchestrator.run_agentic(
        cur=None, ctx={}, question="아무 질문",
        model="qwen2.5-coder",
    )
    assert executed == ["codebase_structure"]
    assert [u["name"] for u in used] == ["codebase_structure"]


def test_execute_tool_passes_domain_to_search_symbols(monkeypatch):
    """semantic_search의 domain 파라미터가 search_symbols까지 그대로 전달돼야
    codebase_structure에서 찾은 도메인으로 검색 범위를 좁히는 게 실제로 동작한다."""
    calls = []

    def fake_search_symbols(cur, scan_id, q, limit, domain=None):
        calls.append({"q": q, "limit": limit, "domain": domain})
        return [{"dummy": True}]

    monkeypatch.setattr(orchestrator, "hybrid_search_symbols", fake_search_symbols)

    orchestrator._execute_tool(
        cur=None, ctx={"scan_id": "s1"}, name="semantic_search",
        params={"q": "회원가입", "domain": "인증 관리"}, question="회원가입 정책 설명해주세요",
    )
    assert calls == [{"q": "회원가입", "limit": 10, "domain": "인증 관리"}]

    calls.clear()
    orchestrator._execute_tool(
        cur=None, ctx={"scan_id": "s1"}, name="semantic_search",
        params={"q": "회원가입"}, question="회원가입 정책 설명해주세요",
    )
    assert calls == [{"q": "회원가입", "limit": 10, "domain": None}]


def test_semantic_search_retries_without_domain_when_scoped_search_is_empty(monkeypatch):
    """도메인 태깅이 틀려 범위 검색이 0건이면 전체에서 한 번 더 찾는다."""
    calls = []

    def fake_search_symbols(cur, scan_id, q, limit, domain=None):
        calls.append(domain)
        return [] if domain else [{"qualified_name": "login"}]

    monkeypatch.setattr(orchestrator, "hybrid_search_symbols", fake_search_symbols)
    out = orchestrator._execute_tool(None, {"scan_id": "s1"}, "semantic_search",
                                     {"q": "로그인", "domain": "잘못된 도메인"}, "로그인 어디서 해?")
    assert calls == ["잘못된 도메인", None]
    assert out == [{"qualified_name": "login"}]


def test_query_terms_strip_particles_and_generic_words():
    from app.scan_queries import _query_terms

    assert _query_terms("금지어 필터링은 어떻게 구현돼 있어?") == ["금지어", "필터링", "구현돼"]
    assert _query_terms("presigned URL은 어디서 만들어?") == ["presigned", "url", "만들어"]
    assert _query_terms("주민등록번호나 전화번호 탐지") == ["주민등록번호", "전화번호", "탐지"]


# --------------------------------------------------------------------- params 정규화

def test_normalize_params_fixes_shapes_local_models_emit():
    n = orchestrator._normalize_params
    assert n(["not", "a", "dict"]) == {}
    assert n(None) == {}
    # keywords 문자열 → 리스트(예전엔 글자 단위로 순회해 필터가 무력화됐다)
    assert n({"keywords": "sql, query execute"}) == {"keywords": ["sql", "query", "execute"]}
    assert n({"keywords": ["a", "a", " ", "b"]}) == {"keywords": ["a", "b"]}
    assert n({"state": "OPEN"}) == {"state": "open"}
    assert n({"state": "머지"}) == {"state": "merged"}
    assert n({"state": "all"}) == {}
    assert n({"direction": "Callee"}) == {"direction": "callees"}
    assert n({"start_line": "12", "end_line": "x"}) == {"start_line": 12}
    assert n({"q": "  로그인 ", "domain": ""}) == {"q": "로그인"}


def test_fallback_parser_normalizes_params_and_survives_non_dict():
    raw = json.dumps({"action": "dataflow_security", "params": {"keywords": "sql,인젝션"}})
    assert orchestrator._parse_step_decision(raw) == ("dataflow_security", {"keywords": ["sql", "인젝션"]})
    assert orchestrator._parse_step_decision(json.dumps(["call_tool"])) is None
    assert orchestrator._parse_step_decision_ex(json.dumps({"action": "call_tool", "tool": "x"}))[1] == "fail"


def test_run_agentic_survives_non_dict_params(monkeypatch):
    """폴백 경로에서 params가 리스트로 와도 루프가 죽지 않는다(예전엔 tool_target_text에서 AttributeError)."""
    responses = [{"steps": []},  # on_step이 있으면 계획 호출이 먼저
                 {"action": "call_tool", "tool": "call_graph", "params": ["login"]}, {"action": "finish"}]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    executed = []
    monkeypatch.setattr(orchestrator, "_execute_tool",
                        lambda cur, ctx, name, params, question: executed.append((name, params)) or [{"x": 1}])
    used, _ = orchestrator.run_agentic(cur=None, ctx={"scan_id": "s1"}, question="login 누가 호출해?",
                                       model="m", on_step=lambda e: None)
    assert executed == [("call_graph", {})]


# --------------------------------------------------------------------- 계획 tool 필드 / 백스톱

def test_plan_steps_carry_tool_and_backstop_uses_it(monkeypatch):
    """계획 문장이 자연어여도(도구 이름이 없어도) tool 필드로 백스톱이 동작한다 —
    예전엔 문장 안에서 도구 이름을 찾아서 거의 발동하지 않았다."""
    responses = [
        {"steps": [{"text": "위험한 데이터 흐름 확인", "tool": "dataflow_security"}]},
        {"action": "call_tool", "tool": "repo_commits", "params": {}},
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    executed = []
    monkeypatch.setattr(orchestrator, "_execute_tool",
                        lambda cur, ctx, name, params, question: executed.append(name) or [{"d": name}])
    events = []
    orchestrator.run_agentic(cur=None, ctx={"scan_id": "s1"}, question="보안 점검해줘",
                             model="m", on_step=events.append)
    assert executed == ["repo_commits", "dataflow_security"]
    plan_done = next(e for e in events if e["type"] == "plan" and e["status"] == "done")
    assert plan_done["plan_steps"] == ["위험한 데이터 흐름 확인"]  # 화면엔 문장만


def test_step_summary_shows_top_results():
    s = orchestrator._summarize_step("semantic_search", {"q": "x"},
                                     [{"qualified_name": "login", "path": "a/auth.js"},
                                      {"qualified_name": "logout", "path": "a/auth.js"}])
    assert s["result"] == "2건"
    assert s["top"] == ["login (a/auth.js)", "logout (a/auth.js)"]


def test_conversation_is_injected_into_plan_and_step_prompts(monkeypatch):
    responses = [{"steps": [{"text": "호출 관계 확인", "tool": "call_graph"}]}, {"action": "finish"}]
    generate, calls = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    monkeypatch.setattr(orchestrator, "_execute_tool", lambda *a, **k: None)
    orchestrator.run_agentic(cur=None, ctx={"scan_id": "s1"}, question="그 함수 누가 호출해?",
                             model="m", on_step=lambda e: None,
                             conversation="사용자: 로그인 어디서 해?\n어시스턴트: auth.service.js의 login")
    assert "auth.service.js의 login" in calls[0]
    assert "auth.service.js의 login" in calls[1]


def test_repeated_search_results_are_deduplicated(monkeypatch):
    responses = [
        {"action": "call_tool", "tool": "semantic_search", "params": {"q": "a"}},
        {"action": "call_tool", "tool": "semantic_search", "params": {"q": "b"}},
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    monkeypatch.setattr(orchestrator, "_execute_tool",
                        lambda cur, ctx, name, params, question: [{"k": 1}, {"k": params["q"]}])
    _, results = orchestrator.run_agentic(cur=None, ctx={"scan_id": "s1"}, question="q", model="m")
    assert results["semantic_search"] == [{"k": 1}, {"k": "a"}, {"k": "b"}]


# --------------------------------------------------------------------- 관련성 필터 견고화

@pytest.mark.parametrize("response, expected", [
    ({"semantic_search": [1]}, [{"n": 1}]),
    ({"semantic_search": 1}, [{"n": 1}]),            # 예전: set(1) → TypeError로 턴 전체 실패
    ({"semantic_search": ["0", "2"]}, [{"n": 0}, {"n": 2}]),  # 예전: 문자열이라 전부 버려짐
    ({"semantic_search": [{"idx": 2}]}, [{"n": 2}]),
    ({"semantic_search": ["login"]}, [{"n": 0}, {"n": 1}, {"n": 2}]),  # 해석 불가 → 원본 유지
    ({"semantic_search": []}, None),                  # 명시적으로 모두 무관 → 그룹 제거
    (["not", "dict"], [{"n": 0}, {"n": 1}, {"n": 2}]),  # 예전: AttributeError
])
def test_check_relevance_tolerates_odd_index_shapes(monkeypatch, response, expected):
    generate, _ = _fake_generate([response])
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    out = orchestrator.check_relevance("q", {"semantic_search": [{"n": 0}, {"n": 1}, {"n": 2}],
                                             "project_info": [{"name": "p"}]}, model="m")
    assert out.get("semantic_search") == expected
    assert out["project_info"] == [{"name": "p"}]  # 메타데이터는 거르지 않는다


def test_check_relevance_keeps_read_code_untouched(monkeypatch):
    generate, calls = _fake_generate([{"semantic_search": []}])
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    code = [{"path": "a.js", "start_line": 1, "end_line": 3, "code": "1| x"}]
    out = orchestrator.check_relevance("q", {"semantic_search": [{"n": 0}], "read_code": code}, model="m")
    assert out == {"read_code": code}
    assert "1| x" not in calls[0]  # 코드는 관련성 판단 프롬프트에 싣지 않는다


# --------------------------------------------------------------------- 코드 자동 첨부 / 예산

def test_attach_code_picks_sinks_targets_then_search_hits(monkeypatch):
    read = []

    def fake_read_symbol(cur, scan_id, key, *, focus_line=None, max_lines=80):
        read.append((key, focus_line))
        return {"symbol_key": key, "path": f"{key}.js", "start_line": 1, "end_line": 2, "code": "1| x"}

    monkeypatch.setattr(orchestrator.source_reader, "read_symbol", fake_read_symbol)
    evidence = {
        "semantic_search": [{"symbol_key": "s1", "kind": "function"}, {"symbol_key": "m", "kind": "module"}],
        "call_graph": [{"relation": "caller", "symbol_key": "c1"}, {"relation": "target", "symbol_key": "t1"}],
        "dataflow_security": [{"sink_symbol_key": "k1", "sink_line": 17}],
    }
    events = []
    out = orchestrator.attach_code(None, "scan", evidence, on_step=events.append)
    assert read == [("k1", 17), ("t1", None), ("s1", None)]
    assert [c["symbol_key"] for c in out["read_code"]] == ["k1", "t1", "s1"]
    assert [(e["type"], e["tool"]) for e in events] == [("tool_call", "read_code"), ("tool_result", "read_code")]


def test_attach_code_noop_without_scan():
    ev = {"semantic_search": [{"symbol_key": "s1"}]}
    assert orchestrator.attach_code(None, None, ev) is ev


def test_synthesize_prompt_respects_local_context_budget():
    """도구 결과가 아무리 커도 로컬 모델(8192) 프롬프트는 예산 안에 든다 — 베이스라인에서
    Ollama가 앞부분(지시문)을 잘라 '코드 조회가 필요 없어 보입니다'로 답한 원인."""
    from app import context_budget

    big = {"dataflow_security": [{"category": "sql_injection", "sink_path": f"src/f{i}.js",
                                  "path": [{"path": f"src/h{j}.js", "line": j} for j in range(12)],
                                  "sink_symbol_key": "x" * 200} for i in range(40)],
           "repo_commits": [{"message": "m" * 2000} for _ in range(20)]}
    prompt = orchestrator._synthesize_prompt("SQL 인젝션 있어?", big, ["dataflow_security"], True,
                                             model="local-qwen-coder")
    budget = 8192 - orchestrator._OUTPUT_RESERVE
    assert context_budget.estimate_tokens(prompt + orchestrator._SYNTHESIZE_SYSTEM) < budget
    assert "생략" in prompt  # 잘라낸 사실을 모델에 알린다
    assert "x" * 200 not in prompt  # 내부 키는 빠진다


def test_tool_target_text_is_human_readable():
    """타임라인 target에 raw JSON을 노출하지 않고 자연어 문구를 낸다."""
    from app.agent_events import tool_target_text

    assert tool_target_text("dataflow_security", {"keywords": ["sql", "주입"]}) == "관심 키워드: sql, 주입"
    assert tool_target_text("semantic_search", {"q": "로그인 처리"}) == "'로그인 처리' 관련 코드 검색"
    assert (tool_target_text("semantic_search", {"q": "로그인 처리", "domain": "인증 관리"})
            == "'로그인 처리' 관련 코드 검색 (인증 관리 도메인 내)")
    assert tool_target_text("call_graph", {"symbol": "runPii"}) == "'runPii' 호출 관계 추적"
    assert tool_target_text("repo_commits", {}) is None
    assert tool_target_text("repo_branches", {}) is None
    # 어떤 경우에도 중괄호/대괄호(=raw JSON 흔적)는 나오지 않는다
    for out in (tool_target_text("dataflow_business", {"keywords": ["a", "b"]}),
                tool_target_text("repo_pull_requests", {"state": "open"})):
        assert out and "{" not in out and "[" not in out


def test_run_agentic_tool_event_target_has_no_raw_json(monkeypatch):
    responses = [
        {"steps": ["1. dataflow_security로 탐지"]},
        {"action": "call_tool", "tool": "dataflow_security", "params": {"keywords": ["sql", "인젝션"]}},
        {"action": "finish"},
    ]
    generate, _ = _fake_generate(responses)
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    monkeypatch.setattr(orchestrator, "_execute_tool",
                         lambda cur, ctx, name, params, question: [{"x": 1}])

    events = []
    orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="sql 인젝션 있어?",
        model="qwen2.5-coder", on_step=events.append,
    )
    tool_call = next(e for e in events if e["type"] == "tool_call")
    assert tool_call["target"] == "관심 키워드: sql, 인젝션"


def test_synthesize_answer_distinguishes_missing_evidence():
    """근거가 비었을 때 '도구 미실행'과 '질문 무관'과 '스캔 없음'을 구분한다."""
    # 분석 도구를 실행했으나 근거가 빔 → "수행했으나 못 찾음"
    assert "찾지 못" in orchestrator.synthesize_answer(
        "보안검사", {}, model="m",
        executed_tools=["dataflow_security"], analysis_available=True,
    )
    # 분석 도구를 실행했는데 스캔이 없음
    assert "스캔이 없어" in orchestrator.synthesize_answer(
        "보안검사", {}, model="m",
        executed_tools=["dataflow_security"], analysis_available=False,
    )
    # 아무 도구도 실행 안 함 → 기존 문구
    assert "코드 조회가 필요 없" in orchestrator.synthesize_answer(
        "안녕", {}, model="m", executed_tools=(),
    )


# --------------------------------------------------------------------- schema-constrained decoding

def test_step_decision_tool_names_match_tools():
    """llm_schemas.StepDecision.tool의 Literal 값이 orchestrator.TOOLS와 어긋나면
    안 된다 — 어긋나면 새 도구가 스키마 제약 때문에 영영 선택될 수 없게 조용히
    막힌다(구조적으로 emit 불가능해짐). 새 도구를 추가할 때 이 테스트가 실패해서
    llm_schemas.py도 같이 고치라고 알려준다."""
    import typing

    tool_annotation = llm_schemas.StepDecision.model_fields["tool"].annotation
    literal_type = next(a for a in typing.get_args(tool_annotation) if a is not type(None))
    schema_tools = set(typing.get_args(literal_type))
    assert schema_tools == orchestrator._TOOL_NAMES


def _capturing_generate(responses):
    """_fake_generate와 같지만 kwargs(특히 response_schema)까지 기록한다 —
    올바른 스키마가 실제로 전달되는지 검증하는 용도."""
    calls = []

    def generate(model, prompt, *, json_mode=False, response_schema=None,
                 project_id=None, purpose="qa", scan_id=None, system=None):
        calls.append({"response_schema": response_schema, "json_mode": json_mode})
        idx = len(calls) - 1
        if idx >= len(responses):
            raise AssertionError("에이전틱 루프가 예상보다 더 많이 LLM을 호출했다")
        return json.dumps(responses[idx])

    return generate, calls


def test_build_plan_uses_plan_steps_schema(monkeypatch):
    generate, calls = _capturing_generate([{"steps": ["a"]}])
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    orchestrator._build_plan("질문", "tools", model="m",
                             project_id=None, scan_id=None)
    assert calls[0]["response_schema"] is llm_schemas.PlanSteps


def test_run_agentic_step_loop_uses_step_decision_schema(monkeypatch):
    generate, calls = _capturing_generate([{"action": "finish"}])
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    orchestrator.run_agentic(
        cur=None, ctx={"scan_id": "s1"}, question="아무 질문",
        model="qwen2.5-coder",
    )
    assert calls[0]["response_schema"] is llm_schemas.StepDecision


def test_check_relevance_and_synthesize_stay_on_loose_mode(monkeypatch):
    """check_relevance는 도구이름을 key로 쓰는 동적 dict라 스키마 제약을 안 쓴다
    (의도적 결정) — 이 회귀 가드가 실수로 스키마가 붙는 걸 잡는다."""
    generate, calls = _capturing_generate([{"a": [0]}])
    monkeypatch.setattr(orchestrator.ai_providers, "generate", generate)
    orchestrator.check_relevance("질문", {"a": [{"x": 1}]}, model="m")
    assert calls[0]["response_schema"] is None
    assert calls[0]["json_mode"] is True


def test_parse_step_decision_falls_back_to_legacy_parser_on_schema_violation():
    """스키마 검증 실패(예: action에 도구명을 직접 넣은 평평한 모양)는 기존 관대한
    파서(_parse_step_action)로 복구돼야 한다 — StepDecision.action은
    Literal["call_tool","finish"]라 "codebase_structure"는 애초에 유효하지 않다."""
    raw = json.dumps({"action": "codebase_structure"})
    assert orchestrator._parse_step_decision(raw) == ("codebase_structure", {})


def test_parse_step_decision_returns_none_on_finish():
    assert orchestrator._parse_step_decision(json.dumps({"action": "finish"})) is None


def test_parse_step_decision_extracts_params():
    raw = json.dumps({"action": "call_tool", "tool": "semantic_search",
                       "params": {"q": "로그인", "domain": "인증"}})
    assert orchestrator._parse_step_decision(raw) == (
        "semantic_search", {"q": "로그인", "domain": "인증"})
