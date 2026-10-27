-- =====================================================================
-- NeoSDL · 039_owner_role_policy.sql
-- 역할 3단계(owner > admin > member) 정책 — workspace / teamspace 공통.
--   owner  — 공간 삭제, admin·owner 강등/제거, owner 지정. 최소 1명 유지.
--   admin  — 설정·초대·member 관리(member→admin 승격까지). admin/owner 는 건드릴 수 없다.
--   member — 이용만.
-- "최소 1명 유지" 보호 대상을 admin 에서 owner 로 옮긴다(유일 owner 는 위임하거나 공간을 삭제해야
-- 나갈 수 있다). 초대로는 owner 를 줄 수 없다 — 초대 role 은 앱에서 admin|member 로 막는다.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. 백필 — 공간마다 owner 1명. 생성자가 아직 멤버면 생성자, 아니면 가장 오래된 admin,
--    admin 도 없으면 가장 오래된 멤버. (admin/member → owner 승격이라 기존 트리거에 안 걸린다.)
-- ---------------------------------------------------------------------
UPDATE workspace_member wm SET role = 'owner'
  FROM (
      SELECT DISTINCT ON (x.workspace_id) x.workspace_id, x.member_id
        FROM workspace_member x JOIN workspace w ON w.id = x.workspace_id
       ORDER BY x.workspace_id,
                (x.member_id = w.created_by) DESC,
                (x.role = 'admin') DESC,
                x.joined_at
  ) pick
 WHERE wm.workspace_id = pick.workspace_id AND wm.member_id = pick.member_id;

UPDATE teamspace_member tm SET role = 'owner'
  FROM (
      SELECT DISTINCT ON (x.teamspace_id) x.teamspace_id, x.member_id
        FROM teamspace_member x JOIN teamspace t ON t.id = x.teamspace_id
       ORDER BY x.teamspace_id,
                (x.member_id = COALESCE(t.personal_owner_id, t.created_by)) DESC,
                (x.role = 'admin') DESC,
                x.joined_at
  ) pick
 WHERE tm.teamspace_id = pick.teamspace_id AND tm.member_id = pick.member_id;

-- ---------------------------------------------------------------------
-- 2. 마지막 owner 보호 트리거로 교체(함수 이름은 그대로 — 트리거 재생성 불필요)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION workspace_member_protect_last_admin() RETURNS trigger AS $$
DECLARE
    other_owners int;
BEGIN
    IF OLD.role <> 'owner' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.role = 'owner' THEN
        RETURN NEW;
    END IF;
    SELECT count(*) INTO other_owners FROM workspace_member
     WHERE workspace_id = OLD.workspace_id AND role = 'owner' AND member_id <> OLD.member_id;
    IF other_owners = 0 THEN
        RAISE EXCEPTION 'workspace % must keep at least one owner', OLD.workspace_id;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION teamspace_member_protect_last_admin() RETURNS trigger AS $$
DECLARE
    other_owners int;
BEGIN
    IF OLD.role <> 'owner' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.role = 'owner' THEN
        RETURN NEW;
    END IF;
    SELECT count(*) INTO other_owners FROM teamspace_member
     WHERE teamspace_id = OLD.teamspace_id AND role = 'owner' AND member_id <> OLD.member_id;
    IF other_owners = 0 THEN
        RAISE EXCEPTION 'teamspace % must keep at least one owner', OLD.teamspace_id;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

COMMIT;
