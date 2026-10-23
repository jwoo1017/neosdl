"""DindRunner — docker SDK 로 dind(Phase 1.5) 위에서 중첩 컨테이너를 실행하는 SandboxRunner 구현.

보안 기본값을 **여기서 강제**한다(호출측이 끌 수 없음, neosdl-sandbox-dind-remediation.md §5.3):
privileged=False, cap_drop=ALL, no-new-privileges, 비root, 바인드마운트 없음, 리소스 상한,
engagement 별 internal(무egress) 네트워크. dind 주소·cert 는 설정값에서만 온다(하드코딩 금지).

실제 Docker 가 필요하므로 서버(dind) 또는 로컬 Docker 에서만 동작한다 — 단위 테스트는
`_container_kwargs`(순수 변환)만 검증하고, 네트워크/실행은 e2e(서버)에서 확인한다.
"""

from __future__ import annotations

import logging

from app.config import settings
from worker.sandbox import ContainerSpec, RunResult

log = logging.getLogger("worker.sandbox.dind")


def _container_kwargs(spec: ContainerSpec, network: str | None) -> dict:
    """ContainerSpec → docker create kwargs(보안 기본값 강제). 순수 함수(테스트 대상)."""
    return {
        "image": spec.image,
        "command": spec.command or None,
        "environment": dict(spec.env),
        "network": network or spec.network,
        "privileged": False,                       # 절대 True 안 됨
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "user": "1000:1000",                       # 비root
        "read_only": spec.read_only,
        "tmpfs": {"/tmp": "size=256m"} if spec.read_only else None,
        "mem_limit": spec.mem_limit,
        "memswap_limit": spec.mem_limit,           # swap 금지(= mem_limit)
        "nano_cpus": spec.nano_cpus,
        "pids_limit": spec.pids_limit,
        "network_disabled": False,
        "detach": True,
        "labels": {"neosdl.sandbox": "1"},
        # 바인드마운트·볼륨·privileged·docker.sock 는 의도적으로 넣지 않는다.
    }


class DindRunner:
    """SandboxRunner 구현. 설정의 dind host/cert 로 접속."""

    def __init__(self) -> None:
        self._client = None

    def _docker(self):
        if self._client is None:
            import docker
            from docker.tls import TLSConfig
            cfg = settings()
            tls = None
            if cfg.sandbox_docker_tls_cert_path:
                p = cfg.sandbox_docker_tls_cert_path.rstrip("/")
                tls = TLSConfig(client_cert=(f"{p}/cert.pem", f"{p}/key.pem"),
                                ca_cert=f"{p}/ca.pem", verify=True)
            self._client = docker.DockerClient(base_url=cfg.sandbox_docker_host, tls=tls, timeout=30)
        return self._client

    def health(self) -> bool:
        try:
            return bool(self._docker().ping())
        except Exception:
            log.warning("dind ping 실패", exc_info=True)
            return False

    def create_network(self, engagement_id: str) -> str:
        name = f"neosdl-sbx-net-{engagement_id}"
        self._docker().networks.create(name, driver="bridge", internal=True,
                                       labels={"neosdl.engagement": engagement_id})
        return name

    def run(self, spec: ContainerSpec, network: str | None = None) -> RunResult:
        d = self._docker()
        c = d.containers.create(**_container_kwargs(spec, network))
        try:
            c.start()
            res = c.wait(timeout=spec.timeout_s)
            logs = c.logs(stdout=True, stderr=True).decode("utf-8", "replace")
            return RunResult(exit_code=int(res.get("StatusCode", -1)), logs=logs)
        finally:
            try:
                c.remove(force=True)
            except Exception:
                pass

    def destroy(self, engagement_id: str) -> None:
        d = self._docker()
        for c in d.containers.list(all=True, filters={"label": f"neosdl.engagement={engagement_id}"}):
            try:
                c.remove(force=True)
            except Exception:
                pass
        for n in d.networks.list(filters={"label": f"neosdl.engagement={engagement_id}"}):
            try:
                n.remove()
            except Exception:
                pass
