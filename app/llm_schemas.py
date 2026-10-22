"""LLM 호출의 grammar/schema-constrained decoding에 쓰는 Pydantic 응답 스키마 모음.

`app/ai_providers.py::generate(response_schema=...)`에 그대로 넘긴다 — 게이트웨이
(LiteLLM)가 프로바이더별로 번역한다(Ollama는 네이티브 `format` 필드로 실제
GBNF 문법 제약 디코딩, Anthropic은 강제 tool-call 에뮬레이션, 미지원
프로바이더는 `litellm/config.yaml`의 `drop_params: true`로 조용히 무시).

이 파일은 스키마 정의만 담당하고, 실제 호출/파싱은 각 호출부(orchestrator/
risk/project_spec/worker.annotate)에 남는다 — `app/agent_events.py`(여러
producer/consumer가 공유하는 작은 공통 모듈)와 같은 위치의 판단이다.

`check_relevance`(도구 이름을 key로 쓰는 동적 dict 응답)는 여기 없다 — 임의
key 집합은 JSON Schema로 의미 있게 제약하기 어려워 당분간 느슨한
`json_mode=True`를 유지한다(app/orchestrator.py 참고).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


ToolName = Literal[
    "codebase_structure", "semantic_search", "call_graph", "read_code",
    "dataflow_security", "dataflow_business", "repo_commits",
    "repo_pull_requests", "repo_releases", "repo_branches",
]


class PlanStep(BaseModel):
    """계획 한 단계 — 화면에 보일 문장(text)과 그 단계가 실행할 도구(tool).

    예전엔 문장만 받아서, 계획 커버리지 백스톱이 문장 안에서 도구 이름 문자열을
    찾았다 — 그런데 프롬프트가 "관련 파일 탐색" 같은 자연어를 요구하니 거의 매칭되지
    않았다. 도구를 별도 필드로 받아 백스톱이 실제로 동작하게 한다.
    """

    text: str
    tool: ToolName | None = None


class PlanSteps(BaseModel):
    """`app/orchestrator.py::_build_plan`의 응답 스키마."""

    steps: list[PlanStep]


class StepParams(BaseModel):
    """`StepDecision.params` — 10개 도구의 파라미터 모양을 전부 optional로 담는
    평평한 합집합. discriminated union(도구별 중첩 모델)이 더 "정확"하지만,
    JSON-Schema의 `oneOf`가 llama.cpp의 GBNF 변환에서 온전히 지원되는지
    검증되지 않아 1차 구현에서는 피한다 — `app/orchestrator.py::_execute_tool`이
    이미 `params.get(...)` 로 도구별 필드만 골라 쓰므로, 여분 필드가 있어도
    무해하다.
    """

    q: str | None = None
    domain: str | None = None
    symbol: str | None = None
    keywords: list[str] | None = None
    query: str | None = None
    state: str | None = None
    direction: str | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class StepDecision(BaseModel):
    """`app/orchestrator.py::run_agentic` 스텝 루프(`_STEP_PROMPT`)의 응답 스키마.

    `tool`의 값(`ToolName`)은 `app/orchestrator.py::TOOLS`의 도구명과 반드시 일치해야
    한다 — 새 도구를 추가하면 여기도 같이 고쳐야 한다(테스트
    `test_step_decision_tool_names_match_tools`가 드리프트를 감지한다).
    """

    action: Literal["call_tool", "finish"]
    tool: ToolName | None = None
    params: StepParams | None = None


class BusinessIpClassification(BaseModel):
    """`app/risk/classify.py::classify_business_ip`의 응답 스키마."""

    classification: Literal[
        "business_core_logic", "business_supporting", "generic_infrastructure", "unknown",
    ]
    business_role: str
    confidence: float
    evidence: list[str] = []


class QueryIntentClassification(BaseModel):
    """`app/risk/classify.py::classify_query_intent`의 응답 스키마."""

    intent: Literal["ip_probe", "generic_syntax", "neutral"]
    confidence: float


class ProjectSpec(BaseModel):
    """`app/project_spec.py::generate_project_spec`의 응답 스키마."""

    summary: str
    tags: list[str] = []


class SymbolSummary(BaseModel):
    """`AnnotateResult.symbols`의 원소 — 원래 `{"함수명": "요약", ...}` dict였던 걸
    스키마 제약 가능하게 리스트로 재구조화했다(동적 key는 제약 불가)."""

    name: str
    summary: str


class AnnotateResult(BaseModel):
    """`worker/handlers/annotate.py::_annotate_file`의 응답 스키마."""

    role: Literal["controller", "service", "repository", "util", "test", "config", "other"]
    domain: str
    summary: str
    symbols: list[SymbolSummary] = []


class FindingFixSuggestion(BaseModel):
    """`app/fix_generation.py::generate_fix`의 응답 스키마.

    자유 텍스트로 unified diff를 통째로 생성시키는 대신, [start_line,end_line]
    범위를 새 코드로 통째로 교체하는 형태로 제약한다 — 모델이 문법적으로
    올바른 diff 헤더/hunk를 직접 조립하다 실패하는 경우가 훨씬 잦다.
    """

    summary: str
    start_line: int
    end_line: int
    replacement_code: str
