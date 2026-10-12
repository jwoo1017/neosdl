-- =====================================================================
-- NeoSDL · 007_teams.sql
-- 팀/역할/권한/초대 — installation 소유권을 member → team 으로 이전.
--
-- 001~006 은 이미 서빙 중인 정본이므로 여기서는 ALTER/CREATE 만 하고
-- 기존 컬럼/제약을 절대 건드리지 않는다 (installation.member_id 는
-- 컬럼명을 바꾸지만 데이터 손실 없는 순수 RENAME).
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/007_teams.sql
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 0. 고정 권한 카탈로그 — 앱에서 게이팅하는 액션 8종.
--    새 권한이 필요해지면 ALTER TYPE ... ADD VALUE 로 추가만 한다.
-- ---------------------------------------------------------------------
CREATE TYPE team_permission AS ENUM (
    'repository.register',
    'repository.webhook_toggle',
    'scan.run',
    'scan.view',
    'team.invite',
    'team.manage_roles',
    'team.remove_members',
    'installation.manage'
);

-- ---------------------------------------------------------------------
-- 1. team — 협업 단위. 여러 installation(=여러 GitHub org/계정)을 소유할 수 있다.
--    deleted_at 은 소프트 삭제용 — 삭제해도 소속 installation/repo/scan/
--    audit_log 는 남기고, 이후 모든 팀 스코프 조회에서만 안 보이게 한다.
-- ---------------------------------------------------------------------
CREATE TABLE team (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text        NOT NULL,
    created_by  uuid        NOT NULL REFERENCES member(id),
    deleted_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 2. team_role — 팀별 역할. PM 은 is_pm=true, 고정/불변(트리거로 보호).
--    권한은 배열 컬럼으로 저장한다 — 카탈로그가 8개뿐이고, 인가 체크가
--    "이미 조인된 role 행의 배열에 값이 있는가" 단일 predicate라
--    별도 join table보다 단순·저렴하다.
-- ---------------------------------------------------------------------
CREATE TABLE team_role (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id     uuid NOT NULL REFERENCES team(id) ON DELETE CASCADE,
    name        text NOT NULL,
    is_pm       boolean NOT NULL DEFAULT false,
    permissions team_permission[] NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT team_role_team_name_uniq UNIQUE (team_id, name)
);

-- 팀당 PM 역할은 정확히 하나만 존재할 수 있다.
CREATE UNIQUE INDEX team_role_one_pm_idx ON team_role (team_id) WHERE is_pm;

COMMENT ON COLUMN team_role.is_pm IS
    'PM 역할 표식. team_role_protect_pm_trg 가 삭제/권한축소/is_pm 해제를 막는다.';

-- ---------------------------------------------------------------------
-- 3. team_member — 팀 소속 + 역할 배정.
-- ---------------------------------------------------------------------
CREATE TABLE team_member (
    team_id     uuid NOT NULL REFERENCES team(id) ON DELETE CASCADE,
    member_id   uuid NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    role_id     uuid NOT NULL REFERENCES team_role(id) ON DELETE RESTRICT,
    invited_by  uuid REFERENCES member(id),
    joined_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, member_id)
);

CREATE INDEX team_member_member_idx ON team_member (member_id);
CREATE INDEX team_member_role_idx ON team_member (role_id);

-- ---------------------------------------------------------------------
-- 4. team_invite — 토큰 기반 초대. 원문 토큰은 저장하지 않고 해시만 저장.
-- ---------------------------------------------------------------------
CREATE TABLE team_invite (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id              uuid NOT NULL REFERENCES team(id) ON DELETE CASCADE,
    role_id              uuid NOT NULL REFERENCES team_role(id) ON DELETE RESTRICT,
    token_hash           text NOT NULL,
    target_github_login  text,          -- NULL 이면 링크 소지자 아무나(로그인 상태) 수락 가능
    created_by           uuid NOT NULL REFERENCES member(id),
    created_at           timestamptz NOT NULL DEFAULT now(),
    expires_at           timestamptz NOT NULL,
    accepted_at          timestamptz,
    accepted_by          uuid REFERENCES member(id),
    revoked_at           timestamptz,
    CONSTRAINT team_invite_token_hash_uniq UNIQUE (token_hash)
);

CREATE INDEX team_invite_team_idx ON team_invite (team_id);
CREATE INDEX team_invite_pending_idx ON team_invite (team_id)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

COMMENT ON COLUMN team_invite.token_hash IS
    'sha256(원문 토큰). 원문은 생성 응답 1회만 노출, DB 에는 저장 안 함 '
    '(세션 JWT 와 별개 원칙 — 초대 링크는 공유/전달되므로 탈취 대비).';

-- ---------------------------------------------------------------------
-- 5. installation — member 소유 → team 소유로 전환.
--    member_id 는 "누가 이 설치를 연결했는지"만 남기고 linked_by_member_id 로 개명.
--    team_id 는 NULLABLE 유지 — GitHub Setup URL 콜백 시점에 team 이 아직
--    정해지지 않은 과도 상태를 위해 repository.installation_id 와 같은 패턴
--    ("NULL 은 INNER JOIN에서 자동으로 안 보임")을 그대로 쓴다.
-- ---------------------------------------------------------------------
ALTER TABLE installation RENAME COLUMN member_id TO linked_by_member_id;
ALTER INDEX installation_member_idx RENAME TO installation_linked_by_idx;

ALTER TABLE installation ADD COLUMN team_id uuid REFERENCES team(id) ON DELETE SET NULL;
CREATE INDEX installation_team_idx ON installation (team_id);

COMMENT ON COLUMN installation.linked_by_member_id IS
    '설치를 GitHub Setup URL 로 연결한 사람 (감사/이력용). 소유권 판정에는 team_id 를 쓴다.';

-- ---------------------------------------------------------------------
-- 6. 백필 — 기존 installation(team_id IS NULL, 이번 마이그레이션 이전 행)을
--    linked_by_member_id 기준 "개인 팀"으로 자동 편입한다. 데이터를 잃지
--    않고 team 모델로 무손실 전환하는 것이 목적이다.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    r RECORD;
    new_team_id uuid;
    pm_role_id uuid;
BEGIN
    FOR r IN
        SELECT DISTINCT i.linked_by_member_id, m.github_login
          FROM installation i
          JOIN member m ON m.id = i.linked_by_member_id
         WHERE i.team_id IS NULL
    LOOP
        INSERT INTO team (name, created_by)
        VALUES (r.github_login || ' (personal)', r.linked_by_member_id)
        RETURNING id INTO new_team_id;

        INSERT INTO team_role (team_id, name, is_pm, permissions)
        VALUES (new_team_id, 'PM', true, enum_range(NULL::team_permission))
        RETURNING id INTO pm_role_id;

        INSERT INTO team_role (team_id, name, permissions) VALUES
            (new_team_id, 'Backend',
             ARRAY['repository.register', 'repository.webhook_toggle',
                   'scan.run', 'scan.view', 'installation.manage']::team_permission[]),
            (new_team_id, 'FrontEnd',
             ARRAY['scan.view']::team_permission[]);

        INSERT INTO team_member (team_id, member_id, role_id)
        VALUES (new_team_id, r.linked_by_member_id, pm_role_id);

        UPDATE installation SET team_id = new_team_id
         WHERE linked_by_member_id = r.linked_by_member_id AND team_id IS NULL;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- 7. PM 보호 트리거 — 앱 레이어 체크와 별개로 DB가 최후 방어선이 된다.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION team_role_protect_pm() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_pm THEN
            RAISE EXCEPTION 'cannot delete the PM role of a team';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.is_pm AND NOT NEW.is_pm THEN
        RAISE EXCEPTION 'cannot strip PM status from the PM role';
    END IF;
    IF NEW.is_pm AND NOT (NEW.permissions @> enum_range(NULL::team_permission)) THEN
        RAISE EXCEPTION 'PM role permissions cannot be reduced from the full catalog';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER team_role_protect_pm_trg
    BEFORE UPDATE OR DELETE ON team_role
    FOR EACH ROW EXECUTE FUNCTION team_role_protect_pm();

CREATE OR REPLACE FUNCTION team_member_protect_last_pm() RETURNS trigger AS $$
DECLARE
    old_is_pm boolean;
    other_pms int;
BEGIN
    SELECT is_pm INTO old_is_pm FROM team_role WHERE id = OLD.role_id;
    IF NOT old_is_pm THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.role_id = OLD.role_id THEN
        RETURN NEW;
    END IF;
    SELECT count(*) INTO other_pms
      FROM team_member tm JOIN team_role tr ON tr.id = tm.role_id
     WHERE tm.team_id = OLD.team_id AND tr.is_pm AND tm.member_id <> OLD.member_id;
    IF other_pms = 0 THEN
        RAISE EXCEPTION 'team % must keep at least one PM member', OLD.team_id;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER team_member_protect_last_pm_trg
    BEFORE UPDATE OR DELETE ON team_member
    FOR EACH ROW EXECUTE FUNCTION team_member_protect_last_pm();

COMMIT;
