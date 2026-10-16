"""AI 모델 호출 — 전량 LiteLLM 게이트웨이(OpenAI 호환 /v1)를 통과 + 호출마다 ai_call_log 기록.

과거엔 provider(ollama/bedrock)별로 분기했지만, 이제 모든 LLM egress 는
`settings().litellm_base_url` 의 게이트웨이 하나만 호출한다. 어떤 upstream
provider(Anthropic/OpenAI/Gemini/로컬 Ollama ...)로 나갈지는 게이트웨이가
`litellm/config.yaml` 의 model_name(공개 별칭)을 보고 결정한다.

- `provider` 인자는 하위 호환/로깅용으로만 남는다(라우팅에 쓰지 않는다).
- `model` 은 게이트웨이 별칭(예: "claude-sonnet-5", "local-qwen-coder"). 비어 있거나
  레거시 Ollama 모델명이면 `settings().default_chat_model` 로 떨어진다.
- 게이트웨이가 죽어 있거나 키가 없으면 예외가 나고, 호출부(orchestrator 등)의
  기존 fail-open 경로가 그대로 흡수한다.
"""

from __future__ import annotations

import json
import logging
import time

import httpx
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app import observability
from app.config import settings
from app.db import tx

log = logging.getLogger("app.ai_providers")

# 스키마 제약 호출이 실패했을 때 재시도할 만한 상태코드만 — 401(인증)/429(rate
# limit)는 스키마를 빼도 결과가 똑같이 실패하므로 재시도가 추가 부하만 만든다.
_SCHEMA_RETRY_STATUSES = frozenset({400, 422, 500, 503, 504})

# 정보용 — 라우팅 분기는 더 이상 없다. project.*_model_provider 에 남아 있을 수 있는 값.
SUPPORTED_PROVIDERS = ("gateway", "ollama")

# 게이트웨이 별칭이 아니라 "provider 이름"이나 레거시 Ollama 모델명이 model 로
# 넘어오는 경우(예: 기존 project 행의 qa_model_name='' → settings().ollama_model),
# 전역 기본 별칭으로 정규화한다.
_LEGACY_MODEL_ALIASES = {"ollama", "bedrock", ""}


def _resolve_model(model: str | None) -> str:
    name = (model or "").strip()
    if not name or name in _LEGACY_MODEL_ALIASES or name == settings().ollama_model:
        return settings().default_chat_model
    return name


def _headers() -> dict[str, str]:
    key = settings().litellm_api_key
    h = {"content-type": "application/json"}
    if key:
        h["authorization"] = f"Bearer {key}"
    return h


def _chat_url() -> str:
    return f"{settings().litellm_base_url.rstrip('/')}/v1/chat/completions"


def _schema_response_format(schema: type[BaseModel]) -> dict:
    """Pydantic 모델 → OpenAI 호환 `response_format`(json_schema 변형).

    LiteLLM이 프로바이더별로 번역한다 — Ollama는 이 스키마를 네이티브 `format`
    필드로 그대로 받아 llama.cpp가 GBNF 문법으로 컴파일해 실제 디코딩을
    제약한다(프롬프트로 "JSON으로 답하라" 부탁하는 것과 질적으로 다르다).
    `strict: true`는 일부러 안 넣는다 — OpenAI strict 모드는 모든 필드를
    required로 강제하는데 우리 스키마 상당수가 의도적으로 optional 필드를
    쓴다(예: StepDecision.tool은 finish일 때 None).
    """
    return {
        "type": "json_schema",
        "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
    }


def _payload(model: str, prompt: str, *, json_mode: bool, stream: bool,
             response_schema: type[BaseModel] | None = None, metadata: dict | None = None) -> dict:
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
    }
    if response_schema is not None:
        body["response_format"] = _schema_response_format(response_schema)
    elif json_mode:
        body["response_format"] = {"type": "json_object"}
    if stream:
        body["stream_options"] = {"include_usage": True}
    if metadata:
        body["metadata"] = metadata
    return body


def _schema_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True  # 복잡한 스키마의 GBNF 문법 컴파일이 시간을 넘겼을 가능성
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _SCHEMA_RETRY_STATUSES
    return False


def generate(provider: str, model: str, prompt: str, *, json_mode: bool = False,
             response_schema: type[BaseModel] | None = None, timeout: int = 120,
             project_id: str | None = None, purpose: str = "qa", scan_id: str | None = None) -> str:
    """게이트웨이 별칭 `model` 로 LLM 을 1회 호출한다. project_id 가 있으면 성공/실패 무관 ai_call_log 1행.

    `response_schema`(Pydantic 모델)를 주면 grammar/schema-constrained decoding을
    요청한다(`json_mode`보다 우선) — 스키마 자체가 원인일 만한 실패(400/422/5xx/
    타임아웃)면 느슨한 `json_mode=True`로 1회만 자동 재시도한다(fail-open).
    401/429처럼 스키마와 무관한 실패는 재시도하지 않는다. 재시도가 발동하면
    Langfuse trace metadata에 `schema_fallback: true`를 남겨 실제 폴백 빈도를
    관측할 수 있게 한다.
    """
    resolved = _resolve_model(model)
    started = time.monotonic()
    error: str | None = None
    usage: dict = {}
    meta = observability.litellm_trace_metadata(purpose, project_id, scan_id)
    schema, mode = response_schema, json_mode
    try:
        for attempt in (1, 2):
            try:
                resp = httpx.post(_chat_url(), headers=_headers(),
                                  json=_payload(resolved, prompt, json_mode=mode, response_schema=schema,
                                                stream=False, metadata=meta),
                                  timeout=timeout)
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage") or {}
                return (data["choices"][0]["message"]["content"] or "")
            except Exception as exc:
                if attempt == 1 and schema is not None and _schema_retryable(exc):
                    log.warning("스키마 제약 생성 실패(%s) — json_mode(느슨)로 1회 재시도: %s",
                               resolved, _describe(exc))
                    schema, mode = None, True
                    meta = {**meta, "schema_fallback": True} if meta else {"schema_fallback": True}
                    continue
                error = _describe(exc)
                raise
        raise AssertionError("unreachable")  # 위 루프는 항상 return/raise로 끝난다
    finally:
        # 재시도가 몇 번 일어나든 ai_call_log는 이 generate() 호출 전체에 1행만
        # 남긴다(기존 계약 유지) — latency는 재시도 포함 전체 소요시간, success/error는
        # 최종 결과 기준.
        if project_id:
            _log_call(project_id, purpose, provider, resolved, scan_id,
                      latency_ms=int((time.monotonic() - started) * 1000),
                      success=error is None, error=error, usage=usage)


async def agenerate_stream(provider: str, model: str, prompt: str, *, timeout: int = 120,
                           project_id: str | None = None, purpose: str = "qa", scan_id: str | None = None):
    """게이트웨이 별칭 `model` 로 스트리밍 호출해 텍스트 조각을 그대로 yield 한다.

    구조화(JSON) 출력은 스트리밍할 이유가 없으므로(파싱은 완결된 문서가 필요)
    자유 형식 답변 합성(synthesize_answer_stream) 전용이다 — 라우팅/관련성
    판단은 여전히 동기 generate(json_mode=True)를 쓴다.
    """
    resolved = _resolve_model(model)
    started = time.monotonic()
    error: str | None = None
    usage: dict = {}
    meta = observability.litellm_trace_metadata(purpose, project_id, scan_id)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=None)) as client:
            async with client.stream("POST", _chat_url(), headers=_headers(),
                                     json=_payload(resolved, prompt, json_mode=False, stream=True, metadata=meta)) as resp:
                if resp.is_error:
                    # 스트리밍 응답은 본문을 안 읽은 상태라 _describe() 가 text 에 접근하면
                    # ResponseNotRead 로 다시 터져 게이트웨이 에러 원문이 사라진다 — 먼저 읽어 둔다.
                    await resp.aread()
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("usage"):
                        usage = obj["usage"]
                    for choice in obj.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield delta
    except Exception as exc:
        error = _describe(exc)
        raise
    finally:
        if project_id:
            await run_in_threadpool(
                _log_call, project_id, purpose, provider, resolved, scan_id,
                latency_ms=int((time.monotonic() - started) * 1000),
                success=error is None, error=error, usage=usage,
            )


_MODELS_CACHE: dict = {"at": 0.0, "ids": ()}


def available_models(*, ttl: int = 300) -> list[str]:
    """게이트웨이가 현재 서빙 중인 model_name 목록(공개 별칭). 5분 캐시.

    요청별 모델 선택 검증(허용 목록)과 GET /models 응답에 쓴다. 게이트웨이가
    응답하지 않으면 직전 캐시(없으면 빈 목록) — 호출부는 "검증 불가"로 보고
    요청값을 그대로 신뢰하거나 기본값으로 폴백한다.
    """
    now = time.monotonic()
    if _MODELS_CACHE["ids"] and now - _MODELS_CACHE["at"] < ttl:
        return list(_MODELS_CACHE["ids"])
    try:
        resp = httpx.get(f"{settings().litellm_base_url.rstrip('/')}/v1/models",
                         headers=_headers(), timeout=10)
        resp.raise_for_status()
        ids = tuple(m["id"] for m in resp.json().get("data", []) if m.get("id"))
        _MODELS_CACHE.update(at=now, ids=ids)
    except Exception:
        log.warning("게이트웨이 모델 목록 조회 실패 — 검증 없이 진행", exc_info=True)
    return list(_MODELS_CACHE["ids"])


def resolve_requested_model(requested: str | None, *, project_default: str | None = None) -> str:
    """요청 model_override → 프로젝트 기본 → 전역 기본 순으로 별칭을 고른다.

    "auto" 또는 게이트웨이 목록에 없는 값은 전역 기본으로 떨어진다(fail-open).
    게이트웨이 목록을 못 받아오면 검증을 건너뛰고 요청값을 그대로 신뢰한다.
    """
    default = settings().default_chat_model
    for candidate in (requested, project_default):
        name = (candidate or "").strip()
        if not name or name == "auto" or name in _LEGACY_MODEL_ALIASES or name == settings().ollama_model:
            continue
        known = available_models()
        if not known or name in known:
            return name
    return default


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            body = exc.response.text[:300]
        except httpx.ResponseNotRead:
            body = "(응답 본문 없음)"
        return f"게이트웨이 {exc.response.status_code}: {body}"
    return str(exc)


def _log_call(project_id: str, purpose: str, provider: str, model: str, scan_id: str | None,
              *, latency_ms: int, success: bool, error: str | None, usage: dict | None = None) -> None:
    # 통계 기록 실패로 실제 AI 호출/응답 흐름이 막히면 안 되므로 독립 트랜잭션 + fail-open.
    usage = usage or {}
    try:
        with tx() as cur:
            cur.execute(
                """
                INSERT INTO ai_call_log (project_id, purpose, provider, model, scan_id,
                                         latency_ms, success, error, prompt_tokens, completion_tokens)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (project_id, purpose, provider, model, scan_id, latency_ms, success, error,
                 usage.get("prompt_tokens"), usage.get("completion_tokens")),
            )
    except Exception:
        log.exception("ai_call_log 기록 실패 — 통계만 손실, 호출 자체는 이미 끝남")
