-- =====================================================================
-- NeoSDL · 005_webhook_ci.sql
-- 레포지토리별 옵트인 웹훅 자동 스캔("웹훅 기반 CI"). scan_trigger enum의
-- 'webhook' 값(001_init.sql)은 WBS 1.1부터 정의만 돼 있었고 이번에 처음
-- 실사용한다 — 고객(팀)이 대시보드에서 켜면 그 레포 push/PR마다 수동
-- "스캔 실행" 없이 자동으로 scan.parse가 큐를 탄다.
--
-- 수동 적용: docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/005_webhook_ci.sql
-- =====================================================================

BEGIN;

ALTER TABLE repository ADD COLUMN webhook_ci_enabled boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN repository.webhook_ci_enabled IS
    '켜져 있으면 GitHub App 웹훅(push/pull_request)이 이 레포에 대해 '
    '자동으로 scan(trigger=webhook)을 생성한다. 기본 꺼짐 — 옵트인.';

COMMIT;
