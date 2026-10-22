"""채팅 에이전트가 실시간으로 흘려보내는 진행 상황 이벤트("AgentEvent").

`app/orchestrator.py`의 `run_agentic()`/`_build_plan()`이 만들고, `app/chat.py`가
WebSocket 프레임(`{"type": "agent_event", ...}`)으로 그대로 중계한다. 이 시스템의
채팅 에이전트는 파일을 쓰거나 코드를 실행하지 않는 읽기 전용 조회 도구
(`orchestrator.TOOLS` — DB/그래프 조회와 스캔한 커밋의 소스 읽기)만 가지고 있으므로,
이벤트 타입도 그 범위를 정직하게 반영한다 — file_operation/code_change 같은 타입은 존재하지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, TypedDict

AgentEventType = Literal["thinking", "plan", "tool_call", "tool_result", "final_response"]
AgentEventStatus = Literal["running", "done", "error"]


class AgentEvent(TypedDict, total=False):
    id: str
    type: AgentEventType
    status: AgentEventStatus
    tool: str | None
    target: str | None
    summary: str
    plan_steps: list[str]
    started_at: str
    finished_at: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_event(id: str, type: AgentEventType, status: AgentEventStatus, **fields) -> AgentEvent:
    event: AgentEvent = {
        "id": id,
        "type": type,
        "status": status,
        "started_at": _now(),
        "finished_at": None,
        **fields,
    }
    return event


def mark_done(event: AgentEvent, *, status: AgentEventStatus = "done", **fields) -> AgentEvent:
    """같은 id를 유지한 채 상태를 종료 상태로 옮긴다 (예: tool_call -> tool_result)."""
    return {**event, "status": status, "finished_at": _now(), **fields}


# 도구 이름 -> "지금 뭘 하는지" 짧은 한국어 상태 문구. 추가 LLM 호출 없이 규칙으로
# 매핑한다(정확한 자연어 사유보다 지연시간/비용 없는 쪽을 택함).
_THINKING_TEXT = {
    "codebase_structure": "코드베이스 구조를 확인하는 중...",
    "semantic_search": "관련 코드를 검색하는 중...",
    "call_graph": "호출 관계를 분석하는 중...",
    "read_code": "소스 코드를 읽는 중...",
    "dataflow_security": "데이터 흐름을 분석하는 중...",
    "dataflow_business": "데이터 흐름을 분석하는 중...",
    "repo_commits": "저장소 이력을 확인하는 중...",
    "repo_pull_requests": "저장소 이력을 확인하는 중...",
    "repo_releases": "저장소 이력을 확인하는 중...",
    "repo_branches": "저장소 이력을 확인하는 중...",
}
_DEFAULT_THINKING_TEXT = "다음 단계를 판단하는 중..."


def thinking_text(tool_name: str | None) -> str:
    return _THINKING_TEXT.get(tool_name, _DEFAULT_THINKING_TEXT)


def tool_result_text(tool_name: str, ok: bool, count: int | None) -> str:
    if not ok:
        return "조회 중 오류가 발생했습니다"
    if count:
        return f"{count}건을 확인했습니다"
    return "관련 결과를 찾지 못했습니다"


def _clean_terms(value: object, limit: int = 6) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = value
    elif value:
        items = [value]
    else:
        return []
    seen: list[str] = []
    for item in items:
        term = str(item).strip()
        if term and term not in seen:
            seen.append(term)
    return seen[:limit]


def tool_target_text(tool_name: str, params: dict | None) -> str | None:
    """도구 호출 파라미터 → 사용자에게 보여줄 한 줄 설명. raw JSON은 화면에 내보내지 않는다.

    타임라인은 서비스 화면이므로 `{"keywords": [...]}` 같은 내부 표현이 아니라
    "무엇을 조사하는 중인지"를 자연어로 보여준다. 표현할 게 없으면 None(행을 안 그림).
    """
    params = params or {}

    if tool_name == "semantic_search":
        q = str(params.get("q") or "").strip()
        if not q:
            return None
        domain = str(params.get("domain") or "").strip()
        return f"'{q}' 관련 코드 검색 ({domain} 도메인 내)" if domain else f"'{q}' 관련 코드 검색"
    if tool_name == "call_graph":
        sym = str(params.get("symbol") or "").strip()
        if not sym:
            return None
        direction = {"callers": " (호출하는 쪽)", "callees": " (호출되는 쪽)"}.get(str(params.get("direction") or ""), "")
        return f"'{sym}' 호출 관계 추적{direction}"
    if tool_name == "read_code":
        target = str(params.get("symbol") or params.get("path") or "").strip()
        return f"'{target}' 코드 읽기" if target else None
    if tool_name in ("dataflow_security", "dataflow_business"):
        terms = _clean_terms(params.get("keywords"))
        return f"관심 키워드: {', '.join(terms)}" if terms else None
    if tool_name == "repo_commits":
        q = str(params.get("query") or "").strip()
        return f"'{q}' 관련 커밋 검색" if q else None
    if tool_name == "repo_pull_requests":
        state = str(params.get("state") or "").strip().lower()
        return {"open": "열려 있는 Pull Request", "closed": "닫힌 Pull Request"}.get(state)
    return None
