-- =====================================================================
-- NeoSDL · 036_sandbox.sql
-- 샌드박스 세션 — 검증 대상 앱을 dind(docker-in-docker, TLS) 격리 컨테이너에서
-- 실제로 기동한 기록(worker/sandbox/*). 하나의 engagement(035) 아래 1:1 기동 핸들.
-- 임의 앱 자동 부팅은 실패가 잦다 — 실패는 오류가 아니라 정상 상태(failed_boot)로
-- 기록하고, 검증 엔진은 ready 가 아니면 정적 판정을 유지한다(graceful degradation).
-- 보안 민감 전이(승인/egress/비allowlist 이미지)는 audit_log 에 남긴다.
-- =====================================================================
BEGIN;

CREATE TYPE sandbox_status AS ENUM (
    'requested', 'pending_approval', 'building', 'booting',
    'ready', 'failed_boot', 'terminated');
CREATE TYPE sandbox_artifact_kind AS ENUM ('log', 'build', 'dockerfile', 'snapshot');

CREATE TABLE sandbox_session (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    engagement_id   uuid NOT NULL REFERENCES engagement(id) ON DELETE CASCADE,
    scan_id         uuid REFERENCES scan(id)       ON DELETE SET NULL,
    repository_id   uuid REFERENCES repository(id) ON DELETE SET NULL,
    status          sandbox_status NOT NULL DEFAULT 'requested',
    image           text,                  -- 빌드/선택된 앱 이미지 태그
    base_url        text,                  -- ready 시 검증 엔진이 때릴 내부 주소(dind 루프백)
    port            integer,
    ready_signal    text,                  -- 'port' | 'health' | 'log' (무엇으로 ready 판정했나)
    failure_stage   text,                  -- detect|build|provision|migrate|boot|ready
    failure_reason  text,                  -- 마스킹된 사유
    ttl_seconds     integer NOT NULL DEFAULT 1800,
    created_by      uuid REFERENCES member(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    ready_at        timestamptz,
    terminated_at   timestamptz
);
CREATE INDEX sandbox_session_engagement_idx ON sandbox_session (engagement_id);
CREATE INDEX sandbox_session_active_idx ON sandbox_session (status)
    WHERE status IN ('requested', 'pending_approval', 'building', 'booting', 'ready');

CREATE TABLE sandbox_artifact (
    id              bigserial PRIMARY KEY,
    session_id      uuid NOT NULL REFERENCES sandbox_session(id) ON DELETE CASCADE,
    kind            sandbox_artifact_kind NOT NULL,
    storage_key     text NOT NULL,         -- app/storage.py 패턴의 루트 상대 키
    bytes           bigint,
    masked          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sandbox_artifact_session_idx ON sandbox_artifact (session_id, kind);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'neosdl_app') THEN
        GRANT SELECT, INSERT, UPDATE ON sandbox_session TO neosdl_app;
        GRANT SELECT, INSERT ON sandbox_artifact TO neosdl_app;
        GRANT USAGE ON SEQUENCE sandbox_artifact_id_seq TO neosdl_app;
    END IF;
END
$$;

COMMIT;
