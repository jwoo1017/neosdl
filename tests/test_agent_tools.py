"""app/agent_tools.py — 도구 레지스트리 seam 단위 테스트(DB/LLM 없음)."""

from __future__ import annotations

from app import orchestrator
from app.agent_tools import ToolRegistry, ToolSpec, build_default_registry


def test_default_registry_mirrors_orchestrator_tools():
    """읽기 레지스트리가 orchestrator.TOOLS 와 1:1 — 드리프트 방지(새 읽기 도구 자동 포함)."""
    reg = build_default_registry()
    assert set(reg.names(kind="read")) == orchestrator._TOOL_NAMES
    assert reg.names(kind="exec") == []


def test_read_tool_delegates_to_execute_tool(monkeypatch):
    calls = []
    monkeypatch.setattr(orchestrator, "_execute_tool",
                        lambda cur, ctx, name, params, question: calls.append((name, params)) or [{"ok": name}])
    reg = build_default_registry()
    spec = reg.get("semantic_search")
    out = spec.handler(cur=None, ctx={"scan_id": "s1"}, params={"q": "x"}, question="질문")
    assert calls == [("semantic_search", {"q": "x"})]
    assert out == [{"ok": "semantic_search"}]


def test_exec_tool_role_gate():
    reg = ToolRegistry()
    reg.register(ToolSpec(name="http_probe", description="", params={}, kind="exec",
                          handler=lambda *a, **k: None, min_role="developer", destructive=True))
    spec = reg.get("http_probe")
    assert spec.role_allows("manager") is True
    assert spec.role_allows("developer") is True
    assert spec.role_allows("viewer") is False
    assert spec.role_allows(None) is False
    assert spec.destructive is True


def test_read_tool_role_always_allowed():
    spec = build_default_registry().get("read_code")
    assert spec.role_allows(None) is True  # 읽기는 역할 무관
