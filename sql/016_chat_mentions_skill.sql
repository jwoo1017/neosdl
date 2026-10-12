-- =====================================================================
-- NeoSDL · 016_chat_mentions_skill.sql
-- 채팅 입력창에 추가된 "@ 프로젝트 멘션"과 "스킬(도구 강제 호출)"을 user
-- 메시지에 남겨서, 새로고침 후에도 칩/배지를 다시 그릴 수 있게 한다.
-- 001~015는 손대지 않는다 — ADD뿐이라 무손실.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/016_chat_mentions_skill.sql
-- =====================================================================

BEGIN;

ALTER TABLE chat_message
    ADD COLUMN mentioned_project_ids uuid[],  -- user 메시지에 한해: @ 멘션한 프로젝트 id들
    ADD COLUMN forced_tool text;              -- user 메시지에 한해: 스킬로 강제 지정한 도구명

COMMIT;
