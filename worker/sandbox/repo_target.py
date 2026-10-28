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
import re
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


def _run_service(client, *, image: str, net: str, aliases: list[str], env: dict, eid: str,
                 command: list | None = None):
    """서비스 컨테이너를 engagement 네트워크에 기동(별칭=compose 서비스명 → 서비스간 DNS).

    하드닝: privileged=False, 바인드마운트/호스트네트워크 없음, 리소스 상한. 임의 레포 앱·DB 가
    실제로 뜨도록 cap_drop/비root 는 강제하지 않는다(dind·internal 네트워크·호스트마운트 없음으로 격리).
    command 는 레시피의 start_command override(이미지 CMD 를 덮음) — None 이면 이미지 기본.
    """
    c = client.containers.create(
        image=image, detach=True, environment=env, command=command,
        labels={"neosdl.engagement": eid, "neosdl.role": "target"},
        mem_limit=settings().sandbox_mem_limit, memswap_limit=settings().sandbox_mem_limit,
        pids_limit=settings().sandbox_pids_limit, privileged=False)
    client.networks.get(net).connect(c, aliases=aliases)
    c.start()
    return c


_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _expand(value, subs: dict) -> str:
    """compose env 값의 `${VAR}`/`${VAR:-기본}`/`$VAR` 를 subs(더미 크리덴셜)로 치환.

    compose 는 원래 .env 로 치환되는데 우리는 그 .env 가 없으므로, 레시피의 단일 더미
    크리덴셜로 backing(mysql) 과 app 의 DB 설정을 일치시킨다. 미지정이면 기본값(없으면 빈값).
    """
    if not isinstance(value, str):
        return value

    def repl(m):
        name = m.group(1) or m.group(3)
        return subs.get(name, m.group(2) or "")

    return _VAR_RE.sub(repl, value)


def _boot_from_recipe(runner, client, root: str, plan, net: str, eid: str, recipe: dict,
                      containers: list):
    """레시피 구동 부팅(추측 없음) — backing(compose/detect) 기동 → app 빌드/기동. (web_container, web_port)."""
    from worker.sandbox import boot_recipe as br

    subs = dict(recipe.get("db_credentials") or {})
    # 1) backing 서비스 — compose 의 백킹서비스(mysql 등)를 ${VAR} 치환해 기동. compose 없으면 detect 추론.
    if plan.compose:
        cplan = compose_mod.load_compose(Path(root) / plan.compose)
        for svc in cplan.services:
            if svc.is_backing and svc.image:
                env = {k: _expand(v, subs) for k, v in (svc.env or {}).items()}
                containers.append(_run_service(client, image=svc.image, net=net,
                                                aliases=[svc.name], env=env, eid=eid))
    else:
        for svc_name in plan.required_services:
            if svc_name in _SERVICES:
                image, svc_env, _ = _SERVICES[svc_name]
                containers.append(_run_service(client, image=image, net=net,
                                                aliases=[svc_name], env=svc_env, eid=eid))

    # 2) app 이미지 — 빌드(dind egress) 또는 prebuilt. base_image/FROM 은 allowlist 검증(방어 심층화).
    if recipe.get("needs_build"):
        base = br.dockerfile_from_image(recipe["dockerfile"]) if recipe.get("dockerfile") \
            else recipe.get("base_image")
        if not br.image_allowed(base):
            raise ValueError(f"base_image_not_allowed: {base}")
        if recipe.get("dockerfile"):
            (Path(root) / "Dockerfile.sbx").write_text(recipe["dockerfile"], encoding="utf-8")
            dfname = "Dockerfile.sbx"
        else:
            dfname = recipe.get("dockerfile_path") or "Dockerfile"
        try:
            app_img = runner.build_image(str(root), f"neosdl-sbx/{eid[:8]}-app", dfname, eid, pull=True)
        finally:
            if recipe.get("dockerfile"):
                try:
                    (Path(root) / "Dockerfile.sbx").unlink()
                except Exception:
                    pass
    else:
        if not br.image_allowed(recipe.get("base_image")):
            raise ValueError(f"base_image_not_allowed: {recipe.get('base_image')}")
        app_img = recipe["base_image"]

    # 3) app 컨테이너 — start_command override + 레시피 env(DB_HOST=backing 별칭)로 기동.
    cmd = recipe.get("start_command")
    command = ["sh", "-c", cmd] if isinstance(cmd, str) and cmd.strip() else None
    app = _run_service(client, image=app_img, net=net, aliases=["app"],
                       env=dict(recipe.get("env") or {}), eid=eid, command=command)
    containers.append(app)
    web_port = recipe.get("app_port")
    return app, int(web_port) if web_port else None


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


def boot_repo_target(engagement: dict, runner, exec_toolbox_factory: Callable, recipe: dict | None = None):
    """engagement 레포를 띄우고 (BootedTarget, None) 반환. 못 띄우면 (None, reason) — reason 은
    UI 리포트에 노출돼 운영자가 "왜 안 떴는지" 바로 본다(조용히 실패 아님).

    recipe(부팅 레시피)를 주면 **결정적 경로**로 띄운다(backing+빌드+기동, 추측 없음) — compose 에
    앱이 없거나 Dockerfile 이 없는 레포도 뜬다. None 이면 기존 자동 감지(Dockerfile/compose) 폴백.

    runner: DindRunner. exec_toolbox_factory(network)->exec_toolbox(argv) : 해당 네트워크에 붙은
    툴박스로 readiness 를 확인하기 위한 콜백(호출측이 툴박스를 그 네트워크에 기동해 넘긴다).
    """
    scan_id, repo_id, eid = engagement.get("scan_id"), engagement["repository_id"], str(engagement["id"])
    if not scan_id:
        log.info("engagement %s: 부팅용 소스 스냅샷(scan_id) 없음", eid)
        return None, "no_source_snapshot"
    root = pipeline_source_root(str(scan_id), str(repo_id))
    plan = detect.detect(root)
    if recipe is None and not plan.ok:
        # 레시피 없이 자동 감지로만 띄우는데 스택을 못 정함 — 명시적 실패(레시피를 주면 이 경로를 우회).
        log.info("engagement %s 부팅 불가(감지: %s)", eid, plan.reason)
        return None, f"detect_failed: {plan.reason}"

    client = runner.client()
    net = runner.create_network(eid)
    containers = []
    try:
        if recipe is not None:
            web_container, web_port = _boot_from_recipe(runner, client, root, plan, net, eid,
                                                        recipe, containers)
        elif plan.compose:
            cplan = compose_mod.load_compose(Path(root) / plan.compose)
            if not cplan.ok:
                log.info("engagement %s compose 파싱 실패(%s)", eid, cplan.reason)
                return None, f"compose_parse_failed: {cplan.reason}"
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
                return None, "no_dockerfile_or_compose"
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
            return None, "no_web_container"
        web_ip = _container_ip(web_container, net)
        if not web_ip:
            log.info("engagement %s: 웹 컨테이너 IP 미할당", eid)
            return None, "no_container_ip"

        def teardown():
            try:
                runner.destroy(eid)
            except Exception:
                log.warning("engagement %s 표적 정리 실패", eid, exc_info=True)

        # readiness: 툴박스를 이 네트워크에 띄워 web_ip:web_port 가 열릴 때까지 대기.
        exec_toolbox = exec_toolbox_factory(net)
        ready = _wait_ready(exec_toolbox, web_ip, web_port, settings().sandbox_boot_timeout_s)
        log.info("engagement %s 표적 기동 %s ip=%s port=%s ready=%s",
                 eid, plan.compose or plan.dockerfile, web_ip, web_port, ready)
        if not ready and web_port:
            # 포트를 알고도 기동 타임아웃 → 빈 스캔으로 혼란 주지 말고 명시적 실패(정리 포함).
            log.info("engagement %s 표적 미기동(timeout) %s:%s", eid, web_ip, web_port)
            teardown()
            return None, f"boot_timeout: {web_ip}:{web_port} 미응답(빌드 실패 또는 기동 지연)"
        return BootedTarget(web_ip=web_ip, web_port=web_port, network=net, teardown=teardown), None
    except Exception as exc:
        log.exception("engagement %s 표적 부팅 실패 — 정리", eid)
        try:
            runner.destroy(eid)
        except Exception:
            pass
        return None, f"boot_exception: {type(exc).__name__}: {str(exc)[:300]}"
