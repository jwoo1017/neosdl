"""무해 프로브 합성 — 카테고리별 안전 템플릿 라이브러리.

1차 안전선: **파괴적 익스플로잇을 만들지 않는다.** 각 프로브는 (a) 고유 nonce 마커, (b) 멱등,
(c) 데이터 변경·네트워크·시스템 손상 없음. LLM 은 자유 생성이 아니라 템플릿의 "슬롯만" 채우고,
반환값은 여기서 카테고리 allowlist(문자 클래스)로 **재검증**한다(방어 심층화 — StepDecision 필드
필터와 같은 패턴). 파괴 템플릿은 라이브러리에 아예 존재하지 않는다.

프로브는 control(기준) + probe(마커/차분) 두 요청으로 구성된다 — 오라클이 둘을 비교한다.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field

CATEGORIES = ("sql_injection", "xss", "command_injection")


@dataclass(frozen=True)
class Probe:
    category: str
    template_id: str
    control_value: str          # 무해 기준값
    probe_value: str            # 마커/차분값
    nonce: str                  # 이 프로브 고유 식별자(응답/로그에서 찾음)
    note: str = ""
    samples: int = 1            # 타이밍 차분 등 반복 횟수(기본 1)
    meta: dict = field(default_factory=dict)


def _nonce(finding_id: object) -> str:
    return f"neosdl-{finding_id}-{secrets.token_hex(3)}"


# 카테고리별 허용 문자 클래스 — LLM/호출자가 넘긴 값이 이 밖이면 거부(파괴 토큰 차단).
# 세미콜론·stacked query·따옴표 이스케이프 조합 등은 애초에 통과 못 한다.
_SAFE_VALUE_RE = {
    "sql_injection": re.compile(r"^[A-Za-z0-9 '=\-_.]{1,40}$"),   # 읽기전용 차분(따옴표/공백/등호/숫자)
    "xss": re.compile(r"^[A-Za-z0-9<>/_\-]{1,60}$"),               # 마커 태그(스크립트/속성/이벤트 핸들러 없음)
    "command_injection": re.compile(r"^[A-Za-z0-9 _\-]{1,40}$"),   # echo/sleep 인자(쉘 메타문자 없음)
}

# SQLi 에서 금지하는 파괴·위험 패턴(이중 안전장치) — 통과하면 프로브 거부.
_SQLI_FORBIDDEN = re.compile(r"(;|--|/\*|\bdrop\b|\bdelete\b|\bupdate\b|\binsert\b|\bunion\b|\bor\b\s+1)", re.I)


def _check(category: str, value: str) -> bool:
    rx = _SAFE_VALUE_RE.get(category)
    if rx is None or not rx.match(value):
        return False
    if category == "sql_injection" and _SQLI_FORBIDDEN.search(value):
        return False
    return True


def synthesize(category: str, finding_id: object, *, base_value: str = "1") -> Probe | None:
    """카테고리별 결정적 기본 프로브. LLM 없이도 동작(LLM 은 base_value 를 더 그럴듯하게 고를 때만).

    None 이면 지원하지 않는 카테고리(호출부는 not_attempted 처리).
    """
    nonce = _nonce(finding_id)
    if category == "sql_injection":
        # 읽기전용 불리언 차분: 참 항등식 vs 거짓 항등식(문자열 조립이면 응답이 달라진다).
        # 데이터 변경 없음. 작은따옴표로 파싱 에러를 유도하는 control/probe 쌍도 겸한다.
        control = f"{base_value}"
        probe = f"{base_value}'"          # 조립되면 파싱 에러 지문, 바인딩이면 무해
        if not (_check(category, control) and _check(category, probe)):
            return None
        return Probe(category, "sqli_quote_error", control, probe, nonce,
                     note="바인딩이면 동일 응답, 문자열 조립이면 probe 에서 DB 파싱 에러")
    if category == "xss":
        marker = f"<neo-{nonce}>"
        if not _check(category, marker):
            # nonce 에 허용문자만 있으므로 보통 통과
            marker = f"neo{re.sub(r'[^A-Za-z0-9]', '', nonce)}"
        return Probe(category, "xss_marker_reflection", "neosdl-x", marker, nonce,
                     note="probe 마커가 미이스케이프로 반사되면 confirmed")
    if category == "command_injection":
        # 무해 nonce echo(출력 오라클). 쉘 메타문자 없음 — 실제 주입이 아니라 '도달' 관측용.
        if not _check(category, nonce.replace("-", "_")):
            return None
        return Probe(category, "cmd_echo_marker", "ping", nonce.replace("-", "_"), nonce,
                     note="probe nonce 가 출력/IAST 에 나타나면 confirmed", samples=1)
    return None


def validate_llm_fill(category: str, template_id: str, control: str, probe: str) -> bool:
    """LLM(ProbePlan)이 채운 슬롯을 카테고리 allowlist 로 재검증. 하나라도 어긋나면 거부."""
    known = {"sqli_quote_error", "xss_marker_reflection", "cmd_echo_marker"}
    return template_id in known and _check(category, control) and _check(category, probe)
