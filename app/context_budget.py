"""LLM 프롬프트에 싣는 근거(evidence)의 토큰 예산 관리.

도구 결과를 통째로 `json.dumps` 해서 프롬프트에 넣으면 dataflow 경로 체인·커밋
메시지·반복 검색 결과가 쌓여 로컬 모델의 컨텍스트(vLLM `--max-model-len 8192`,
deploy/compose.llm.yml)를 넘는다. 넘으면 vLLM은 400으로 실패하고 Ollama는 앞부분을
조용히 잘라 지시문이 사라진다 — 어느 쪽이든 답변 품질이 무너진다.

여기서 하는 일은 두 가지다.
1. `slim_item` — 항목별로 LLM에 의미 없는 필드(내부 키, 임베딩 등)를 빼고 긴 값을 줄인다.
2. `pack_evidence` — 도구 우선순위대로 예산 안에 들어가는 만큼만 담고, 잘라낸 개수를
   `truncated`로 돌려준다(프롬프트에 "일부 생략" 사실을 알리기 위함).

토큰 수는 토크나이저 없이 문자 수로 어림한다(의존성 추가 없이). 한국어·코드 혼합
프롬프트에서 Qwen 토크나이저 실측(scripts/eval_chat.py의 prompt_chars/prompt_tokens)
평균 1.88자/토큰, 최소 1.57자/토큰 — 한국어는 토큰 효율이 낮다. 최소치 쪽으로 잡는다.
"""

from __future__ import annotations

import json

# 넘치는 것보다 덜 채우는 게 안전하다 — 넘치면 vLLM은 400, Ollama는 앞부분(지시문)을 조용히 자른다.
_CHARS_PER_TOKEN = 1.6

# 근거로서의 가치가 높은 순서. 실제 코드 > 경로가 있는 흐름 분석 > 호출 관계 > 의미 검색 >
# 저장소 이력 > 도메인 개요. 목록에 없는 키(project_info 등)는 맨 앞(작고 항상 필요).
TOOL_PRIORITY = (
    "read_code", "dataflow_security", "call_graph", "dataflow_business",
    "semantic_search", "repo_commits", "repo_pull_requests", "repo_releases",
    "repo_branches", "codebase_structure",
)

# LLM이 답변에 쓸 일이 없는 내부 식별자 — symbol_key는 path/qualified_name과 중복이다.
_DROP_KEYS = {"symbol_key", "source_symbol_key", "sink_symbol_key", "caller_symbol_key",
              "callee_symbol_key", "embedding", "id", "author_email", "head_sha", "fingerprint"}
_MAX_STR = 300
_MAX_CODE_CHARS = 4000
_MAX_CHAIN = 5


def estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _shorten(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit] + "…"


def _slim_chain(chain: list) -> list:
    """dataflow/call_graph 경로가 길면 앞 2홉 + 생략 + 뒤 2홉만 남긴다."""
    if len(chain) <= _MAX_CHAIN:
        return chain
    return [*chain[:2], f"…({len(chain) - 4}단계 생략)…", *chain[-2:]]


def slim_item(item: object) -> object:
    if not isinstance(item, dict):
        return _shorten(item, _MAX_STR) if isinstance(item, str) else item
    out: dict = {}
    for k, v in item.items():
        if k in _DROP_KEYS or v is None or v == "" or v == []:
            continue
        if k == "code" and isinstance(v, str):
            out[k] = _shorten(v, _MAX_CODE_CHARS)
        elif isinstance(v, str):
            out[k] = _shorten(v, _MAX_STR)
        elif isinstance(v, list) and k in ("path", "chain"):
            out[k] = _slim_chain([slim_item(x) for x in v])
        elif isinstance(v, float):
            out[k] = round(v, 3)
        else:
            out[k] = slim_item(v) if isinstance(v, dict) else v
    return out


def _ordered_names(tool_results: dict) -> list[str]:
    rank = {name: i for i, name in enumerate(TOOL_PRIORITY)}
    return sorted(tool_results, key=lambda n: rank.get(n, -1))


def pack_evidence(tool_results: dict, budget_tokens: int) -> tuple[dict, dict[str, int]]:
    """우선순위 순서로 예산 안에 들어가는 항목만 담는다.

    반환: (담긴 근거, {도구이름: 잘라낸 항목 수}). 리스트가 아닌 값은 통째로 1항목 취급.
    한 항목이 혼자 예산을 넘으면 건너뛰고 다음(더 작은) 항목을 계속 시도한다 —
    큰 read_code 하나 때문에 나머지 근거가 전부 빠지지 않게.
    """
    packed: dict = {}
    truncated: dict[str, int] = {}
    used = 2  # 바깥 중괄호
    for name in _ordered_names(tool_results):
        value = tool_results[name]
        items = value if isinstance(value, list) else [value]
        kept = []
        overhead = estimate_tokens(_dumps(name)) + 2
        for item in items:
            slim = slim_item(item)
            cost = estimate_tokens(_dumps(slim)) + 1
            if used + overhead + cost > budget_tokens:
                truncated[name] = truncated.get(name, 0) + 1
                continue
            kept.append(slim)
            used += cost
        if kept:
            used += overhead
            packed[name] = kept if isinstance(value, list) else kept[0]
    return packed, truncated


def truncation_note(truncated: dict[str, int]) -> str:
    if not truncated:
        return ""
    parts = ", ".join(f"{name} {n}건" for name, n in truncated.items())
    return f"(컨텍스트 한도 때문에 일부 근거를 생략했다: {parts})"
