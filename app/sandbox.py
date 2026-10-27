"""샌드박스 실행 게이트 — 서비스 계층.

privileged dind = 호스트 root 이므로, "어떤 저장소의 코드를 실제로 실행해도 되는가"를 가장 먼저
막는다. 외부/고객 저장소는 Phase 2.5 격리 경계(KVM VM) 완료 전까지 allowlist 에 넣지 않으며,
요청이 오면 조용히 실패하지 않고 명시적으로 거부 + audit 기록한다(neosdl-sandbox-dind-remediation.md).

allowlist 는 fail-closed: 설정이 비어 있으면 아무 레포도 실행하지 못한다.
"""

from __future__ import annotations

from fastapi import HTTPException
from psycopg import Cursor

from app import audit, toolbox
from app.config import settings


def repo_allowed(full_name: str | None) -> bool:
    """"namespace/name" 이 샌드박스 실행 allowlist 에 있는가(순수 함수, 테스트 용이)."""
    if not full_name:
        return False
    return full_name in settings().sandbox_repo_allowlist_set


def _repo_full_name(cur: Cursor, repository_id: str) -> str | None:
    cur.execute("SELECT namespace, name FROM repository WHERE id=%s", (str(repository_id),))
    row = cur.fetchone()
    return f"{row['namespace']}/{row['name']}" if row else None


def assert_repo_allowed(cur: Cursor, repository_id: str, *, actor: str) -> str:
    """repository 가 샌드박스 실행 allowlist 에 없으면 403 + audit. 통과하면 full_name 반환.

    샌드박스 부팅을 enqueue 하기 직전(Phase 2 `sandbox.request_boot`)에 호출한다. 정적 engagement
    (코드 실행 없음)는 이 게이트를 거치지 않는다.
    """
    full_name = _repo_full_name(cur, repository_id)
    if not settings().sandbox_enabled:
        audit.record(cur, actor=actor, action="sandbox.rejected",
                     subject={"repository_id": str(repository_id), "repo": full_name},
                     payload={"reason": "sandbox_disabled"})
        raise HTTPException(409, "sandbox_disabled")
    if not repo_allowed(full_name):
        # 외부/고객 레포 또는 미등록 자사 레포 — Phase 2.5 경계 전까지 실행 금지.
        audit.record(cur, actor=actor, action="sandbox.rejected",
                     subject={"repository_id": str(repository_id), "repo": full_name},
                     payload={"reason": "external_repo_not_allowed"})
        raise HTTPException(403, "external_repo_not_allowed")
    return full_name


def assert_target_allowed(cur: Cursor, target: str, *, actor: str) -> str:
    """공격 도구(terminal)를 쏠 표적(URL/host)이 인가되지 않았으면 403/409 + audit.

    블랙박스 외부표적 engagement·채팅 terminal 툴을 실행하기 직전에 호출한다. assert_repo_allowed
    와 동형: fail-closed(allowlist 비면 전부 거부), 조용히 실패 금지. 통과하면 표적 host 반환.
    """
    if not settings().sandbox_enabled:
        audit.record(cur, actor=actor, action="sandbox.target_rejected",
                     subject={"target": target}, payload={"reason": "sandbox_disabled"})
        raise HTTPException(409, "sandbox_disabled")
    if not toolbox.target_allowed(target):
        # 미인가 표적(법적 권한 없음) — 공격 도구 실행 금지.
        audit.record(cur, actor=actor, action="sandbox.target_rejected",
                     subject={"target": target}, payload={"reason": "target_not_authorized"})
        raise HTTPException(403, "target_not_authorized")
    host, _ = toolbox.parse_target(target)
    return host
