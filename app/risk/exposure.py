"""Exposure Boundary 산출 — Phase 2 핵심 출력 중 하나(phase2 plan §1.1, §13).

Risk 를 ALLOW/BLOCK Boolean 으로 만들지 않는다(§1.1). representation ladder
(schema.REPRESENTATIONS)의 어느 칸까지 허용되는지를 낸다.

입력:
    vector          — Risk Vector (rules.score_floors 적용 후)
    rule_outcomes   — Rule Engine 의 결정론적 제약 (min_representation 바닥)
    policy          — 임계값
    query_context   — (선택) Contextual Risk. 질문 의도에 따라 사다리를 이동(§11)

실제 masking/sanitization 은 하지 않는다 — "어느 수준까지 허용되는가"만 산출하고
변환은 Phase 3 이 한다(§13).
"""

from __future__ import annotations

from app.risk.policy import RiskPolicy
from app.risk.schema import ExposureBoundary, RiskVector, bump, stricter

# 차원 점수 → 그 차원 하나만 봤을 때의 representation. 가장 제한적인 것이 이긴다(§1.2:
# 약한 business_ip 라도 secret 이 있으면 secret 이 지배).
def _dimension_representation(vector: RiskVector, policy: RiskPolicy) -> tuple[str, list[str]]:
    t = policy.threshold
    rep = "RAW"
    reasons: list[str] = []

    if vector.score("secret") >= t("secret_any"):
        rep = stricter(rep, "BLOCK")
        reasons.append("비밀정보 포함 — 전달 불가")

    b = vector.score("business_ip")
    if b >= t("business_ip_external_raw_deny"):
        rep = stricter(rep, "INTERNAL_ONLY")
        reasons.append(f"Business/IP 위험 높음({b:.2f}) — 내부 처리만")
    elif b >= t("business_ip_sanitize"):
        rep = stricter(rep, "PSEUDOCODE")
        reasons.append(f"Business/IP 위험 중간({b:.2f}) — 구조/의사코드 수준까지")

    s = vector.score("security")
    if s >= t("security_sanitize"):
        rep = stricter(rep, "SANITIZED")
        reasons.append(f"보안 위험({s:.2f}) — 악용 세부 정리 필요")

    p = vector.score("pii")
    if p >= t("pii_mask"):
        rep = stricter(rep, "MASKED")
        reasons.append(f"PII 위험({p:.2f}) — 마스킹 필요")

    ss = vector.score("source_sensitivity")
    if ss >= t("source_sensitivity_sanitize"):
        rep = stricter(rep, "SANITIZED")
        reasons.append(f"대외 비공개 소스({ss:.2f}) — 정리 필요")

    if rep == "RAW":
        reasons.append("민감 신호 없음 — 원문 전송 허용")
    return rep, reasons


def _min_representation_floor(rule_outcomes) -> tuple[str, list[str]]:
    rep = "RAW"
    reasons: list[str] = []
    for o in rule_outcomes:
        if o.effect == "min_representation":
            target = o.detail.get("representation", "INTERNAL_ONLY")
            rep = stricter(rep, target)
            reasons.append(f"[규칙:{o.rule}] {o.reason}")
    return rep, reasons


def derive_exposure(
    vector: RiskVector, rule_outcomes, policy: RiskPolicy, *,
    query_context: dict | None = None,
) -> ExposureBoundary:
    dim_rep, reasons = _dimension_representation(vector, policy)
    rule_rep, rule_reasons = _min_representation_floor(rule_outcomes)
    rep = stricter(dim_rep, rule_rep)
    reasons = reasons + rule_reasons

    query_adjusted = False
    if query_context:
        rep, extra, query_adjusted = _apply_query_context(rep, vector, policy, query_context)
        # Contextual 완화는 Rule Engine 이 세운 결정론적 바닥 아래로 내려갈 수 없다(§9, §25.6).
        rep = stricter(rep, rule_rep)
        reasons += extra

    deny_raw = any(o.effect == "deny_external_raw" for o in rule_outcomes) or rep != "RAW"
    specialized = any(o.effect == "require_specialized_model" for o in rule_outcomes)

    return ExposureBoundary(
        allowed_representation=rep,
        external_raw_allowed=(rep == "RAW") and not deny_raw,
        specialized_model_required=specialized,
        reasons=reasons,
        query_adjusted=query_adjusted,
    )


def _apply_query_context(rep: str, vector: RiskVector, policy: RiskPolicy, qc: dict) -> tuple[str, list[str], bool]:
    """질문 의도에 따라 사다리를 이동한다(§11).

    qc 는 classify.classify_query_intent() 의 출력 형태를 기대한다:
        {"intent": "ip_probe" | "generic_syntax" | "neutral", "confidence": float}
    """
    intent = qc.get("intent")
    reasons: list[str] = []
    if intent == "ip_probe" and vector.score("business_ip") >= policy.threshold("business_ip_sanitize"):
        steps = int(policy.get("contextual", "ip_intent_bump_representation", default=2) or 2)
        new_rep = bump(rep, steps)
        reasons.append(f"질문이 핵심 IP 구현을 직접 캐묻는 의도 — 노출 수준을 {steps}단계 상향")
        return new_rep, reasons, new_rep != rep
    if intent == "generic_syntax":
        steps = int(policy.get("contextual", "generic_syntax_intent_relax_representation", default=1) or 1)
        # 완화는 Rule Engine 이 세운 바닥 아래로는 못 내려간다 — 그건 exposure 호출부에서
        # 이미 stricter() 로 합쳐졌으므로 여기서는 dimension 기반 상향분만 되돌린다.
        new_rep = bump(rep, -steps)
        if new_rep != rep:
            reasons.append("일반 문법/사용법 질문 — 노출 수준을 1단계 완화")
        return new_rep, reasons, new_rep != rep
    return rep, reasons, False
