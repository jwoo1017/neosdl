"""risk.build — 스캔 하나의 Risk Object 를 계산해 영속화한다(phase2 plan §8, §24 Step 8).

`symbol.annotate` / `symbol.embed` / `pdg.build` 와 같은 별도 잡이다 — Business/IP
분류에 내부 LLM 을 부르므로 느릴 수 있고(게이트웨이 왕복 × 관심 심볼 수),
그 지연이 사용자의 첫 조회 요청을 붙잡으면 안 된다. 실패해도 스캔/구조
그래프에는 영향이 없다.

의존: 이 잡이 의미 있으려면 `symbol_semantic`(annotate)·`security_finding`·
CFG/PDG 가 이미 채워져 있어야 근거 수집이 풍부해진다. 아직 안 됐어도 잡은
성공한다 — 그때는 대부분 심볼이 LOW/RAW 로 나올 뿐이다.

증분(§12): 스캔이 parent 를 가지면 변경 파일 심볼 + 그 caller 만 LLM 재분류하고
나머지는 parent 의 분류를 재사용한다. 파이프라인의 마지막 소스-읽기 단계이므로
여기서 스캔 worktree 도 정리한다.
"""

from __future__ import annotations

import logging

from app import incremental, repo_activity_bus
from app.config import settings
from app.db import tx
from app.risk.synthesis import ensure_risk_objects
from app.risk_identity import ensure_risk_identities
from app.scan_queries import ensure_security_findings
from app.security_snapshot import ensure_security_snapshot
from worker.handlers.clone import pipeline_source_root, remove_scan_worktree

log = logging.getLogger("worker.risk_build")


def handle(payload: dict) -> None:
    scan_id = payload["scan_id"]

    with tx() as cur:
        cur.execute(
            """
            SELECT s.repository_id, p.id AS project_id, p.ci_model_name
              FROM scan s
              LEFT JOIN project_repository pr ON pr.repository_id = s.repository_id
              LEFT JOIN project p ON p.id = pr.project_id
             WHERE s.id = %s
             LIMIT 1
            """,
            (scan_id,),
        )
        row = cur.fetchone()
        if row is None:
            log.warning("scan %s 없음 — 건너뜀", scan_id)
            return
        repository_id = row["repository_id"]
        project_id = str(row["project_id"]) if row["project_id"] else None
        model = row["ci_model_name"] or settings().default_chat_model
        parent_scan_id, changed_paths = incremental.read_incremental_context(cur, str(scan_id))

    root = pipeline_source_root(str(scan_id), str(repository_id))

    def source_reader(path: str) -> str | None:
        try:
            return (root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    try:
        # taint 분석 결과(security_finding)는 최초 조회 시 계산되는 캐시라, 아무도
        # 보안 탭을 안 열었으면 비어 있다 — Risk 근거로 쓰려면 여기서 먼저 채운다.
        with tx() as cur:
            ensure_security_findings(cur, str(scan_id))

        # Security History — 이 커밋의 보안 상태 스냅샷을 물질화(별도 tx + fail-safe:
        # 실패해도 스캔 파이프라인은 계속). 웹훅 스캔은 전부 risk.build 를 거치므로
        # 별도 잡 없이 여기서 매 스캔 스냅샷이 쌓인다.
        try:
            with tx() as cur:
                ensure_security_snapshot(cur, str(scan_id))
            with tx() as cur:
                ensure_risk_identities(cur, str(scan_id))
        except Exception:
            log.warning("scan %s: security history 갱신 실패 — 스캔은 계속", scan_id, exc_info=True)

        with tx() as cur:
            ensure_risk_objects(
                cur, str(scan_id), model=model,
                project_id=project_id, source_reader=source_reader,
                parent_scan_id=parent_scan_id, changed_paths=changed_paths,
            )
        log.info("scan %s: risk.build 완료", scan_id)

        # 파이프라인의 마지막 단계 — 여기서 "이 스캔 끝났다"를 마스코트 사이드바에
        # 알린다. 실패해도(알림 자체 오류) 스캔 결과에는 영향이 없어야 하므로
        # 별도 try로 감싼다(모듈 전체의 fail-open 철학과 동일).
        try:
            with tx() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM security_finding WHERE scan_id=%s AND status='open'",
                    (scan_id,),
                )
                findings_count = cur.fetchone()["n"]
                repo_activity_bus.notify_repo_activity(
                    cur, repository_id,
                    {"type": "scan_completed", "scan_id": str(scan_id), "findings_count": findings_count},
                )
        except Exception:
            log.warning("scan %s: scan_completed 알림 실패 — 무시", scan_id, exc_info=True)
    finally:
        # 파이프라인의 마지막 소스-읽기 단계 — 스캔 worktree 정리(best-effort).
        remove_scan_worktree(str(repository_id), str(scan_id))
