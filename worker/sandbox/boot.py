"""engagement 의 대상 앱을 샌드박스(dind)에서 기동하고 (app_client, log_source, teardown,
iast_nonce) 를 돌려준다. self-test(scripts/sbx_selftest.py)에서 검증된 패턴을 실제 레포로 확장:
이미지 빌드 → IAST 파생 이미지 → 백킹서비스 프로비전 → 앱 기동 → 컨테이너 내부 exec 로 readiness·HTTP.

포트 publish·호스트 네트워킹을 쓰지 않고 **컨테이너 내부 exec** 로 때리므로 로컬 Docker·서버 dind
동일하게 동작한다(네트워킹 불변). 실제 Docker 가 필요하므로 서버 e2e 로 검증한다. 빌드 단계는
pip/npm egress(또는 내부 미러)가 필요하다 — 없으면 build 가 실패하고 None 으로 폴백(정적 유지).

phase: Dockerfile/compose 제공 레포 우선. 매니페스트 합성 빌드는 detect 가 run_cmd 를 주면 시도.
"""

from __future__ import annotations

import io
import logging
import secrets
import tarfile
import time
from dataclasses import dataclass
from typing import Callable

from app.config import settings
from app.dynamic import instrumentation
from worker.handlers.clone import pipeline_source_root
from worker.sandbox import detect

log = logging.getLogger("worker.sandbox.boot")

# 백킹서비스 종류 → (allowlist 이미지, 기동 env, 앱에 줄 연결 env 템플릿)
_SERVICES = {
    "postgres": ("postgres:16-alpine",
                 {"POSTGRES_PASSWORD": "sbx", "POSTGRES_USER": "sbx", "POSTGRES_DB": "app"},
                 {"DATABASE_URL": "postgres://sbx:sbx@{host}:5432/app", "PGHOST": "{host}"}),
    "mysql": ("mysql:8",
              {"MYSQL_ROOT_PASSWORD": "sbx", "MYSQL_DATABASE": "app", "MYSQL_USER": "sbx",
               "MYSQL_PASSWORD": "sbx"},
              {"DATABASE_URL": "mysql://sbx:sbx@{host}:3306/app", "DB_HOST": "{host}",
               "MYSQL_HOST": "{host}"}),
    "redis": ("redis:7", {}, {"REDIS_URL": "redis://{host}:6379"}),
}


@dataclass
class SandboxBoot:
    app_client: Callable
    log_source: Callable
    teardown: Callable
    iast_nonce: str


def _iast_layer(plan) -> tuple[str, dict]:
    """(언어별 IAST 프리로드 파일 내용, 파생 이미지가 설정할 ENV). 미지원 언어면 ("", {})."""
    lang = plan.language or ("python" if plan.framework in ("django", "flask", "fastapi") else None)
    if lang == "python" or plan.dockerfile and plan.framework != "javascript":
        return instrumentation.PYTHON_SITECUSTOMIZE, {"PYTHONPATH": "/neosdl_iast"}
    if lang == "javascript":
        return instrumentation.NODE_PRELOAD, {"NODE_OPTIONS": "--require /neosdl_iast/preload.js"}
    return "", {}


def _dockerfile_bytes(context_tar_add, base_image: str, iast_env: dict, iast_fname: str) -> bytes:
    env_lines = "\n".join(f'ENV {k}="{v}"' for k, v in iast_env.items())
    return (f"FROM {base_image}\nCOPY {iast_fname} /neosdl_iast/{iast_fname}\n{env_lines}\n").encode()


def _build_instrumented(client, base_image: str, plan, tag: str) -> str:
    """base_image 위에 IAST 프리로드만 얹은 파생 이미지를 빌드해 tag 반환. 미지원이면 base 그대로."""
    content, iast_env = _iast_layer(plan)
    if not content:
        return base_image
    fname = "preload.js" if "NODE_OPTIONS" in iast_env else "sitecustomize.py"
    df = _dockerfile_bytes(None, base_image, iast_env, fname)
    # 인메모리 tar 빌드 컨텍스트(파생 Dockerfile + IAST 파일).
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in (("Dockerfile", df), (fname, content.encode())):
            ti = tarfile.TarInfo(name); ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    buf.seek(0)
    client.images.build(fileobj=buf, custom_context=True, tag=tag, rm=True, forcerm=True, pull=False)
    return tag


def _exec_http(container, port: int, path: str):
    """컨테이너 내부에서 http://localhost:{port}{path} GET. (status_code, text) 유사 객체 | None.
    python→node→wget 순으로 시도(이미지에 있는 것)."""
    attempts = [
        ["python", "-c", f"import urllib.request;print(urllib.request.urlopen('http://localhost:{port}{path}',timeout=5).read().decode('utf-8','replace'))"],
        ["python3", "-c", f"import urllib.request;print(urllib.request.urlopen('http://localhost:{port}{path}',timeout=5).read().decode('utf-8','replace'))"],
        ["node", "-e", f"require('http').get('http://localhost:{port}{path}',r=>{{let d='';r.on('data',c=>d+=c);r.on('end',()=>process.stdout.write(d))}}).on('error',e=>process.exit(1))"],
        ["wget", "-qO-", f"http://localhost:{port}{path}"],
    ]
    for cmd in attempts:
        try:
            res = container.exec_run(cmd)
            if res.exit_code == 0:
                class _R:
                    status_code = 200
                    text = res.output.decode("utf-8", "replace")
                return _R()
        except Exception:
            continue
    return None


def boot_engagement(engagement: dict) -> SandboxBoot | None:
    scan_id = str(engagement["scan_id"])
    repo_id = str(engagement["repository_id"])
    eid = str(engagement["id"])
    root = pipeline_source_root(scan_id, repo_id)
    plan = detect.detect(root)
    if not plan.ok:
        log.info("engagement %s 부팅 불가(감지 실패: %s)", eid, plan.reason)
        return None
    if not plan.dockerfile:
        # 매니페스트 합성 빌드는 phase-3. 지금은 Dockerfile 제공 레포만.
        log.info("engagement %s: Dockerfile 없음(%s) — 합성 빌드는 phase-3", eid, plan.framework)
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
    client = runner.client()
    nonce = f"neosdl-{eid[:8]}-{secrets.token_hex(3)}"
    port = plan.port or 8000
    net = None
    app = None
    service_containers = []

    try:
        app_img = runner.build_image(str(root), f"neosdl-sbx/app-{eid[:8]}", plan.dockerfile, eid)
        inst_img = _build_instrumented(client, app_img, plan, f"neosdl-sbx/inst-{eid[:8]}")
        net = runner.create_network(eid)

        # 백킹서비스 프로비전(detect 가 지목한 것) — 더미 크리덴셜, engagement 네트워크에만.
        app_env = {"NEOSDL_IAST_NONCE": nonce}
        for svc in plan.required_services:
            if svc not in _SERVICES:
                continue
            image, svc_env, conn_tmpl = _SERVICES[svc]
            host = f"{svc}-{eid[:8]}"
            c = client.containers.run(image, name=host, detach=True, environment=svc_env,
                                      network=net, labels={"neosdl.engagement": eid},
                                      mem_limit="1g", pids_limit=512)
            service_containers.append(c)
            for k, v in conn_tmpl.items():
                app_env.setdefault(k, v.format(host=host))

        # 앱 기동(보안 기본값). 포트 publish 없음 — 내부 exec 로 접근.
        app = client.containers.run(
            inst_img, detach=True, environment=app_env, network=net,
            labels={"neosdl.engagement": eid},
            cap_drop=["ALL"], security_opt=["no-new-privileges:true"], user="1000:1000",
            mem_limit=settings().sandbox_mem_limit, pids_limit=settings().sandbox_pids_limit)

        # readiness: 내부 exec 로 health/루트 포트가 응답할 때까지.
        deadline = time.monotonic() + settings().sandbox_boot_timeout_s
        ready = False
        while time.monotonic() < deadline:
            if _exec_http(app, port, "/") is not None or _exec_http(app, port, "/health") is not None:
                ready = True
                break
            time.sleep(2)
        if not ready:
            log.info("engagement %s 앱 미기동(timeout) — 정적 유지", eid)
            _teardown(runner, eid, app_img, inst_img)
            return None

        def app_client(method, path, param_name, param_in, value):
            from urllib.parse import quote
            q = f"?{param_name}={quote(str(value))}" if param_in in ("query", "path") else ""
            return _exec_http(app, port, f"{path}{q}")

        def log_source(_marker):
            logs = runner.container_logs(app)
            reached = None
            import re as _re
            for m in _re.finditer(r"NEOSDL_IAST reached=(\d)", logs):
                reached = bool(int(m.group(1)))   # 가장 최근 신호
            return {"app_log": logs[-8000:], "iast_reached": reached}

        def teardown():
            _teardown(runner, eid, app_img, inst_img)

        log.info("engagement %s 앱 기동 완료(%s) nonce=%s", eid, plan.framework or "docker", nonce)
        return SandboxBoot(app_client=app_client, log_source=log_source, teardown=teardown, iast_nonce=nonce)
    except Exception:
        log.exception("engagement %s 부팅 실패 — 정적 유지", eid)
        try:
            runner.destroy(eid)
        except Exception:
            pass
        return None


def _teardown(runner, eid: str, *images: str) -> None:
    try:
        runner.destroy(eid)
    except Exception:
        log.warning("engagement %s 컨테이너/네트워크 정리 실패", eid, exc_info=True)
    for img in images:
        try:
            runner.client().images.remove(img, force=True)
        except Exception:
            pass
