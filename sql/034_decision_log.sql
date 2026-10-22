-- =====================================================================
-- NeoSDL · 034_decision_log.sql
-- 채팅 에이전트 결정 지점 기록(app/decision_log.py).
--
-- ai_call_log 는 호출 성공 여부만 남긴다. 여기는 "무엇을 결정했는가"를 남긴다:
--   point       plan(조사 계획) | step(다음 도구 선택) | relevance(근거 선별)
--   decision    결정 내용(도구·params·계획 단계·유지한 근거 인덱스 등)
--   parse_path  schema(스키마 제약 통과) | fallback(관대한 파서로 복구) | json | guard(백스톱이 강제) | fail | error
--   guard_fired 백스톱이 개입했으면 그 이름(orientation_followup / plan_backstop)
-- 결정 모델(Laya) 도입 전 베이스라인 측정, 이후 shadow 비교·학습 데이터로 쓴다.
-- =====================================================================
BEGIN;

CREATE TABLE IF NOT EXISTS decision_log (
    id          bigserial   PRIMARY KEY,
    project_id  uuid        NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    scan_id     uuid,
    point       text        NOT NULL,
    model       text        NOT NULL,
    question    text        NOT NULL,
    decision    jsonb       NOT NULL,
    parse_path  text        NOT NULL,
    guard_fired text,
    latency_ms  integer,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS decision_log_project_created_idx ON decision_log (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS decision_log_point_idx ON decision_log (point, parse_path);

-- 운영은 마이그레이션 계정과 앱 실행 계정(neosdl_app, DDL 권한 없음)이 분리돼 있다 —
-- 앱은 기록(INSERT)과 조회(SELECT)만 한다. bigserial 이라 INSERT 에 시퀀스 USAGE 도 필요하다.
-- 행은 project CASCADE 로만 지워진다. 로컬 dev 처럼 그 계정이 없으면 건너뛴다.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'neosdl_app') THEN
        GRANT SELECT, INSERT ON decision_log TO neosdl_app;
        GRANT USAGE ON SEQUENCE decision_log_id_seq TO neosdl_app;
    END IF;
END
$$;

COMMIT;
