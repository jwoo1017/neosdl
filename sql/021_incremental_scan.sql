-- =====================================================================
-- NeoSDL · 021_incremental_scan.sql
-- 증분 스캔 — 웹훅 push/PR마다 레포 전체를 다시 분석하는 대신, 직전 성공
-- 스캔(parent) 결과를 재사용하고 변경된 파일 + 그 caller만 다시 분석한다
-- (phase2 plan §12 Dynamic Risk).
--
-- 이번 범위는 **비싼 LLM 단계만** 증분화한다:
--   symbol.annotate / symbol.embed / risk.build
-- 구조 추출(scan.parse / pdg.build / graph.project / dataflow taint)은 매번
-- 전체 실행한다 — 심볼/엣지/CFG/PDG 정확성을 손대지 않기 위함. 그래서
-- 증분은 "순수 최적화"다: parent를 못 찾거나 GitHub compare가 실패하면
-- 지금처럼 전체 분석으로 폴백하고, 결과는 항상 전체 스캔과 동일하다.
--
-- parent_scan_id 만 추가한다. 변경 파일 목록은 scan.stats(jsonb)에
-- 진단용으로 남긴다(별도 테이블 없음):
--   { "incremental": true, "changed_paths": [...], "removed_paths": [...],
--     "parent_commit": "<sha>", "reused_symbols": <n>, "reanalyzed_symbols": <n> }
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/021_incremental_scan.sql
-- =====================================================================

BEGIN;

ALTER TABLE scan
    ADD COLUMN parent_scan_id uuid REFERENCES scan(id) ON DELETE SET NULL;

COMMENT ON COLUMN scan.parent_scan_id IS
    '증분 스캔이 결과를 copy-forward 해온 직전 성공 스캔. NULL이면 전체 스캔.';

-- parent로부터 파생된 스캔을 역추적(재빌드/무효화 시).
CREATE INDEX scan_parent_idx ON scan (parent_scan_id) WHERE parent_scan_id IS NOT NULL;

COMMIT;
