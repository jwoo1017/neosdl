"""샌드박스 실행 백엔드 — 격리 경계 추상화.

privileged dind(Phase 1.5)와 전용 KVM VM(Phase 2.5)은 격리 수준이 다르지만, 앱/워커 코드는
동일한 `SandboxRunner` 인터페이스만 쓴다. 경계를 바꿀 때 구현(docker_client / vm_client)만
교체하고 호출부는 손대지 않는다. dind 주소·cert 는 설정값으로만 받는다(하드코딩 금지).

phase-1.5 에서는 인터페이스와 설정만 둔다. 실제 docker SDK 구현(DindRunner)은 phase-2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ContainerSpec:
    """중첩 컨테이너 생성 요청. 보안 기본값은 여기서 강제되고 호출측이 끌 수 없다
    (neosdl-sandbox-dind-remediation.md §5.3). privileged·host 경로 마운트·docker.sock 는 아예 필드가 없다.
    """

    image: str
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    network: str | None = None          # engagement 별 internal 네트워크 이름
    mem_limit: str = "2g"
    nano_cpus: int = 2_000_000_000       # 2.0 CPU
    pids_limit: int = 512
    read_only: bool = True
    timeout_s: int = 600
    # 아래는 항상 고정(강제): privileged=False, cap_drop=ALL, no-new-privileges, 비root, 바인드마운트 없음.


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    logs: str
    timed_out: bool = False


@runtime_checkable
class SandboxRunner(Protocol):
    """격리 경계 백엔드 계약. 구현: DindRunner(phase-2), VmRunner(phase-2.5)."""

    def health(self) -> bool:
        """백엔드(dind/VM)가 응답하는가."""

    def create_network(self, engagement_id: str) -> str:
        """engagement 전용 internal(무egress) 네트워크를 만들고 이름을 반환."""

    def run(self, spec: ContainerSpec) -> RunResult:
        """컨테이너를 보안 기본값으로 실행(강제 가드 적용)하고 결과를 반환."""

    def publish_app(self, spec: ContainerSpec, ready_port: int) -> str:
        """앱을 기동하고 준비될 때까지 기다린 뒤 검증 엔진이 때릴 base_url 을 반환."""

    def destroy(self, engagement_id: str) -> None:
        """engagement 의 컨테이너·네트워크·볼륨·이미지를 모두 정리(실패해도 best-effort)."""
