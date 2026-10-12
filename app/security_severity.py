"""security_finding.category -> severity 매핑.

taint 엔진(`app/dataflow.py`)이 실제로 탐지하는 카테고리만 다룬다 — 의존성
취약점(SCA/CVE) 스캔은 이 프로젝트에 아직 없으므로 "Dependency" 같은
카테고리는 여기 없다(추후 별도 스캐너 연동 시 이 매핑에 추가).
"""

from __future__ import annotations

SEVERITY_BY_CATEGORY: dict[str, str] = {
    "sql_injection": "critical",
    "command_injection": "critical",
    "xss": "high",
}

# domain_crossing:<domain> 은 도메인명에 따라 동적으로 심각도를 매긴다 —
# 인증/결제 관련 도메인을 넘나드는 흐름은 medium, 그 외는 low.
_SENSITIVE_DOMAIN_KEYWORDS = ("auth", "인증", "payment", "결제", "pay")


def severity_for(category: str) -> str:
    if category in SEVERITY_BY_CATEGORY:
        return SEVERITY_BY_CATEGORY[category]
    if category.startswith("domain_crossing:"):
        domain = category.removeprefix("domain_crossing:").lower()
        if any(keyword in domain for keyword in _SENSITIVE_DOMAIN_KEYWORDS):
            return "medium"
        return "low"
    return "low"
