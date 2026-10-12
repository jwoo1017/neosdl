"""개인식별정보(PII) 탐지 — deterministic 정규식/식별자 휴리스틱(phase2 plan §6).

secret_scan 과 같은 원칙: 재현 가능성 우선, 애매하면 낮은 confidence.
두 종류를 본다.

  1. 값 리터럴  — 코드에 박힌 주민등록번호/카드번호 형식(테스트 픽스처, 하드코딩)
  2. 식별자     — `resident_number` / `ssn` / `card_no` 같은 PII 를 다루는 변수/필드/파라미터명

식별자 기반이 실무에서 훨씬 흔하다 — "이 함수가 PII 를 다룬다"는 신호는 보통
값이 아니라 이름에서 온다.
"""

from __future__ import annotations

import re
from typing import Any

_VALUE_PATTERNS: list[tuple[str, re.Pattern[str], float, str]] = [
    ("krrn_literal", re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)"), 0.6,
     "주민등록번호 형식의 리터럴"),
    ("card_number_literal", re.compile(r"(?<!\d)(?:\d[ -]?){15,16}\d(?!\d)"), 0.4,
     "신용카드 번호 형식의 리터럴"),
    ("email_literal", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), 0.25,
     "이메일 주소 리터럴"),
    ("krphone_literal", re.compile(r"(?<!\d)01[016789][-\s]?\d{3,4}[-\s]?\d{4}(?!\d)"), 0.3,
     "휴대전화 번호 형식의 리터럴"),
]

_PII_IDENTIFIER = re.compile(
    r"""(?ix)
    \b(
        (?:resident|social[_-]?security|jumin|주민)[_-]?(?:number|no|num|id)?
      | \bssn\b
      | (?:passport)[_-]?(?:number|no|num)?
      | (?:card|credit[_-]?card)[_-]?(?:number|no|num)
      | (?:phone|mobile|tel)[_-]?(?:number|no|num)?
      | (?:date[_-]?of[_-]?birth|birth[_-]?date|birthday|생년월일)
      | (?:home[_-]?address|street[_-]?address|우편번호|zip[_-]?code)
      | (?:full[_-]?name|first[_-]?name|last[_-]?name|real[_-]?name)
      | (?:driver[_-]?license)
    )\b
    """,
)


def scan_text(text: str, *, path: str | None = None) -> list[dict[str, Any]]:
    if not text:
        return []
    findings: list[dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for rule, pattern, confidence, desc in _VALUE_PATTERNS:
            if pattern.search(line):
                findings.append(_mk(rule, desc, confidence, path, i))
        seen_ids: set[str] = set()
        for m in _PII_IDENTIFIER.finditer(line):
            token = m.group(0).lower()
            if token in seen_ids:
                continue
            seen_ids.add(token)
            findings.append(_mk("pii_identifier", f"PII 관련 식별자 `{m.group(0)}`", 0.5, path, i,
                                identifier=m.group(0)))
    return _dedupe(findings)


def scan_identifiers(names: list[str]) -> list[dict[str, Any]]:
    """심볼 시그니처/파라미터명 등 이미 파싱된 식별자 목록에 대한 PII 매칭.

    소스 원문이 없어도(예: pdg_function.param_names 만 있는 경우) 식별자
    신호는 잡을 수 있게 한다.
    """
    out: list[dict[str, Any]] = []
    for name in names:
        if name and _PII_IDENTIFIER.search(name):
            out.append(_mk("pii_identifier", f"PII 관련 식별자 `{name}`", 0.5, None, 0, identifier=name))
    return _dedupe(out)


def _mk(rule: str, description: str, confidence: float, path: str | None, line: int, **meta) -> dict[str, Any]:
    return {
        "rule": rule,
        "kind": "pii_detection",
        "description": description,
        "confidence": confidence,
        "location": {"path": path, "line": line},
        "metadata": meta,
    }


def _dedupe(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for f in findings:
        key = (f["rule"], f["location"]["path"], f["location"]["line"], f["metadata"].get("identifier"))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
