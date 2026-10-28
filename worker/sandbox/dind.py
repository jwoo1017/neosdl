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


def _toolbox_kwargs(image: str, network: str | None, engagement_id: str) -> dict:
    """툴박스(보안 도구) 컨테이너 create kwargs — idle 로 띄워 두고 명령마다 exec_run 한다.

    보안 도구(nmap 등)는 raw 소켓 등 네트워크 권한이 필요하고, Kali 류 이미지는 바이너리에
    file capability 가 박혀 있어 no-new-privileges 하에선 execve 가 EPERM 으로 막힌다. 그래서
    툴박스에서만: cap_drop=ALL 위에 설정된 net cap 만 cap_add 하고 no-new-privileges 는 해제한다.
    나머지 가드(privileged=False, 비root 기본, 바인드마운트 없음, read_only+tmpfs, 리소스 상한,
    네트워크 스코프)는 유지. **대상 앱 컨테이너(_container_kwargs)는 이 완화와 무관하게 완전 하드닝.**
    순수 함수(테스트 대상).
    """
    cfg = settings()
    kw = {
        "image": image,
        "command": ["sleep", "infinity"],          # idle — 명령은 exec_run 으로 주입
        "environment": {"HOME": "/tmp"},
        "network": network,
        "privileged": False,                        # 절대 True 안 됨(완화해도 privileged 는 아님)
        "cap_drop": ["ALL"],
        "cap_add": cfg.sandbox_toolbox_cap_list,    # 네트워크 도구에 필요한 cap 만(NET_RAW/NET_ADMIN/…)
        # no-new-privileges 는 툴박스에서 설정하지 않는다(fscap 바이너리 실행 허용).
        "read_only": True,
        "tmpfs": {"/tmp": "size=512m"},
        "working_dir": "/tmp",
        "mem_limit": "2g",
        "memswap_limit": "2g",
        "nano_cpus": 2_000_000_000,
        "pids_limit": 512,
        "network_disabled": False,
        "detach": True,
        "labels": {"neosdl.sandbox": "1", "neosdl.engagement": engagement_id,
                   "neosdl.role": "toolbox"},
    }
    if cfg.sandbox_toolbox_user:                    # 비면 이미지 기본 사용자(root) — Kali 가 root 요구 시
        kw["user"] = cfg.sandbox_toolbox_user
    return kw


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

    def client(self):
        """내부 docker 클라이언트(boot.py 오케스트레이션이 직접 쓴다)."""
        return self._docker()

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
                    engagement_id: str | None = None, *, pull: bool = False) -> str:
        """context_dir 를 빌드해 tag 로 태깅. 실패 시 예외.

        pull=True 는 레시피 합성 빌드용 — 베이스 이미지(node:20 등)를 당겨온다. 빌드는 dind 기본
        브리지(egress 有)에서 수행되고(npm/pip 설치 가능), 런타임 앱은 engagement internal(무egress)
        네트워크로 분리된다. pull=False(기본)는 베이스가 이미 dind 스토어에 있는 레포 Dockerfile 용.
        """
        d = self._docker()
        d.images.build(path=context_dir, dockerfile=dockerfile, tag=tag, rm=True, forcerm=True,
                       labels={"neosdl.engagement": engagement_id or ""}, pull=pull)
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

    def start_toolbox(self, engagement_id: str, *, network: str | None = None, image: str | None = None):
        """보안 도구 툴박스 컨테이너를 idle 로 기동하고 container 반환(명령은 exec_toolbox 로).

        image 기본은 설정 sandbox_toolbox_image. network 는 표적에 닿는 네트워크(로컬 검증은 표적
        컨테이너와 같은 internal 네트워크를 넘겨 egress 없이 contained 표적만 때린다).
        """
        d = self._docker()
        img = image or settings().sandbox_toolbox_image
        c = d.containers.create(**_toolbox_kwargs(img, network, engagement_id))
        c.start()
        return c

    def exec_toolbox(self, container, argv: list[str], *, timeout_s: int = 120) -> RunResult:
        """idle 툴박스 안에서 argv 를 실행하고 출력 캡처. 컨테이너 내 `timeout` 으로 하드 타임아웃.

        argv 는 이미 검증된 명령(app.toolbox.validate_command 통과분)이어야 한다 — 여기서는 셸을
        거치지 않고 exec_run 으로 바로 실행한다(셸 주입 표면 없음).
        """
        wrapped = ["timeout", "--signal=KILL", str(timeout_s), *argv]
        try:
            res = container.exec_run(wrapped, demux=False, workdir="/tmp", environment={"HOME": "/tmp"})
        except Exception as exc:
            return RunResult(exit_code=-1, logs=f"exec 실패: {exc}")
        out = res.output.decode("utf-8", "replace") if isinstance(res.output, (bytes, bytearray)) else str(res.output)
        # timeout 바이너리는 타임아웃 시 124(또는 137=KILL) 로 끝낸다.
        return RunResult(exit_code=int(res.exit_code or 0), logs=out,
                         timed_out=res.exit_code in (124, 137))

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
