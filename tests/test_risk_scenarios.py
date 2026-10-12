"""Risk 평가 데이터셋 — 대표 시나리오 12종 (phase2 plan §19–21, §24 Step 13).

DB/LLM 없이 `synthesis.assemble_risk` 를 직접 구동한다(순수 결합 로직). 각 시나리오는
손으로 만든 Evidence 목록 + (필요 시) canned Business/IP 분류 + 기대 결과다.

- 개별 테스트: representation / external_raw / specialized_model 은 정확 일치,
  business_ip score 는 밴드(lo ≤ score ≤ hi)로.
- 집계 테스트(`test_scenario_metrics`, §20): Detection / False Positive / False
  Negative / Evidence Quality 를 전 시나리오에서 계산해 목표치와 대조.

목표치 근거: 이 하니스는 "정적 근거 + 결정론 규칙"만 검증한다(LLM 분산은
test_risk_stability.py). 그래서 민감 코드 탐지는 100%, 비민감 오탐은 0%,
secret/PII 놓침은 0% 를 요구할 수 있다 — 애매한 건 시나리오에 넣지 않는다.
"""

from __future__ import annotations

import pytest

from app.risk.evidence import Evidence
from app.risk.policy import default_policy
from app.risk.schema import repr_rank
from app.risk.synthesis import assemble_risk

POLICY = default_policy()


def _ev(dimension: str, kind: str, *, conf: float = 0.7, weight: float | None = None,
        related: str | None = None) -> Evidence:
    return Evidence(
        target_symbol_key="t", kind=kind, dimension=dimension,
        weight=POLICY.weight(kind) if weight is None else weight,
        confidence=conf, description=f"{kind} 근거", source="test",
        related_symbol_key=related, id=abs(hash((kind, dimension, conf))) % 100000,
    )


def _core(role: str, conf: float = 0.9) -> dict:
    return {"classification": "business_core_logic", "business_role": role,
            "confidence": conf, "evidence": ["semantic"]}


def _supporting(conf: float = 0.7) -> dict:
    return {"classification": "business_supporting", "business_role": "지원 로직",
            "confidence": conf, "evidence": ["domain"]}


def _generic(conf: float = 0.8) -> dict:
    return {"classification": "generic_infrastructure", "business_role": "없음",
            "confidence": conf, "evidence": []}


# --------------------------------------------------------------------- 시나리오 표
# name -> (evidence, llm_classification|None, expect)
#   expect = {biz: (lo, hi), repr: <exact>, raw: <bool>, specialized: <bool>, sensitive: <bool>}
SCENARIOS: dict[str, tuple] = {
    "01_generic_crud": (
        [_ev("business_ip", "centrality", conf=0.6), _ev("none", "semantic", conf=0.35)],
        None,
        {"biz": (0.0, 0.35), "repr": "RAW", "raw": True, "specialized": False, "sensitive": False},
    ),
    "02_generic_utility": (
        [_ev("business_ip", "cfg_complexity", conf=0.5)],
        None,
        {"biz": (0.0, 0.35), "repr": "RAW", "raw": True, "specialized": False, "sensitive": False},
    ),
    "03_core_pricing": (
        [_ev("business_ip", "business_semantic", conf=0.6),
         _ev("business_ip", "centrality", conf=0.7),
         _ev("business_ip", "pdg_core_dependency", conf=0.5)],
        _core("할인 가격 계산"),
        {"biz": (0.75, 1.0), "repr": "INTERNAL_ONLY", "raw": False, "specialized": False, "sensitive": True},
    ),
    "04_recommendation_algo": (
        [_ev("business_ip", "business_semantic", conf=0.6), _ev("business_ip", "centrality", conf=0.6)],
        _core("추천 순위 산정", conf=0.85),
        {"biz": (0.75, 1.0), "repr": "INTERNAL_ONLY", "raw": False, "specialized": False, "sensitive": True},
    ),
    "05_payment_logic": (
        [_ev("business_ip", "business_semantic", conf=0.6),
         _ev("business_ip", "called_by_sensitive", conf=0.6, related="pay.checkout"),
         _ev("business_ip", "domain_crossing", conf=0.5)],
        _core("결제 실행", conf=0.9),
        {"biz": (0.75, 1.0), "repr": "INTERNAL_ONLY", "raw": False, "specialized": False, "sensitive": True},
    ),
    "06_internal_workflow": (
        [_ev("business_ip", "business_semantic", conf=0.65), _ev("business_ip", "cfg_complexity", conf=0.6)],
        _supporting(conf=0.7),
        {"biz": (0.45, 0.65), "repr": "PSEUDOCODE", "raw": False, "specialized": False, "sensitive": True},
    ),
    "07_generic_api_wrapper": (
        [_ev("business_ip", "centrality", conf=0.6), _ev("business_ip", "cfg_complexity", conf=0.5)],
        _generic(),
        {"biz": (0.0, 0.35), "repr": "RAW", "raw": True, "specialized": False, "sensitive": False},
    ),
    "08_sql_injection": (
        [_ev("security", "security_pattern", conf=0.9, weight=POLICY.weight("security_pattern_critical"))],
        None,
        {"biz": (0.0, 0.4), "repr": "SANITIZED", "raw": False, "specialized": True, "sensitive": True},
    ),
    "09_pii_present": (
        [_ev("pii", "pii_detection", conf=0.6), _ev("pii", "pii_detection", conf=0.5)],
        None,
        {"biz": (0.0, 0.4), "repr": "MASKED", "raw": False, "specialized": False, "sensitive": True},
    ),
    "10_secret_present": (
        [_ev("secret", "secret_detection", conf=0.97, weight=1.0)],
        None,
        {"biz": (0.0, 0.4), "repr": "BLOCK", "raw": False, "specialized": False, "sensitive": True},
    ),
    "11_mixed_business_security_pii": (
        [_ev("business_ip", "business_semantic", conf=0.6),
         _ev("security", "security_pattern", conf=0.95, weight=POLICY.weight("security_pattern_critical")),
         _ev("pii", "pii_detection", conf=0.4)],
        _core("가격 산정", conf=0.9),
        {"biz": (0.75, 1.0), "repr": "INTERNAL_ONLY", "raw": False, "specialized": True, "sensitive": True},
    ),
    "12_domain_crossing_flow": (
        [_ev("business_ip", "domain_crossing", conf=0.6, related="ledger.post"),
         _ev("business_ip", "domain_crossing", conf=0.5),
         _ev("business_ip", "called_by_sensitive", conf=0.6, related="settlement.run")],
        None,
        {"biz": (0.35, 0.65), "repr": "PSEUDOCODE", "raw": False, "specialized": False, "sensitive": True},
    ),
}


def _run(name: str):
    evidence, llm, _expect = SCENARIOS[name]
    return assemble_risk(evidence, POLICY, llm_classification=llm)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario(name):
    vector, classification, outcomes, exposure = _run(name)
    exp = SCENARIOS[name][2]

    lo, hi = exp["biz"]
    assert lo <= vector.score("business_ip") <= hi, (
        f"{name}: business_ip={vector.score('business_ip'):.3f} not in [{lo}, {hi}]")
    assert exposure.allowed_representation == exp["repr"], (
        f"{name}: representation={exposure.allowed_representation} != {exp['repr']}")
    assert exposure.external_raw_allowed is exp["raw"]
    assert exposure.specialized_model_required is exp["specialized"]

    # 민감 판정에는 반드시 근거가 붙는다(§20 Evidence Quality, §25.3)
    if exp["sensitive"]:
        assert vector.all_evidence_refs(), f"{name}: 민감 판정인데 evidence_refs 없음"


# ------------------------------------------------------- §20 집계 지표
_TARGET_DETECTION = 1.0      # 민감 코드는 전부 RAW 가 아니어야
_TARGET_FALSE_POSITIVE = 0.0  # 비민감 코드는 전부 RAW 유지
_TARGET_FN_SECRET_PII = 0.0   # secret/PII 놓침 0


def test_scenario_metrics():
    sensitive, non_sensitive = [], []
    for name, (_evs, _llm, exp) in SCENARIOS.items():
        vector, _c, _o, exposure = _run(name)
        row = (name, vector, exposure, exp)
        (sensitive if exp["sensitive"] else non_sensitive).append(row)

    # Detection: 민감 코드 중 RAW 가 아닌 비율
    detected = sum(1 for _n, _v, e, _x in sensitive if e.allowed_representation != "RAW")
    detection_rate = detected / len(sensitive)
    assert detection_rate >= _TARGET_DETECTION, f"Detection {detection_rate:.2f} < {_TARGET_DETECTION}"

    # False Positive: 비민감 코드 중 RAW 가 아닌 비율
    fp = sum(1 for _n, _v, e, _x in non_sensitive if e.allowed_representation != "RAW")
    fp_rate = fp / len(non_sensitive)
    assert fp_rate <= _TARGET_FALSE_POSITIVE, f"False positive {fp_rate:.2f} > {_TARGET_FALSE_POSITIVE}"

    # False Negative: secret/PII 시나리오에서 해당 차원 점수가 0 이면 놓침
    missed = 0
    for name, v, _e, _x in sensitive:
        if name == "10_secret_present" and v.score("secret") <= 0:
            missed += 1
        if name == "09_pii_present" and v.score("pii") <= 0:
            missed += 1
    assert missed == _TARGET_FN_SECRET_PII, f"secret/PII 놓침 {missed}건"

    # Granularity: 서로 다른 시나리오가 서로 다른 representation 을 만든다(단일값 붕괴 방지)
    reps = {e.allowed_representation for _n, _v, e, _x in sensitive + non_sensitive}
    assert len(reps) >= 4, f"representation 다양성 부족: {reps}"

    # 강도 순서 sanity: secret(BLOCK) 이 가장 제한적
    secret_rep = next(e.allowed_representation for n, _v, e, _x in sensitive if n == "10_secret_present")
    assert repr_rank(secret_rep) == repr_rank("BLOCK")
