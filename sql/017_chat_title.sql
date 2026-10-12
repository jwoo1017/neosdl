-- =====================================================================
-- NeoSDL · 017_chat_title.sql
-- 프로젝트 채팅의 첫 질문으로부터 LLM이 짓는 대화 제목. 프로젝트당 대화
-- 스레드가 1개뿐이라(sql/014 참고) project에 직접 둔다 — 세션이 여러 개로
-- 늘어나면 그때 세션 테이블로 옮긴다.
-- 001~016은 손대지 않는다 — ADD뿐이라 무손실.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/017_chat_title.sql
-- =====================================================================

BEGIN;

ALTER TABLE project
    ADD COLUMN chat_title text;

COMMIT;
