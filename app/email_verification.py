"""이메일/비밀번호 가입의 이메일 소유 확인 — 6자리 인증 코드.

흐름: 가입(또는 재발송 요청) → issue_code 가 코드를 만들어 메일로 보내고 해시만 저장 →
사용자가 코드를 입력 → verify_code 가 맞으면 member.email_verified_at 을 채운다.
세션 JWT 의 `ev` 클레임은 호출부(github_oauth)가 인증 직후 다시 발급해 갱신한다.

정책(일반적인 서비스 기준):
- 코드: 6자리 숫자, `secrets` 로 생성, 10분 유효
- 오입력: 코드 1개당 5회까지 — 넘으면 그 코드는 무효(재발송 필요). 무차별 대입은
  10^6 경우의 수 대비 5회라 사실상 불가능하다.
- 재발송: 직전 발송 후 60초 쿨다운 + 1시간에 5통 — 메일 폭탄/비용 남용 방지
- 새 코드를 발급하면 이전 미사용 코드는 즉시 만료(마지막 메일의 코드만 유효)
- DB 엔 HMAC-SHA256(SESSION_SECRET, member_id:code) 만 저장 — DB 가 새도 코드 원문은 안 샌다

SMTP 미설정(로컬 개발)이면 메일 대신 서버 로그에 코드를 찍는다 — 로컬에서도 가입
흐름을 끝까지 테스트할 수 있게. SMTP 가 설정된 환경에서는 절대 로그에 남기지 않는다.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets

from fastapi import HTTPException

from app import audit, mailer
from app.config import settings

log = logging.getLogger("app.email_verification")

CODE_TTL_SECONDS = 10 * 60
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
MAX_SENDS_PER_HOUR = 5


def _hash_code(member_id: str, code: str) -> str:
    key = settings().session_secret.encode()
    return hmac.new(key, f"{member_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def _send_code_email(email: str, nickname: str, code: str) -> bool:
    minutes = CODE_TTL_SECONDS // 60
    subject = f"[NeoSDL] 이메일 인증 코드 {code}"
    body = (
        f"{nickname}님, NeoSDL 가입을 완료하려면 아래 인증 코드를 입력하세요.\n\n"
        f"인증 코드: {code}\n\n"
        f"이 코드는 {minutes}분 동안 유효합니다. 본인이 요청하지 않았다면 이 메일을 무시하세요."
    )
    html = f"""\
<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;color:#1a1a1a;">
  <h2 style="margin:0 0 16px;font-size:18px;">NeoSDL 이메일 인증</h2>
  <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#444;">{nickname}님, 가입을 완료하려면 아래 인증 코드를 입력하세요.</p>
  <div style="display:inline-block;padding:12px 20px;background:#f2f7f6;border-radius:6px;
              font-size:28px;font-weight:700;letter-spacing:6px;color:#2f9c8f;">{code}</div>
  <p style="margin:24px 0 0;font-size:12px;color:#888;">이 코드는 {minutes}분 동안 유효합니다. 본인이 요청하지 않았다면 이 메일을 무시하세요.</p>
</div>
"""
    if not settings().smtp_host:
        log.warning("SMTP 미설정(로컬 개발) — 인증 코드를 로그로 대신 출력: email=%s code=%s", email, code)
        return False
    return mailer.send_mail(email, subject, body, html)


def issue_code(cur, member: dict, *, enforce_rate_limit: bool = True) -> dict:
    """새 인증 코드를 발급·발송한다. 반환: {sent, expires_in, resend_after}.

    `member` 는 id/email/nickname 을 가진 dict. 이미 인증됐거나 이메일이 없으면 400.
    """
    member_id = str(member["id"])
    cur.execute("SELECT email, nickname, email_verified_at FROM member WHERE id=%s FOR UPDATE", (member_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "member not found")
    if not row["email"]:
        raise HTTPException(400, "no_email")
    if row["email_verified_at"] is not None:
        raise HTTPException(400, "already_verified")

    if enforce_rate_limit:
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE created_at > now() - interval '1 hour') AS last_hour,
                   ceil(extract(epoch FROM (max(created_at) + make_interval(secs => %s) - now()))) AS wait
              FROM email_verification WHERE member_id=%s
            """,
            (RESEND_COOLDOWN_SECONDS, member_id),
        )
        stat = cur.fetchone()
        wait = int(stat["wait"] or 0)
        if wait > 0:
            raise HTTPException(429, f"resend_cooldown:{wait}")
        if stat["last_hour"] >= MAX_SENDS_PER_HOUR:
            raise HTTPException(429, "too_many_requests")

    # 이전 미사용 코드는 즉시 만료 — 가장 최근 메일의 코드만 유효하게.
    cur.execute(
        "UPDATE email_verification SET expires_at=now() WHERE member_id=%s AND consumed_at IS NULL AND expires_at > now()",
        (member_id,),
    )
    code = _generate_code()
    cur.execute(
        """
        INSERT INTO email_verification (member_id, email, code_hash, expires_at)
        VALUES (%s, %s, %s, now() + make_interval(secs => %s))
        """,
        (member_id, row["email"], _hash_code(member_id, code), CODE_TTL_SECONDS),
    )
    sent = _send_code_email(row["email"], row["nickname"], code)
    audit.record(cur, actor=f"user:{row['nickname']}", action="member.email_verification.send",
                 subject={"member_id": member_id}, payload={"sent": sent})
    return {"sent": sent, "expires_in": CODE_TTL_SECONDS, "resend_after": RESEND_COOLDOWN_SECONDS}


def verify_code(cur, member_id: str, code: str) -> dict:
    """결과 dict 를 반환한다 — 성공: {"member": {...}}, 실패: {"error": "<사유>"}.

    실패를 예외로 던지지 않는 이유: app/db.py 의 tx() 는 예외가 나면 통째로 롤백한다.
    오입력 횟수(attempts) 증가가 롤백되면 5회 제한이 무력화되므로, 호출부가 트랜잭션을
    커밋한 **뒤에** error 를 HTTP 400 으로 바꿔 응답해야 한다.
    error 값(프론트가 문구를 고를 수 있게): `code_expired`(유효한 코드 없음/만료),
    `too_many_attempts`, `invalid_code:<남은 횟수>`.
    """
    code = (code or "").strip()
    cur.execute(
        """
        SELECT id, code_hash, attempts FROM email_verification
         WHERE member_id=%s AND consumed_at IS NULL AND expires_at > now()
         ORDER BY created_at DESC LIMIT 1
           FOR UPDATE
        """,
        (member_id,),
    )
    row = cur.fetchone()
    if row is None:
        return {"error": "code_expired"}
    if row["attempts"] >= MAX_ATTEMPTS:
        return {"error": "too_many_attempts"}

    if not (code.isdigit() and len(code) == 6
            and hmac.compare_digest(row["code_hash"], _hash_code(member_id, code))):
        cur.execute("UPDATE email_verification SET attempts = attempts + 1 WHERE id=%s RETURNING attempts",
                    (row["id"],))
        remaining = MAX_ATTEMPTS - cur.fetchone()["attempts"]
        return {"error": "too_many_attempts" if remaining <= 0 else f"invalid_code:{remaining}"}

    cur.execute("UPDATE email_verification SET consumed_at=now() WHERE id=%s", (row["id"],))
    cur.execute(
        "UPDATE member SET email_verified_at=now(), updated_at=now() WHERE id=%s RETURNING id, email, nickname",
        (member_id,),
    )
    member = cur.fetchone()
    audit.record(cur, actor=f"user:{member['nickname']}", action="member.email_verified",
                 subject={"member_id": member_id}, payload={})
    return {"member": {**member, "email_verified": True}}
