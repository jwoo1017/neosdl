"""검증 오라클 — 응답·앱로그·IAST 신호로 verdict 를 결정하는 순수 함수(DB/LLM 없음).

secret_scan/assemble_risk 처럼 결정적·단위테스트 가능하게 둔다. 신뢰도는
엔트리포인트 신뢰 × 신호 강도 × 오라클 배수(IAST > 블랙박스)로 합친다.

verdict: confirmed | false_positive | inconclusive. (not_attempted 는 엔트리포인트 단계에서 결정)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# DB 드라이버 파싱/문법 에러 지문(여러 DB) — probe 응답에만 나타나면 문자열 조립 신호.
_SQL_ERROR = re.compile(
    r"(SQLSTATE|ER_PARSE_ERROR|You have an error in your SQL syntax|"
    r"unterminated quoted string|syntax error at or near|ORA-\d{5}|"
    r"SQLITE_ERROR|psycopg2?\.|near \"|quoted string not properly terminated)", re.I)


@dataclass(frozen=True)
class Verdict:
    verdict: str                # confirmed | false_positive | inconclusive
    confidence: float           # 0..1
    signals: dict


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def decide(category: str, *, control_body: str = "", probe_body: str,
           control_status: int = 200, probe_status: int = 200,
           nonce: str = "", app_logs: str | None = None,
           iast_reached: bool | None = None,
           entry_confidence: float = 0.6,
           timing_delta_s: float | None = None) -> Verdict:
    """control/probe 응답 + (선택) 앱로그·IAST·타이밍으로 판정.

    iast_reached: IAST-lite 가 있으면 nonce 가 sink 의 '위험 위치'(쿼리 문자열/명령)에 도달했는지.
      True → 강한 confirmed, False → 강한 false_positive(이번 9건: 바인딩이라 미도달).
    """
    signals: dict = {"category": category}

    # IAST 단락(가장 강함) — 화이트박스라 sink 도달을 직접 관측.
    if iast_reached is True:
        signals["iast"] = "nonce reached sink raw position"
        return Verdict("confirmed", _clamp(0.9 * (0.5 + entry_confidence / 2)), signals)
    if iast_reached is False:
        signals["iast"] = "nonce did not reach sink (bound/sanitized)"
        return Verdict("false_positive", _clamp(0.85 * (0.5 + entry_confidence / 2)), signals)

    logs = app_logs or ""
    if category == "sql_injection":
        prob_err = bool(_SQL_ERROR.search(probe_body) or _SQL_ERROR.search(logs))
        ctrl_err = bool(_SQL_ERROR.search(control_body))
        signals.update(probe_sql_error=prob_err, control_sql_error=ctrl_err)
        if prob_err and not ctrl_err:
            return Verdict("confirmed", _clamp(0.6 * entry_confidence + 0.2), signals)
        if not prob_err and control_status == probe_status and control_body == probe_body:
            # 따옴표를 넣어도 응답이 완전히 동일 = 바인딩 가능성 높음(약한 false_positive).
            return Verdict("false_positive", _clamp(0.4 * entry_confidence + 0.1), signals)
        return Verdict("inconclusive", 0.2, signals)

    if category == "xss":
        esc = _html_escaped(nonce)
        reflected_raw = bool(nonce and nonce in probe_body)
        # 이스케이프는 '이스케이프 형태가 원형과 실제로 다를 때'만 의미가 있다(마커에 <>가 있어야).
        escaped = bool(nonce and esc != nonce and esc in probe_body)
        signals.update(reflected_unescaped=reflected_raw, reflected_escaped=escaped)
        if reflected_raw:
            return Verdict("confirmed", _clamp(0.7 * entry_confidence + 0.25), signals)
        if escaped:
            return Verdict("false_positive", _clamp(0.6 * entry_confidence + 0.1), signals)
        return Verdict("inconclusive", 0.2, signals)

    if category == "command_injection":
        echoed = bool(nonce and (nonce in probe_body or nonce in logs))
        timing = timing_delta_s is not None and timing_delta_s >= 4.0
        signals.update(nonce_echoed=echoed, timing_delta_s=timing_delta_s)
        if echoed or timing:
            return Verdict("confirmed", _clamp(0.6 * entry_confidence + 0.2), signals)
        return Verdict("inconclusive", 0.2, signals)

    return Verdict("inconclusive", 0.0, signals)


def _html_escaped(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
