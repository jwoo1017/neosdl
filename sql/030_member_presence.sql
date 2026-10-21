-- =====================================================================
-- NeoSDL · 030_member_presence.sql
-- 접속 상태 — 상단바에 "지금 이 팀스페이스에서 활동 중인 멤버" 를 보여준다(app/presence.py).
--
-- 사용자당 1행(upsert). 프론트가 30초마다 하트비트(state=active|idle, 보고 있는 팀스페이스)를
-- 보내고, 조회는 last_seen_at 90초 이내만 "접속 중" 으로 본다 — 탭을 그냥 닫아도 90초 뒤 사라진다.
-- 정상 종료(탭 닫기)는 sendBeacon 으로 행을 지운다.
-- =====================================================================

BEGIN;

CREATE TABLE member_presence (
    member_id     uuid        PRIMARY KEY REFERENCES member(id) ON DELETE CASCADE,
    teamspace_id  uuid        REFERENCES teamspace(id) ON DELETE SET NULL,   -- 지금 보고 있는 팀스페이스
    state         text        NOT NULL,
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT member_presence_state_ck CHECK (state IN ('active', 'idle'))
);

CREATE INDEX member_presence_teamspace_idx ON member_presence (teamspace_id, last_seen_at);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'neosdl_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON member_presence TO neosdl_app;
    END IF;
END
$$;

COMMIT;
