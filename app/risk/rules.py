"""Rule Engine — deterministic guardrail(phase2 plan §9, §25.5, §25.6).

LLM 판단만으로 노출 결정을 내리지 않는다. Rule Engine 은 Risk Vector 와
분류 결과를 받아 **결정론적** 오버라이드/제약을 낸다. 여기의 숫자는 전부
`RiskPolicy` 에서 온다 — 하드코딩 금지(§9, §25.12).

산출물은 `RuleOutcome` 목록이다. 각 effect:

    force_score               — 특정 차원 점수를 하한선까지 끌어올림 (secret 탐지 시)
    deny_external_raw         — 외부로 원문(RAW) 전송 금지
    require_specialized_model — 보안 특화 모델로만 처리
    min_representation        — Exposure 사다리의 최소 제한 수준 강제

`exposure.py` 가 이 목록을 소비해 최종 Exposure Boundary 를 만든다. Rule 은
"이보다 느슨할 수 없다"는 바닥만 정하고, 그 위 판단(정확한 representation)은
`exposure.py` 가 vector 를 보고 결정한다.
"""

from __future__ import annotations

from app.risk.policy import RiskPolicy
from app.risk.schema import RiskVector, RuleOutcome


def apply_rules(vector: RiskVector, classification: dict, policy: RiskPolicy) -> list[RuleOutcome]:
    outcomes: list[RuleOutcome] = []
    t = policy.threshold

    # --- Secret: 탐지되면 무조건 CRITICAL + 외부 원문 금지(§9) ---
    if vector.score("secret") >= t("secret_any"):
        outcomes.append(RuleOutcome(
            rule="secret_detected", effect="force_score", dimension="secret",
            reason="하드코딩된 비밀정보가 탐지됨 — 심각도를 최대로 고정",
            detail={"floor": 1.0},
        ))
        outcomes.append(RuleOutcome(
            rule="secret_detected", effect="min_representation", dimension="secret",
            reason="비밀정보가 포함된 코드는 어떤 형태로도 외부 전송 불가",
            detail={"representation": "BLOCK"},
        ))

    # --- Business/IP: 임계값 이상이면 외부 원문 금지(§9) ---
    if vector.score("business_ip") >= t("business_ip_external_raw_deny"):
        outcomes.append(RuleOutcome(
            rule="business_ip_high", effect="deny_external_raw", dimension="business_ip",
            reason=f"Business/IP 위험이 임계값({t('business_ip_external_raw_deny'):.2f}) 이상",
            detail={"score": round(vector.score("business_ip"), 3)},
        ))
        outcomes.append(RuleOutcome(
            rule="business_ip_high", effect="min_representation", dimension="business_ip",
            reason="핵심 IP 로 판정 — 내부 처리만 허용",
            detail={"representation": "INTERNAL_ONLY"},
        ))
    elif vector.score("business_ip") >= t("business_ip_sanitize"):
        outcomes.append(RuleOutcome(
            rule="business_ip_moderate", effect="min_representation", dimension="business_ip",
            reason="Business/IP 위험이 중간 수준 — 구현 디테일 제거 후에만 외부 전송",
            detail={"representation": "PSEUDOCODE"},
        ))

    # --- Security: 높으면 특화 모델 요구(§9) ---
    if vector.score("security") >= t("security_specialized_model"):
        outcomes.append(RuleOutcome(
            rule="security_high", effect="require_specialized_model", dimension="security",
            reason=f"보안 위험이 임계값({t('security_specialized_model'):.2f}) 이상 — 보안 특화 모델 필요",
            detail={"score": round(vector.score("security"), 3),
                    "security_types": classification.get("security_types", [])},
        ))
    if vector.score("security") >= t("security_sanitize"):
        outcomes.append(RuleOutcome(
            rule="security_moderate", effect="min_representation", dimension="security",
            reason="취약 패턴이 있는 코드 — 악용 세부를 정리한 후에만 외부 전송",
            detail={"representation": "SANITIZED"},
        ))

    # --- PII: 임계값 이상이면 마스킹 필요 ---
    if vector.score("pii") >= t("pii_mask"):
        outcomes.append(RuleOutcome(
            rule="pii_present", effect="min_representation", dimension="pii",
            reason=f"PII 위험이 임계값({t('pii_mask'):.2f}) 이상 — 마스킹 후에만 외부 전송",
            detail={"representation": "MASKED"},
        ))

    # --- Source sensitivity ---
    if vector.score("source_sensitivity") >= t("source_sensitivity_sanitize"):
        outcomes.append(RuleOutcome(
            rule="source_sensitive", effect="min_representation", dimension="source_sensitivity",
            reason="대외 비공개로 표시된 소스 — 정리 후에만 외부 전송",
            detail={"representation": "SANITIZED"},
        ))

    return outcomes


def specialized_model_required(outcomes: list[RuleOutcome]) -> bool:
    return any(o.effect == "require_specialized_model" for o in outcomes)


def score_floors(outcomes: list[RuleOutcome]) -> dict[str, float]:
    floors: dict[str, float] = {}
    for o in outcomes:
        if o.effect == "force_score" and o.dimension:
            floors[o.dimension] = max(floors.get(o.dimension, 0.0), float(o.detail.get("floor", 1.0)))
    return floors
