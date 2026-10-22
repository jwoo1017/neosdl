"""게이트웨이 별칭 → 화면 표시/집계용 메타데이터.

litellm/config.yaml(config.internal.yaml) 의 model_name 과 맞춘다. 라우팅은 게이트웨이가
하므로 여기 값은 표시(GET /api/models 드롭다운)와 ai_call_log.provider 집계에만 쓴다.
여기에 없는 별칭이 게이트웨이에 있으면 provider "other" / 별칭 그대로 label 로 노출된다.
"""

from __future__ import annotations

MODEL_CATALOG: list[dict] = [
    {"id": "claude-opus-5", "label": "Claude Opus 5", "provider": "anthropic", "group": "가장 어려운 작업에 적합", "starred": True},
    {"id": "gpt-5.1", "label": "GPT-5.1", "provider": "openai", "group": "가장 어려운 작업에 적합"},
    {"id": "grok-4", "label": "Grok 4", "provider": "xai", "group": "가장 어려운 작업에 적합"},
    {"id": "deepseek-reasoner", "label": "DeepSeek Reasoner", "provider": "deepseek", "group": "가장 어려운 작업에 적합"},
    {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "provider": "anthropic", "group": "Claude"},
    {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5", "provider": "anthropic", "group": "Claude"},
    {"id": "claude-fable-5-1", "label": "Claude Fable 5.1", "provider": "anthropic", "group": "Claude"},
    {"id": "gpt-5.1-mini", "label": "GPT-5.1 mini", "provider": "openai", "group": "GPT"},
    {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "provider": "gemini", "group": "Gemini", "starred": True},
    {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "provider": "gemini", "group": "Gemini"},
    {"id": "deepseek-chat", "label": "DeepSeek Chat", "provider": "deepseek", "group": "그 외"},
    {"id": "mistral-large", "label": "Mistral Large", "provider": "mistral", "group": "그 외"},
    {"id": "kimi-k2", "label": "Kimi K2", "provider": "kimi", "group": "그 외"},
    {"id": "llama-3.3-70b", "label": "Llama 3.3 70B", "provider": "meta", "group": "그 외"},
    {"id": "local-qwen-coder", "label": "Qwen2.5 Coder (로컬)", "provider": "local", "group": "로컬"},
]

_PROVIDER_BY_ID = {m["id"]: m["provider"] for m in MODEL_CATALOG}


def provider_of(alias: str) -> str:
    """별칭이 실제로 어느 upstream 으로 나가는지(집계용). local-* 는 서버 GPU(vLLM/TEI)."""
    if alias in _PROVIDER_BY_ID:
        return _PROVIDER_BY_ID[alias]
    return "local" if alias.startswith("local-") else "other"


# 외부 모델은 모두 128k 이상이지만, 근거를 많이 싣는다고 답이 좋아지지 않고 비용·지연만
# 는다 — 채팅 근거 예산 계산용 상한으로 32k를 쓴다.
_EXTERNAL_CONTEXT_CAP = 32768


def context_window(alias: str) -> int:
    """채팅 프롬프트 예산(app/context_budget.py)의 기준이 되는 컨텍스트 길이(토큰)."""
    from app.config import settings

    if provider_of(alias) == "local":
        return settings().local_model_context
    return _EXTERNAL_CONTEXT_CAP
