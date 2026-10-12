"""LLM 실행 추적(Langfuse, 자체호스팅, 개발 전용).

`app/ai_providers.py`는 게이트웨이(LiteLLM) 하나로만 LLM을 호출하고, 게이트웨이가
자체 success/failure_callback(langfuse)으로 "어떤 요청이 실제로 어느 upstream에
갔나"를 이미 추적한다(litellm/config.yaml). 이 모듈은 그 위에 "오케스트레이터가
왜 그 호출을 했나" — 계획 수립→에이전틱 도구 선택→관련성 필터→답변 합성 —
체인 구조를 앱 프로세스 쪽에서 덧붙인다. 로컬 7B 모델의 답변 품질을 진단하려면
프롬프트/응답뿐 아니라 "어떤 도구를 왜 골랐는지"까지 봐야 하기 때문이다.

fail-open: `langfuse` 패키지가 없거나 LANGFUSE_PUBLIC_KEY/SECRET_KEY 가 비어
있으면 `observe`는 원본 함수를 그대로 돌려주고 `litellm_trace_metadata`는 빈
dict를 돌려준다 — 앱 동작에는 절대 영향 없다.
"""

from __future__ import annotations

import inspect
import logging
from functools import wraps

from app.config import settings

log = logging.getLogger("app.observability")

try:
    from langfuse.decorators import langfuse_context, observe as _observe
    _IMPORT_OK = True
except Exception:
    _IMPORT_OK = False
    langfuse_context = None  # type: ignore[assignment]
    _observe = None  # type: ignore[assignment]


_configured = False


def _enabled() -> bool:
    """Langfuse 로 추적할 수 있는 상태인지 — 매 호출마다 재평가한다(키가
    런타임에 없다가 생기는 시나리오는 없지만, 실패해도 다음 호출에서 다시
    시도할 수 있게 캐시하지 않는다. `_configured`만 "클라이언트 설정 1회 성공"을 기억).
    """
    global _configured
    if not _IMPORT_OK:
        return False
    s = settings()
    if not (s.langfuse_public_key and s.langfuse_secret_key):
        return False
    if not _configured:
        try:
            langfuse_context.configure(
                public_key=s.langfuse_public_key,
                secret_key=s.langfuse_secret_key,
                host=s.langfuse_host,
            )
            _configured = True
        except Exception:
            log.warning("Langfuse 클라이언트 설정 실패 — 추적 없이 진행", exc_info=True)
            return False
    return True


def observe(*d_args, **d_kwargs):
    """`langfuse.decorators.observe`의 fail-open 래퍼.

    비동기 제너레이터(`async def ... yield`)도 지원한다 — SDK의 `observe`는
    `asyncio.iscoroutinefunction()`으로 코루틴 함수만 async 경로(`_async_observe`)로
    보내고, 비동기 제너레이터 함수는 (코루틴이 아니므로) sync 경로로 가는데,
    그 안에서 호출 결과가 async generator 인지 감지해 값 하나하나를 그대로
    다시 yield 하는 래퍼로 감싼다(`_wrap_async_generator_result`) — 그래서
    이 래퍼도 async generator 함수는 코루틴이 아닌 것으로 취급해 sync_wrapper로
    보낸다(await 없이 그대로 호출·반환해야 제너레이터 객체가 정상적으로 나온다).
    """
    def decorator(fn):
        if not _IMPORT_OK:
            return fn
        observed = _observe(*d_args, **d_kwargs)(fn)

        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def async_wrapper(*args, **kwargs):
                if _enabled():
                    return await observed(*args, **kwargs)
                return await fn(*args, **kwargs)
            return async_wrapper

        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            if _enabled():
                return observed(*args, **kwargs)
            return fn(*args, **kwargs)
        return sync_wrapper
    return decorator


def label_current(name: str) -> None:
    """현재 관측(observation)의 표시 이름을 바꾼다 — `_execute_tool`처럼 함수 하나가
    매 호출마다 다른 논리적 작업(도구 이름)을 수행할 때, 데코레이터가 고정으로
    붙이는 함수명 대신 실제 도구 이름으로 Langfuse UI에 보이게 한다. no-op 이면
    조용히 무시한다(fail-open)."""
    if not _enabled():
        return
    try:
        langfuse_context.update_current_observation(name=name)
    except Exception:
        pass


def tag_current_trace(*, session_id: str | None = None, project_id: str | None = None) -> None:
    """현재 trace에 session_id/metadata를 붙인다 — Langfuse UI에서 채팅 세션별로
    trace를 묶어 보기 위함. no-op이면 조용히 무시한다(fail-open)."""
    if not _enabled():
        return
    try:
        langfuse_context.update_current_trace(
            session_id=session_id, metadata={"project_id": project_id},
        )
    except Exception:
        pass


def litellm_trace_metadata(purpose: str, project_id: str | None, scan_id: str | None) -> dict:
    """게이트웨이 호출(`app/ai_providers.py`)에 실어 보낼 Langfuse 메타데이터.

    현재 활성 trace(=가장 가까운 `@observe` 스코프)가 있으면 그 아래 generation으로
    붙인다(`existing_trace_id`/`parent_observation_id` — litellm 쪽 langfuse
    콜백이 인식하는 키). 활성 trace가 없어도(예: 워커의 annotate/embed 호출처럼
    `@observe` 밖에서 부르는 경우) `generation_name`/`trace_metadata`만 채워서
    게이트웨이가 스스로 만드는 독립 trace를 검색 가능하게 태그한다.
    """
    if not _enabled():
        return {}
    try:
        trace_id = langfuse_context.get_current_trace_id()
        observation_id = langfuse_context.get_current_observation_id()
    except Exception:
        trace_id = None
        observation_id = None

    meta: dict = {
        "generation_name": purpose,
        "trace_metadata": {"purpose": purpose, "project_id": project_id, "scan_id": scan_id},
    }
    if trace_id:
        meta["existing_trace_id"] = trace_id
        if observation_id:
            meta["parent_observation_id"] = observation_id
    return meta
