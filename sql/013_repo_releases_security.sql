-- =====================================================================
-- NeoSDL · 013_repo_releases_security.sql
-- GitHub 스타일 프로젝트 대시보드(Repository/Releases/Security Analysis)를
-- 위한 신규 테이블·컬럼. 001~012는 손대지 않는다 — 전부 ADD뿐이라 무손실.
--
-- Release는 "이 릴리즈 시점 이전 중 가장 최근 scan"을 간접 추정하지 않고,
-- release.commit_sha(태그가 가리키는 정확한 커밋)와 정확히 일치하는 scan만
-- release.scan_id로 명시 연결한다 — Release → Commit SHA → Scan Snapshot →
-- (Findings + 기존 그래프 뷰어의 Call Graph/CFG/PDG/Dataflow) 구조를 DB에서
-- 바로 따라갈 수 있게 하기 위함. 아직 그 커밋을 스캔한 적 없으면 scan_id는
-- NULL이고, 프론트는 "이 릴리즈 스캔하기" CTA를 보여준다.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/013_repo_releases_security.sql
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. Pull Request — 웹훅 이벤트로 들어오는 것 + 수동 동기화로 백필한 것
-- ---------------------------------------------------------------------
CREATE TABLE pull_request (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id   uuid        NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    number          integer     NOT NULL,
    title           text        NOT NULL,
    state           text        NOT NULL,           -- 'open' | 'closed' (GitHub 원문 그대로)
    author_login    text,
    base_ref        text        NOT NULL,
    head_ref        text        NOT NULL,
    head_sha        char(40)    NOT NULL,
    merged          boolean     NOT NULL DEFAULT false,
    html_url        text,
    opened_at       timestamptz,
    closed_at       timestamptz,
    merged_at       timestamptz,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pull_request_repo_number_uniq UNIQUE (repository_id, number)
);

CREATE INDEX pull_request_repo_idx ON pull_request (repository_id, opened_at DESC);
-- Release 드릴다운이 "이전 릴리즈~이 릴리즈 사이 merge된 PR 수"를 셀 때 사용.
CREATE INDEX pull_request_merged_idx ON pull_request (repository_id, merged_at) WHERE merged_at IS NOT NULL;

-- ---------------------------------------------------------------------
-- 2. Release — GitHub Releases API 동기화
-- ---------------------------------------------------------------------
CREATE TABLE release (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id     uuid        NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    tag_name          text        NOT NULL,
    name              text,
    is_draft          boolean     NOT NULL DEFAULT false,
    is_prerelease     boolean     NOT NULL DEFAULT false,
    target_commitish  text,                          -- GitHub 원문(브랜치명인 경우가 많음)
    commit_sha        char(40),                       -- tag_name을 ref로 해석한 정확한 커밋(동기화 시 채움)
    scan_id           uuid REFERENCES scan(id) ON DELETE SET NULL,
    html_url          text,
    published_at      timestamptz,
    synced_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT release_repo_tag_uniq UNIQUE (repository_id, tag_name)
);

CREATE INDEX release_repo_published_idx ON release (repository_id, published_at DESC);
CREATE INDEX release_scan_idx ON release (scan_id) WHERE scan_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- 3. Security Finding — dataflow.py의 taint 분석 결과를 scan마다 영속화
-- ---------------------------------------------------------------------
CREATE TABLE security_finding (
    id                 bigserial PRIMARY KEY,
    scan_id            uuid        NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    repository_id      uuid        NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    kind               text        NOT NULL,          -- 'security' | 'business'
    category           text        NOT NULL,          -- 'sql_injection' | 'command_injection' | 'xss' | 'domain_crossing:<domain>'
    severity           text        NOT NULL,           -- 'critical' | 'high' | 'medium' | 'low'
    sink_symbol_key    text        NOT NULL,
    sink_line          integer     NOT NULL,
    source_symbol_key  text,
    source_var         text        NOT NULL DEFAULT '',  -- business(domain_crossing) 모드는 소스 변수 개념이 없어 빈 문자열
    path               jsonb       NOT NULL DEFAULT '[]'::jsonb,  -- hop 배열(taint 경유지)
    evidence_kinds     text[]      NOT NULL DEFAULT '{}',
    status             text        NOT NULL DEFAULT 'open',        -- 'open' | 'whitelisted'
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT security_finding_uniq UNIQUE (scan_id, kind, category, sink_symbol_key, sink_line, source_var)
);

CREATE INDEX security_finding_repo_idx ON security_finding (repository_id, created_at DESC);
CREATE INDEX security_finding_scan_idx ON security_finding (scan_id);

-- ---------------------------------------------------------------------
-- 4. Whitelist — 프로젝트별 오탐/승인 위험 억제
-- ---------------------------------------------------------------------
CREATE TABLE finding_whitelist (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      uuid        NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    repository_id   uuid        NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    category        text        NOT NULL,
    sink_symbol_key text,                              -- NULL이면 그 category 전체 억제
    reason          text,
    created_by      uuid        REFERENCES member(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX finding_whitelist_project_idx ON finding_whitelist (project_id);
CREATE INDEX finding_whitelist_repo_category_idx ON finding_whitelist (repository_id, category);

-- ---------------------------------------------------------------------
-- 5. AI 호출 로그 — CI/CD 자동분석 + 질문 응답 양쪽 다 기록
-- ---------------------------------------------------------------------
CREATE TABLE ai_call_log (
    id            bigserial PRIMARY KEY,
    project_id    uuid        NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    purpose       text        NOT NULL,      -- 'ci_annotate' | 'qa'
    provider      text        NOT NULL,      -- 'ollama' | 'bedrock'
    model         text        NOT NULL,
    scan_id       uuid        REFERENCES scan(id) ON DELETE SET NULL,
    latency_ms    integer,
    success       boolean     NOT NULL,
    error         text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ai_call_log_project_idx ON ai_call_log (project_id, created_at DESC);

-- ---------------------------------------------------------------------
-- 6. Project 별 AI 모델 선택(CI/CD용, 질문용) — provider는 지금 'ollama'만
--    실제 동작, 'bedrock'은 선택은 가능하되 호출 시점에 NotImplementedError
--    (app/ai_providers.py) — 나중에 실제 연동을 그 함수 하나만 채우면 되게
--    자리를 미리 마련해둔다.
-- ---------------------------------------------------------------------
ALTER TABLE project ADD COLUMN ci_model_provider text NOT NULL DEFAULT 'ollama';
ALTER TABLE project ADD COLUMN ci_model_name     text NOT NULL DEFAULT '';
ALTER TABLE project ADD COLUMN qa_model_provider text NOT NULL DEFAULT 'ollama';
ALTER TABLE project ADD COLUMN qa_model_name     text NOT NULL DEFAULT '';

COMMIT;
