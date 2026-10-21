-- =====================================================================
-- NeoSDL · 031_personal_space.sql
-- 개인 페이지 — 워크스페이스마다 "나만 보는" 공간. 기능은 팀스페이스와 같고, 프로젝트를
-- 드래그해 팀스페이스로 옮기면(POST /projects/{id}/move) 그 팀과 공유된다.
--
-- 별도 테이블을 만들지 않고 팀스페이스의 한 종류로 둔다: personal_owner_id 가 있으면
-- 그 사람의 개인 공간이다. 항상 private + 멤버는 주인 한 명이라 기존 접근 규칙
-- (private 은 멤버만 보임, project 접근은 teamspace 멤버십)이 그대로 "나만 보임"을 보장한다.
-- 행은 첫 조회 때 앱이 만든다(GET /api/workspaces/{id}/personal-space — DML 만).
-- =====================================================================

BEGIN;

ALTER TABLE teamspace
    ADD COLUMN personal_owner_id uuid REFERENCES member(id) ON DELETE CASCADE;

-- 개인 공간은 반드시 비공개.
ALTER TABLE teamspace ADD CONSTRAINT teamspace_personal_private_ck
    CHECK (personal_owner_id IS NULL OR visibility = 'private');

-- 워크스페이스당 사람마다 하나(삭제된 것은 제외 — 탈퇴 후 재가입 등).
CREATE UNIQUE INDEX teamspace_personal_uniq
    ON teamspace (workspace_id, personal_owner_id)
 WHERE personal_owner_id IS NOT NULL AND deleted_at IS NULL;

COMMENT ON COLUMN teamspace.personal_owner_id IS
    '개인 페이지의 주인. NULL 이면 일반 팀스페이스. 개인 공간은 초대·가입·멤버 관리·삭제 불가.';

COMMIT;
