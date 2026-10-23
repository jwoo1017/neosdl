"""채팅 에이전트의 결정 지점(계획·도구 선택·근거 선별) 기록 — sql/034_decision_log.sql.

ai_call_log는 "호출이 성공했는가"만 남겨서, 모델이 *무엇을* 결정했고 그게 맞았는지는
알 수 없었다. 여기서는 결정 내용과 파싱 경로(스키마 제약이 지켜졌는지), 백스톱이
개입했는지를 남긴다 — 결정 모델(Laya) 도입 전 베이스라인 측정과 학습 데이터의 원천이다.

기록 실패가 채팅을 막으면 안 되므로 독립 트랜잭션 + fail-open. project_id가 없으면
(단위 테스트, 프로젝트 밖 호출) 아무것도 하지 않는다 — ai_call_log와 같은 규칙.
"""

from __future__ import annotations

import json
import logging

from app.db import tx

log = logging.getLogger("app.decision_log")


def record(project_id: str | None, scan_id: str | None, point: str, model: str, question: str,
           decision: dict, parse_path: str, *, guard_fired: str | None = None,
           latency_ms: int | None = None, engagement_id: str | None = None) -> None:
    """point: plan|step|relevance|monitor. parse_path: schema|fallback|json|guard|fail|error.

    engagement_id(sql/035)는 자동화 run 의 결정(실행 모니터링 StepMonitor/Reflector/Adviser 포함)을
    engagement 에 묶는다. 채팅 경로는 None.
    """
    if not project_id:
        return
    try:
        with tx() as cur:
            cur.execute(
                """
                INSERT INTO decision_log (project_id, scan_id, point, model, question, decision,
                                          parse_path, guard_fired, latency_ms, engagement_id)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                """,
                (project_id, scan_id, point, model, question[:2000],
                 json.dumps(decision, ensure_ascii=False, default=str), parse_path, guard_fired,
                 latency_ms, engagement_id),
            )
    except Exception:
        log.warning("decision_log 기록 실패 — 무시", exc_info=True)
