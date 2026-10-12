"""Phase 3~6 이 의존하는 Risk 계약 고정(phase2 plan §23, §24 Step 15).

이 파일이 깨지면 downstream(Context Transformation / Multi-LLM Router / …)이 깨진다.
계약을 의도적으로 바꿀 때만 이 테스트를 같이 바꾼다.
"""

from __future__ import annotations

from app.risk.evidence import Evidence
from app.risk.exposure import derive_exposure
from app.risk.policy import DIMENSIONS, default_policy
from app.risk.rules import apply_rules
from app.risk.schema import REPRESENTATIONS, ExposureBoundary, RiskObject, repr_rank
from app.risk.vector import compute_vector


def test_dimensions_frozen():
    assert DIMENSIONS == ("business_ip", "source_sensitivity", "security", "pii", "secret")


def test_representation_ladder_frozen():
    assert REPRESENTATIONS == (
        "RAW", "SANITIZED", "MASKED", "PSEUDOCODE", "STRUCTURED",
        "SUMMARY", "INTERNAL_ONLY", "BLOCK",
    )
    # 순위는 정의 순서와 일치하고 유일하다
    assert [repr_rank(r) for r in REPRESENTATIONS] == list(range(len(REPRESENTATIONS)))


def _sample_object() -> RiskObject:
    pol = default_policy()
    evidence = [
        Evidence("t", "business_semantic", "business_ip", 0.7, 0.6, "pricing", "semantic_search", id=1),
        Evidence("t", "security_pattern", "security", 0.9, 0.9, "sqli", "security_finding",
                 related_symbol_key="sink", id=2),
    ]
    vector = compute_vector(evidence, pol)
    outcomes = apply_rules(vector, {"security_types": ["sql_injection"]}, pol)
    exposure = derive_exposure(vector, outcomes, pol)
    return RiskObject(
        target={"type": "function", "symbol": "calc", "file": "p.py", "lines": [1, 9], "symbol_key": "t"},
        risk=vector, classification={"business_role": "가격 계산"}, exposure=exposure,
        rule_outcomes=outcomes, evidence_refs=[1, 2],
        provenance={"decision_source": "static+rules", "policy_version": 1, "generated_at": "2026-01-01T00:00:00Z"},
    )


def test_risk_object_dict_shape():
    d = _sample_object().to_dict()
    assert set(d) == {"target", "risk", "classification", "exposure", "rule_outcomes", "evidence", "provenance"}

    assert set(d["target"]) >= {"type", "symbol", "file", "lines", "symbol_key"}

    # risk: 근거가 있는 차원만 실리되(§10 absence 는 벡터엔 있으나 to_dict 는 5개 다 낸다)
    assert set(d["risk"]) == set(DIMENSIONS)
    for dim, entry in d["risk"].items():
        assert set(entry) == {"score", "confidence", "evidence_refs"}
        assert isinstance(entry["score"], (int, float))
        assert isinstance(entry["confidence"], (int, float))
        assert isinstance(entry["evidence_refs"], list)

    assert set(d["exposure"]) == {
        "allowed_representation", "external_raw_allowed", "specialized_model_required",
        "reasons", "query_adjusted",
    }
    assert d["exposure"]["allowed_representation"] in REPRESENTATIONS
    assert isinstance(d["exposure"]["external_raw_allowed"], bool)

    assert isinstance(d["rule_outcomes"], list)
    for r in d["rule_outcomes"]:
        assert set(r) == {"rule", "effect", "dimension", "reason", "detail"}
        assert r["effect"] in {
            "force_score", "deny_external_raw", "require_specialized_model", "min_representation",
        }

    assert isinstance(d["evidence"], list)
    assert {"decision_source", "policy_version", "generated_at"} <= set(d["provenance"])


def test_exposure_boundary_is_not_boolean():
    """§1.1 — Risk 를 ALLOW/BLOCK 이분법으로 만들지 않는다."""
    b = ExposureBoundary("SANITIZED", False, True, ["x"])
    d = b.to_dict()
    assert d["allowed_representation"] == "SANITIZED"          # 표현 수준
    assert d["external_raw_allowed"] is False                  # 원문 전송 여부는 별도 플래그
    assert d["specialized_model_required"] is True             # 모델 제약도 별도


def test_evidence_to_dict_shape():
    e = Evidence("t", "called_by", "business_ip", 0.45, 0.6, "결제 흐름에서 호출", "callers_within",
                 related_symbol_key="caller", location={"path": "p.py", "line": 12}, id=7)
    d = e.to_dict()
    assert set(d) == {"id", "target", "related", "kind", "dimension", "weight",
                      "confidence", "description", "source", "location", "metadata"}
