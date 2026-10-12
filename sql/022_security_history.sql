-- =====================================================================
-- NeoSDL · 022_security_history.sql
-- Security History Phase 1 — 커밋/브랜치 축의 보안 상태 추적을 위한 최소 인프라
-- (`NeoSDL_Security_History_Plan.md`).
--
-- 오늘의 NeoSDL 은 "커밋 X 시점에 뭐가 취약한가"만 답한다. Security History 는
-- 시간/브랜치 축을 얹어 "이 취약점이 언제·어느 브랜치에서 생겨서 어떤 PR 을
-- 거쳐 main 까지 왔고 언제 해결됐나"를 답하게 한다.
--
-- 이번 slice 는 인프라의 바닥만 깐다:
--   1. branch            — 웹훅으로 관측한 브랜치 생애주기 (삭제돼도 이력 보존)
--   2. security_snapshot  — scan(=커밋 1개) 하나의 taint-finding 상태 롤업
--   3. security_finding.fingerprint — 스캔 간 동일 위험(Risk Identity)을 잇는
--                                     결정적 지문 (앱 계산 — app/security_fingerprint.py)
--
-- Risk Identity 생애주기 상태기계 / PR-merge 전파 그래프 / 타임라인 UI 는
-- 다음 라운드. 001~021 은 손대지 않는다 — 전부 ADD 라 무손실.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/022_security_history.sql
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. branch — 웹훅 push/create/delete 로 관측한 브랜치. 삭제돼도 행은 남기고
--    deleted_at 만 채운다 — 과거 스냅샷이 "어느 브랜치였나"를 잃지 않기 위함.
--    라이브 GitHub 브랜치 조회(repo_queries.list_repo_branches)와 별개: 여기는
--    "우리가 스캔한 시점의 브랜치 상태"의 시간축 기록이다.
-- ---------------------------------------------------------------------
CREATE TABLE branch (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id  uuid        NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    name           text        NOT NULL,               -- refs/heads/<name> 의 <name>
    head_sha       char(40),
    head_scan_id   uuid        REFERENCES scan(id) ON DELETE SET NULL,
    is_default     boolean     NOT NULL DEFAULT false,
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz,
    CONSTRAINT branch_repo_name_uniq UNIQUE (repository_id, name)
);

CREATE INDEX branch_repo_live_idx ON branch (repository_id) WHERE deleted_at IS NULL;

COMMENT ON TABLE branch IS
    '웹훅으로 관측한 브랜치 생애주기. 삭제돼도 deleted_at 만 채우고 행 유지 — '
    'Security History 가 과거 스냅샷의 브랜치 소속을 잃지 않게.';

-- ---------------------------------------------------------------------
-- 2. security_snapshot — scan(=커밋 1개) 하나의 taint-finding 상태 롤업.
--    1 scan : 1 snapshot. scan 은 불변 스냅샷(001)이므로 이 행도 UPDATE 하지
--    않고 새 scan 이 새 행을 만든다(재계산 시 ON CONFLICT 로 덮되 값은 결정적).
--
--    score 는 **타임라인 UI 전용 합성 점수**다(가중 severity 합, app/
--    security_score.py). phase2 Risk Vector(business_ip/pii/secret/security 5차원
--    독립)와 다르다 — 그건 의도적으로 단일 점수를 만들지 않는다
--    (NeoSDL_Security_History_Plan.md 가 참조하는 phase2 §1.2). 트렌드 라인 하나를
--    그리기 위한 롤업일 뿐이다.
-- ---------------------------------------------------------------------
CREATE TABLE security_snapshot (
    scan_id       uuid PRIMARY KEY REFERENCES scan(id) ON DELETE CASCADE,
    repository_id uuid        NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    commit_sha    char(40)    NOT NULL,
    branch        text,                                -- scan.ref 파싱값. PR ref 면 pull_request.head_ref 폴백, 그래도 없으면 NULL
    committed_at  timestamptz,                         -- git_commit 있으면 그 값, 없으면 scan.created_at
    score         numeric     NOT NULL DEFAULT 0,
    n_critical    integer     NOT NULL DEFAULT 0,
    n_high        integer     NOT NULL DEFAULT 0,
    n_medium      integer     NOT NULL DEFAULT 0,
    n_low         integer     NOT NULL DEFAULT 0,
    n_findings    integer     NOT NULL DEFAULT 0,
    n_evidence    integer     NOT NULL DEFAULT 0,      -- sum(cardinality(evidence_kinds))
    generated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX security_snapshot_repo_time_idx   ON security_snapshot (repository_id, committed_at DESC);
CREATE INDEX security_snapshot_repo_branch_idx ON security_snapshot (repository_id, branch, committed_at);

COMMENT ON COLUMN security_snapshot.score IS
    '타임라인 UI 전용 합성 점수(가중 severity 합). phase2 Risk Vector 아님.';

-- ---------------------------------------------------------------------
-- 3. security_finding.fingerprint — 스캔 간 동일 위험(Risk Identity)을 잇는
--    결정적 지문. 앱에서 계산한다(정규화 로직 필요 — app/security_fingerprint.py).
--    라인번호/커밋/로컬 변수명에 불변. 파일·함수 rename 시 새 지문
--    (symbol_key 가 이미 가진 한계와 동일 — 문서화된 Phase 1 한계).
-- ---------------------------------------------------------------------
ALTER TABLE security_finding ADD COLUMN fingerprint text;

COMMENT ON COLUMN security_finding.fingerprint IS
    'v1|kind|category|repo-UUID 제거한 sink symbol_key|source 패턴 클래스|정규화된 '
    'dataflow 경로 의 sha1[:16]. app/security_fingerprint.compute_fingerprint 가 유일한 정의. '
    '스캔 간 동일 위험(Risk Identity) 식별용. 파일/함수 rename 시 새 지문.';

CREATE INDEX security_finding_fingerprint_idx ON security_finding (repository_id, fingerprint);

COMMIT;
