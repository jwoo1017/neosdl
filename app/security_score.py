"""합성 타임라인 점수 — Security Timeline 의 트렌드 라인 하나를 위한 롤업.

**phase2 Risk Vector 가 아니다.** phase2 는 의도적으로 단일 점수를 만들지
않는다(`NeoSDL_Security_History_Plan.md` 가 참조하는 phase2 §1.2 — 5개 차원을
독립적으로 관리). 이 점수는 "이번 커밋으로 보안이 좋아졌나 나빠졌나"를 한 줄
그래프로 보기 위한 것일 뿐, 정책 결정에 쓰지 않는다.

가중치는 `security_severity.SEVERITY_BY_CATEGORY` 의 심각도 서열과 방향이 같다
(critical > high > medium > low). 정확한 숫자는 조정 가능 — 여기 한 곳만 바꾼다.
"""

from __future__ import annotations

SCORE_WEIGHTS: dict[str, float] = {
    "critical": 10.0,
    "high": 5.0,
    "medium": 2.0,
    "low": 1.0,
}


def synthetic_score(counts: dict[str, int]) -> float:
    """{'critical': n, 'high': n, ...} -> 가중 합. 없는 severity 는 0."""
    return round(sum(w * counts.get(sev, 0) for sev, w in SCORE_WEIGHTS.items()), 2)
