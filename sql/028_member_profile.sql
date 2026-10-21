-- =====================================================================
-- NeoSDL · 028_member_profile.sql
-- 프로필(이름·아바타) + 세션 무효화(token_version) + 회원 탈퇴(soft delete)
--
-- - name: 실명. 화면 표시는 nickname, 프로필/멤버 목록에는 name 도 함께 보인다.
-- - avatar_*: 파일은 스토리지(app/storage.py), DB 엔 키만 둔다(예: "avatars/<uuid4>.webp").
--   URL·절대경로는 저장하지 않는다 — 응답 시 app/profiles.py 가 조립한다.
-- - token_version: 세션 JWT 의 tv 클레임과 비교 — 비밀번호 변경/탈퇴 시 +1 해서 기존 세션을 끊는다.
-- - deleted_at: 회원 탈퇴. 행은 남기고(프로젝트 created_by 등 이력 FK 보존) 개인정보만 지운다.
-- =====================================================================

BEGIN;

ALTER TABLE member ADD COLUMN name text;
UPDATE member SET name = nickname WHERE name IS NULL;
ALTER TABLE member ALTER COLUMN name SET NOT NULL;

ALTER TABLE member ADD COLUMN avatar_key        text;
ALTER TABLE member ADD COLUMN avatar_source     text NOT NULL DEFAULT 'initials';
ALTER TABLE member ADD COLUMN avatar_updated_at timestamptz;
ALTER TABLE member ADD CONSTRAINT member_avatar_source_ck
    CHECK (avatar_source IN ('initials', 'upload', 'github'));
-- 업로드 아바타면 키가 반드시 있어야 한다(키 없는 upload 상태로 남지 않게).
ALTER TABLE member ADD CONSTRAINT member_avatar_upload_key_ck
    CHECK (avatar_source <> 'upload' OR avatar_key IS NOT NULL);

ALTER TABLE member ADD COLUMN token_version int NOT NULL DEFAULT 0;
ALTER TABLE member ADD COLUMN deleted_at    timestamptz;

-- 인증 코드 테이블(027)을 이메일 변경에도 쓴다 — 가입 인증과 섞이지 않게 용도를 구분.
-- email_change 행의 email 은 "바꾸려는 새 주소"다(확인 전엔 member.email 이 바뀌지 않는다).
ALTER TABLE email_verification ADD COLUMN purpose text NOT NULL DEFAULT 'signup';
ALTER TABLE email_verification ADD CONSTRAINT email_verification_purpose_ck
    CHECK (purpose IN ('signup', 'email_change'));

COMMENT ON COLUMN member.name IS '실명(가입 시 필수). 표시용 별칭은 nickname.';
COMMENT ON COLUMN member.avatar_key IS '스토리지 키(예: avatars/<uuid4>.webp). URL/절대경로 아님.';
COMMENT ON COLUMN member.token_version IS '세션 JWT tv 클레임과 비교 — 올리면 그 이전에 발급된 세션 전부 무효.';
COMMENT ON COLUMN member.deleted_at IS '회원 탈퇴 시각. 탈퇴 계정은 개인정보가 지워지고 로그인 불가.';

COMMIT;
