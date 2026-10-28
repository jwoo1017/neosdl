"""스택 감지 — 스캔한 소스 트리에서 빌드·기동 방법을 추론한다(결정적 우선).

우선순위: (1) 레포 자체 Dockerfile/compose(최고 성공률, Phase 1/2 기반) → (2) 매니페스트
(package.json/pom.xml/requirements) → (3) LLM 빈칸(Phase 3). 순수 파일시스템 검사라 오프라인
단위 테스트 가능. `StackPlan` 을 돌려주고, 못 정하면 ok=False.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class StackPlan:
    ok: bool
    language: str | None = None
    framework: str | None = None
    dockerfile: str | None = None          # 레포 기준 상대 경로
    compose: str | None = None
    build_cmds: list[str] = field(default_factory=list)
    run_cmd: str | None = None
    port: int | None = None
    required_services: list[str] = field(default_factory=list)
    source: str = "deterministic"
    reason: str | None = None


_SERVICE_IMPORT = {
    "postgres": ("pg", "psycopg", "postgres", "sequelize", "typeorm", "prisma"),
    "mysql": ("mysql2", "mysql", "mariadb"),
    "redis": ("redis", "ioredis"),
    "mongo": ("mongoose", "mongodb"),
}


def _detect_services(text: str) -> list[str]:
    low = text.lower()
    return [svc for svc, keys in _SERVICE_IMPORT.items() if any(k in low for k in keys)]


def detect(root: str | Path) -> StackPlan:
    root = Path(root)
    if not root.exists():
        return StackPlan(ok=False, reason="source_root_missing")

    # (1) 레포 Dockerfile / compose — 최우선.
    dockerfile = next((p for p in (root / "Dockerfile", root / "dockerfile") if p.exists()), None)
    compose = next((root / n for n in ("docker-compose.yml", "docker-compose.yaml",
                                       "compose.yml", "compose.yaml")
                    if (root / n).exists()), None)
    if compose is not None:
        return StackPlan(ok=True, source="deterministic", compose=compose.name,
                         framework="compose", reason=None)
    if dockerfile is not None:
        port = _dockerfile_port(dockerfile.read_text("utf-8", "replace"))
        return StackPlan(ok=True, source="deterministic", dockerfile=dockerfile.name, port=port)

    # (2) 매니페스트 기반.
    pkg = root / "package.json"
    if pkg.exists():
        return _node_plan(pkg)
    if (root / "pom.xml").exists():
        return StackPlan(ok=True, language="java", framework="spring", build_cmds=["mvn -q -DskipTests package"],
                         run_cmd="java -jar target/*.jar", port=8080)
    if (root / "requirements.txt").exists() or (root / "pyproject.toml").exists():
        return _python_plan(root)
    return StackPlan(ok=False, reason="stack_undetermined")


def _dockerfile_port(text: str) -> int | None:
    m = re.search(r"^\s*EXPOSE\s+(\d+)", text, re.I | re.M)
    return int(m.group(1)) if m else None


def _node_plan(pkg: Path) -> StackPlan:
    try:
        data = json.loads(pkg.read_text("utf-8", "replace"))
    except Exception:
        return StackPlan(ok=False, reason="package_json_unparsable")
    scripts = data.get("scripts") or {}
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    fw = next((f for f in ("next", "nestjs", "@nestjs/core", "express", "koa", "fastify") if f in deps), None)
    run = "npm start" if "start" in scripts else ("node ." if data.get("main") else None)
    port = 3000 if fw in ("next", "express", None) else 8080
    return StackPlan(ok=run is not None, language="javascript", framework=fw,
                     build_cmds=["npm ci --no-audit --no-fund"], run_cmd=run, port=port,
                     required_services=_detect_services(json.dumps(deps)),
                     reason=None if run else "no_start_script")


def _python_plan(root: Path) -> StackPlan:
    if (root / "manage.py").exists():
        return StackPlan(ok=True, language="python", framework="django",
                         build_cmds=["pip install -r requirements.txt"],
                         run_cmd="python manage.py runserver 0.0.0.0:8000", port=8000,
                         required_services=["postgres"])
    reqs = (root / "requirements.txt")
    text = reqs.read_text("utf-8", "replace") if reqs.exists() else ""
    fw = "fastapi" if "fastapi" in text.lower() else ("flask" if "flask" in text.lower() else None)
    run = {"fastapi": "uvicorn app.main:app --host 0.0.0.0 --port 8000",
           "flask": "flask run --host 0.0.0.0 --port 8000"}.get(fw)
    return StackPlan(ok=run is not None, language="python", framework=fw,
                     build_cmds=["pip install -r requirements.txt"] if reqs.exists() else ["pip install ."],
                     run_cmd=run, port=8000, required_services=_detect_services(text),
                     reason=None if run else "python_entrypoint_undetermined")
