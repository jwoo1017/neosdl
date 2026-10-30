"""사내 협업 사이트용 가입·계정 정책 — 판단 로직을 한곳에 모은다.

가입 자격
  - 회사 도메인(ALLOWED_SIGNUP_DOMAINS, 정확히 일치 — 하위 도메인 불허)이면 바로 가입.
    도메인 밖이라도 ALLOWED_SIGNUP_EMAILS 에 적힌 개별 주소는 회사 계정으로 취급한다(시연용 등).
  - 그 외 주소는 그 주소로 온 **대기 중인 초대**(워크스페이스/팀스페이스/프로젝트)가 있을
    때만 가입(게스트). 링크 초대(target_email 없음)로는 외부인이 가입할 수 없다.
  - 게스트 여부는 컬럼 없이 이메일 도메인으로 계산한다 — 도메인 목록을 바꿔도 백필 불필요.

비밀번호(KISA/ISMS-P 관례)
  - 10자 이상, 영문·숫자·특수문자 중 2종 이상, 72바이트 이하(bcrypt 가 그 뒤를 버린다),
    이메일 아이디 포함 금지, 흔한 비밀번호 차단. 가입·변경·재설정에 적용하고 기존
    비밀번호를 강제로 바꾸게 하지는 않는다.

로그인 잠금
  - 계정당 연속 5회 실패 → 15분 잠금. 성공·비밀번호 재설정·관리자 재활성화 시 초기화.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import HTTPException
from pydantic import BeforeValidator, EmailStr

from app.config import settings

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_BYTES = 72
MAX_FAILED_LOGINS = 5
LOCK_SECONDS = 15 * 60

# 유출 사전 상위권 + 회사/서비스 이름 조합 — 규칙(10자·2종)을 통과하는 것만 의미 있다.
_COMMON_PASSWORDS = {
    "password123", "password1!", "password12", "qwerty1234", "qwerty123!", "1q2w3e4r5t", "1q2w3e4r!@",
    "qwer1234!@", "asdf1234!@", "zxcv1234!@", "abcd1234!@", "abc123456!", "iloveyou12", "welcome123",
    "admin12345", "admin1234!", "letmein123", "p@ssw0rd12", "passw0rd12", "test123456", "test1234!@",
    "neosdl1234", "neosdl123!", "finble1234", "finble123!", "zerokleek1", "aeonlab123", "aeonlab12!",
}


def allowed_domains() -> set[str]:
    raw = settings().allowed_signup_domains or ""
    return {d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()}


def allowed_emails() -> set[str]:
    raw = settings().allowed_signup_emails or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower()


def is_company_email(email: str | None) -> bool:
    if email and email.strip().lower() in allowed_emails():
        return True
    return email_domain(email) in allowed_domains()


def is_guest(email: str | None) -> bool:
    """회사 도메인이 아닌(또는 이메일이 없는) 계정 — 초대받은 공간만 쓰고 워크스페이스는 못 만든다."""
    return not is_company_email(email)


def has_pending_invite(cur, email: str) -> bool:
    """그 주소로 온, 아직 유효한 초대가 하나라도 있는지(세 초대 테이블)."""
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM workspace_invite wi JOIN workspace w ON w.id = wi.workspace_id AND w.deleted_at IS NULL
             WHERE wi.target_email = %(e)s AND wi.accepted_at IS NULL AND wi.revoked_at IS NULL
               AND wi.expires_at > now()
            UNION ALL
            SELECT 1 FROM teamspace_invite ti JOIN teamspace t ON t.id = ti.teamspace_id AND t.deleted_at IS NULL
             WHERE ti.target_email = %(e)s AND ti.accepted_at IS NULL AND ti.revoked_at IS NULL
               AND ti.expires_at > now()
            UNION ALL
            SELECT 1 FROM project_invite pi JOIN project p ON p.id = pi.project_id AND p.deleted_at IS NULL
             WHERE pi.target_email = %(e)s AND pi.accepted_at IS NULL AND pi.revoked_at IS NULL
               AND pi.expires_at > now()
        ) AS ok
        """,
        {"e": email.lower()},
    )
    return bool(cur.fetchone()["ok"])


def can_sign_up(cur, email: str) -> bool:
    return is_company_email(email) or has_pending_invite(cur, email)


def password_problems(password: str, email: str | None = None) -> list[str]:
    """규칙 위반 코드 목록(빈 목록이면 통과). 프론트 체크리스트와 같은 코드."""
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append("too_short")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        problems.append("too_long")
    kinds = sum(bool(re.search(p, password)) for p in (r"[A-Za-z]", r"[0-9]", r"[^A-Za-z0-9\s]"))
    if kinds < 2:
        problems.append("needs_two_kinds")
    local = (email or "").split("@", 1)[0].lower()
    if len(local) >= 3 and local in password.lower():
        problems.append("contains_email")
    if password.lower() in _COMMON_PASSWORDS:
        problems.append("too_common")
    return problems


def validate_password(password: str, email: str | None = None) -> None:
    """위반이면 400 `weak_password:<코드,코드>`."""
    problems = password_problems(password, email)
    if problems:
        raise HTTPException(400, "weak_password:" + ",".join(problems))


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_display_text(value: str, *, field: str, max_len: int) -> str:
    """이름/닉네임: 앞뒤 공백 제거 후 1~max_len 자, 제어문자 금지. 위반이면 400 `invalid_<field>`."""
    text = (value or "").strip()
    if not text or len(text) > max_len or _CONTROL.search(text):
        raise HTTPException(400, f"invalid_{field}")
    return text


def _blank_to_none(v):
    if isinstance(v, str) and not v.strip():
        return None
    return v.strip() if isinstance(v, str) else v


# 초대 생성 입력용 — 빈 문자열은 "링크 초대(대상 없음)", 값이 있으면 이메일 형식이어야 한다.
OptionalEmail = Annotated[EmailStr | None, BeforeValidator(_blank_to_none)]
