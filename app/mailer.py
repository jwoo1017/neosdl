"""초대 메일 발송. smtplib만 쓴다 — 이 프로젝트는 ORM도 안 쓸 만큼 의존성을

최소로 유지하므로 별도 이메일 서비스 SDK를 추가하지 않는다. SMTP는
SendGrid/SES/Mailgun/사내 메일 서버 등 대부분의 발송 서비스가 표준으로
제공하는 인터페이스라, 특정 벤더에 묶이지 않으면서도 실사용에 바로 쓸 수
있다.

smtp_host가 설정돼 있지 않으면(로컬 개발 환경) 발송을 건너뛰고 경고 로그만
남긴다. 발송 실패도 예외를 삼키고 로그만 남긴다 — 초대는 이미 DB에 만들어졌고
invite_url도 응답에 실려 나가므로, 메일이 안 갔다고 초대 생성 자체를 막을
이유가 없다(수동 링크 전달로 항상 폴백 가능). 호출자는 반환값(bool)으로
실제 발송 여부를 확인해 UI/DB에 정직하게 반영해야 한다.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger("app.mailer")


def send_mail(to: str, subject: str, body: str, html_body: str | None = None) -> bool:
    """반환값은 "실제로 SMTP 서버에 발송을 넘겼는가"다 — 스팸함행/미수신 여부까지는

    보장하지 않지만, 최소한 "미설정이라 아예 시도 안 함"과 "시도했으나 실패"를
    호출자가 구분해 사용자에게 보여줄 수 있게 한다.
    """
    cfg = settings()
    if not cfg.smtp_host:
        log.warning("SMTP 미설정 — 메일 발송 건너뜀 (to=%s, subject=%r)", to, subject)
        return False

    msg = EmailMessage()
    msg["From"] = cfg.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10) as smtp:
            if cfg.smtp_use_tls:
                smtp.starttls()
            if cfg.smtp_user:
                smtp.login(cfg.smtp_user, cfg.smtp_password or "")
            smtp.send_message(msg)
        return True
    except Exception:
        log.exception("초대 메일 발송 실패 (to=%s)", to)
        return False


def invite_email_html(*, heading: str, body_line: str, invite_url: str, expires_note: str) -> str:
    """초대 메일 공용 HTML 템플릿 — 이메일 클라이언트 호환을 위해 인라인 스타일만 쓴다."""
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;color:#1a1a1a;">
  <h2 style="margin:0 0 16px;font-size:18px;">{heading}</h2>
  <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#444;">{body_line}</p>
  <a href="{invite_url}"
     style="display:inline-block;padding:10px 20px;background:#2f9c8f;color:#fff;
            text-decoration:none;border-radius:6px;font-weight:600;font-size:14px;">
    초대 수락하기
  </a>
  <p style="margin:24px 0 0;font-size:12px;color:#888;">{expires_note}</p>
  <p style="margin:8px 0 0;font-size:12px;color:#888;word-break:break-all;">{invite_url}</p>
</div>
"""
