-- =====================================================================
-- NeoSDL · 014_chat.sql
-- 프로젝트 단위 AI 채팅. 프로젝트당 세션은 1개(단일 스레드)로 충분하므로
-- 별도 session 테이블 없이 메시지에 project_id를 직접 둔다.
-- 001~013은 손대지 않는다 — ADD뿐이라 무손실.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/014_chat.sql
-- =====================================================================

BEGIN;

CREATE TABLE chat_message (
    id          bigserial PRIMARY KEY,
    project_id  uuid        NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    role        text        NOT NULL,   -- 'user' | 'assistant'
    content     text        NOT NULL,
    evidence    jsonb,                   -- assistant 메시지에 한해 orchestrator 근거 스냅샷
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chat_message_role_ck CHECK (role IN ('user', 'assistant'))
);

CREATE INDEX chat_message_project_idx ON chat_message (project_id, created_at);

COMMIT;
