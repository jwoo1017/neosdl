-- =====================================================================
-- NeoSDL · 025_finding_fix.sql
-- 마스코트 사이드바 — security_finding 1건에 대해 사내 LLM이 생성한 수정
-- 코드 제안(및 그 결과로 만들어진 PR)을 영속화한다. worker job "fix.generate"
-- (app/fix_generation.py)가 채우고, "PR 생성"(app/finding_fix_api.py)이
-- pr_url/pr_number/branch_name/status를 갱신한다.
--
-- 수동 적용: docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/025_finding_fix.sql
-- =====================================================================

BEGIN;

CREATE TABLE finding_fix (
    id                bigserial PRIMARY KEY,
    finding_id        bigint      NOT NULL REFERENCES security_finding(id) ON DELETE CASCADE,
    repository_id     uuid        NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    scan_id           uuid        REFERENCES scan(id) ON DELETE SET NULL,
    status            text        NOT NULL DEFAULT 'pending',  -- 'pending'|'ready'|'failed'|'pr_created'
    file_path         text,
    start_line        integer,
    end_line          integer,
    original_snippet  text,
    replacement_code  text,
    diff              text,
    summary           text,
    provider          text,
    model             text,
    error             text,
    pr_url            text,
    pr_number         integer,
    branch_name       text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX finding_fix_finding_idx ON finding_fix (finding_id);
CREATE INDEX finding_fix_repo_created_idx ON finding_fix (repository_id, created_at DESC);

COMMENT ON COLUMN finding_fix.status IS
    'pending: fix.generate job 대기/실행 중, ready: 수정 코드 생성 완료(사용자 검토 대기), '
    'failed: 생성 실패(error 참고), pr_created: create-pr 성공(pr_url 참고).';
COMMENT ON COLUMN finding_fix.original_snippet IS
    'fix 생성 시점의 [start_line,end_line] 원본 코드 — create-pr 시 파일이 그 사이 '
    '바뀌었는지 확인하는 동시성 체크에 쓴다(app/finding_fix_api.py).';

COMMIT;
