-- =====================================================================
-- NeoSDL · 032_scan_analysis_state.sql
-- 분석 완료/실패 상태 — "채팅 가능" 판단과 완료 알림의 기준(app/analysis.py).
--
-- scan.status='succeeded' 는 파싱(scan.parse)까지만 끝났다는 뜻이고, 그 뒤로 의미 분석
-- (symbol.annotate)·위험 분석(risk.build)이 이어진다. 사용자에게 "분석 완료"는 파이프라인
-- 마지막(risk.build)이 끝난 시점이므로 그 시각을 따로 둔다.
-- 핵심 단계 작업이 재시도를 다 써서 dead 가 되면 analysis_error 에 사유를 남긴다 —
-- 이전엔 아무 기록이 없어 화면이 영원히 "분석 중" 에 머물렀다.
-- =====================================================================

BEGIN;

ALTER TABLE scan ADD COLUMN analysis_completed_at timestamptz;
ALTER TABLE scan ADD COLUMN analysis_error        text;

-- 이 기능 이전에 이미 끝난 스캔은 완료로 본다 — 기존 프로젝트의 채팅이 갑자기 잠기지 않게.
UPDATE scan SET analysis_completed_at = coalesce(finished_at, now())
 WHERE status = 'succeeded' AND analysis_completed_at IS NULL;

CREATE INDEX scan_repo_analysis_idx ON scan (repository_id, analysis_completed_at DESC)
 WHERE analysis_completed_at IS NOT NULL;

COMMENT ON COLUMN scan.analysis_completed_at IS '분석 파이프라인(risk.build) 완료 시각 — 채팅 가능 기준.';
COMMENT ON COLUMN scan.analysis_error IS '핵심 단계(fetch/parse/annotate/risk) 작업이 최종 실패한 사유.';

COMMIT;
