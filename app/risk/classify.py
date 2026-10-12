"""Business/IP 분류용 내부 LLM 인터페이스(phase2 plan §8) + 질문 의도 분류(§11).

LLM 은 코드 전체를 임의로 판단하지 않는다 — NeoSDL 이 추출한 evidence 만 보고
판단한다(§8). 이 파일은 evidence → 분류 프롬프트 변환과 fail-open 파싱만
담당하고, 실제 호출은 `ai_providers.generate(..., json_mode=True)` 를 그대로 쓴다
(orchestrator/annotate 와 동일 경로 — LiteLLM 게이트웨이).

실패/파싱오류/빈 응답이면 None 을 돌려준다. `synthesis.py` 는 그때 static
evidence + Rule Engine 만으로 Risk Object 를 만든다("분류를 못 했다"가 "판정을
못 한다"가 되지 않게 — 이 프로젝트 전반의 fail-open 철학).
"""

from __future__ import annotations

import json
import logging

from app import ai_providers, llm_schemas
from app.risk.evidence import Evidence

log = logging.getLogger("app.risk.classify")

_BUSINESS_PROMPT = """다음은 코드 심볼 하나에 대해 정적 분석기가 추출한 근거(evidence)다.
이 근거만 근거로, 이 심볼이 회사의 Business/IP 관점에서 어떤 성격인지 분류하라.
코드 원문은 주어지지 않는다 — 근거에 없는 내용을 지어내지 마라.

심볼: {symbol}
파일: {path}

근거:
{evidence_block}

분류 기준:
- business_core_logic : 가격/정산/추천/부정탐지/승인규칙 등 회사 고유의 핵심 알고리즘·업무 프로세스
- business_supporting : 핵심은 아니지만 특정 도메인 업무에 묶인 로직
- generic_infrastructure : 프레임워크/유틸/CRUD/로깅 등 어느 회사에나 있는 코드
- unknown : 근거가 부족해 판단 불가

아래 JSON 스키마로만 답하라 (다른 설명 금지, 코드블록 금지):
{{"classification": "business_core_logic|business_supporting|generic_infrastructure|unknown",
  "business_role": "한국어 3~8단어로 이 심볼의 업무 역할 (예: '할인 가격 계산', '없음')",
  "confidence": 0.0~1.0 사이 숫자,
  "evidence": ["판단에 실제로 쓴 근거를 근거 목록에서 골라 짧게 인용", ...]}}
"""

_QUERY_INTENT_PROMPT = """사용자가 코드에 대해 던진 질문의 "의도"를 분류하라.
외부 AI 에 코드를 얼마나 공개해도 되는지가 질문 의도에 따라 달라진다(같은 코드라도).

질문: {query}
관련 심볼(있으면): {targets}

분류:
- ip_probe        : 회사 고유 로직의 "구현/공식/규칙"을 구체적으로 캐묻는다
                    (예: "우리 회사 할인 정책이 코드에서 어떻게 계산되는지 설명해줘")
- generic_syntax  : 언어/프레임워크 문법·사용법 등 회사 IP 와 무관한 일반 질문
                    (예: "이 데코레이터 문법이 무슨 뜻이야?")
- neutral         : 위 둘 다 아님 (버그, 리팩터링, 일반 이해 등)

아래 JSON 스키마로만 답하라 (다른 설명 금지, 코드블록 금지):
{{"intent": "ip_probe|generic_syntax|neutral", "confidence": 0.0~1.0 사이 숫자}}
"""


def _evidence_block(evidence: list[Evidence]) -> str:
    lines = []
    for e in evidence:
        if e.dimension in ("business_ip", "source_sensitivity", "security", "pii", "secret"):
            lines.append(f"- ({e.dimension}, conf {e.confidence:.2f}) {e.description}")
    return "\n".join(lines) or "(근거 없음)"


def classify_business_ip(
    symbol: str, path: str, evidence: list[Evidence], *,
    provider: str, model: str, project_id: str | None = None, scan_id: str | None = None,
) -> dict | None:
    prompt = _BUSINESS_PROMPT.format(
        symbol=symbol, path=path or "(unknown)", evidence_block=_evidence_block(evidence),
    )
    try:
        raw = ai_providers.generate(provider, model, prompt, response_schema=llm_schemas.BusinessIpClassification,
                                    project_id=project_id, purpose="risk_classify", scan_id=scan_id)
        parsed = json.loads(raw)
    except Exception:
        log.warning("Business/IP 분류 실패 — static 근거만으로 진행", exc_info=True)
        return None

    classification = parsed.get("classification")
    if classification not in ("business_core_logic", "business_supporting",
                               "generic_infrastructure", "unknown"):
        return None
    conf = parsed.get("confidence")
    return {
        "classification": classification,
        "business_role": str(parsed.get("business_role") or "").strip() or None,
        "confidence": float(conf) if isinstance(conf, (int, float)) else 0.5,
        "evidence": [str(x) for x in (parsed.get("evidence") or [])][:8],
    }


def classify_query_intent(
    query: str, targets: list[str], *,
    provider: str, model: str, project_id: str | None = None, scan_id: str | None = None,
) -> dict:
    """질문 의도 분류. 실패하면 'neutral'(fail-open — Contextual 조정 없이 static 노출 경계 사용)."""
    prompt = _QUERY_INTENT_PROMPT.format(query=query, targets=", ".join(targets) or "(없음)")
    try:
        raw = ai_providers.generate(provider, model, prompt, response_schema=llm_schemas.QueryIntentClassification,
                                    project_id=project_id, purpose="risk_query_intent", scan_id=scan_id)
        parsed = json.loads(raw)
        intent = parsed.get("intent")
        if intent in ("ip_probe", "generic_syntax", "neutral"):
            conf = parsed.get("confidence")
            return {"intent": intent, "confidence": float(conf) if isinstance(conf, (int, float)) else 0.5}
    except Exception:
        log.warning("질문 의도 분류 실패 — neutral 로 진행", exc_info=True)
    return {"intent": "neutral", "confidence": 0.0}


# §8 의 "Business/IP Classification → Evidence Validation" 단계. LLM 분류가 static
# 집계를 검증/조정한다 — core 면 하한을 올리고, generic 이면 상한을 눌러 과탐을 잡는다(§20).
_FLOOR_BY_CLASS = {"business_core_logic": 0.80, "business_supporting": 0.40}
_CAP_BY_CLASS = {"generic_infrastructure": 0.20, "business_supporting": 0.55}


def reconcile_business_ip(static_score: float, classification: dict | None, policy,
                           *, has_primary_evidence: bool = False) -> tuple[float, str | None]:
    """(조정된 business_ip 점수, 사유).

    분류가 없거나 'unknown' 이면 static-only 상한만 적용 — DENY 티어는 primary
    신호나 LLM 확인이 있어야 도달한다(§8, §20).

    `has_primary_evidence` 면(정적 분석이 핵심 어휘/도메인 경계 흐름을 이미
    발견) LLM 이 generic 이라 우겨도 sanitize 임계값 아래로는 못 내려간다 —
    7B 로컬 모델의 라벨 뒤집힘이 "핵심 IP → 원문 그대로 노출"로 이어지지
    않게 하는 안전 하한이다(§25.3: 모든 중요한 판단은 Evidence 에 연결).
    """
    static_only_ceiling = float(
        policy.get("thresholds", "business_ip_static_only_ceiling", default=0.65) or 0.65
    )
    primary_floor = float(
        policy.get("thresholds", "business_ip_sanitize", default=0.45) or 0.45
    ) if has_primary_evidence else 0.0

    def _out(score: float, note: str | None) -> tuple[float, str | None]:
        if score < primary_floor:
            return primary_floor, (note + "; " if note else "") + "primary 근거 존재 — sanitize 하한 유지"
        return score, note

    cls = (classification or {}).get("classification")

    if cls in _FLOOR_BY_CLASS:
        conf = float(classification.get("confidence", 0.5) or 0.5)
        target = _FLOOR_BY_CLASS[cls] * (conf if cls == "business_supporting" else 1.0)
        if cls in _CAP_BY_CLASS and static_score > _CAP_BY_CLASS[cls]:
            return _out(_CAP_BY_CLASS[cls], f"LLM 분류 '{cls}'(conf {conf:.2f}) — 상한 {_CAP_BY_CLASS[cls]:.2f}")
        if target > static_score:
            return _out(target, f"LLM 분류 '{cls}'(conf {conf:.2f}) — 하한 상향")
        return _out(static_score, None)

    if cls == "generic_infrastructure":
        conf = float(classification.get("confidence", 0.5) or 0.5)
        if static_score > _CAP_BY_CLASS["generic_infrastructure"]:
            return _out(_CAP_BY_CLASS["generic_infrastructure"],
                        f"LLM 분류 'generic_infrastructure'(conf {conf:.2f}) — 상한 적용")
        return _out(static_score, None)

    # 분류 없음 / unknown
    if static_score > static_only_ceiling:
        return _out(static_only_ceiling, f"LLM 확인 없음 — static-only 상한 {static_only_ceiling:.2f} 적용")
    return _out(static_score, None)
