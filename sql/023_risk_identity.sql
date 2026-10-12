-- =====================================================================
-- NeoSDL · 023_risk_identity.sql
-- Security History Phase 2 — Risk Identity 생애주기 추적
-- (`NeoSDL_Security_History_Design.md` §2.3, §2.4, §4, §6).
--
-- Phase 1(sql/022)이 만든 재료:
--   security_finding.fingerprint  — 스캔 간 동일 위험 식별
--   security_snapshot(branch, committed_at)
--   branch                        — 브랜치 생애주기
--   pull_request(base_ref, head_ref, merged, merged_at)  — 기존
--
-- 여기서 얹는 것:
--   risk_identity        — 지문 하나의 레포 시간축 생애주기(상태·최초/최종 관측)
--   risk_observation     — (지문, 스캔) 관측 사실 (VIEW — 별도 populate 없음)
--   risk_lifecycle_event — Detected/Introduced/Propagated/Merged/Fixed/Resolved 이벤트 로그
--
-- scan_branch(m:n)는 만들지 않는다: `scan_commit_uniq (repository_id, commit_sha)`
-- 때문에 한 커밋 = 한 scan = 한 ref 라, security_snapshot.branch 단일값으로 충분.
--
-- risk_identity/risk_lifecycle_event 는 도출값이다 — app/risk_identity.py 의
-- ensure_risk_identities 가 스냅샷마다 재계산한다(security_analyzed 처럼 멱등).
-- 001~022 는 손대지 않는다.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/023_risk_identity.sql
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. risk_identity — 지문(Risk Identity) 하나의 생애주기. 지문당 한 행.
-- ---------------------------------------------------------------------
CREATE TABLE risk_identity (
    repository_id      uuid        NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    fingerprint        text        NOT NULL,
    kind               text        NOT NULL,               -- 'security' | 'business'
    category           text        NOT NULL,
    worst_severity     text        NOT NULL,               -- 관측된 것 중 가장 높은 심각도
    first_seen_scan_id uuid        REFERENCES scan(id) ON DELETE SET NULL,
    first_seen_commit  char(40),
    first_seen_branch  text,
    first_seen_at      timestamptz,                         -- 그 스냅샷의 committed_at
    last_seen_scan_id  uuid        REFERENCES scan(id) ON DELETE SET NULL,
    last_seen_at       timestamptz,
    status             text        NOT NULL DEFAULT 'active',   -- 'active' | 'fixed' | 'resolved'
    fixed_at           timestamptz,                         -- 기본 브랜치에서 사라진 시각
    resolved_at        timestamptz,                         -- 기본 브랜치 N개 연속 부재 확인
    sample             jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- 대표 finding (sink/source/path) — UI용
    updated_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repository_id, fingerprint)
);

CREATE INDEX risk_identity_repo_status_idx ON risk_identity (repository_id, status);

COMMENT ON TABLE risk_identity IS
    '지문 하나의 레포 시간축 생애주기. security_finding.fingerprint 당 한 행. '
    'app/risk_identity.ensure_risk_identities 가 스냅샷마다 재계산(멱등).';

-- ---------------------------------------------------------------------
-- 2. risk_observation — (지문, 스캔) 관측 사실. security_finding + security_snapshot
--    조인 VIEW. 같은 지문이 sink_line 만 다르게 중복될 수 있어 DISTINCT ON.
-- ---------------------------------------------------------------------
CREATE VIEW risk_observation AS
SELECT DISTINCT ON (sf.repository_id, sf.fingerprint, sf.scan_id)
       sf.repository_id, sf.fingerprint, sf.scan_id, sf.kind, sf.category, sf.severity,
       ss.branch, ss.commit_sha, ss.committed_at
  FROM security_finding sf
  JOIN security_snapshot ss ON ss.scan_id = sf.scan_id
 WHERE sf.fingerprint IS NOT NULL AND sf.status = 'open'
 ORDER BY sf.repository_id, sf.fingerprint, sf.scan_id, sf.sink_line;

COMMENT ON VIEW risk_observation IS
    '(지문, 스캔) 관측 사실 — security_finding 에 스냅샷의 branch/committed_at 을 얹은 것.';

-- ---------------------------------------------------------------------
-- 3. risk_lifecycle_event — 생애주기 이벤트 로그. 도출값 —
--    ensure_risk_identities 가 지문마다 DELETE 후 재삽입(멱등).
-- ---------------------------------------------------------------------
CREATE TABLE risk_lifecycle_event (
    id            bigserial   PRIMARY KEY,
    repository_id uuid        NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    fingerprint   text        NOT NULL,
    kind          text        NOT NULL,   -- detected|introduced|propagated|merged|fixed|resolved
    scan_id       uuid        REFERENCES scan(id) ON DELETE SET NULL,
    pr_number     integer,                -- propagated|merged
    branch        text,
    detail        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    at            timestamptz NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX risk_lifecycle_event_lookup_idx ON risk_lifecycle_event (repository_id, fingerprint, at);

COMMIT;
