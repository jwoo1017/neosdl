"""Risk Vector 집계 — 근거 목록 → 차원별 {score, confidence, evidence_refs}(phase2 plan §10).

단일 overall score 로 접지 않는다(§1.2). 각 차원은 독립적으로 집계한다.

score  : 포화 결합(noisy-OR)  1 - Π(1 - weightᵢ·confidenceᵢ)
         - 근거가 많을수록 단조 증가하지만 1 을 넘지 않는다.
         - 순서 무관(§6: 근거는 재현 가능한 구조).
confidence : 근거가 있으면 max(confidenceᵢ). 없으면 정책의 absence_confidence.

**business_ip 특례(§8, §20)** — 이 차원은 약한 신호가 많다(도메인 소속, fan-in,
분기 수 …). 이것들이 쌓여 DENY 티어까지 오르면 순수 유틸까지 과탐된다. 그래서:

    primary  = business_semantic(핵심 어휘) · domain_crossing(도메인 경계 흐름)
    amplifier = centrality · called_by · cfg · pdg

primary 근거가 하나도 없으면 amplifier 기여를 정책 계수만큼 줄이고 상한을
씌운다. "핵심 IP 로 판정"하려면 primary 신호 또는 LLM 확인(§8)이 있어야 한다 —
그 승격/강등은 `synthesis.compute_risk_object` 가 분류 결과로 처리한다.
"""

from __future__ import annotations

from app.risk.evidence import Evidence
from app.risk.policy import DIMENSIONS, RiskPolicy
from app.risk.schema import RiskDimensionScore, RiskVector

_BUSINESS_IP_PRIMARY_KINDS = ("business_semantic", "domain_crossing")


def _noisy_or(pairs: list[tuple[float, float]]) -> float:
    acc = 1.0
    for weight, confidence in pairs:
        acc *= (1.0 - max(0.0, min(1.0, weight)) * max(0.0, min(1.0, confidence)))
    return 1.0 - acc


def _business_ip_score(items: list[Evidence], policy: RiskPolicy) -> float:
    has_primary = any(e.kind in _BUSINESS_IP_PRIMARY_KINDS for e in items)
    if has_primary:
        return _noisy_or([(e.weight, e.confidence) for e in items])

    factor = float(policy.get("aggregation", "business_ip_amplifier_factor_without_primary", default=0.4) or 0.4)
    ceiling = float(policy.get("thresholds", "business_ip_amplifier_only_ceiling", default=0.35) or 0.35)
    score = _noisy_or([(e.weight * factor, e.confidence) for e in items])
    return min(score, ceiling)


def compute_vector(evidence: list[Evidence], policy: RiskPolicy) -> RiskVector:
    by_dim: dict[str, list[Evidence]] = {d: [] for d in DIMENSIONS}
    for e in evidence:
        if e.dimension in by_dim:
            by_dim[e.dimension].append(e)

    scores: dict[str, RiskDimensionScore] = {}
    for dim in DIMENSIONS:
        items = by_dim[dim]
        if not items:
            scores[dim] = RiskDimensionScore(
                score=0.0, confidence=policy.absence_confidence(dim), evidence_refs=[],
            )
            continue
        if dim == "business_ip":
            score = _business_ip_score(items, policy)
        else:
            score = _noisy_or([(e.weight, e.confidence) for e in items])
        confidence = max(e.confidence for e in items)
        # evidence_refs 는 입력 순서와 무관하게 결정적이어야 한다(§20 Stability).
        refs = sorted(e.id for e in items if e.id is not None)
        scores[dim] = RiskDimensionScore(score=score, confidence=confidence, evidence_refs=refs)

    return RiskVector(scores=scores)
