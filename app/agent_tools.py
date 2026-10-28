"""에이전트 도구 레지스트리 — 읽기전용 도구와 실행 도구를 가르는 seam.

기존 채팅은 `app/orchestrator.py`의 `TOOLS`(정의) + `_execute_tool`(실행)로 읽기전용 DB/그래프
조회만 한다. 자율 검증(화이트박스 DAST)은 그 위에 "실행 도구"(샌드박스 쉘·HTTP 프로브 등)를
더한다. 두 종류를 한 인터페이스로 두되, 실행 도구는 permission_mode·역할 게이트를 통과해야만
부를 수 있게 하는 게 이 모듈의 목적이다.

phase-0 원칙: 읽기전용 도구는 **재구현하지 않는다**. `orchestrator._execute_tool`에 그대로 위임해
채팅 동작을 바이트 단위로 보존한다(그 함수에는 실전 사고를 고친 로직이 쌓여 있다). 실행
레지스트리는 지금은 비어 있고, 샌드박스/검증 모듈(app/dynamic/*, worker/sandbox/*)이 phase-2에서
`register_exec`로 채운다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

# project_role 랭크(app/auth.py _PROJECT_ROLE_RANK 와 동일) — 실행 도구 최소 권한 판정용.
_ROLE_RANK = {"viewer": 0, "developer": 1, "manager": 2, "owner": 3}

ToolKind = Literal["read", "exec"]


@dataclass(frozen=True)
class ToolSpec:
    """도구 하나의 메타 + 실행 핸들러.

    handler(cur, ctx, params, question) -> object.  읽기 도구는 orchestrator._execute_tool 로
    위임하는 얇은 래퍼라 시그니처를 맞춘다. exec 도구는 샌드박스/engagement 컨텍스트를 ctx 로 받는다.
    """

    name: str
    description: str
    params: dict
    kind: ToolKind
    handler: Callable
    min_role: str = "viewer"   # exec 도구가 요구하는 최소 project_role
    destructive: bool = False  # True 면 permission_mode=auto 라도 항상 사람 승인(하드 가드)
    destructive_fn: Callable | None = None  # params 로 destructive 를 동적 판정(예: terminal 은 명령별로 다름)

    def role_allows(self, effective_role: str | None) -> bool:
        if self.kind == "read":
            return True
        return _ROLE_RANK.get(effective_role or "", -1) >= _ROLE_RANK.get(self.min_role, 99)

    def is_destructive(self, params: dict | None) -> bool:
        """이 호출이 파괴/침투성인가 — 고정 플래그 또는 params 기반 동적 판정."""
        if self.destructive_fn is not None:
            return bool(self.destructive_fn(params or {}))
        return self.destructive


@dataclass(frozen=True)
class AuthzDecision:
    """exec 도구 호출 1건의 집행 결정(순수). allowed=False 면 needs_approval 로 승인대기/거부 구분."""

    allowed: bool
    needs_approval: bool
    reason: str | None = None


def authorize(spec: "ToolSpec", *, permission_mode: str, effective_role: str | None,
              params: dict | None = None, approved: bool = False,
              trusted_target: bool = False) -> AuthzDecision:
    """도구 호출을 permission_mode·역할·destructive 로 게이트(순수 함수 — Docker/DB 무의존).

    - read 도구: 항상 허용(채팅과 동일, 이미 엔드포인트에서 인가됨).
    - plan: exec 자체를 금지(설계·정적만).
    - ask: 모든 exec 가 승인 필요.
    - auto: 자동 진행하되 destructive 는 항상 승인 필요(하드 가드).
    역할 미달은 승인으로도 못 넘는 거부(needs_approval=False).

    trusted_target=True 는 **우리가 직접 띄운 일회용 샌드박스 앱**(self-boot)이 표적일 때 — 그
    경우 파괴 도구(sqlmap 등)도 격리된 throwaway 대상이라 auto 에서 자동 허용한다(destructive
    하드가드 면제). 단 plan(exec 금지)·ask(건건 승인)·역할 게이트는 그대로다. 외부 표적
    (trusted_target=False)은 기존대로 auto 라도 destructive 는 승인 필요.
    """
    if spec.kind == "read":
        return AuthzDecision(True, False)
    if not spec.role_allows(effective_role):
        return AuthzDecision(False, False, "insufficient_role")
    if permission_mode == "plan":
        return AuthzDecision(False, False, "plan_mode_no_exec")
    needs_approval = (permission_mode == "ask") or (spec.is_destructive(params) and not trusted_target)
    if needs_approval and not approved:
        return AuthzDecision(False, True, "approval_required")
    return AuthzDecision(True, needs_approval)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def register_exec(self, name: str, description: str, params: dict, handler: Callable, *,
                      min_role: str = "manager", destructive: bool = False,
                      destructive_fn: Callable | None = None) -> None:
        """실행 도구(샌드박스 쉘 등)를 레지스트리에 등록. 기본 min_role=manager(코드/표적을 실제로 때림)."""
        self.register(ToolSpec(name=name, description=description, params=params, kind="exec",
                               handler=handler, min_role=min_role, destructive=destructive,
                               destructive_fn=destructive_fn))

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self, *, kind: ToolKind | None = None) -> list[str]:
        return [n for n, s in self._tools.items() if kind is None or s.kind == kind]

    def read_only(self) -> list[ToolSpec]:
        return [s for s in self._tools.values() if s.kind == "read"]

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())


def _read_tool_handler(name: str) -> Callable:
    """orchestrator._execute_tool 로 위임하는 래퍼. import 는 함수 안에서(순환 임포트 회피 —
    orchestrator 가 이 모듈을 import 하므로)."""

    def handler(cur, ctx, params, question):
        from app import orchestrator
        return orchestrator._execute_tool(cur, ctx, name, params, question)

    return handler


def build_default_registry() -> ToolRegistry:
    """현 채팅의 읽기전용 도구를 orchestrator.TOOLS 정의 그대로 레지스트리에 싣는다.

    orchestrator.TOOLS 가 단일 진실원천 — 여기서 이름/설명/params 를 복제하지 않고 그대로 읽어
    위임 핸들러만 붙인다. 새 읽기 도구를 orchestrator 에 추가하면 자동으로 여기에도 들어온다.
    """
    from app import orchestrator

    reg = ToolRegistry()
    for t in orchestrator.TOOLS:
        reg.register(ToolSpec(
            name=t["name"], description=t["description"], params=t["params"],
            kind="read", handler=_read_tool_handler(t["name"]),
        ))
    return reg
