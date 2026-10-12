"""안정성 — 같은 입력에 결과가 크게 흔들리지 않는다 (phase2 plan §20 Stability, §24 Step 14).

두 층:
  1. 결정론(항상 실행): 근거·정책이 같으면 `assemble_risk` 출력이 완전히 동일.
     근거 순서를 섞어도(§6: 근거는 재현 가능한 구조) 결과 불변.
  2. LLM 분산(opt-in, `RISK_EVAL_LIVE=1` + `-m live_llm`): 같은 근거로
     `classify_business_ip` 를 여러 번 실호출 → `reconcile_business_ip` 후
     business_ip 가 한 representation tier 안에서만 흔들리는지(가드레일이 감쇠).
"""

from __future__ import annotations

import os
import random

import pytest

from app.risk.evidence import Evidence
from app.risk.policy import default_policy
from app.risk.schema import repr_rank
from app.risk.synthesis import assemble_risk

POLICY = default_policy()


def _evidence() -> list[Evidence]:
    return [
        Evidence("t", "business_semantic", "business_ip", POLICY.weight("business_semantic"), 0.6,
                 "pricing 어휘", "semantic_search", id=1),
        Evidence("t", "centrality", "business_ip", POLICY.weight("call_centrality"), 0.7,
                 "fan-in 4", "callers_within", id=2),
        Evidence("t", "security_pattern", "security", POLICY.weight("security_pattern_high"), 0.8,
                 "xss sink", "security_finding", related_symbol_key="s", id=3),
        Evidence("t", "pii_detection", "pii", POLICY.weight("pii_detection"), 0.5,
                 "email 식별자", "pii_scan", id=4),
    ]


def test_assemble_is_deterministic():
    base = assemble_risk(_evidence(), POLICY)
    base_dump = (base[0].to_dict(), base[2], base[3].to_dict())
    for _ in range(20):
        v, _c, o, e = assemble_risk(_evidence(), POLICY)
        assert (v.to_dict(), o, e.to_dict()) == base_dump


def test_evidence_order_does_not_matter():
    ref = _evidence()
    base = assemble_risk(ref, POLICY)
    for _ in range(10):
        shuffled = ref[:]
        random.shuffle(shuffled)
        v, _c, _o, e = assemble_risk(shuffled, POLICY)
        assert v.to_dict() == base[0].to_dict()
        assert e.to_dict() == base[3].to_dict()


def test_classification_is_monotonic():
    """더 민감한 라벨일수록 더(같거나) 제한적인 representation — 역전 없음."""
    evs = _evidence()

    def rank_for(cls):
        llm = {"classification": cls, "business_role": "x", "confidence": 0.7, "evidence": []}
        return repr_rank(assemble_risk(evs, POLICY, llm_classification=llm)[3].allowed_representation)

    core = rank_for("business_core_logic")
    supporting = rank_for("business_supporting")
    generic = rank_for("generic_infrastructure")
    unknown = rank_for("unknown")
    assert core >= supporting >= generic
    assert core >= unknown


def test_confidence_wobble_within_label_is_damped():
    """분류 라벨이 같으면 confidence 가 흔들려도 representation tier 는 안 바뀐다
    (가드레일이 LLM 진동을 흡수, §20)."""
    evs = _evidence()
    for cls in ("business_core_logic", "business_supporting", "generic_infrastructure"):
        reps = set()
        for conf in (0.55, 0.65, 0.7, 0.8, 0.9):
            llm = {"classification": cls, "business_role": "x", "confidence": conf, "evidence": []}
            reps.add(assemble_risk(evs, POLICY, llm_classification=llm)[3].allowed_representation)
        assert len(reps) == 1, f"{cls}: confidence 변화로 representation 이 흔들림: {reps}"


@pytest.mark.live_llm
@pytest.mark.skipif(not os.environ.get("RISK_EVAL_LIVE"), reason="RISK_EVAL_LIVE 미설정")
def test_live_llm_classification_never_exposes_flagged_ip():
    """로컬 7B 모델은 같은 코드를 core/generic 으로 뒤집기도 한다 — 그래도
    정적 분석이 핵심 어휘를 이미 발견한 심볼(primary 근거)은 LLM 뒤집힘과
    무관하게 절대 RAW/SANITIZE-이하로 노출되지 않는다(§20, §25.3의 안전 하한)."""
    from app import ai_providers
    from app.risk import classify

    evs = [e for e in _evidence() if e.dimension == "business_ip"]      # business_semantic 포함
    model = ai_providers.resolve_requested_model(None)

    reps, labels = [], []
    for _ in range(5):
        llm = classify.classify_business_ip("calculateDiscount", "pricing/service.py", evs,
                                            provider="gateway", model=model)
        labels.append((llm or {}).get("classification"))
        _v, _c, _o, exposure = assemble_risk(evs, POLICY, llm_classification=llm)
        reps.append(repr_rank(exposure.allowed_representation))

    # 안전 속성: 아무리 흔들려도 PSEUDOCODE(구현 디테일 제거) 아래로는 안 내려간다
    assert min(reps) >= repr_rank("PSEUDOCODE"), f"labels={labels} reps={reps}"
