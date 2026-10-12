-- =====================================================================
-- NeoSDL · 010_invite_email_sent.sql
-- 초대 메일이 실제로 발송됐는지를 초대 레코드 자체에 남긴다 — SMTP
-- 미설정/발송 실패 시에도 초대(및 링크)는 그대로 유효하지만, 관리자가
-- "메일이 갔는지"를 나중에 다시 봐도 알 수 있어야 재발송 여부를 판단할 수
-- 있다. 응답 한 번에 실려오는 임시 값이 아니라 영구 컬럼으로 남긴다.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/010_invite_email_sent.sql
-- =====================================================================

BEGIN;

ALTER TABLE workspace_invite ADD COLUMN email_sent boolean NOT NULL DEFAULT false;
ALTER TABLE project_invite   ADD COLUMN email_sent boolean NOT NULL DEFAULT false;

COMMIT;
