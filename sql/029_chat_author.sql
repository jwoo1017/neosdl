-- =====================================================================
-- NeoSDL · 029_chat_author.sql
-- 팀스페이스 채팅을 채팅방 형식으로 — 질문(user 메시지)마다 누가 보냈는지 기록한다.
--
-- 이전 메시지는 작성자를 알 수 없어 NULL 로 남는다(화면에 "알 수 없음").
-- 세션 생성자(chat_session.created_by)로 백필하지 않는다 — 한 세션에 여러 사람이
-- 질문했을 수 있어서 틀린 이름을 붙일 위험이 더 크다.
-- 탈퇴는 soft delete 라 행이 남지만, 혹시 member 행이 지워지면 메시지는 남기고 작성자만 비운다.
-- =====================================================================

BEGIN;

ALTER TABLE chat_message
    ADD COLUMN author_member_id uuid REFERENCES member(id) ON DELETE SET NULL;

COMMENT ON COLUMN chat_message.author_member_id IS
    'user 메시지를 보낸 멤버. assistant 메시지와 029 이전 메시지는 NULL.';

COMMIT;
