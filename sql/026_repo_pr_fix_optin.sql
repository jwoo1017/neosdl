-- =====================================================================
-- NeoSDL · 026_repo_pr_fix_optin.sql
-- LLM이 생성한 수정 코드로 GitHub PR을 자동으로 여는 기능의 저장소별 옵트인.
-- webhook_ci_enabled(sql/005)와 같은 원칙 — GitHub App 권한(Contents/PR
-- write) 재승인과는 별개로, 이 저장소에서 실제로 자동 PR 생성을 허용할지는
-- 팀이 명시적으로 한 번 더 켜야 한다(기본 꺼짐).
--
-- 수동 적용: docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/026_repo_pr_fix_optin.sql
-- =====================================================================

BEGIN;

ALTER TABLE repository ADD COLUMN pr_fix_enabled boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN repository.pr_fix_enabled IS
    '켜져 있으면 POST /findings/{id}/create-pr 가 이 레포에 대해 실제로 브랜치를 '
    '만들고 PR을 연다. 기본 꺼짐 — 옵트인. GitHub App 자체의 Contents/Pull '
    'requests write 권한 재승인과는 별개의, 저장소별 2차 동의 게이트다.';

COMMIT;
