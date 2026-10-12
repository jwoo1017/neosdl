-- =====================================================================
-- NeoSDL · 002_auth.sql
-- GitHub OAuth 로그인 + GitHub App 설치 연동 — 인증/멀티테넌시 스키마
--
-- 001_init.sql 은 이미 서빙 중인 정본이므로 여기서는 ALTER 만 하고
-- 기존 컬럼/제약을 절대 건드리지 않는다.
--
-- 수동 적용 (마이그레이션 러너 없음):
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/002_auth.sql
-- =====================================================================

BEGIN;

CREATE TYPE installation_status AS ENUM ('active', 'suspended', 'deleted');

-- ---------------------------------------------------------------------
-- 1. Member — GitHub OAuth 로 로그인한 사용자
--    github_user_id(숫자) 가 identity key. login(핸들)은 바뀔 수 있으므로
--    표시용일 뿐, 절대 조회 키로 쓰지 않는다.
-- ---------------------------------------------------------------------
CREATE TABLE member (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_user_id  bigint      NOT NULL,
    github_login    text        NOT NULL,
    email           text,
    avatar_url      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    last_login_at   timestamptz,
    CONSTRAINT member_github_user_id_uniq UNIQUE (github_user_id)
);

COMMENT ON COLUMN member.github_user_id IS
    'GET /user 의 numeric id. Identity key — login(핸들)은 바뀔 수 있어 절대 키로 쓰지 않는다.';

-- ---------------------------------------------------------------------
-- 2. Installation — GitHub App 이 특정 계정/조직에 설치된 단위
--    한 installation 은 정확히 한 member 에 귀속된다 (설치를 시작한 사람).
-- ---------------------------------------------------------------------
CREATE TABLE installation (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_installation_id  bigint               NOT NULL,
    member_id               uuid                 NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    account_login           text                 NOT NULL,   -- 설치 대상 org/user (표시용)
    account_type            text                 NOT NULL,   -- 'User' | 'Organization'
    status                  installation_status  NOT NULL DEFAULT 'active',
    created_at              timestamptz          NOT NULL DEFAULT now(),
    updated_at              timestamptz          NOT NULL DEFAULT now(),
    CONSTRAINT installation_github_id_uniq UNIQUE (github_installation_id)
);

CREATE INDEX installation_member_idx ON installation (member_id);
CREATE INDEX installation_status_idx ON installation (status) WHERE status <> 'active';

COMMENT ON TABLE installation IS
    'Setup URL 콜백이 1차 기록자. 웹훅(installation.deleted/suspend/unsuspend)은 '
    '브라우저 세션과 무관하게 status 를 동기화하는 2차/보강 경로.';

-- ---------------------------------------------------------------------
-- 3. repository — 소유자(installation) 연결
--    NULLABLE: 이전 세션 테스트로 이미 들어간 기존 행들을 깨지 않기 위함.
--    installation_id IS NULL 인 행은 INNER JOIN 기반 조회에서 자동으로
--    아무 사용자에게도 보이지 않게 된다 (삭제하지 않아도 안전하게 격리됨).
-- ---------------------------------------------------------------------
ALTER TABLE repository
    ADD COLUMN installation_id uuid REFERENCES installation(id) ON DELETE SET NULL;

CREATE INDEX repository_installation_idx ON repository (installation_id);

COMMIT;
