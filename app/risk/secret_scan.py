"""하드코딩된 비밀정보 탐지 — deterministic 정규식(phase2 plan §6, §9).

Rule Engine 의 시드다: "IF secret detected → secret = CRITICAL"(§9)은 LLM
판단이 아니라 이 스캐너의 결정론적 출력에 걸린다. 그래서 여기서는
재현 가능성을 정확도보다 우선한다 — 애매하면 낮은 confidence 로 보고하고,
최종 억제는 whitelist/정책이 한다.

`scan_text()` 는 소스 한 덩어리(파일 또는 함수 본문)를 받아 매치 목록을
돌려준다. DB/LLM 의존 없음 — `tests/test_risk.py` 가 문자열만으로 검증한다.
"""

from __future__ import annotations

import re
from typing import Any

# (rule, 정규식, confidence, 설명). confidence 는 "이게 진짜 비밀정보일 확률"의
# 보수적 추정이다. 형식이 고정된 것(AWS AKIA, PEM 헤더)은 높게, 일반
# 대입문 휴리스틱은 낮게 준다.
_PATTERNS: list[tuple[str, re.Pattern[str], float, str]] = [
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 0.97,
     "AWS Access Key ID 형식의 리터럴"),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"), 0.98,
     "PEM 개인키 블록"),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0.9,
     "Google API 키 형식의 리터럴"),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"), 0.92,
     "Slack 토큰 형식의 리터럴"),
    ("github_token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b"), 0.92,
     "GitHub 토큰 형식의 리터럴"),
    ("jwt_literal", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), 0.75,
     "JWT 형식의 리터럴"),
    ("bearer_literal", re.compile(r"[Bb]earer\s+[A-Za-z0-9._\-]{20,}"), 0.6,
     "코드에 박힌 Bearer 토큰"),
]

# 이름이 비밀정보를 암시하는 식별자에 문자열/긴 값을 직접 대입하는 패턴.
_ASSIGN_SECRET = re.compile(
    r"""(?ix)
    \b(?P<name>[A-Za-z0-9_]*?
        (?:secret|passwd|password|api[_-]?key|apikey|access[_-]?token|
           auth[_-]?token|client[_-]?secret|private[_-]?key|encryption[_-]?key)
     )\b
    \s* [:=] \s*
    (?P<qext>["'`])(?P<val>[^"'`\n]{8,})(?P=qext)
    """,
)

# 대입 우변이 환경변수/설정 조회면 하드코딩이 아니다 — 오탐 억제.
_NOT_A_SECRET_VALUE = re.compile(
    r"(?i)^(?:process\.env|os\.environ|os\.getenv|config\.|settings\.|\$\{?[A-Z_]+\}?|<|xxx+|changeme|placeholder|your[_-])"
)


def _iter_lines(text: str):
    for i, line in enumerate(text.splitlines(), start=1):
        yield i, line


def scan_text(text: str, *, path: str | None = None) -> list[dict[str, Any]]:
    """소스 텍스트에서 비밀정보 후보를 찾는다. 매치 dict 리스트를 돌려준다.

    각 dict: {rule, kind, description, confidence, location:{path,line}, metadata}
    `kind` 는 항상 "secret_detection"(§6) — 세부 규칙은 `rule` 에 담는다.
    """
    if not text:
        return []

    findings: list[dict[str, Any]] = []
    for lineno, line in _iter_lines(text):
        for rule, pattern, confidence, desc in _PATTERNS:
            if pattern.search(line):
                findings.append(_mk(rule, desc, confidence, path, lineno))

        m = _ASSIGN_SECRET.search(line)
        if m and not _NOT_A_SECRET_VALUE.match(m.group("val").strip()):
            findings.append(_mk(
                "hardcoded_credential_assignment",
                f"비밀정보를 암시하는 식별자 `{m.group('name')}` 에 리터럴 대입",
                0.55, path, lineno, name=m.group("name"),
            ))
    return _dedupe(findings)


def _mk(rule: str, description: str, confidence: float, path: str | None, line: int, **meta) -> dict[str, Any]:
    return {
        "rule": rule,
        "kind": "secret_detection",
        "description": description,
        "confidence": confidence,
        "location": {"path": path, "line": line},
        "metadata": meta,
    }


def _dedupe(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for f in findings:
        key = (f["rule"], f["location"]["path"], f["location"]["line"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
