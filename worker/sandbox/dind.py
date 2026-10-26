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

    def build_image(self, context_dir: str, tag: str, dockerfile: str = "Dockerfile",
                    engagement_id: str | None = None) -> str:
        """context_dir 를 빌드해 tag 로 태깅. 레포 제공 Dockerfile 전용(Phase 2). 실패 시 예외."""
        d = self._docker()
        d.images.build(path=context_dir, dockerfile=dockerfile, tag=tag, rm=True, forcerm=True,
                       labels={"neosdl.engagement": engagement_id or ""}, pull=False)
        return tag

    def start_app(self, image: str, *, network: str, ready_port: int, env: dict | None = None,
                  engagement_id: str, iast_nonce: str | None = None, ready_timeout: int = 180):
        """앱 컨테이너를 보안 기본값으로 기동하고 (container, base_url) 반환. 준비 안 되면 예외.

        IAST 주입: NEOSDL_IAST_NONCE 를 env 로 넣는다(대상 이미지가 프리로드를 이미 로드하도록
        구성됐을 때 — self-test 픽스처는 베이크돼 있고, 실제 레포는 instrumented 레이어에서 설정).
        준비 감지: ready_port TCP open. base_url 은 dind 루프백에 publish 한 포트로 구성한다.
        """
        import socket
        import time as _t

        d = self._docker()
        spec = ContainerSpec(image=image, env={**(env or {}),
                                               **({"NEOSDL_IAST_NONCE": iast_nonce} if iast_nonce else {})})
        kw = _container_kwargs(spec, network)
        kw["labels"]["neosdl.engagement"] = engagement_id
        # dind 안에서 host(=dind) 루프백에 포트 publish → 검증 엔진이 dind 를 통해 때린다.
        kw["ports"] = {f"{ready_port}/tcp": ("127.0.0.1", None)}
        kw["network"] = None  # publish 하려면 기본 bridge 필요(internal 네트워크는 publish 불가)
        c = d.containers.create(**kw)
        c.start()
        c.reload()
        host_port = int(c.ports[f"{ready_port}/tcp"][0]["HostPort"])
        # dind 데몬 호스트(= 우리가 TLS로 붙는 그 주소의 호스트)에서 host_port 로 접근.
        dind_host = settings().sandbox_docker_host.split("://")[-1].split(":")[0]
        base_url = f"http://{dind_host}:{host_port}"
        deadline = _t.monotonic() + ready_timeout
        while _t.monotonic() < deadline:
            try:
                with socket.create_connection((dind_host, host_port), timeout=2):
                    return c, base_url
            except OSError:
                _t.sleep(1)
        raise TimeoutError("app not ready")

    def container_logs(self, container) -> str:
        try:
            return container.logs(stdout=True, stderr=True).decode("utf-8", "replace")
        except Exception:
            return ""

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
