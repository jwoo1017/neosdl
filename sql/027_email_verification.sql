-- =====================================================================
-- NeoSDL · 027_email_verification.sql
-- 이메일/비밀번호 가입의 이메일 소유 확인(6자리 인증 코드) — app/email_verification.py
--
-- 초대는 member.email 기준으로 매칭된다(workspaces/teamspaces/projects 의 accept).
-- 인증이 없으면 남의 이메일로 가입해 그 주소 앞으로 온 초대를 가로챌 수 있어서,
-- 인증 전 계정은 /me·인증 코드 발송/확인·로그아웃 외의 API 를 쓰지 못한다(app/auth.py).
-- =====================================================================

BEGIN;

ALTER TABLE member ADD COLUMN email_verified_at timestamptz;

COMMENT ON COLUMN member.email_verified_at IS
    '이메일 소유 확인 시각. NULL 이면 미인증(email 이 있는데 NULL 이면 기능 제한). '
    'GitHub 로그인으로 들어온 이메일은 GitHub 가 이미 인증한 주소라 생성 시점에 채운다.';

-- 기존 계정은 인증 완료로 간주한다 — 이 기능 이전에 가입한 사람들을 갑자기 잠그지 않기 위함.
UPDATE member SET email_verified_at = now() WHERE email IS NOT NULL AND email_verified_at IS NULL;

-- 코드 원문은 저장하지 않는다 — HMAC-SHA256(SESSION_SECRET, member_id:code) 만 둔다.
-- 새 코드를 발급하면 이전 미사용 코드는 즉시 만료시킨다(행은 재발송 횟수 제한 집계에 남김).
CREATE TABLE email_verification (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    member_id    uuid        NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    email        text        NOT NULL,          -- 발송 당시 주소(나중에 이메일 변경 기능이 생겨도 혼동 없게)
    code_hash    text        NOT NULL,
    attempts     int         NOT NULL DEFAULT 0,
    expires_at   timestamptz NOT NULL,
    consumed_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX email_verification_member_idx ON email_verification (member_id, created_at DESC);

-- 운영은 마이그레이션 계정과 앱 실행 계정(neosdl_app, DDL 권한 없음)이 분리돼 있다 —
-- 새 테이블은 앱 계정에 필요한 권한만 준다(DELETE 불필요: 행은 member CASCADE 로만 지워짐).
-- 로컬 dev 처럼 그 계정이 없으면 건너뛴다. member 의 새 컬럼은 기존 테이블 권한을 따른다.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'neosdl_app') THEN
        GRANT SELECT, INSERT, UPDATE ON email_verification TO neosdl_app;
    END IF;
END
$$;

COMMIT;
