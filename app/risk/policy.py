"""Risk 정책 — 임계값/가중치를 코드에 하드코딩하지 않기 위한 계층(phase2 plan §9, §25.12).

Rule Engine(`rules.py`)·Risk Vector 집계(`vector.py`)·Exposure 산출(`exposure.py`)
이 쓰는 숫자는 전부 여기를 통해 들어온다. 우선순위:

    DB `risk_policy` 행('default')  →  이 파일의 `_DEFAULT_POLICY`

DB 를 못 읽어도(개발 환경에 마이그레이션 미적용, 커넥션 실패 등) 판정이
멈추면 안 되므로(`ai_providers`·`orchestrator` 와 같은 fail-open 철학),
`_DEFAULT_POLICY` 가 `sql/020_risk_intelligence.sql` 의 시드 doc 과 같은 값을
미러링한다. 두 곳을 바꿀 때는 반드시 같이 바꾼다.

`load_policy()` 는 조회 결과를 캐시하지 않는다 — 호출 빈도가 낮고(스캔당 1회
Risk 합성), 운영 중 정책을 바꾸면 다음 스캔부터 바로 반영되는 게 맞다.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("app.risk.policy")

DIMENSIONS = ("business_ip", "source_sensitivity", "security", "pii", "secret")

# sql/020_risk_intelligence.sql 의 risk_policy('default') 시드와 동일해야 한다.
_DEFAULT_POLICY: dict[str, Any] = {
    "dimensions": list(DIMENSIONS),
    "priority": ["business_ip", "source_sensitivity", "security", "pii", "secret"],
    "evidence_weights": {
        "business_semantic": 0.70,
        "domain_crossing": 0.40,
        "called_by_sensitive": 0.45,
        "call_centrality": 0.35,
        "pdg_core_dependency": 0.30,
        "cfg_complexity": 0.20,
        "semantic_ip_summary": 0.45,
        "security_pattern_critical": 0.90,
        "security_pattern_high": 0.70,
        "security_pattern_medium": 0.45,
        "pii_detection": 0.80,
        "secret_detection": 1.00,
        "source_sensitivity_semantic": 0.55,
    },
    "absence_confidence": {
        "business_ip": 0.55,
        "source_sensitivity": 0.60,
        "security": 0.80,
        "pii": 0.85,
        "secret": 0.95,
    },
    "thresholds": {
        "business_ip_external_raw_deny": 0.75,
        "business_ip_sanitize": 0.45,
        # primary IP 신호(핵심 어휘/도메인 경계)가 없을 때 amplifier(fan-in·분기·PDG)만으로
        # 도달 가능한 business_ip 상한. LLM 확인만 있으면 static-only 상한(아래)까지 허용.
        "business_ip_amplifier_only_ceiling": 0.35,
        "business_ip_static_only_ceiling": 0.65,
        "source_sensitivity_sanitize": 0.55,
        "security_specialized_model": 0.70,
        "security_sanitize": 0.55,
        "pii_mask": 0.45,
        "secret_any": 0.01,
    },
    "aggregation": {"business_ip_amplifier_factor_without_primary": 0.4},
    "centrality": {"fan_in_saturation": 6},
    "contextual": {
        "ip_intent_bump_representation": 2,
        "generic_syntax_intent_relax_representation": 1,
    },
}


@dataclass(frozen=True)
class RiskPolicy:
    version: int
    doc: dict[str, Any]

    def weight(self, evidence_kind: str) -> float:
        return float(self.doc["evidence_weights"].get(evidence_kind, 0.3))

    def absence_confidence(self, dimension: str) -> float:
        return float(self.doc["absence_confidence"].get(dimension, 0.5))

    def threshold(self, name: str) -> float:
        return float(self.doc["thresholds"][name])

    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self.doc
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def default_policy() -> RiskPolicy:
    return RiskPolicy(version=1, doc=copy.deepcopy(_DEFAULT_POLICY))


def load_policy(cur, policy_id: str = "default") -> RiskPolicy:
    """`risk_policy` 행을 읽어 `RiskPolicy` 로. 실패하면 기본 정책(fail-open).

    누락된 최상위 키는 기본값으로 채운다 — DB doc 이 부분적으로만 채워져
    있어도(운영자가 thresholds 만 손봄) 나머지 로직이 KeyError 없이 돈다.
    """
    try:
        cur.execute("SELECT version, doc FROM risk_policy WHERE id = %s", (policy_id,))
        row = cur.fetchone()
    except Exception:
        log.warning("risk_policy 조회 실패 — 기본 정책으로 진행", exc_info=True)
        return default_policy()

    if row is None:
        return default_policy()

    doc = copy.deepcopy(_DEFAULT_POLICY)
    for key, value in (row["doc"] or {}).items():
        if isinstance(value, dict) and isinstance(doc.get(key), dict):
            doc[key] = {**doc[key], **value}
        else:
            doc[key] = value
    return RiskPolicy(version=int(row["version"]), doc=doc)
