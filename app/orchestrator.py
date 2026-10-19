"""질의응답 오케스트레이터 — 그래프 위에 배치된 '전문가 도구'들을 LLM이 매 스텝 골라 부른다.

사용자 비전: CFG/PDG/도메인 그래프/시맨틱 검색은 사람이 클릭해서 보는 화면일
뿐 아니라, LLM 에이전트가 질의에 맞는 것만 골라 협업하기 위한 근거 계층이다.
질의와 무관한 도구까지 전부 돌리면 컴퓨팅 낭비이므로 라우팅이 먼저 필요하다.

로컬 모델(Qwen2.5-Coder-7B)은 네이티브 function-calling이 7B급에서
불안정해서, `worker/handlers/annotate.py`가 이미 쓰고 검증한 JSON 구조화
출력(`format:"json"`) 방식을 그대로 재사용한다.

v2: 한 번의 LLM 호출로 도구 세트를 통째로 정하고 intent별 고정 백스톱
(REQUIRED_TOOLS)으로 빠뜨린 도구를 채워 넣던 v1 방식을 버렸다 — intent 분류가
한 번 잘못되면 되돌릴 기회가 없었고, 백스톱 자체가 "intent → 도구" 하드코딩
매핑이었다. 대신 매 스텝마다 지금까지 모은 근거를 보고 "도구를 하나 더
부를지 / 그만 모을지"를 LLM이 스스로 판단하는 루프로 바꿨다 — 잘못 판단해도
다음 스텝에서 되돌릴 기회가 있고, call_graph 같은 새 도구도 intent 카테고리
없이 그냥 목록에 얹으면 된다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Callable

from pydantic import ValidationError

from app import ai_providers, llm_schemas, observability, repo_queries
from app.agent_events import (
    AgentEvent, mark_done, new_event, thinking_text, tool_result_text, tool_target_text,
)
from app.scan_queries import call_graph_paths, domain_overview, run_dataflow, search_symbols

log = logging.getLogger("app.orchestrator")

MAX_STEPS = 5

# 계획이 이 도구들 중 하나라도 지목했는데 루프가 하나도 실행하지 않고 끝나면,
# run_agentic 말미의 계획 커버리지 백스톱이 계획에 등장한 첫 번째 미실행 도구를 1회 강제 실행한다.
_ANALYSIS_TOOLS = ("dataflow_security", "dataflow_business", "semantic_search", "call_graph")

# codebase_structure는 도메인/파일수 개요만 주고 파일 경로·코드·심볼이 없다 — 이것만
# 보고 finish하면 synthesize_answer가 얇은 근거를 그럴듯한 환각으로 채운 사례가 실전
# Langfuse 트레이스에서 확인됐다(존재하지 않는 파일 경로를 지어냄). run_agentic 안의
# _should_force_semantic_followup/_force_tool_call이 이 상태에서의 조기 종료를
# 가로채 semantic_search를 1회 강제 실행해 LLM에게 재판단 기회를 준다.
_ORIENTATION_ONLY_TOOL = "codebase_structure"
_ORIENTATION_FOLLOWUP_TOOL = "semantic_search"

TOOLS = [
    {
        "name": "codebase_structure",
        "description": "이 코드베이스가 어떤 도메인/모듈로 구성되어 있는지 개요(도메인별 파일 수) 조회 — "
                        "semantic_search/call_graph에 넣을 정확한 이름/키워드를 모르겠거나, 방금 쓴 검색이 "
                        "엉뚱한 결과만 줬을 때 먼저 이걸로 방향을 잡는다. 여기서 관련 도메인을 찾으면 그 "
                        "자체로 답하지 말고, 그 도메인 이름을 semantic_search의 domain 파라미터에 넣어 "
                        "그 안에서 실제 파일/함수를 찾아야 한다 — 도메인을 찾은 것과 근거를 찾은 것은 다르다",
        "params": {},
    },
    {
        "name": "semantic_search",
        "description": "자연어로 코드 의미 검색(예: '로그인 처리하는 함수', '이메일 인증 토큰 검증 로직')",
        "params": {
            "q": "검색할 자연어 질의 문자열",
            "domain": "(선택) codebase_structure에서 찾은 도메인 이름을 그대로 넣으면 "
                      "그 도메인에 속한 파일로만 검색 범위를 좁힌다 — 관련 없는 도메인의 "
                      "파일이 우연히 높은 유사도로 걸리는 오탐을 줄인다",
        },
    },
    {
        "name": "call_graph",
        "description": "특정 함수를 누가 호출하는지, 또는 그 함수까지의 호출 경로(위험도 전파 경로)를 조회",
        "params": {"symbol": "찾을 함수/메서드 이름 또는 설명"},
    },
    {
        "name": "dataflow_security",
        "description": "외부 입력(요청 파라미터 등)이 SQL 실행/명령 실행 같은 위험 함수까지 도달하는 경로(source→sink)를 탐지",
        "params": {"keywords": "이 질문의 구체적 관심사를 나타내는 키워드 목록(예: ['pii','개인정보'])"},
    },
    {
        "name": "dataflow_business",
        "description": "값이 서로 다른 비즈니스 도메인(예: 결제↔주문) 경계를 넘어 흐르는 함수 호출을 탐지",
        "params": {"keywords": "이 질문의 구체적 관심사를 나타내는 키워드 목록"},
    },
    {
        "name": "repo_commits",
        "description": "레포지토리의 최근 커밋 이력 조회(예: '최근에 뭐가 바뀌었어', '누가 이 부분을 고쳤어')",
        "params": {"query": "커밋 메시지에서 찾을 키워드(선택, 없으면 최신순 전체)"},
    },
    {
        "name": "repo_pull_requests",
        "description": "레포지토리의 Pull Request 목록 조회(예: '지금 열려있는 PR 뭐 있어', '이번에 머지된 PR')",
        "params": {"state": "'open' 또는 'closed'(선택, 없으면 전체)"},
    },
    {
        "name": "repo_releases",
        "description": "레포지토리의 릴리즈(태그) 목록 조회(예: '최근 릴리즈가 언제야', '어떤 버전들이 나왔어')",
        "params": {},
    },
    {
        "name": "repo_branches",
        "description": "레포지토리의 브랜치 목록 조회(예: '브랜치 뭐 있어', '기본 브랜치가 뭐야')",
        "params": {},
    },
]

_TOOL_NAMES = {t["name"] for t in TOOLS}

_STEP_PROMPT = """사용자 질문에 답할 근거를 모으는 중이다. 지금까지 실행한 도구와 결과 요약을 보고,
도구를 하나 더 불러야 하는지 판단하시오.

질문: {question}

사용 가능한 도구:
{tools_block}

이 질문에 대해 세운 조사 계획:
{plan_block}

지금까지 실행한 도구(요약):
{history_json}

규칙:
- 계획의 조사 단계 중 아직 실행하지 않은 도구가 있으면 finish 하지 마라 — 남은 단계에 해당하는 도구를 골라라.
- 지금까지 도구를 한 번도 성공적으로 실행하지 못했다면 finish 하지 마라(어떤 도구도 이 질문에 명백히 무의미한 경우만 예외).
- 위 두 경우가 아니고, 이미 답하는 데 충분한 근거를 모았거나 이 질문에 어떤 도구도 도움이 안 되면 그만 모은다(action: finish).
- 이미 시도한 도구를 같은 params로 다시 부르지 마라 — 더 얻을 근거가 없다는 뜻이다.
- semantic_search나 call_graph를 이미 불렀는데 결과가 질문과 무관해 보이거나(엉뚱한 파일/도메인)
  비어 있으면, 같은 검색어를 바꿔가며 반복하지 말고 codebase_structure로 이 코드베이스에
  실제로 어떤 도메인이 있는지 먼저 확인한 뒤 그 도메인 이름으로 다시 검색하라.
- codebase_structure만 실행한 상태에서는 절대 finish 하지 마라 — 도메인 이름을 찾은 것은
  아직 근거가 아니다. 관련 도메인을 찾았으면 그 이름을 semantic_search의 params.domain에
  넣어 그 안에서 실제 파일/함수를 찾아야 finish할 수 있다.
- semantic_search를 고를 땐 params.q에 질문을 그대로 옮기지 말고, 검색 적중률을 높이도록
  관련 동의어/영어 용어 등을 보태 확장한 질의를 넣는다
  (예: "사진은 어디에 저장되나요?" → "사진 이미지 저장 upload storage S3 파일 저장").
- call_graph를 고를 땐 params.symbol에 찾으려는 함수/메서드 이름을 최대한 정확히 넣는다.
- dataflow_security/dataflow_business를 고를 땐 params.keywords에 이 질문의 "구체적" 관심사를
  나타내는 키워드 3~6개를 넣는다(한국어/영어 혼용 가능) — 일반적인 "보안"/"취약점" 같은 단어
  말고, 질문에서 실제로 뭘 찾는지 나타내는 명사를 넣는다
  (예: "개인정보 검사 로직에 취약점 있어?" → ["pii","개인정보","주민번호","전화번호","validation"],
  "SQL 인젝션 위험있는 코드있어?" → ["sql","query","execute","인젝션"]).
- 한 스텝에 도구는 최대 하나만 고른다 — 더 필요하면 다음 스텝에서 또 고르면 된다.

아래 JSON 스키마로만 답하라 (다른 설명 금지, 코드블록 금지). "action" 값은 반드시
"call_tool" 또는 "finish" 둘 중 하나여야 한다 — 도구 이름을 "action" 자리에 직접
쓰지 마라(예: "action": "dataflow_security"는 틀렸다. "action": "call_tool",
"tool": "dataflow_security"가 맞다):
{{"action": "call_tool", "tool": "도구이름", "params": {{"q": "..."}} 또는 {{"q": "...", "domain": "..."}} 또는 {{"keywords": ["..."]}} 또는 {{"symbol": "..."}} 또는 {{}}}}
또는
{{"action": "finish"}}
"""

_PLAN_PROMPT = """사용자 질문에 답하기 위해 어떤 순서로 조사할지 1~4개의 짧은 계획을 세워라.

질문: {question}

사용 가능한 도구:
{tools_block}

규칙:
- 실제로 이 시스템이 할 수 있는 일만 계획에 넣어라(코드 검색, 호출 관계 분석, 데이터 흐름 분석,
  저장소 이력 확인 등) — 파일 수정이나 코드 실행 같은 건 이 시스템이 할 수 없다.
- 각 항목은 한국어 한 문장으로, 실제 수행할 조사 단계처럼 간결하게 쓴다
  (예: "관련 파일 탐색", "API 호출 구조 확인", "관련 커밋 이력 확인").
- 항목 앞에 "1.", "2." 같은 번호를 붙이지 마라 — 순서는 배열 순서로 이미 정해진다.
- 각 단계는 위 도구 중 하나를 실제로 실행하는 것에 대응해야 한다. "키워드 정리", "생각 정리"
  같은 도구 실행이 없는 단계는 넣지 마라.
- 질문이 매우 단순해서 계획이랄 게 없으면 steps를 1개만 넣어도 된다.

아래 JSON 스키마로만 답하라 (다른 설명 금지, 코드블록 금지):
{{"steps": ["관련 파일 탐색", "API 호출 구조 확인"]}}
"""

_RELEVANCE_PROMPT = """아래는 사용자 질문과, 도구로 수집한 근거 후보들이다. 각 그룹에서 질문에
실제로 답하는 데 쓸 수 있는 항목의 인덱스만 골라라(0부터 시작). 관련 있는 게 하나도 없으면
그 그룹은 빈 리스트로 낸다. 아래 근거 후보에 있는 도구 이름은 빠짐없이 결과에 포함시켜라.

질문: {question}

근거 후보(JSON, 각 항목에 idx 필드로 인덱스 표시):
{indexed_json}

아래 JSON 스키마로만 답하라 (다른 설명 금지, 코드블록 금지):
{{"도구이름": [관련 idx, ...], ...}}
"""

_SYNTHESIZE_PROMPT = """아래는 사용자 질문과, 그 질문에 답하기 위해 코드베이스에서 실제로 조회한 근거(evidence)다.

질문: {question}

실행한 도구: {executed_tools}
코드 분석 도구 사용 가능 여부(이 프로젝트에 완료된 스캔이 있는가): {analysis_available}

근거(JSON):
{evidence_json}

규칙:
- 반드시 위 근거만 사용해서 답하라. 근거에 없는 내용을 지어내지 마라(환각 금지).
- 답변에는 가능하면 구체적인 파일 경로, 함수 이름, 줄 번호, 도메인 이름을 인용하라.
- 근거가 부족할 때는 아래를 구분해서 솔직히 답하라(아는 척 금지):
  - 실행한 도구가 없고 질문도 코드 조회가 필요 없어 보이면: "이 질문은 코드 조회가 필요 없어 보입니다".
  - 질문이 코드/보안 분석을 요구하는데 '코드 분석 도구 사용 가능 여부'가 False이면:
    "이 프로젝트에는 완료된 코드 스캔이 없어 요청하신 분석을 수행할 수 없습니다".
  - 질문이 코드/보안 분석을 요구하는데 관련 분석 도구(dataflow_security, semantic_search, call_graph 등)를
    실행하지 않았으면: "요청하신 분석을 아직 수행하지 못했습니다. 다시 시도해 주세요".
  - 관련 분석 도구를 실행했으나 결과가 비어 있으면: "분석했으나 해당 위험/항목이 발견되지 않았습니다"
    (이때 "근거를 찾지 못했다"거나 "코드 조회가 필요 없다"고 말하지 마라).
  - 근거에 codebase_structure(도메인 개요) 결과만 있고 semantic_search/call_graph/
    dataflow_security/dataflow_business 결과가 전혀 없으면, 구체적인 파일 경로나
    함수 이름을 절대 지어내지 마라 — 이때는 관련 도메인 이름까지만 언급하고
    ("관련 도메인은 '{{도메인 이름}}'으로 보입니다") 정확한 파일/함수 위치는
    확인하지 못했다고 명시하라.
- 한국어로, 3~6문장 이내로 간결하게 답하라. 코드블록/JSON 형식 없이 자연어 문장으로만 답하라.

답변:
"""


@observability.observe(name="tool")
def _execute_tool(cur, ctx: dict, name: str, params: dict, question: str):
    """도구 하나를 실행한다. 필요한 컨텍스트(scan_id/repository_id)가 없으면 None.

    `ctx = {"scan_id": ..., "repository_id": ..., "project_info": {...} | None}` —
    scan_id/repository_id 중 하나는 없을 수 있다. scan 단위 호출
    (`/scans/{scan_id}/ask`)은 repository_id 없이 scan_id만 준다. project 단위
    채팅(`/ws/projects/{id}/chat/sessions/{session_id}`)은 project에 아직 스캔이 없으면 scan_id가
    None일 수 있는데, 그래도 repo_* 도구는 repository_id만 있으면 동작해야
    한다 — semantic_search/call_graph/dataflow만 scan_id가 필수라 scan_id가 없으면
    그 도구만 조용히 건너뛴다("스캔 없음"이 곧 "도구 실행 실패"가 되지 않게).
    repo_commits/repo_pull_requests/repo_releases는 `repo_queries.*_for_chat`
    래퍼를 쓴다 — 로컬에 동기화된 게 없으면 GitHub 라이브로 즉석 조회한다
    (REST 라우트가 쓰는 DB 전용 버전과는 별개 — release.id 드릴다운처럼 DB 행
    존재를 전제하는 흐름이 없는 채팅에서만 안전한 절충이다).

    실제 실행 로직(`search_symbols`/`call_graph_paths`/`run_dataflow`/
    `repo_queries.*`)은 REST 라우트와 공유한다 — HTTP 왕복 없이 같은 프로세스/
    트랜잭션 안에서 직접 호출한다. 접근 권한은 오케스트레이터 진입 시(엔드포인트
    에서) 이미 확인했으므로 여기서는 다시 확인하지 않는다.

    dataflow 도구는 LLM이 준 keywords로 결과를 좁힌다(질문의 구체적 관심사와
    무관한 고정 finding 세트를 그대로 근거로 주지 않기 위함).

    `@observability.observe()`로 감싸 run_agentic의 trace 아래 도구별 span을
    남긴다(`observability.label_current`로 함수명 대신 실제 도구 이름을 붙임) —
    로컬 모델이 고른 도구별 소요시간/입출력을 개별로 보기 위함.
    """
    observability.label_current(f"tool:{name}")
    scan_id = ctx.get("scan_id")
    repository_id = ctx.get("repository_id")

    if name == "codebase_structure":
        if not scan_id:
            return None
        return domain_overview(cur, scan_id)
    if name == "semantic_search":
        if not scan_id:
            return None
        q = params.get("q") or question
        domain = params.get("domain") or None
        return search_symbols(cur, scan_id, q, limit=10, domain=domain)
    if name == "call_graph":
        if not scan_id:
            return None
        symbol_query = params.get("symbol") or question
        return call_graph_paths(cur, scan_id, symbol_query, depth=3)
    if name == "dataflow_security":
        if not scan_id:
            return None
        kws = params.get("keywords") or None
        return run_dataflow(cur, scan_id, "security", limit=20, keywords=kws)["findings"]
    if name == "dataflow_business":
        if not scan_id:
            return None
        kws = params.get("keywords") or None
        return run_dataflow(cur, scan_id, "business", limit=20, keywords=kws)["findings"]
    if name == "repo_commits":
        if not repository_id:
            return None
        q = params.get("query") or None
        return repo_queries.list_commits_for_chat(cur, repository_id, limit=20, query=q)
    if name == "repo_pull_requests":
        if not repository_id:
            return None
        state = params.get("state") or None
        return repo_queries.list_pull_requests_for_chat(cur, repository_id, state=state, limit=20)
    if name == "repo_releases":
        if not repository_id:
            return None
        return repo_queries.list_releases_for_chat(cur, repository_id, limit=10)
    if name == "repo_branches":
        if not repository_id:
            return None
        repo_info = repo_queries.repo_github_info(cur, repository_id)
        return repo_queries.list_repo_branches(repo_info)
    return None


def _parse_step_action(parsed: dict) -> tuple[str, dict] | None:
    """`{"action":"call_tool","tool":"..."}`가 정석이지만, 로컬 7B 모델은 종종
    중첩 스키마를 평평하게 접어 도구 이름을 "action" 자리에 바로 써버린다
    (예: `{"action":"dataflow_security","params":{...}}`) — 실전에서 재현된
    실패 패턴이다. 이걸 곧이곧대로 "call_tool이 아니니 finish"로 해석하면
    모델이 분명히 고른 도구 호출을 조용히 잃어버리므로, 두 형태 다 허용한다.
    `_STEP_PROMPT`가 요구하는 스키마 자체를 흉내 내는 게 아니라, 모델이
    실제로 내놓는 동등한 의미의 변형만 관대하게 받아들이는 것이다.
    """
    action = parsed.get("action")
    if action == "call_tool":
        name = parsed.get("tool")
    elif action in _TOOL_NAMES:
        name = action
    elif parsed.get("tool") in _TOOL_NAMES:
        name = parsed.get("tool")
    else:
        return None
    if name not in _TOOL_NAMES:
        return None
    return name, (parsed.get("params") or {})


def _parse_step_decision(raw: str) -> tuple[str, dict] | None:
    """스텝 결정 응답 파싱 — 2단계.

    1차: `llm_schemas.StepDecision`으로 검증한다. 정상 경로(스키마 제약
    디코딩이 실제로 걸린 경우)라면 이 모양을 벗어난 응답 자체가 구조적으로
    불가능하다.
    2차: 검증이 실패하면(스키마 제약 호출이 `ai_providers.generate`의
    fail-open 재시도로 느슨한 json_mode에 떨어진 경우 — 그 경로엔 문법 제약이
    없으므로 로컬 7B가 여전히 예전처럼 평평한 모양을 낼 수 있다) 기존 관대한
    파서(`_parse_step_action`)로 한 번 더 시도한다. 이 폴백 경로가 있는 한
    `_parse_step_action`은 죽은 코드가 아니다 — 지우면 안 된다.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    try:
        decision = llm_schemas.StepDecision.model_validate(parsed)
    except ValidationError:
        return _parse_step_action(parsed)
    if decision.action == "finish" or decision.tool is None:
        return None
    params = decision.params.model_dump(exclude_none=True) if decision.params else {}
    return decision.tool, params


@observability.observe(name="plan")
def _build_plan(question: str, tools_block: str, *, model: str,
                 project_id: str | None, scan_id: str | None) -> list[str] | None:
    """계획 수립 전용 LLM 호출 — 실패해도 전체 흐름을 막지 않는다(fail-open).

    실패/파싱 오류/빈 응답이면 None을 돌려주고, 호출부는 plan 이벤트를 그냥
    건너뛴다 — 이 파일 전체의 fail-open 철학과 동일하게, "계획을 못 세웠다"가
    "질문에 답을 못 한다"가 되지 않게 한다.
    """
    prompt = _PLAN_PROMPT.format(question=question, tools_block=tools_block)
    try:
        raw = ai_providers.generate(model, prompt, response_schema=llm_schemas.PlanSteps,
                                     project_id=project_id, purpose="qa", scan_id=scan_id)
        parsed = json.loads(raw)
        steps = parsed.get("steps")
        if not isinstance(steps, list) or not steps:
            return None
        # 모델이 "1. ", "2) " 같은 번호를 붙여 오는 경우가 잦다 — 화면은 ol로 다시
        # 번호를 매기므로 그대로 두면 "1. 1. ..."로 이중 표시된다. 여기서 제거한다.
        steps = [re.sub(r"^\s*\d+[.)]\s*", "", str(s).strip()) for s in steps if str(s).strip()]
        steps = [s for s in steps if s][:4]
        return steps or None
    except Exception:
        log.exception("계획 수립 실패 — plan 이벤트 없이 진행")
        return None


def _summarize_step(name: str, params: dict, value: object) -> dict:
    """다음 스텝 프롬프트에 넣을 압축 요약.

    전체 evidence를 매번 다시 프롬프트에 실으면 스텝마다 입력이 커지므로,
    "무엇을 어떤 params로 불렀고 몇 건 나왔는지"만 남긴다 — 최종 답변 합성
    (`synthesize_answer`)에는 여전히 원본 `tool_results`가 그대로 쓰인다.

    codebase_structure만 예외: 결과의 실제 도메인 이름 목록을 남긴다. 단순
    "37건"으로만 압축하면, 다음 스텝에서 semantic_search의 domain 파라미터에
    넣을 실제 문자열을 모델이 볼 수 없어 값을 지어내거나(라이브 관측:
    "codebase_structure의 결과 도메인" 같은 플레이스홀더, 또는 파라미터 설명
    문구를 그대로 복사) `search_symbols`가 그 값을 인식 못 해 스코핑이 무력화된다
    (`app/scan_queries.py::search_symbols`의 존재하지 않는 domain 폴백 참고).
    """
    if name == _ORIENTATION_ONLY_TOOL and isinstance(value, list) and value:
        domains = list(dict.fromkeys(str(v.get("domain")) for v in value if v.get("domain")))
        result = f"{len(value)}건 — 도메인: {', '.join(domains[:10])}"
    elif isinstance(value, list):
        result = f"{len(value)}건"
    elif value:
        result = "결과 있음"
    else:
        result = "결과 없음"
    return {"tool": name, "params": params, "result": result}


def _should_force_semantic_followup(used: list[dict], ctx: dict) -> bool:
    """codebase_structure만 실행된 채 루프가 끝나려는 참인지 판단한다.

    도구를 하나도 안 부르고 finish하는 건(used가 비어있음) "코드 조회가 필요
    없다"는 정당한 케이스이므로 건드리지 않는다 — used가 정확히
    {"codebase_structure"} 하나뿐일 때만 개입한다. scan_id가 없으면
    semantic_search도 `_execute_tool`과 같은 가드로 결과 없이 끝나므로
    트리거하지 않는다.
    """
    if not ctx.get("scan_id"):
        return False
    return bool(used) and {u["name"] for u in used} == {_ORIENTATION_ONLY_TOOL}


def _force_tool_call(cur, ctx: dict, question: str, name: str, params: dict, event_id: str,
                      emit: Callable[[AgentEvent], None], used: list[dict],
                      tool_results: dict[str, object], seen: set[tuple[str, str]] | None = None) -> None:
    """LLM 판단 없이 도구 하나를 직접 실행하고 tool_call/tool_result 이벤트를 내보낸 뒤
    used/tool_results에 반영한다 — 계획 커버리지 백스톱과 오리엔테이션 전용 도구
    백스톱이 공유하는 "강제 실행" 로직(중복 제거).
    """
    tool_event = new_event(event_id, "tool_call", "running", tool=name,
                            target=tool_target_text(name, params), summary=thinking_text(name))
    emit(tool_event)
    try:
        value = _execute_tool(cur, ctx, name, params, question)
        ok = True
    except Exception:
        log.exception("강제 실행 도구 %s 실패", name)
        value = {"error": "도구 실행 중 오류가 발생했습니다"}
        ok = False
    count = len(value) if isinstance(value, list) else None
    emit(mark_done(tool_event, status="done" if ok else "error", type="tool_result",
                    summary=tool_result_text(name, ok, count)))
    used.append({"name": name, "params": params})
    if seen is not None:
        seen.add((name, json.dumps(params, sort_keys=True, ensure_ascii=False)))
    if value:
        existing = tool_results.get(name)
        if isinstance(existing, list) and isinstance(value, list):
            tool_results[name] = existing + value
        else:
            tool_results[name] = value


@observability.observe(name="agentic_loop")
def run_agentic(cur, ctx: dict, question: str, *, model: str,
                 project_id: str | None = None, scan_id: str | None = None,
                 max_steps: int = MAX_STEPS,
                 on_step: Callable[[AgentEvent], None] | None = None,
                 forced_tool: str | None = None) -> tuple[list[dict], dict]:
    """LLM이 매 스텝 도구를 하나씩 고르고, 근거가 충분하면 스스로 멈춘다.

    intent 분류나 "intent→도구" 하드코딩 없이, "지금까지 뭘 모았는지"를 보고
    다음 행동을 다시 묻는 방식이라 첫 판단이 부족해도 다음 스텝에서 만회할 수
    있다. 같은 도구를 같은 params로 다시 고르면(더 얻을 근거가 없다는 신호)
    즉시 멈춘다. LLM 호출/파싱이 실패해도 지금까지 모은 근거로 진행한다
    (fail-open) — synthesize_answer가 근거 부족을 정직하게 답한다.

    단 하나의 결정적 안전장치: 루프가 끝난 뒤, `_build_plan`이 세운 계획이
    분석 도구를 지목했는데 루프가 그 중 어느 것도 실행하지 않았고 스캔이
    존재하면(7B가 계획을 무시하고 조기 종료한 사례), 계획에 등장한 첫 번째
    미실행 분석 도구를 1회 강제 실행한다 — 키워드 매핑이 아니라 "LLM 자신이
    세운 계획을 이행"하는 것이다.

    `on_step`은 화면이 멈춘 것처럼 보이지 않게 진행 상황을 실시간으로 흘려
    보내는 용도다(`app/chat.py`가 WebSocket으로 중계). 이 루프의 정확성과는
    무관한 부가 기능이라, 콜백 자체가 실패해도(예: 소켓이 끊김) 루프는
    막지 않는다 — 이 함수 전체의 fail-open 철학과 동일.

    `forced_tool`은 채팅 입력창의 "스킬" 선택(예: 호출 그래프)에서 온다 — 첫
    스텝만 LLM의 도구 선택을 건너뛰고 그 도구를 곧바로 부른다(params는
    비워서 `_execute_tool`이 question으로 폴백하게 둔다). 이후 스텝은 평소처럼
    LLM이 이어서 판단한다 — 스킬은 "이 도구는 반드시 포함" 힌트일 뿐, 전체
    루프를 그 도구 하나로 제한하는 게 아니다.
    """
    def emit(event: AgentEvent) -> None:
        if on_step is None:
            return
        try:
            on_step(event)
        except Exception:
            log.exception("on_step 콜백 실패 — 무시하고 계속")

    tool_results: dict[str, object] = {}
    if ctx.get("project_info"):
        tool_results["project_info"] = [ctx["project_info"]]
    if ctx.get("mentioned_projects"):
        tool_results["mentioned_projects"] = ctx["mentioned_projects"]

    tools_block = "\n".join(
        f"- {t['name']}: {t['description']} (params: {t['params'] or '없음'})" for t in TOOLS
    )

    # 계획 수립은 사용자에게 보여줄 게 있을 때만(on_step이 있을 때만) 호출한다 —
    # 아무도 안 볼 계획을 위해 /scans/{scan_id}/ask 같은 비-스트리밍 호출까지
    # 추가 LLM 왕복 비용을 치르지 않게 한다. 세운 계획은 화면 표시뿐 아니라
    # 매 스텝 프롬프트(_STEP_PROMPT)와 루프 종료 후 계획 커버리지 백스톱(아래)
    # 에도 쓰인다 — "계획 따로, 실행 따로"를 막는다.
    plan_steps: list[str] | None = None
    if on_step is not None:
        # running -> done 두 번 내보낸다(도구 이벤트와 같은 패턴) — 그래야 plan
        # 이벤트에 finished_at이 채워져 화면의 경과 시간이 계획 수립에 실제로
        # 걸린 시간으로 고정된다(안 그러면 finished_at=None이라 now까지 계속 증가).
        plan_event = new_event("plan-0", "plan", "running", summary="조사 계획을 세우는 중...")
        emit(plan_event)
        plan_steps = _build_plan(question, tools_block, model=model,
                                  project_id=project_id, scan_id=scan_id)
        if plan_steps:
            emit(mark_done(plan_event, plan_steps=plan_steps, summary="계획을 세웠습니다"))
        else:
            emit(mark_done(plan_event, summary="계획 없이 조사를 시작합니다"))
    plan_block = "\n".join(plan_steps) if plan_steps else "(계획 없음)"

    used: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for step_index in range(max_steps):
        thinking_event = new_event(f"thinking-{step_index}", "thinking", "running",
                                    tool=None, summary=thinking_text(None))
        emit(thinking_event)

        if step_index == 0 and forced_tool in _TOOL_NAMES:
            step_action = (forced_tool, {})
        else:
            history = [_summarize_step(u["name"], u["params"], tool_results.get(u["name"])) for u in used]
            prompt = _STEP_PROMPT.format(
                tools_block=tools_block,
                plan_block=plan_block,
                question=question,
                history_json=json.dumps(history, ensure_ascii=False) if history else "(아직 없음)",
            )

            try:
                raw = ai_providers.generate(model, prompt, response_schema=llm_schemas.StepDecision,
                                             project_id=project_id, purpose="qa", scan_id=scan_id)
            except Exception:
                log.exception("에이전틱 스텝 실패 — 지금까지 모은 근거로 종료")
                emit(mark_done(thinking_event, status="error"))
                if _should_force_semantic_followup(used, ctx):
                    _force_tool_call(cur, ctx, question, _ORIENTATION_FOLLOWUP_TOOL, {},
                                      f"tool-{step_index}-forced", emit, used, tool_results, seen)
                    continue
                break

            step_action = _parse_step_decision(raw)
            if step_action is None:
                emit(mark_done(thinking_event))
                if _should_force_semantic_followup(used, ctx):
                    _force_tool_call(cur, ctx, question, _ORIENTATION_FOLLOWUP_TOOL, {},
                                      f"tool-{step_index}-forced", emit, used, tool_results, seen)
                    continue
                break
        emit(mark_done(thinking_event))
        name, params = step_action

        step_key = (name, json.dumps(params, sort_keys=True, ensure_ascii=False))
        if step_key in seen:
            # 같은 도구를 같은 params로 또 고른 건 "더 얻을 근거가 없다"는 신호라
            # 원래는 그냥 멈춘다 — 단, codebase_structure만 실행한 채 그 codebase_structure를
            # 또 고른 경우(실전 관측: 로컬 7B가 다음 수를 못 정하고 방금 한 걸 반복)는
            # 여전히 얇은 근거뿐이므로 finish/파싱실패와 동일하게 게이트를 거친다.
            if _should_force_semantic_followup(used, ctx):
                _force_tool_call(cur, ctx, question, _ORIENTATION_FOLLOWUP_TOOL, {},
                                  f"tool-{step_index}-forced", emit, used, tool_results, seen)
                continue
            break
        seen.add(step_key)

        tool_event = new_event(f"tool-{step_index}", "tool_call", "running", tool=name,
                                target=tool_target_text(name, params),
                                summary=thinking_text(name))
        emit(tool_event)
        try:
            value = _execute_tool(cur, ctx, name, params, question)
            ok = True
        except Exception:
            log.exception("도구 %s 실행 실패 — 건너뜀", name)
            value = {"error": "도구 실행 중 오류가 발생했습니다"}
            ok = False
        count = len(value) if isinstance(value, list) else None
        emit(mark_done(tool_event, status="done" if ok else "error", type="tool_result",
                        summary=tool_result_text(name, ok, count)))

        used.append({"name": name, "params": params})
        if value:
            existing = tool_results.get(name)
            # 같은 도구를 다른 params로 두 번 이상 고를 수 있다(예: semantic_search를
            # 검색어를 바꿔가며 재시도) — 두 번째 호출 결과로 덮어쓰면 첫 근거를
            # 잃으므로, 둘 다 리스트면 이어붙인다.
            if isinstance(existing, list) and isinstance(value, list):
                tool_results[name] = existing + value
            else:
                tool_results[name] = value

    # 계획 커버리지 백스톱 — 계획이 코드 분석 도구를 지목했는데 루프가 하나도
    # 실행하지 않고 끝났으면(7B가 계획을 무시하고 엉뚱한 도구만 고른 뒤 조기
    # 종료한 사례), 계획에 등장한 첫 번째 미실행 분석 도구를 1회만 강제 실행한다.
    # intent 분류나 키워드 하드코딩이 아니라 "LLM이 스스로 세운 계획을 이행"하는
    # 것이고, 스캔이 있어야(=분석 도구가 실제로 돌 수 있어야) 트리거한다
    # (`_execute_tool`이 보는 것과 같은 `ctx["scan_id"]`).
    if plan_steps and ctx.get("scan_id"):
        ran = {u["name"] for u in used}
        plan_text = " ".join(plan_steps)
        pending = [t for t in _ANALYSIS_TOOLS if t in plan_text and t not in ran]
        if pending and not (ran & set(_ANALYSIS_TOOLS)):
            _force_tool_call(cur, ctx, question, pending[0], {}, "tool-backstop", emit, used, tool_results)

    return used, tool_results


@observability.observe(name="relevance_filter")
def check_relevance(question: str, tool_results: dict, *, model: str,
                     project_id: str | None = None, scan_id: str | None = None) -> dict:
    """LLM에 근거를 넘기기 전에 실제로 질문과 관련 있는 항목만 남긴다.

    dataflow는 keywords로 이미 좁혔지만, semantic_search는 pgvector 특성상
    관련성과 무관하게 top-N을 항상 반환한다 — 이 단계가 특히 semantic_search
    에 중요하다(관측 사례: 관련 없는 top-N을 근거 삼아 엉뚱한 답을 만듦).

    실패해도 정상 흐름을 막으면 안 되므로 실패 시 원본을 그대로 통과시킨다
    (fail-open). 마찬가지로 LLM이 특정 도구를 언급 안 했으면(응답에서
    빠뜨렸으면) "관련 없음"이 아니라 원본을 그대로 유지한다 — "관련
    없다고 명시함"과 "실수로 빠뜨림"을 구분해서, 후자로 근거를 잃지 않는다.
    """
    tool_results = {k: v for k, v in tool_results.items() if isinstance(v, list) and v}
    if not tool_results:
        return tool_results

    indexed = {name: [{"idx": i, **item} for i, item in enumerate(items)]
               for name, items in tool_results.items()}
    prompt = _RELEVANCE_PROMPT.format(
        question=question,
        indexed_json=json.dumps(indexed, ensure_ascii=False, default=str),
    )

    try:
        raw = ai_providers.generate(model, prompt, json_mode=True,
                                     project_id=project_id, purpose="qa", scan_id=scan_id)
        parsed = json.loads(raw)
    except Exception:
        log.exception("근거 관련성 검증 실패 — 원본 그대로 진행")
        return tool_results

    filtered: dict[str, object] = {}
    for name, items in tool_results.items():
        if name not in parsed:
            filtered[name] = items  # LLM이 언급 안 함 — fail-open, 원본 유지
            continue
        keep_idx = set(parsed.get(name) or [])
        kept = [item for i, item in enumerate(items) if i in keep_idx]
        if kept:
            filtered[name] = kept
    return filtered


def _synthesize_prompt(question: str, tool_results: dict, executed_tools, analysis_available: bool) -> str:
    return _SYNTHESIZE_PROMPT.format(
        question=question,
        executed_tools=", ".join(executed_tools) if executed_tools else "(없음)",
        analysis_available="True" if analysis_available else "False",
        evidence_json=json.dumps(tool_results, ensure_ascii=False, default=str),
    )


def _no_evidence_answer(executed_tools, analysis_available: bool) -> str:
    if executed_tools and not analysis_available:
        return "이 프로젝트에는 완료된 코드 스캔이 없어 요청하신 분석을 수행할 수 없습니다."
    if executed_tools:
        return "요청하신 분석을 수행했으나 관련 근거를 찾지 못했습니다."
    return "이 질문에는 코드 조회가 필요 없어 보이거나, 답할 근거를 찾지 못했습니다."


@observability.observe(name="synthesize")
def synthesize_answer(question: str, tool_results: dict, *, model: str,
                       project_id: str | None = None, scan_id: str | None = None,
                       executed_tools=(), analysis_available: bool = True) -> str:
    if not tool_results:
        return _no_evidence_answer(executed_tools, analysis_available)

    prompt = _synthesize_prompt(question, tool_results, executed_tools, analysis_available)
    try:
        return ai_providers.generate(model, prompt, json_mode=False,
                                      project_id=project_id, purpose="qa", scan_id=scan_id).strip()
    except Exception:
        log.exception("답변 합성 실패")
        return "근거는 찾았지만 답변을 생성하는 중 오류가 발생했습니다."


@observability.observe(name="synthesize")
async def synthesize_answer_stream(question: str, tool_results: dict, *, model: str,
                                    project_id: str | None = None, scan_id: str | None = None,
                                    executed_tools=(), analysis_available: bool = True):
    """synthesize_answer의 스트리밍 버전 — WebSocket 채팅(app/chat.py) 전용.

    프롬프트/근거-없음 처리는 동기 버전과 동일하게 맞춘다. 실패해도 흐름이
    끊기면 안 되므로 예외 시 안내 문구 하나를 통째로 yield하고 끝낸다(부분
    출력 후 에러가 나도 최소한 사용자에게 상태를 알린다).
    """
    if not tool_results:
        yield _no_evidence_answer(executed_tools, analysis_available)
        return

    prompt = _synthesize_prompt(question, tool_results, executed_tools, analysis_available)
    try:
        async for chunk in ai_providers.agenerate_stream(model, prompt, project_id=project_id,
                                                           purpose="qa", scan_id=scan_id):
            yield chunk
    except Exception:
        log.exception("답변 합성(스트리밍) 실패")
        yield "\n\n근거는 찾았지만 답변을 생성하는 중 오류가 발생했습니다."
