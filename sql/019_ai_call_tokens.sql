-- WBS 2.2 / LLM 게이트웨이 — 호출별 토큰 사용량 기록.
--
-- 모든 LLM egress 가 LiteLLM 게이트웨이를 지나가게 되면서(app/ai_providers.py),
-- 게이트웨이 응답의 usage(prompt/completion 토큰)를 ai_call_log 에 같이 남긴다.
-- 게이트웨이 자체 DB(litellm)가 비용·키·감사를 담당하지만, 프로젝트 대시보드
-- (/projects/{id}/ai-usage, SecurityAiSettingsPage)는 이 표를 그대로 쓰므로
-- 앱 관점 집계에 필요한 최소 컬럼만 여기 둔다.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/019_ai_call_tokens.sql

BEGIN;

ALTER TABLE ai_call_log ADD COLUMN IF NOT EXISTS prompt_tokens     integer;
ALTER TABLE ai_call_log ADD COLUMN IF NOT EXISTS completion_tokens integer;

COMMIT;
