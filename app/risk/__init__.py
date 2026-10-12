"""Code/IP Risk Intelligence Layer — Phase 2 / WBS 2.3.

이 패키지의 목적은 `risk_score` 계산이 아니다(phase2 plan §0, §26). NeoSDL 이
이미 가진 정적 분석/코드 이해 능력(CFG·PDG·Dataflow·Call Graph·Semantic
Search)을 근거로,

    "현재 코드와 사용자 요청에서 어떤 정보가 민감하고, 어느 범위까지 외부
     AI 에 공개할 수 있는지"

를 **Evidence 기반으로 설명 가능한 형태로** 결정한다.

산출물은 3개다: Risk Object · Risk Evidence Graph · Exposure Boundary.

모듈 구성:
    policy.py     — 임계값/가중치 (DB risk_policy 또는 기본값). 하드코딩 금지(§9).
    schema.py     — Risk Object / Risk Vector / Exposure Boundary 계약.
    evidence.py   — 기존 분석에서 근거 수집(§4). 새 분석을 만들지 않는다.
    secret_scan.py / pii_scan.py — deterministic 정규식 탐지(Rule Engine 시드).
    graph.py      — Risk Evidence Graph (근거 역추적, §5, §16).
    vector.py     — Risk Vector 집계 (차원별 score/confidence/evidence_refs, §10).
    rules.py      — deterministic guardrail (§9). LLM 판단과 분리(§25.6).
    classify.py   — Business/IP 분류용 내부 LLM 인터페이스(§8). evidence-only, fail-open.
    exposure.py   — Exposure Boundary 산출 (representation ladder, §13).
    synthesis.py  — 위를 결합해 Risk Object 를 만든다(§8 Risk Synthesis) + Contextual Risk(§11).
"""
