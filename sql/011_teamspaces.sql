-- =====================================================================
-- NeoSDL · 011_teamspaces.sql
-- workspace와 project 사이에 teamspace 계층을 추가한다(Notion의
-- workspace/teamspace 모델 참고). project는 이제 workspace에 직접 붙지
-- 않고 반드시 teamspace 하나에 속한다 — workspace_id 컬럼은 조인 부담을
-- 줄이려고 project에 그대로 남겨두되(자주 쓰는 조회라 비정규화), 값은
-- 항상 자신이 속한 teamspace.workspace_id와 같아야 한다(앱 레벨에서 보장).
--
-- teamspace.visibility 3단계:
--   open    — workspace 멤버 누구나 열람 + 자율 가입(참여) 가능
--   closed  — workspace 멤버 누구나 열람은 가능하지만 가입은 초대로만("참가 제한")
--   private — teamspace_member가 아니면 존재 자체가 안 보임
-- open/closed는 workspace 멤버라면 그 안의 project에 최소 viewer로 접근
-- 가능(기존 [[_effective_project_role]] 규칙 확장), private은 teamspace_member만.
--
-- 기존 project는 전부 그 workspace의 새 기본 teamspace('일반')로 편입한다
-- — teamspace 없는 project를 허용하지 않기로 했으므로 무손실 전환이 필수.
-- 그 기본 teamspace는 visibility='open'으로 만들고, 전환 시점의 workspace
-- 멤버 전원을 동일 role로 teamspace_member에 백필해서, 이 마이그레이션
-- 전후로 "누가 어떤 project를 볼 수 있는가"가 달라지지 않게 한다.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/011_teamspaces.sql
-- =====================================================================

BEGIN;

CREATE TYPE teamspace_visibility AS ENUM ('open', 'closed', 'private');

CREATE TABLE teamspace (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    name         text NOT NULL,
    description  text,
    visibility   teamspace_visibility NOT NULL DEFAULT 'open',
    created_by   uuid NOT NULL REFERENCES member(id),
    deleted_at   timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX teamspace_workspace_idx ON teamspace (workspace_id);

-- role은 workspace_member와 같은 admin/member 2단계라 workspace_role enum을
-- 그대로 재사용한다(팀스페이스 자체를 관리할 수 있는지만 가른다 — project
-- 단위 세부 권한은 지금처럼 project_member가 계속 담당).
CREATE TABLE teamspace_member (
    teamspace_id uuid NOT NULL REFERENCES teamspace(id) ON DELETE CASCADE,
    member_id    uuid NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    role         workspace_role NOT NULL DEFAULT 'member',
    invited_by   uuid REFERENCES member(id),
    joined_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (teamspace_id, member_id)
);

CREATE INDEX teamspace_member_member_idx ON teamspace_member (member_id);

CREATE TABLE teamspace_invite (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    teamspace_id  uuid NOT NULL REFERENCES teamspace(id) ON DELETE CASCADE,
    role          workspace_role NOT NULL DEFAULT 'member',
    token_hash    text NOT NULL,
    target_email  text,
    created_by    uuid NOT NULL REFERENCES member(id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    email_sent    boolean NOT NULL DEFAULT false,
    accepted_at   timestamptz,
    accepted_by   uuid REFERENCES member(id),
    revoked_at    timestamptz,
    CONSTRAINT teamspace_invite_token_hash_uniq UNIQUE (token_hash)
);

CREATE INDEX teamspace_invite_teamspace_idx ON teamspace_invite (teamspace_id);
CREATE INDEX teamspace_invite_pending_idx ON teamspace_invite (teamspace_id)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

CREATE OR REPLACE FUNCTION teamspace_member_protect_last_admin() RETURNS trigger AS $$
DECLARE
    other_admins int;
BEGIN
    IF OLD.role <> 'admin' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.role = 'admin' THEN
        RETURN NEW;
    END IF;
    SELECT count(*) INTO other_admins FROM teamspace_member
     WHERE teamspace_id = OLD.teamspace_id AND role = 'admin' AND member_id <> OLD.member_id;
    IF other_admins = 0 THEN
        RAISE EXCEPTION 'teamspace % must keep at least one admin', OLD.teamspace_id;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER teamspace_member_protect_last_admin_trg
    BEFORE UPDATE OR DELETE ON teamspace_member
    FOR EACH ROW EXECUTE FUNCTION teamspace_member_protect_last_admin();

-- ---------------------------------------------------------------------
-- 기존 project를 workspace별 기본 teamspace로 편입
-- ---------------------------------------------------------------------
ALTER TABLE project ADD COLUMN teamspace_id uuid REFERENCES teamspace(id) ON DELETE CASCADE;

DO $$
DECLARE
    w RECORD;
    new_teamspace_id uuid;
BEGIN
    -- 소프트 삭제된 workspace도 포함한다 — 그 밑에 남아있는(마찬가지로
    -- 소프트 삭제된) project까지 전부 teamspace_id를 채워야 바로 아래
    -- NOT NULL 제약이 걸린다.
    FOR w IN SELECT id, created_by FROM workspace
    LOOP
        INSERT INTO teamspace (workspace_id, name, visibility, created_by)
        VALUES (w.id, '일반', 'open', w.created_by)
        RETURNING id INTO new_teamspace_id;

        INSERT INTO teamspace_member (teamspace_id, member_id, role)
        SELECT new_teamspace_id, wm.member_id, wm.role
          FROM workspace_member wm
         WHERE wm.workspace_id = w.id;

        UPDATE project SET teamspace_id = new_teamspace_id
         WHERE workspace_id = w.id AND teamspace_id IS NULL;
    END LOOP;
END $$;

ALTER TABLE project ALTER COLUMN teamspace_id SET NOT NULL;
CREATE INDEX project_teamspace_idx ON project (teamspace_id);

COMMIT;
