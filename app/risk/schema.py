"""Risk Object / Risk Vector / Exposure Boundary 데이터 계약(phase2 plan §10, §13, §14, §23).

Phase 3~6 이 이 계약을 소비한다(§23). 그래서 필드 추가는 자유롭게 하되, 기존
필드의 의미/타입은 버전을 올리지 않는 한 바꾸지 않는다. 실제 DB 저장 형태
(`risk_object` 테이블, jsonb)와 API 응답은 여기 `to_dict()` 한 곳에서만 만든다.

Risk 를 Boolean(ALLOW/BLOCK)으로 만들지 않는다(§1.1). Exposure 는 표현 수준의
사다리(representation ladder)로 나타낸다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.risk.policy import DIMENSIONS

# Exposure representation ladder — 낮을수록 원본에 가깝고(느슨), 높을수록 제한적(§13).
# 실제 masking/sanitization 구현은 Phase 3 이며, Phase 2 는 "어느 수준까지
# 허용되는가"만 산출한다(§1.1, §13).
REPRESENTATIONS = (
    "RAW",           # 원문 그대로 외부 전송 가능
    "SANITIZED",     # 취약점 악용 세부/주석/식별자 정리 후
    "MASKED",        # 민감 리터럴/PII 마스킹 후
    "PSEUDOCODE",    # 알고리즘 구조만, 구현 디테일 제거
    "STRUCTURED",    # 시그니처/타입/호출관계 등 구조 메타데이터만
    "SUMMARY",       # 자연어 요약만
    "INTERNAL_ONLY", # 내부 모델로만, 외부 egress 금지
    "BLOCK",         # 어떤 형태로도 전달 불가
)
_REPR_RANK = {name: i for i, name in enumerate(REPRESENTATIONS)}


def repr_rank(name: str) -> int:
    return _REPR_RANK.get(name, _REPR_RANK["INTERNAL_ONLY"])


def stricter(a: str, b: str) -> str:
    """두 representation 중 더 제한적인 쪽."""
    return a if repr_rank(a) >= repr_rank(b) else b


def bump(name: str, steps: int) -> str:
    """사다리를 `steps` 칸 더 제한적으로(또는 음수면 느슨하게) 이동. 범위는 클램프."""
    idx = max(0, min(len(REPRESENTATIONS) - 1, repr_rank(name) + steps))
    return REPRESENTATIONS[idx]


@dataclass(frozen=True)
class RiskDimensionScore:
    """한 위험 차원의 값. 숫자만 보고 끝나지 않도록 confidence 와 evidence_refs 를 함께 갖는다(§10)."""

    score: float
    confidence: float
    evidence_refs: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class RiskVector:
    """독립적인 차원들의 집합(§1.2). 단일 overall score 로 접지 않는다."""

    scores: dict[str, RiskDimensionScore]

    def score(self, dim: str) -> float:
        s = self.scores.get(dim)
        return s.score if s else 0.0

    def confidence(self, dim: str) -> float:
        s = self.scores.get(dim)
        return s.confidence if s else 0.0

    def all_evidence_refs(self) -> list[int]:
        seen: list[int] = []
        for s in self.scores.values():
            for ref in s.evidence_refs:
                if ref not in seen:
                    seen.append(ref)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {dim: self.scores[dim].to_dict() for dim in DIMENSIONS if dim in self.scores}


@dataclass(frozen=True)
class RuleOutcome:
    """Rule Engine 이 적용한 결정론적 가드레일 하나(§9). 왜 적용됐는지를 반드시 남긴다."""

    rule: str
    effect: str            # force_score | deny_external_raw | require_specialized_model | min_representation
    dimension: str | None
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "effect": self.effect,
            "dimension": self.dimension,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ExposureBoundary:
    """Phase 2 핵심 출력 중 하나(§13). Phase 3 Context Transformation 의 입력이 된다."""

    allowed_representation: str
    external_raw_allowed: bool
    specialized_model_required: bool
    reasons: list[str] = field(default_factory=list)
    query_adjusted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_representation": self.allowed_representation,
            "external_raw_allowed": self.external_raw_allowed,
            "specialized_model_required": self.specialized_model_required,
            "reasons": list(self.reasons),
            "query_adjusted": self.query_adjusted,
        }


@dataclass(frozen=True)
class RiskObject:
    """Phase 2 의 핵심 API/data contract(§14)."""

    target: dict[str, Any]
    risk: RiskVector
    classification: dict[str, Any]
    exposure: ExposureBoundary
    rule_outcomes: list[RuleOutcome]
    evidence_refs: list[int]
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "risk": self.risk.to_dict(),
            "classification": self.classification,
            "exposure": self.exposure.to_dict(),
            "rule_outcomes": [r.to_dict() for r in self.rule_outcomes],
            "evidence": list(self.evidence_refs),
            "provenance": self.provenance,
        }
