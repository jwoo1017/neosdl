-- =====================================================================
-- NeoSDL · 009_workspace_type_project_spec.sql
-- workspace 생성 마법사(개인/회사 선택)와 project 생성 마법사(소개 입력 +
-- 담당자 선택 + 자동 스펙 생성)를 지원하기 위한 컬럼 추가.
--
-- workspace.type은 순수 표시/배지용이다 — 동작(권한, 초대 흐름)은 값과
-- 무관하게 동일하다.
-- project.spec_summary/spec_tags는 GitHub 레포 메타데이터 + 사용자가 입력한
-- 소개(description)를 바탕으로 LLM이 생성해 채운다(app/project_spec.py).
-- 실패하거나 아직 생성 전이면 NULL/빈 배열로 남는다 — 필수 값이 아니다.
--
-- 수동 적용:
--   docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/009_workspace_type_project_spec.sql
-- =====================================================================

BEGIN;

CREATE TYPE workspace_type AS ENUM ('personal', 'company');
ALTER TABLE workspace ADD COLUMN type workspace_type NOT NULL DEFAULT 'company';

ALTER TABLE project ADD COLUMN description  text;
ALTER TABLE project ADD COLUMN spec_summary text;
ALTER TABLE project ADD COLUMN spec_tags    text[] NOT NULL DEFAULT '{}';

COMMIT;
