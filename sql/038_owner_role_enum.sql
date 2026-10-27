-- =====================================================================
-- NeoSDL · 038_owner_role_enum.sql
-- workspace/teamspace 역할에 'owner'를 추가한다(Notion·Slack·GitHub Org의 소유자 계층).
--
-- enum 값 추가는 같은 트랜잭션 안에서 바로 쓸 수 없어서(PostgreSQL 제약) 단독 파일로 둔다 —
-- 실제 백필·트리거 교체는 039 에서 한다. BEGIN/COMMIT 으로 감싸지 않는다.
-- =====================================================================

ALTER TYPE workspace_role ADD VALUE IF NOT EXISTS 'owner';
