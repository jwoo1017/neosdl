-- =====================================================================
-- NeoSDL · 024_whitelist_scoring.sql
-- Security History — whitelist(오탐/승인) 처리한 finding 을 Risk Identity /
-- Snapshot 점수에서 제외한다.
--
-- 기존 `/scans/{id}/security/findings`(app/main.py)는 조회 시점에
-- finding_whitelist 를 LEFT JOIN 해서 걸렀지만, Security History 의
-- risk_observation VIEW / security_snapshot 점수는 그걸 안 봤다. 여기서
-- risk_observation 을 whitelist-aware 로 재정의한다(다른 곳은 app 코드가 처리).
--
-- finding_whitelist 는 project 단위지만 매칭은 repository_id + category
-- (+ 선택 sink_symbol_key)로 한다 — app/main.py 의 기존 관례와 동일.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/024_whitelist_scoring.sql
-- =====================================================================

BEGIN;

CREATE OR REPLACE VIEW risk_observation AS
SELECT DISTINCT ON (sf.repository_id, sf.fingerprint, sf.scan_id)
       sf.repository_id, sf.fingerprint, sf.scan_id, sf.kind, sf.category, sf.severity,
       ss.branch, ss.commit_sha, ss.committed_at
  FROM security_finding sf
  JOIN security_snapshot ss ON ss.scan_id = sf.scan_id
 WHERE sf.fingerprint IS NOT NULL
   AND sf.status = 'open'
   AND NOT EXISTS (
        SELECT 1 FROM finding_whitelist fw
         WHERE fw.repository_id = sf.repository_id
           AND fw.category = sf.category
           AND (fw.sink_symbol_key IS NULL OR fw.sink_symbol_key = sf.sink_symbol_key)
   )
 ORDER BY sf.repository_id, sf.fingerprint, sf.scan_id, sf.sink_line;

COMMIT;
