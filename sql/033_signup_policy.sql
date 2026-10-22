-- =====================================================================
-- NeoSDL · 033_signup_policy.sql
-- 사내 협업 사이트용 계정 정책(app/signup_policy.py, app/admin.py).
--
-- - is_system_admin: 회원 비활성화(퇴사자 처리)를 할 수 있는 시스템 관리자. 웹에서 스스로
--   올릴 수 없고 서버에서 deploy/admin.py grant 로만 부여한다.
-- - suspended_at/by: 비활성화된 계정 — 모든 세션 무효, 이메일·GitHub 로그인 403.
-- - failed_login_count/locked_until: 연속 5회 로그인 실패 시 15분 잠금(ISMS-P 관례).
-- - privacy_agreed_at: 가입 시 개인정보 수집·이용 동의 시각.
-- - email_verification.purpose 에 password_reset(비밀번호 찾기) 추가.
--
-- 기존 회원은 그대로 둔다(도메인 제한은 신규 가입에만). 게스트 여부는 컬럼 없이 이메일
-- 도메인과 설정(ALLOWED_SIGNUP_DOMAINS)으로 계산한다 — 도메인 목록을 바꿔도 백필이 필요 없게.
-- =====================================================================

BEGIN;

ALTER TABLE member ADD COLUMN is_system_admin    boolean     NOT NULL DEFAULT false;
ALTER TABLE member ADD COLUMN suspended_at       timestamptz;
ALTER TABLE member ADD COLUMN suspended_by       uuid REFERENCES member(id) ON DELETE SET NULL;
ALTER TABLE member ADD COLUMN failed_login_count integer     NOT NULL DEFAULT 0;
ALTER TABLE member ADD COLUMN locked_until       timestamptz;
ALTER TABLE member ADD COLUMN privacy_agreed_at  timestamptz;

ALTER TABLE email_verification DROP CONSTRAINT email_verification_purpose_ck;
ALTER TABLE email_verification ADD CONSTRAINT email_verification_purpose_ck
    CHECK (purpose IN ('signup', 'email_change', 'password_reset'));

COMMENT ON COLUMN member.is_system_admin IS '시스템 관리자(회원 비활성화). deploy/admin.py 로만 부여.';
COMMENT ON COLUMN member.suspended_at IS '관리자가 비활성화한 시각 — 있으면 로그인·세션 모두 거부.';
COMMENT ON COLUMN member.locked_until IS '연속 로그인 실패 잠금 해제 시각.';
COMMENT ON COLUMN member.privacy_agreed_at IS '개인정보 수집·이용 동의 시각.';

COMMIT;
