-- =====================================================================
-- NeoSDL · 004_git_history.sql
-- WBS 1.4 — Git History 및 변경 이력 연계
--
-- scan 은 "커밋 1개 = 불변 스냅샷"이라 시점 고정이지만, git 히스토리는
-- 레포의 시간 축 전체(과거 커밋들)라 특정 스캔 하나에 속하지 않는다.
-- 그래서 repository_id 에만 묶고, "이 스캔의 이 파일이 최근 몇 번
-- 바뀌었나"는 조회 시점에 path 로 symbol(module)과 JOIN한다
-- (symbol_semantic 을 module 심볼과 path 로 조인한 것과 같은 패턴).
--
-- 수동 적용: docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/004_git_history.sql
-- =====================================================================

BEGIN;

CREATE TABLE git_commit (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id uuid        NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    commit_sha    char(40)    NOT NULL,
    author_name   text,
    author_email  text,
    committed_at  timestamptz NOT NULL,
    message       text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT git_commit_repo_sha_uniq UNIQUE (repository_id, commit_sha)
);

CREATE INDEX git_commit_repo_time_idx ON git_commit (repository_id, committed_at DESC);

CREATE TABLE commit_file (
    commit_id   uuid    NOT NULL REFERENCES git_commit(id) ON DELETE CASCADE,
    path        text    NOT NULL,
    change_type text    NOT NULL,   -- GitHub API 의 status: added|modified|removed|renamed
    additions   integer NOT NULL DEFAULT 0,
    deletions   integer NOT NULL DEFAULT 0,
    PRIMARY KEY (commit_id, path)
);

CREATE INDEX commit_file_path_idx ON commit_file (path);

COMMENT ON TABLE git_commit IS
    '레포별 최근 커밋 이력(GitHub API, 최근 N개). scan 과 무관 — repository 시간축.';
COMMENT ON TABLE commit_file IS
    '커밋이 건드린 파일 목록(파일 단위, 심볼 단위 아님). symbol.path 와는 조회 시점에 JOIN.';

COMMIT;
