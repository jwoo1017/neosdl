"""engagement 의 대상 앱을 샌드박스(dind)에서 기동하고, 검증 엔진용 (app_client, log_source,
teardown) 을 돌려준다. 실제 Docker 가 필요하므로 서버(dind)/로컬 Docker 에서만 동작한다 —
단위 테스트는 검증 두뇌(runner/oracle/feedback)에 가짜를 주입해서 하고, 이 모듈은 e2e(서버)로 확인.

Phase 2 범위: detect 가 ok 인 레포(특히 Dockerfile/compose 제공)만. 미기동/실패는 None 을 돌려
호출부(engagement.run)가 정적 판정을 유지하게 한다(graceful degradation).
"""

from __future__ import annotations

import logging

from app.config import settings
from worker.handlers.clone import pipeline_source_root
from worker.sandbox import detect

log = logging.getLogger("worker.sandbox.boot")


def boot_engagement(engagement: dict):
    """(app_client, log_source, teardown) | None.

    현재 구현은 뼈대다: 스택 감지 → (성공 시) DindRunner 로 빌드/기동/준비. 빌드·기동 세부는
    서버 e2e 에서 완성한다. 감지 실패나 Docker 불가면 None.
    """
    scan_id = str(engagement["scan_id"])
    repo_id = str(engagement["repository_id"])
    root = pipeline_source_root(scan_id, repo_id)
    plan = detect.detect(root)
    if not plan.ok:
        log.info("engagement %s 부팅 불가(감지 실패: %s)", engagement["id"], plan.reason)
        return None

    try:
        from worker.sandbox.dind import DindRunner
    except Exception:
        log.warning("docker SDK 미설치 — 동적 검증 건너뜀", exc_info=True)
        return None
    runner = DindRunner()
    if not runner.health():
        log.warning("dind 미응답(%s) — 동적 검증 건너뜀", settings().sandbox_docker_host)
        return None

    # TODO(server e2e): plan 으로 이미지 빌드 → 백킹서비스 프로비저닝 → 앱 기동 → readiness →
    # base_url + IAST 로그 소스 구성. 완성 전까지는 안전하게 미기동 처리.
    log.info("engagement %s: 스택 감지 OK(%s) — 부팅 구현은 서버 e2e 에서 완성 예정",
             engagement["id"], plan.framework or plan.language or "docker")
    runner.destroy(str(engagement["id"]))
    return None
