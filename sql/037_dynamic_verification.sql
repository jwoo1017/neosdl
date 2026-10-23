-- =====================================================================
-- NeoSDL · 037_dynamic_verification.sql
-- 정적→동적 검증 판정 — 정적 taint finding(security_finding)을 샌드박스에서 기동한
-- 앱에 무해 마커로 실제 요청해 confirmed/false_positive 로 판정한 결과(app/dynamic/*).
-- 이번 세션에서 드러난 SQL 인젝션 오탐 9건(전부 파라미터 바인딩 쿼리)을 제거하는 것이 1차 목표.
--
-- security_finding.status 를 덮어쓰지 않는다 — finding 은 스캔별 불변 스냅샷이고, 검증은
-- 특정 engagement(기동 시점)에 묶인다. 판정은 별도 테이블에 쌓고 최신 판정은 조인으로 노출.
-- =====================================================================
BEGIN;

CREATE TYPE verification_verdict AS ENUM (
    'confirmed', 'false_positive', 'inconclusive', 'not_attempted');
CREATE TYPE verification_oracle  AS ENUM ('blackbox', 'iast', 'blackbox+iast');
CREATE TYPE verification_artifact_kind AS ENUM ('request', 'response', 'app_log', 'iast_event');

CREATE TABLE finding_verification (
    id              bigserial PRIMARY KEY,
    finding_id      bigint NOT NULL REFERENCES security_finding(id) ON DELETE CASCADE,
    engagement_id   uuid   NOT NULL REFERENCES engagement(id)       ON DELETE CASCADE,
    verdict         verification_verdict NOT NULL,
    confidence      real NOT NULL DEFAULT 0,       -- 0..1
    oracle          verification_oracle NOT NULL DEFAULT 'blackbox',
    entry_route     jsonb,                 -- {method, path, param_name, param_in, auth_required, resolved_by}
    signals         jsonb,                 -- 오라클이 쓴 카테고리별 신호
    reason          text,                  -- not_attempted/inconclusive 사유(no_http_entrypoint 등)
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (finding_id, engagement_id)
);
CREATE INDEX finding_verification_finding_idx ON finding_verification (finding_id, created_at DESC);

CREATE TABLE verification_artifact (
    id              bigserial PRIMARY KEY,
    verification_id bigint NOT NULL REFERENCES finding_verification(id) ON DELETE CASCADE,
    kind            verification_artifact_kind NOT NULL,
    content         text,                  -- 저장 전 secret_scan + pii_scan 마스킹
    redactions      integer NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX verification_artifact_verification_idx ON verification_artifact (verification_id, kind);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'neosdl_app') THEN
        GRANT SELECT, INSERT ON finding_verification, verification_artifact TO neosdl_app;
        GRANT USAGE ON SEQUENCE finding_verification_id_seq, verification_artifact_id_seq TO neosdl_app;
    END IF;
END
$$;

COMMIT;
