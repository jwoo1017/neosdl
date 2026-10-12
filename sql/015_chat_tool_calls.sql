-- =====================================================================
-- NeoSDL · 015_chat_tool_calls.sql
-- app/orchestrator.py의 멀티스텝 에이전틱 루프가 실제로 어떤 도구를 어떤
-- params로 불렀는지(Tool Call UI용) assistant 메시지에 같이 남긴다.
-- 001~014는 손대지 않는다 — ADD뿐이라 무손실.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/015_chat_tool_calls.sql
-- =====================================================================

BEGIN;

ALTER TABLE chat_message
    ADD COLUMN tools_used jsonb;  -- assistant 메시지에 한해 [{"name":...,"params":{...}}, ...]

COMMIT;
