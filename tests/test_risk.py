"""app/risk/* 순수 로직 단위 테스트 — DB/LLM 없이(test_orchestrator.py 와 같은 방식).

검증 대상: deterministic 탐지기(secret/pii), Risk Vector 집계, Rule Engine
가드레일, Exposure representation ladder, Contextual(query-aware) 조정,
Business/IP 분류 fail-open.
"""

from __future__ import annotations

import json

import pytest

from app.risk import classify, exposure as exposure_mod, pii_scan, rules as rules_mod, secret_scan
from app.risk.evidence import Evidence
from app.risk.policy import default_policy
from app.risk.schema import REPRESENTATIONS, bump, repr_rank, stricter
from app.risk.vector import compute_vector


# --------------------------------------------------------------------- schema ladder

def test_representation_ladder_ordering():
    assert repr_rank("RAW") < repr_rank("SANITIZED") < repr_rank("INTERNAL_ONLY") < repr_rank("BLOCK")
    assert stricter("RAW", "BLOCK") == "BLOCK"
    assert stricter("MASKED", "SANITIZED") == "MASKED"
    assert bump("RAW", -3) == "RAW"                       # 하한 클램프
    assert bump("BLOCK", 5) == "BLOCK"                    # 상한 클램프
    assert bump("PSEUDOCODE", 2) == REPRESENTATIONS[repr_rank("PSEUDOCODE") + 2]


# --------------------------------------------------------------------- secret_scan

def test_secret_scan_detects_formatted_secrets():
    text = "\n".join([
        "const k = 'AKIA1234567890ABCDEF'",
        "-----BEGIN RSA PRIVATE KEY-----",
        "api_key = \"sk_live_abcdefghijklmnop\"",
    ])
    hits = {h["rule"] for h in secret_scan.scan_text(text, path="x.py")}
    assert "aws_access_key_id" in hits
    assert "private_key_block" in hits
    assert "hardcoded_credential_assignment" in hits


def test_secret_scan_ignores_env_lookups():
    text = "API_KEY = os.environ['API_KEY']\nclient_secret = process.env.SECRET"
    assert secret_scan.scan_text(text) == []


# --------------------------------------------------------------------- pii_scan

def test_pii_scan_identifiers_and_values():
    by_id = pii_scan.scan_identifiers(["resident_number", "user_id", "ssn"])
    assert {h["metadata"]["identifier"] for h in by_id} == {"resident_number", "ssn"}

    hits = pii_scan.scan_text("rrn = '900101-1234567'", path="x.py")
    assert any(h["rule"] == "krrn_literal" for h in hits)


# --------------------------------------------------------------------- vector

def _ev(dim, weight, conf, kind=None, _id=1):
    # business_ip 은 primary 신호(business_semantic)가 있어야 noisy-OR 로 집계된다 —
    # 기본 kind 를 dimension 에 맞춘다.
    kind = kind or ("business_semantic" if dim == "business_ip" else "x")
    return Evidence(target_symbol_key="t", kind=kind, dimension=dim, weight=weight,
                    confidence=conf, description="d", source="s", id=_id)


def test_vector_absence_uses_policy_confidence():
    pol = default_policy()
    v = compute_vector([], pol)
    assert v.score("secret") == 0.0
    assert v.confidence("secret") == pytest.approx(pol.absence_confidence("secret"))
    assert v.confidence("business_ip") == pytest.approx(pol.absence_confidence("business_ip"))
    assert v.confidence("secret") > v.confidence("business_ip")  # §10


def test_vector_noisy_or_is_monotonic_and_bounded():
    pol = default_policy()
    one = compute_vector([_ev("business_ip", 0.7, 0.6, _id=1)], pol).score("business_ip")
    two = compute_vector([_ev("business_ip", 0.7, 0.6, _id=1),
                          _ev("business_ip", 0.4, 0.5, _id=2)], pol).score("business_ip")
    assert 0 < one < two < 1.0
    # 약한 근거 다수가 강한 근거 하나를 압도하지 않는다
    weak = compute_vector([_ev("business_ip", 0.2, 0.3, _id=i) for i in range(3)], pol).score("business_ip")
    assert weak < one


def test_vector_business_ip_amplifier_only_is_capped():
    """primary IP 신호 없이 amplifier(centrality/cfg/pdg)만 쌓이면 상한에 눌린다(§20)."""
    pol = default_policy()
    amp = [Evidence("t", k, "business_ip", 0.9, 0.9, "d", "s", id=i)
           for i, k in enumerate(("centrality", "cfg", "pdg", "called_by"))]
    v = compute_vector(amp, pol)
    assert v.score("business_ip") <= pol.threshold("business_ip_amplifier_only_ceiling")
    # primary 하나만 끼면 정상 noisy-OR 로 풀린다
    v2 = compute_vector(amp + [_ev("business_ip", 0.7, 0.6, _id=9)], pol)
    assert v2.score("business_ip") > v.score("business_ip")


def test_vector_collects_evidence_refs():
    v = compute_vector([_ev("security", 0.9, 0.9, _id=11), _ev("security", 0.7, 0.8, _id=12)], default_policy())
    assert v.scores["security"].evidence_refs == [11, 12]


# --------------------------------------------------------------------- rules

def test_rule_secret_forces_critical_and_block():
    pol = default_policy()
    v = compute_vector([_ev("secret", 1.0, 0.97, kind="secret_detection", _id=1)], pol)
    outcomes = rules_mod.apply_rules(v, {}, pol)
    assert rules_mod.score_floors(outcomes).get("secret") == 1.0
    assert any(o.effect == "min_representation" and o.detail["representation"] == "BLOCK" for o in outcomes)


def test_rule_business_ip_high_denies_external_raw():
    pol = default_policy()
    # business_ip 를 임계값 위로 올릴 강한 근거
    v = compute_vector([_ev("business_ip", 1.0, 0.95, _id=1), _ev("business_ip", 0.9, 0.9, _id=2)], pol)
    assert v.score("business_ip") >= pol.threshold("business_ip_external_raw_deny")
    outcomes = rules_mod.apply_rules(v, {}, pol)
    assert any(o.effect == "deny_external_raw" for o in outcomes)
    assert any(o.effect == "min_representation" and o.detail["representation"] == "INTERNAL_ONLY"
               for o in outcomes)


def test_rule_security_high_requires_specialized_model():
    pol = default_policy()
    v = compute_vector([_ev("security", 0.9, 0.95, _id=1)], pol)
    outcomes = rules_mod.apply_rules(v, {"security_types": ["sql_injection"]}, pol)
    assert rules_mod.specialized_model_required(outcomes)


# --------------------------------------------------------------------- exposure

def test_exposure_raw_when_no_signal():
    pol = default_policy()
    v = compute_vector([], pol)
    b = exposure_mod.derive_exposure(v, [], pol)
    assert b.allowed_representation == "RAW"
    assert b.external_raw_allowed is True


def test_exposure_strictest_dimension_wins():
    pol = default_policy()
    v = compute_vector([
        _ev("secret", 1.0, 0.97, kind="secret_detection", _id=1),
        _ev("business_ip", 0.5, 0.5, _id=2),
    ], pol)
    outcomes = rules_mod.apply_rules(v, {}, pol)
    v = _apply_floors(v, outcomes)
    b = exposure_mod.derive_exposure(v, outcomes, pol)
    assert b.allowed_representation == "BLOCK"
    assert b.external_raw_allowed is False


def test_exposure_query_ip_probe_bumps_but_generic_relaxes_within_floor():
    pol = default_policy()
    # 중간 수준 business_ip (sanitize ≤ score < deny)
    v = compute_vector([_ev("business_ip", 0.8, 0.8, _id=1)], pol)
    assert pol.threshold("business_ip_sanitize") <= v.score("business_ip") < pol.threshold("business_ip_external_raw_deny")
    outcomes = rules_mod.apply_rules(v, {}, pol)

    base = exposure_mod.derive_exposure(v, outcomes, pol)
    probed = exposure_mod.derive_exposure(v, outcomes, pol, query_context={"intent": "ip_probe", "confidence": 0.9})
    assert repr_rank(probed.allowed_representation) > repr_rank(base.allowed_representation)
    assert probed.query_adjusted is True

    # generic_syntax 완화는 Rule Engine 이 세운 바닥(PSEUDOCODE) 아래로 못 내려간다
    relaxed = exposure_mod.derive_exposure(v, outcomes, pol,
                                           query_context={"intent": "generic_syntax", "confidence": 0.9})
    assert repr_rank(relaxed.allowed_representation) >= repr_rank("PSEUDOCODE")


def _apply_floors(vector, outcomes):
    from app.risk.schema import RiskDimensionScore, RiskVector
    floors = rules_mod.score_floors(outcomes)
    scores = dict(vector.scores)
    for dim, floor in floors.items():
        prev = scores.get(dim)
        if not prev or floor > prev.score:
            scores[dim] = RiskDimensionScore(score=floor, confidence=0.95,
                                             evidence_refs=prev.evidence_refs if prev else [])
    return RiskVector(scores=scores)


# --------------------------------------------------------------------- classify (LLM, mocked)

def test_classify_business_ip_fail_open_on_bad_json(monkeypatch):
    monkeypatch.setattr(classify.ai_providers, "generate",
                        lambda *a, **k: "not json at all")
    assert classify.classify_business_ip("f", "p.py", [], provider="gateway", model="m") is None


def test_classify_business_ip_parses_valid(monkeypatch):
    payload = {"classification": "business_core_logic", "business_role": "할인 계산",
               "confidence": 0.9, "evidence": ["pricing semantic"]}
    monkeypatch.setattr(classify.ai_providers, "generate", lambda *a, **k: json.dumps(payload))
    out = classify.classify_business_ip("calcDiscount", "pricing.py", [], provider="gateway", model="m")
    assert out["classification"] == "business_core_logic"


def test_reconcile_business_ip_core_raises_generic_caps_unknown_ceilings():
    pol = default_policy()
    # core → 하한 0.80 으로 상향
    s, note = classify.reconcile_business_ip(0.3, {"classification": "business_core_logic", "confidence": 1.0}, pol)
    assert s == pytest.approx(0.80) and note
    # generic → 상한 0.20 으로 강등 (과탐 억제, §20)
    s, note = classify.reconcile_business_ip(0.95, {"classification": "generic_infrastructure", "confidence": 0.8}, pol)
    assert s == pytest.approx(0.20) and note
    # 분류 없음 → static-only 상한 0.65
    s, note = classify.reconcile_business_ip(0.95, None, pol)
    assert s == pytest.approx(0.65) and note
    # 분류 없고 static 이 낮으면 그대로
    s, note = classify.reconcile_business_ip(0.30, None, pol)
    assert s == pytest.approx(0.30) and note is None


def test_classify_query_intent_fail_open_to_neutral(monkeypatch):
    monkeypatch.setattr(classify.ai_providers, "generate", lambda *a, **k: "boom")
    assert classify.classify_query_intent("q", [], provider="gateway", model="m")["intent"] == "neutral"


# --------------------------------------------------------------------- policy

def test_policy_default_mirrors_dimensions():
    pol = default_policy()
    assert pol.threshold("secret_any") > 0
    assert pol.weight("secret_detection") == 1.0
    assert pol.weight("unknown_kind") == 0.3  # 미정의 kind 는 보수적 기본값
