-- =====================================================================
-- NeoSDL · 020_risk_intelligence.sql
-- Phase 2 / WBS 2.3 — Code/IP Risk Intelligence Layer.
--
-- 이 마이그레이션은 "risk_score 컬럼 하나"를 추가하는 게 아니다. Phase 2의
-- 산출물은 3개다(phase2 plan §0): Risk Object · Risk Evidence Graph ·
-- Exposure Boundary. risk_score 는 이 셋을 설명하기 위한 파생값일 뿐이다.
--
-- 기존 분석 계층(symbol / symbol_edge / symbol_semantic / security_finding /
-- cfg_* / pdg_*)은 정본으로 그대로 두고, 그 위에 얹는 파생 테이블만 추가한다
-- (003_semantics.sql 이 symbol_semantic 을 얹은 것과 같은 방식). 001~019 는
-- 손대지 않는다 — 전부 ADD 뿐이라 무손실이다.
--
--   * risk_policy   — 임계값/가중치를 코드에 하드코딩하지 않기 위한 설정
--                     (phase2 plan §9, §25.12). 'default' 한 행이 전역 정책.
--   * risk_evidence — Risk 판단의 구조화된 근거(§5, §6). 모든 항목은
--                     기존 분석 결과에서 파생된다(§4) — source 컬럼이 어디서
--                     왔는지를 남긴다.
--   * risk_object   — Phase 2의 핵심 data contract(§14, §23). scan × 평가단위
--                     (보통 function/symbol) 하나당 한 행.
--
-- Risk 재계산은 security_finding 과 같은 "최초 조회 시 1회 계산+캐싱" 방식이다
-- (app/risk/synthesis.py). 코드가 바뀌면 새 scan 이 새 행을 만든다 — scan 은
-- 불변 스냅샷이므로(001) risk_object 도 UPDATE 하지 않고 새 scan_id 로 쌓인다.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/020_risk_intelligence.sql
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. Risk Policy — deterministic guardrail 의 임계값/가중치
--
-- LLM 판단만으로 노출 결정을 내리지 않는다(§9, §25.5). Rule Engine 이 쓰는
-- 숫자는 전부 이 doc 안에 있고, app/risk/policy.py 의 _DEFAULT_POLICY 가
-- 같은 값을 미러링한다(DB 가 없거나 행이 비어도 동작하도록 — fail-open).
-- 값을 바꿀 때는 이 행을 UPDATE 하고 version 을 올린다 → risk_object.provenance
-- 에 policy_version 이 남아 "그때 어떤 정책으로 판정했는지" 재현된다(§18).
-- ---------------------------------------------------------------------
CREATE TABLE risk_policy (
    id          text        PRIMARY KEY,             -- 'default' = 전역. (미래) 'project:<uuid>' 오버라이드
    version     integer     NOT NULL DEFAULT 1,
    doc         jsonb       NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE risk_policy IS
    'Rule Engine(app/risk/rules.py)이 쓰는 임계값/가중치. 코드에 하드코딩하지 '
    '않고 여기서 관리한다. app/risk/policy.py 의 _DEFAULT_POLICY 가 같은 값을 '
    '미러링하므로 이 테이블이 비어도 판정은 성립한다.';

INSERT INTO risk_policy (id, version, doc) VALUES ('default', 1, $json$
{
  "dimensions": ["business_ip", "source_sensitivity", "security", "pii", "secret"],
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
    "source_sensitivity_semantic": 0.55
  },

  "absence_confidence": {
    "business_ip": 0.55,
    "source_sensitivity": 0.60,
    "security": 0.80,
    "pii": 0.85,
    "secret": 0.95
  },

  "thresholds": {
    "business_ip_external_raw_deny": 0.75,
    "business_ip_sanitize": 0.45,
    "business_ip_amplifier_only_ceiling": 0.35,
    "business_ip_static_only_ceiling": 0.65,
    "source_sensitivity_sanitize": 0.55,
    "security_specialized_model": 0.70,
    "security_sanitize": 0.55,
    "pii_mask": 0.45,
    "secret_any": 0.01
  },

  "aggregation": { "business_ip_amplifier_factor_without_primary": 0.4 },

  "centrality": { "fan_in_saturation": 6 },

  "contextual": {
    "ip_intent_bump_representation": 2,
    "generic_syntax_intent_relax_representation": 1
  }
}
$json$::jsonb);

-- ---------------------------------------------------------------------
-- 2. Risk Evidence — 구조화된 근거(§5, §6)
--
-- "business_ip = 0.94" 만 저장하지 않는다(§5). 왜 그 값이 나왔는지를
-- 그래프로 추적할 수 있어야 한다 — 각 근거는 target_symbol_key(평가 단위)에
-- 붙고, 다른 심볼과의 관계(called_by, dataflow 등)면 related_symbol_key 로
-- 잇는다. 이 두 컬럼이 곧 Risk Evidence Graph 의 엣지다(app/risk/graph.py).
--
-- 모든 근거는 기존 분석에서 파생된다(§4). source 컬럼이 재현 경로를 남긴다:
--   semantic_search  → symbol_semantic (role/domain/summary)
--   callers_within   → 호출 경로 / fan-in centrality
--   security_finding → taint source→sink (app/dataflow.py)
--   secret_scan      → app/risk/secret_scan.py (정규식, deterministic)
--   pii_scan         → app/risk/pii_scan.py    (정규식, deterministic)
--   cfg / pdg        → cfg_edge / pdg_data_edge 집계
-- ---------------------------------------------------------------------
CREATE TABLE risk_evidence (
    id                 bigserial   PRIMARY KEY,
    scan_id            uuid        NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    target_symbol_key  text        NOT NULL,
    related_symbol_key text,                                  -- 관계형 근거면 상대 심볼
    kind               text        NOT NULL,                  -- §6 evidence kind
    dimension          text        NOT NULL,                  -- business_ip|source_sensitivity|security|pii|secret|none
    weight             real        NOT NULL,                  -- 정책상 이 kind 가 dimension 에 주는 강도
    confidence         real        NOT NULL,
    description        text        NOT NULL,                  -- 사람이 읽는 근거 문장
    source             text        NOT NULL,                  -- 어느 기존 분석에서 왔는지
    location           jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- {path, line, ...}
    metadata           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    fingerprint        text        NOT NULL,                  -- 재계산 멱등을 위한 앱 계산 키
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT risk_evidence_uniq UNIQUE (scan_id, target_symbol_key, fingerprint),
    CONSTRAINT risk_evidence_weight_ck     CHECK (weight >= 0.0 AND weight <= 1.0),
    CONSTRAINT risk_evidence_confidence_ck CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE INDEX risk_evidence_target_idx  ON risk_evidence (scan_id, target_symbol_key);
CREATE INDEX risk_evidence_related_idx ON risk_evidence (scan_id, related_symbol_key)
    WHERE related_symbol_key IS NOT NULL;

COMMENT ON TABLE risk_evidence IS
    'Risk 판단의 구조화된 근거. 단순 설명 문자열이 아니라 Risk 판단을 재현할 수 '
    '있는 근거다(§6). 모든 항목은 기존 분석 결과에서 파생된다(§4).';

-- ---------------------------------------------------------------------
-- 3. Risk Object — Phase 2 의 핵심 data contract(§14, §23)
--
-- scan × 평가단위 하나당 한 행. 평가단위는 보통 function/method symbol 이지만
-- (§2, §25.4 — 파일 단위 차단보다 function/region 우선), file / code-region 도
-- 같은 스키마로 담을 수 있게 target 을 jsonb 로 둔다.
--
-- risk(Risk Vector, §10):   {dim: {score, confidence, evidence_refs:[risk_evidence.id]}}
-- exposure(Exposure Boundary, §13): {allowed_representation, external_raw_allowed, reasons}
-- provenance(§18): 미래에 사람이 Risk classification 을 수정/승인할 수 있도록
--                  결정 근거를 남긴다 — decision_source / model / policy_version / timestamp.
-- ---------------------------------------------------------------------
CREATE TABLE risk_object (
    scan_id            uuid        NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    target_symbol_key  text        NOT NULL,
    target             jsonb       NOT NULL,                      -- {type, symbol, file, lines}
    risk               jsonb       NOT NULL,                      -- Risk Vector
    classification     jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- {business_role, security_types, ...}
    exposure           jsonb       NOT NULL,                      -- Exposure Boundary
    rule_outcomes      jsonb       NOT NULL DEFAULT '[]'::jsonb,  -- Rule Engine 이 적용한 결정론적 가드레일
    provenance         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    generated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scan_id, target_symbol_key)
);

CREATE INDEX risk_object_scan_idx ON risk_object (scan_id);

COMMENT ON TABLE risk_object IS
    'Phase 2 핵심 산출물(§14). Risk Evidence Graph + Risk Vector + Contextual '
    'Risk + Exposure Boundary 를 하나로 묶은 계약. Phase 3~6 이 이 계약을 소비한다(§23).';

COMMIT;
