-- =====================================================================
-- NeoSDL · 012_teamspace_discipline.sql
-- teamspace_member(와 그 초대)에 직무(discipline: PM/frontend/backend)를
-- 추가한다. 직무는 teamspace 합류 단계에서 정해두고, project 생성
-- 마법사의 "담당자" 단계는 그 teamspace 멤버 중에서 골라 project_role
-- (manager/developer/viewer)만 고르면 되게 한다 — 소속(누가 이 팀인가)과
-- project 권한(그 팀 안에서 무엇을 할 수 있는가)을 분리한 것과 같은 이유로,
-- 직무(무슨 역할을 하는 사람인가)도 project 권한과 별개로 둔다.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/012_teamspace_discipline.sql
-- =====================================================================

BEGIN;

CREATE TYPE teamspace_discipline AS ENUM ('pm', 'frontend', 'backend');

ALTER TABLE teamspace_member ADD COLUMN discipline teamspace_discipline;
ALTER TABLE teamspace_invite ADD COLUMN discipline teamspace_discipline;

COMMIT;
