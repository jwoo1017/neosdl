-- =====================================================================
-- NeoSDL · 008_workspaces_projects.sql
-- team/커스텀 역할 모델(007) → workspace/project/고정 4단계 역할 모델로 전환.
--
-- member(서비스 계정) ≠ GitHub 신원. 여러 GitHub 계정을 한 member에 연결할
-- 수 있어야 해서(개인 계정 + 회사 계정 등) GitHub 신원을 별도 테이블
-- (github_account)로 뺀다. Repository는 installation 전체가 아니라 개별
-- 단위로 project에 붙는다(project_repository).
--
-- 001~007은 이미 서빙 중인 정본이다. 이 마이그레이션은 007이 만든 것
-- 위에서 RENAME/ALTER/DROP을 적용해 재구조화한다 — 007은 이 세션에서
-- 방금 적용된 것이라 실제 데이터가 없다시피 하므로(개발용 테스트 데이터
-- 뿐) 안전하게 재구조화 대상으로 삼는다. 001~006(핵심 스캔/그래프
-- 파이프라인)의 테이블/컬럼은 이번에도 건드리지 않는다.
--
-- 순서가 중요하다 — 특히 "옛 role_id 컬럼을 지우기 전에 그 컬럼을 참조하는
-- 옛 트리거부터 지운다"는 순서를 지킨다. 트랜잭션 안이라 실질적 위험은
-- 없지만(중간에 project_member에 DML이 없음), 방어적으로 순서를 고정한다.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/008_workspaces_projects.sql
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 0. 신규 enum
-- ---------------------------------------------------------------------
CREATE TYPE workspace_role AS ENUM ('admin', 'member');
CREATE TYPE project_role AS ENUM ('owner', 'manager', 'developer', 'viewer');

-- ---------------------------------------------------------------------
-- 1. workspace / workspace_member / workspace_invite
-- ---------------------------------------------------------------------
CREATE TABLE workspace (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text        NOT NULL,
    created_by  uuid        NOT NULL REFERENCES member(id),
    deleted_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE workspace_member (
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    member_id    uuid NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    role         workspace_role NOT NULL DEFAULT 'member',
    invited_by   uuid REFERENCES member(id),
    joined_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, member_id)
);

CREATE INDEX workspace_member_member_idx ON workspace_member (member_id);

CREATE TABLE workspace_invite (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    role          workspace_role NOT NULL DEFAULT 'member',
    token_hash    text NOT NULL,
    target_email  text,          -- NULL 이면 링크 소지자 아무나(로그인 상태) 수락 가능
    created_by    uuid NOT NULL REFERENCES member(id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    accepted_at   timestamptz,
    accepted_by   uuid REFERENCES member(id),
    revoked_at    timestamptz,
    CONSTRAINT workspace_invite_token_hash_uniq UNIQUE (token_hash)
);

CREATE INDEX workspace_invite_workspace_idx ON workspace_invite (workspace_id);
CREATE INDEX workspace_invite_pending_idx ON workspace_invite (workspace_id)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

-- ---------------------------------------------------------------------
-- 2. team -> project. 007의 커스텀 역할 시스템을 고정 4단계 enum으로 대체.
-- ---------------------------------------------------------------------
ALTER TABLE team RENAME TO project;
ALTER TABLE project ADD COLUMN workspace_id uuid REFERENCES workspace(id) ON DELETE CASCADE;

ALTER TABLE team_member RENAME TO project_member;
ALTER TABLE project_member RENAME COLUMN team_id TO project_id;
ALTER INDEX team_member_member_idx RENAME TO project_member_member_idx_old;
ALTER INDEX team_member_role_idx RENAME TO project_member_role_idx_old;

ALTER TABLE project_member ADD COLUMN role project_role;
UPDATE project_member pm
   SET role = CASE WHEN tr.is_pm THEN 'owner' ELSE 'developer' END::project_role
  FROM team_role tr
 WHERE tr.id = pm.role_id;
ALTER TABLE project_member ALTER COLUMN role SET NOT NULL;

ALTER TABLE team_invite RENAME TO project_invite;
ALTER TABLE project_invite RENAME COLUMN team_id TO project_id;
ALTER TABLE project_invite ADD COLUMN role project_role;
UPDATE project_invite pi
   SET role = CASE WHEN tr.is_pm THEN 'owner' ELSE 'developer' END::project_role
  FROM team_role tr
 WHERE tr.id = pi.role_id;
ALTER TABLE project_invite ALTER COLUMN role SET NOT NULL;
ALTER TABLE project_invite RENAME COLUMN target_github_login TO target_email;
-- 옛 값은 GitHub 로그인 문자열이라 이메일이 아니다 — dev 전용 테스트 데이터라
-- 무손실 전환 대상이 아니므로 초기화한다.
UPDATE project_invite SET target_email = NULL WHERE target_email IS NOT NULL;

-- role_id를 참조하는 옛 트리거를 먼저 지우고(트랜잭션 중간에 이미 지워진
-- 컬럼을 참조하는 트리거가 남아있는 구간이 없도록), role_id 컬럼을 지워
-- team_role로의 FK를 끊은 다음에야 team_role 테이블 자체를 지울 수 있다.
DROP TRIGGER team_member_protect_last_pm_trg ON project_member;
DROP FUNCTION team_member_protect_last_pm();

ALTER TABLE project_member DROP COLUMN role_id;
ALTER TABLE project_invite DROP COLUMN role_id;

DROP TRIGGER team_role_protect_pm_trg ON team_role;
DROP FUNCTION team_role_protect_pm();
DROP TABLE team_role;
DROP TYPE team_permission;

CREATE INDEX project_member_member_idx ON project_member (member_id);
CREATE INDEX project_invite_project_idx ON project_invite (project_id);
CREATE INDEX project_invite_pending_idx ON project_invite (project_id)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;
-- project_member_role_idx_old(옛 role_id 인덱스)는 role_id 컬럼 DROP 시
-- Postgres가 자동으로 같이 지운다 — 여기서 또 지우면 "없는 인덱스" 에러.
DROP INDEX project_member_member_idx_old;

-- ---------------------------------------------------------------------
-- 3. project_repository — installation 전체가 아니라 repository 개별 단위로
--    project에 연결한다. installation.team_id를 지우기 전에 옛 소유 체인을
--    이 조인 테이블로 백필한다.
-- ---------------------------------------------------------------------
CREATE TABLE project_repository (
    project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    repository_id uuid NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    attached_by   uuid NOT NULL REFERENCES member(id),
    attached_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, repository_id)
);

CREATE INDEX project_repository_repository_idx ON project_repository (repository_id);

INSERT INTO project_repository (project_id, repository_id, attached_by)
SELECT i.team_id, r.id, p.created_by
  FROM repository r
  JOIN installation i ON i.id = r.installation_id
  JOIN project p ON p.id = i.team_id
 WHERE i.team_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- 4. 각 project를 개인 workspace에 편입 (생성자 기준). team 모델 때와 달리
--    앞으로의 신규 workspace는 자동 생성하지 않지만, 기존 project는 무손실
--    전환을 위해 백필이 필요하다.
-- ---------------------------------------------------------------------
-- 창작자(created_by) 1명당 개인 workspace 하나만 만든다 — project별로 매번
-- 새 workspace를 만들면 같은 사람이 project를 여러 개 갖고 있었을 때
-- 워크스페이스가 쓸데없이 여러 개로 쪼개진다.
DO $$
DECLARE
    r RECORD;
    new_workspace_id uuid;
BEGIN
    FOR r IN
        SELECT DISTINCT p.created_by, m.github_login
          FROM project p JOIN member m ON m.id = p.created_by
         WHERE p.workspace_id IS NULL
    LOOP
        INSERT INTO workspace (name, created_by)
        VALUES (r.github_login || ' 개인 워크스페이스', r.created_by)
        RETURNING id INTO new_workspace_id;

        INSERT INTO workspace_member (workspace_id, member_id, role)
        VALUES (new_workspace_id, r.created_by, 'admin');

        UPDATE project SET workspace_id = new_workspace_id
         WHERE created_by = r.created_by AND workspace_id IS NULL;
    END LOOP;
END $$;

ALTER TABLE project ALTER COLUMN workspace_id SET NOT NULL;

-- ---------------------------------------------------------------------
-- 5. github_account — member에서 GitHub 신원을 분리. 컬럼을 지우기 전에
--    먼저 백필한다.
-- ---------------------------------------------------------------------
CREATE TABLE github_account (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    member_id      uuid NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    github_user_id bigint NOT NULL,
    github_login   text NOT NULL,
    avatar_url     text,
    linked_at      timestamptz NOT NULL DEFAULT now(),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT github_account_github_user_id_uniq UNIQUE (github_user_id)
);

CREATE INDEX github_account_member_idx ON github_account (member_id);

INSERT INTO github_account (member_id, github_user_id, github_login, avatar_url, linked_at)
SELECT id, github_user_id, github_login, avatar_url, created_at FROM member;

-- ---------------------------------------------------------------------
-- 6. installation — team_id 제거(3번에서 이미 소비), 소유권을 member에서
--    github_account로 재배선.
-- ---------------------------------------------------------------------
ALTER TABLE installation ADD COLUMN github_account_id uuid REFERENCES github_account(id) ON DELETE CASCADE;

UPDATE installation i
   SET github_account_id = ga.id
  FROM github_account ga
 WHERE ga.member_id = i.linked_by_member_id;

ALTER TABLE installation ALTER COLUMN github_account_id SET NOT NULL;
ALTER TABLE installation DROP COLUMN linked_by_member_id;
ALTER TABLE installation DROP COLUMN team_id;

CREATE INDEX installation_github_account_idx ON installation (github_account_id);

-- ---------------------------------------------------------------------
-- 7. member 정리 — 이메일/비밀번호 가입 지원 + GitHub 컬럼 제거(5번에서
--    이미 github_account로 옮겨졌으므로 안전).
-- ---------------------------------------------------------------------
ALTER TABLE member ADD COLUMN password_hash text;
ALTER TABLE member ADD COLUMN nickname text;
UPDATE member SET nickname = github_login WHERE nickname IS NULL;
ALTER TABLE member ALTER COLUMN nickname SET NOT NULL;

CREATE UNIQUE INDEX member_email_uniq_idx ON member (lower(email)) WHERE email IS NOT NULL;

ALTER TABLE member DROP CONSTRAINT member_github_user_id_uniq;
ALTER TABLE member DROP COLUMN github_user_id;
ALTER TABLE member DROP COLUMN github_login;
ALTER TABLE member DROP COLUMN avatar_url;

COMMENT ON COLUMN member.password_hash IS
    'bcrypt 해시. GitHub OAuth로만 가입한 계정은 NULL일 수 있다.';
COMMENT ON COLUMN member.nickname IS
    'GitHub 로그인과 무관한 표시 이름. GitHub 가입 시 기본값은 github_login.';

-- ---------------------------------------------------------------------
-- 8. "마지막 Owner/Admin 보호" 트리거. 역할이 더 이상 행이 아니라 enum
--    값이므로(007의 PM 역할 삭제/편집 방지 트리거는 대응물이 필요 없다),
--    "마지막 하나를 강등/제거하지 못하게"만 막으면 된다.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION project_member_protect_last_owner() RETURNS trigger AS $$
DECLARE
    other_owners int;
BEGIN
    IF OLD.role <> 'owner' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.role = 'owner' THEN
        RETURN NEW;
    END IF;
    SELECT count(*) INTO other_owners FROM project_member
     WHERE project_id = OLD.project_id AND role = 'owner' AND member_id <> OLD.member_id;
    IF other_owners = 0 THEN
        RAISE EXCEPTION 'project % must keep at least one owner', OLD.project_id;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER project_member_protect_last_owner_trg
    BEFORE UPDATE OR DELETE ON project_member
    FOR EACH ROW EXECUTE FUNCTION project_member_protect_last_owner();

CREATE OR REPLACE FUNCTION workspace_member_protect_last_admin() RETURNS trigger AS $$
DECLARE
    other_admins int;
BEGIN
    IF OLD.role <> 'admin' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.role = 'admin' THEN
        RETURN NEW;
    END IF;
    SELECT count(*) INTO other_admins FROM workspace_member
     WHERE workspace_id = OLD.workspace_id AND role = 'admin' AND member_id <> OLD.member_id;
    IF other_admins = 0 THEN
        RAISE EXCEPTION 'workspace % must keep at least one admin', OLD.workspace_id;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER workspace_member_protect_last_admin_trg
    BEFORE UPDATE OR DELETE ON workspace_member
    FOR EACH ROW EXECUTE FUNCTION workspace_member_protect_last_admin();

COMMIT;
