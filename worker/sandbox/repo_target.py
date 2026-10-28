"""레포(Dockerfile/compose)를 샌드박스에 띄워 **블랙박스 점검의 표적**으로 만든다.

boot.py(화이트박스 IAST, 내부 exec)와 달리 여기서는 앱을 engagement 네트워크에 올리고 그 **컨테이너
IP** 를 돌려준다 — 같은 네트워크에 붙은 툴박스(nmap/sqlmap)가 그 IP 를 때린다. 그래서 운영자는
표적 IP 를 조회하거나 .env allowlist 에 등록할 필요가 없다(표적은 self-boot → trusted).

compose 는 그대로 `up` 하지 않고 worker/sandbox/compose.py 로 파싱해 하드닝된 SDK 경로로 재구성한다.
Dockerfile 단일 레포는 detect 의 백킹서비스 추론을 재사용한다. 실제 Docker 가 필요하므로 서버 e2e 로
검증한다(순수 파싱은 compose.py 가 오프라인 테스트). 부팅 실패는 조용히 None 폴백.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.config import settings
from worker.handlers.clone import pipeline_source_root
from worker.sandbox import compose as compose_mod
from worker.sandbox import detect
from worker.sandbox.boot import _SERVICES

log = logging.getLogger("worker.sandbox.repo_target")


@dataclass
class BootedTarget:
    web_ip: str
    web_port: int | None
    network: str
    teardown: Callable


def _container_ip(container, network: str) -> str | None:
    container.reload()
    nets = (container.attrs.get("NetworkSettings") or {}).get("Networks") or {}
    info = nets.get(network) or {}
    return info.get("IPAddress") or None


def _run_service(client, *, image: str, net: str, aliases: list[str], env: dict, eid: str):
    """서비스 컨테이너를 engagement 네트워크에 기동(별칭=compose 서비스명 → 서비스간 DNS).

    하드닝: privileged=False, 바인드마운트/호스트네트워크 없음, 리소스 상한. 임의 레포 앱·DB 가
    실제로 뜨도록 cap_drop/비root 는 강제하지 않는다(dind·internal 네트워크·호스트마운트 없음으로 격리).
    """
    c = client.containers.create(
        image=image, detach=True, environment=env,
        labels={"neosdl.engagement": eid, "neosdl.role": "target"},
        mem_limit=settings().sandbox_mem_limit, memswap_limit=settings().sandbox_mem_limit,
        pids_limit=settings().sandbox_pids_limit, privileged=False)
    client.networks.get(net).connect(c, aliases=aliases)
    c.start()
    return c


def _wait_ready(exec_toolbox, web_ip: str, web_port: int | None, timeout_s: int) -> bool:
    """툴박스에서 web_ip:web_port 가 열릴 때까지 폴링(nc -z). 포트 미상이면 대기 생략(nmap 이 찾음)."""
    if not web_port:
        return True
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        res = exec_toolbox(["nc", "-z", "-w", "2", web_ip, str(web_port)])
        if getattr(res, "exit_code", 1) == 0:
            return True
        time.sleep(2)
    return False


def boot_repo_target(engagement: dict, runner, exec_toolbox_factory: Callable) -> BootedTarget | None:
    """engagement 레포를 띄우고 BootedTarget 반환. 못 띄우면 None(정적/빈 결과 폴백).

    runner: DindRunner. exec_toolbox_factory(network)->exec_toolbox(argv) : 해당 네트워크에 붙은
    툴박스로 readiness 를 확인하기 위한 콜백(호출측이 툴박스를 그 네트워크에 기동해 넘긴다).
    """
    scan_id, repo_id, eid = engagement.get("scan_id"), engagement["repository_id"], str(engagement["id"])
    if not scan_id:
        log.info("engagement %s: 부팅용 소스 스냅샷(scan_id) 없음", eid)
        return None
    root = pipeline_source_root(str(scan_id), str(repo_id))
    plan = detect.detect(root)
    if not plan.ok:
        log.info("engagement %s 부팅 불가(감지: %s)", eid, plan.reason)
        return None

    client = runner.client()
    net = runner.create_network(eid)
    containers = []
    try:
        if plan.compose:
            cplan = compose_mod.load_compose(Path(root) / plan.compose)
            if not cplan.ok:
                log.info("engagement %s compose 파싱 실패(%s)", eid, cplan.reason)
                return None
            web_container = None
            for svc in cplan.services:            # 의존성 순서(백킹 먼저)
                if svc.build_context:
                    ctx = str((Path(root) / svc.build_context).resolve())
                    image = runner.build_image(ctx, f"neosdl-sbx/{eid[:8]}-{svc.name}",
                                               svc.dockerfile or "Dockerfile", eid)
                elif svc.image:
                    image = svc.image
                else:
                    continue                      # image 도 build 도 없으면 건너뜀
                c = _run_service(client, image=image, net=net, aliases=[svc.name], env=svc.env, eid=eid)
                containers.append(c)
                if svc.name == cplan.web_service:
                    web_container = c
            web_port = cplan.web_port
        else:
            # 단일 Dockerfile(또는 매니페스트 run_cmd 없는 Dockerfile 레포). 백킹은 detect 추론.
            if not plan.dockerfile:
                log.info("engagement %s: compose/Dockerfile 없음 — 합성 부팅은 미지원", eid)
                return None
            app_env: dict = {}
            for svc_name in plan.required_services:
                if svc_name not in _SERVICES:
                    continue
                image, svc_env, conn_tmpl = _SERVICES[svc_name]
                c = _run_service(client, image=image, net=net, aliases=[svc_name], env=svc_env, eid=eid)
                containers.append(c)
                for k, v in conn_tmpl.items():
                    app_env.setdefault(k, v.format(host=svc_name))
            app_img = runner.build_image(str(root), f"neosdl-sbx/{eid[:8]}-app", plan.dockerfile, eid)
            web_container = _run_service(client, image=app_img, net=net, aliases=["app"], env=app_env, eid=eid)
            containers.append(web_container)
            web_port = plan.port

        if web_container is None:
            log.info("engagement %s: 웹 서비스 컨테이너를 못 정함", eid)
            return None
        web_ip = _container_ip(web_container, net)
        if not web_ip:
            log.info("engagement %s: 웹 컨테이너 IP 미할당", eid)
            return None

        def teardown():
            try:
                runner.destroy(eid)
            except Exception:
                log.warning("engagement %s 표적 정리 실패", eid, exc_info=True)

        # readiness: 툴박스를 이 네트워크에 띄워 web_ip:web_port 가 열릴 때까지 대기.
        exec_toolbox = exec_toolbox_factory(net)
        ready = _wait_ready(exec_toolbox, web_ip, web_port, settings().sandbox_boot_timeout_s)
        if not ready:
            log.info("engagement %s 표적 미기동(timeout) %s:%s", eid, web_ip, web_port)
        log.info("engagement %s 표적 기동 %s ip=%s port=%s ready=%s",
                 eid, plan.compose or plan.dockerfile, web_ip, web_port, ready)
        return BootedTarget(web_ip=web_ip, web_port=web_port, network=net, teardown=teardown)
    except Exception:
        log.exception("engagement %s 표적 부팅 실패 — 정리", eid)
        try:
            runner.destroy(eid)
        except Exception:
            pass
        return None
