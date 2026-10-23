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

    def role_allows(self, effective_role: str | None) -> bool:
        if self.kind == "read":
            return True
        return _ROLE_RANK.get(effective_role or "", -1) >= _ROLE_RANK.get(self.min_role, 99)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

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
