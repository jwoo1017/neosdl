-- =====================================================================
-- NeoSDL · 018_chat_sessions.sql
-- 프로젝트 채팅을 "프로젝트당 스레드 1개"에서 "프로젝트 안에 여러 세션"으로
-- 분리한다. 014/017이 미뤄뒀던 세션 테이블을 실제로 만든다.
--
--   * chat_session — 프로젝트에 속한 대화 세션(제목은 세션별로 가진다).
--   * chat_message.session_id — 메시지가 어느 세션에 속하는지.
--   * project.chat_title — 세션별 제목(chat_session.title)으로 이관 후 제거.
--
-- 백필은 무손실이다 — chat_message가 있는 프로젝트마다 세션 1개를 만들어
-- 기존 메시지를 전부 그 세션으로 옮기고, project.chat_title을 그 세션의
-- 제목으로 넣는다. 001~017은 손대지 않는다.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/018_chat_sessions.sql
-- =====================================================================

BEGIN;

CREATE TABLE chat_session (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      uuid        NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    title           text,                    -- 첫 질문으로 LLM이 짓거나 사용자가 직접 바꾼다. NULL이면 프론트가 "새 채팅"으로 표시.
    created_by      uuid        REFERENCES member(id) ON DELETE SET NULL,  -- 백필분은 NULL
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    last_message_at timestamptz,             -- 세션 목록 정렬용(메시지 저장 시마다 갱신)
    deleted_at      timestamptz              -- 소프트 삭제 — 메시지는 CASCADE로 같이 사라지지 않게 여기서만 가린다
);

CREATE INDEX chat_session_project_idx ON chat_session (project_id)
    WHERE deleted_at IS NULL;

ALTER TABLE chat_message
    ADD COLUMN session_id uuid REFERENCES chat_session(id) ON DELETE CASCADE;

-- 백필: 메시지가 하나라도 있는 프로젝트마다 세션 1개를 만든다.
INSERT INTO chat_session (project_id, title, created_at, updated_at, last_message_at)
SELECT p.id, p.chat_title, min(cm.created_at), now(), max(cm.created_at)
  FROM project p
  JOIN chat_message cm ON cm.project_id = p.id
 GROUP BY p.id, p.chat_title;

UPDATE chat_message cm
   SET session_id = cs.id
  FROM chat_session cs
 WHERE cs.project_id = cm.project_id AND cm.session_id IS NULL;

ALTER TABLE chat_message ALTER COLUMN session_id SET NOT NULL;
CREATE INDEX chat_message_session_idx ON chat_message (session_id, created_at);

-- project.chat_title은 chat_session.title로 이관 완료 — 더 이상 쓰지 않는다.
ALTER TABLE project DROP COLUMN chat_title;

COMMIT;
