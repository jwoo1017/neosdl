"""부팅 레시피 — 레포 소스에서 "표적 앱을 어떻게 띄울지"를 결정적으로 뽑고(빈칸만 LLM) 산출한다.

왜: compose 가 DB 류만 담고 앱 서비스·Dockerfile 이 없는 레포(예: exchange-sample-backend)는
자동 감지로 못 띄운다(`no_web_service`). 그래서 사람이 모달에서 확인·수정할 **레시피**를
만든다 — 베이스 이미지/시작 명령/포트/런타임 env/필요 시 Dockerfile 초안. 이 모듈은 순수(파일시스템
읽기만, Docker·네트워크 무의존)라 오프라인 단위 테스트가 가능하다. 실제 빌드·기동은
worker/sandbox/repo_target.py 가 이 레시피를 받아 dind 에서 수행한다.

결정적 우선: detect.py(StackPlan)+compose.py 로 백킹서비스·포트·시작명령을 먼저 채우고, 애매할
때만 LLM(`llm_generate` 콜백, BootRecipe 스키마)으로 빈칸을 메운다. LLM 이 없어도(=llm_generate
None) node/python/java 는 결정적으로 완성된다. base_image·Dockerfile FROM 은 allowlist 로 검증한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import settings
from worker.sandbox import compose as compose_mod
from worker.sandbox import detect

# 언어별 기본 베이스 이미지(sandbox_allowed_images 와 레포 접두가 일치해야 한다).
_BASE_IMAGE = {
    "javascript": "node:20-bookworm-slim",
    "python": "python:3.12-slim",
    "java": "eclipse-temurin:21-jdk",
}
# 관례적 Node 엔트리 파일 우선순위(start 스크립트가 없을 때).
_NODE_ENTRIES = ("server.js", "app.js", "index.js", "main.js")


def image_allowed(image: str | None) -> bool:
    """base_image 가 sandbox_allowed_images 와 **레포 수준**으로 일치하는가(태그는 유연).

    allowlist 는 repo(node/python/...)를 통제하고 태그는 사내 재량으로 둔다 — `node:20` 은
    allowlist `node:20-bookworm-slim` 과 같은 repo 라 허용. fail-closed: 미상/불일치면 False.
    """
    if not image:
        return False
    repo = image.split("@")[0].rsplit(":", 1)[0]
    for entry in settings().sandbox_allowed_image_list:
        if image == entry or repo == entry.split("@")[0].rsplit(":", 1)[0]:
            return True
    return False


def dockerfile_from_image(text: str) -> str | None:
    """Dockerfile 텍스트의 마지막 FROM 이미지(멀티스테이지면 최종 런타임 베이스)를 뽑는다."""
    froms = re.findall(r"^\s*FROM\s+(\S+)", text, re.I | re.M)
    return froms[-1] if froms else None


def _parse_env_file(path: Path) -> dict[str, str]:
    """.env / .env.example → dict(주석·빈줄·export 접두 무시, 따옴표 벗김)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:]
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _app_port(root: Path, plan: detect.StackPlan) -> int:
    """표적 웹 포트 — .env(.example) PORT → Dockerfile EXPOSE/detect → 언어 기본."""
    env = {**_parse_env_file(root / ".env.example"), **_parse_env_file(root / ".env")}
    for key in ("PORT", "APP_PORT", "HTTP_PORT", "SERVER_PORT"):
        v = env.get(key)
        if v and v.isdigit():
            return int(v)
    if plan.port:
        return plan.port
    return 8000 if plan.language == "python" else 3000


def _node_start(root: Path, pkg: dict) -> str:
    scripts = pkg.get("scripts") or {}
    if "start" in scripts:
        return "npm start"
    for entry in _NODE_ENTRIES:
        if (root / entry).exists():
            return f"node {entry}"
    main = pkg.get("main")
    return f"node {main}" if main else "node ."


def _dockerfile_node(base: str, port: int, start: str) -> str:
    # npm ci 는 lockfile 을 요구하므로 install 로(없는 레포도 뜨게). 빌드는 dind egress 로 수행.
    return (
        f"FROM {base}\n"
        "WORKDIR /app\n"
        "COPY package*.json ./\n"
        "RUN npm install --no-audit --no-fund\n"
        "COPY . .\n"
        f"EXPOSE {port}\n"
        f'CMD ["sh","-c","{start}"]\n'
    )


def _dockerfile_python(base: str, port: int, start: str, has_reqs: bool) -> str:
    install = "RUN pip install --no-cache-dir -r requirements.txt\n" if has_reqs else \
              "RUN pip install --no-cache-dir .\n"
    return (
        f"FROM {base}\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        f"{install}"
        f"EXPOSE {port}\n"
        f'CMD ["sh","-c","{start}"]\n'
    )


def _dummy_db_credentials(host_alias: str | None) -> dict[str, str]:
    """backing(mysql/postgres) ${VAR} 치환과 app env 를 **동일한 더미값**으로 묶는다.

    compose 의 `${DB_PASSWORD}` 류는 원래 .env 로 치환되는데 우리는 그 .env 가 없으므로, 여기서
    정한 단일 더미 크리덴셜로 backing·app 양쪽을 일치시켜 앱이 DB 에 실제로 붙게 한다.
    """
    creds = {
        "DB_NAME": "neosdl_sbx", "DB_USER": "neosdl", "DB_PASSWORD": "neosdl_sbx_pw",
        "DB_ROOT_PASSWORD": "neosdl_sbx_root", "DB_PORT": "3306",
    }
    if host_alias:
        creds["DB_HOST"] = host_alias
    return creds


def _backing_alias(root: Path, plan: detect.StackPlan) -> str | None:
    """app 의 DB_HOST 가 가리킬 backing 서비스 별칭 — compose 의 백킹서비스명 우선."""
    if plan.compose:
        cplan = compose_mod.load_compose(root / plan.compose)
        for svc in cplan.services:
            if svc.is_backing:
                return svc.name
    return plan.required_services[0] if plan.required_services else None


def _app_env(root: Path, db_creds: dict[str, str], app_port: int) -> dict[str, str]:
    """런타임 app env — .env.example 을 더미로 채우고 DB_* 통일 + PORT 고정."""
    env: dict[str, str] = {}
    for k, v in _parse_env_file(root / ".env.example").items():
        env[k] = v or "sbx"
    env.update(db_creds)              # DB_* 통일(DB_HOST=backing 별칭 포함)
    env["PORT"] = str(app_port)
    env.setdefault("NODE_ENV", "production")
    return env


def _from_dockerfile(root: Path, plan: detect.StackPlan) -> dict:
    """레포가 Dockerfile 을 제공하는 경우 — 그걸 그대로 쓰되 FROM/포트만 뽑는다."""
    text = (root / plan.dockerfile).read_text("utf-8", "replace")
    base = dockerfile_from_image(text) or "unknown"
    return {"base_image": base, "needs_build": True, "dockerfile": None,
            "dockerfile_path": plan.dockerfile, "start_command": None,
            "app_port": _app_port(root, plan), "confidence": 0.9,
            "notes": "레포 제공 Dockerfile 사용"}


def _from_manifest(root: Path, plan: detect.StackPlan) -> dict | None:
    """매니페스트(package.json/requirements)로 결정적 레시피를 만든다. 모르면 None."""
    port = _app_port(root, plan)
    if (root / "package.json").exists():
        try:
            pkg = json.loads((root / "package.json").read_text("utf-8", "replace"))
        except Exception:
            pkg = {}
        base = _BASE_IMAGE["javascript"]
        start = _node_start(root, pkg)
        return {"base_image": base, "needs_build": True,
                "dockerfile": _dockerfile_node(base, port, start), "dockerfile_path": None,
                "start_command": start, "app_port": port, "confidence": 0.8,
                "notes": "package.json 기반 합성 Dockerfile(node)"}
    if plan.language == "python" and plan.run_cmd:
        base = _BASE_IMAGE["python"]
        has_reqs = (root / "requirements.txt").exists()
        return {"base_image": base, "needs_build": True,
                "dockerfile": _dockerfile_python(base, port, plan.run_cmd, has_reqs),
                "dockerfile_path": None, "start_command": plan.run_cmd, "app_port": port,
                "confidence": 0.7, "notes": "requirements/매니페스트 기반 합성 Dockerfile(python)"}
    return None


def _llm_fill(root: Path, plan: detect.StackPlan, llm_generate) -> dict | None:
    """결정적으로 못 정했을 때만 LLM 으로 레시피를 제안받는다(BootRecipe 스키마, 사람이 확인)."""
    from app.llm_schemas import BootRecipe

    readme = ""
    for name in ("README.md", "README", "readme.md", "README.rst"):
        p = root / name
        if p.exists():
            readme = p.read_text("utf-8", "replace")[:4000]
            break
    pkg = (root / "package.json")
    pkg_text = pkg.read_text("utf-8", "replace")[:2000] if pkg.exists() else ""
    compose_text = (root / plan.compose).read_text("utf-8", "replace")[:2000] if plan.compose else ""
    allowed = ", ".join(settings().sandbox_allowed_image_list)
    prompt = (
        "다음 레포를 샌드박스에 띄우기 위한 부팅 레시피를 BootRecipe JSON 으로 제안하라. "
        f"base_image 는 반드시 다음 중 하나의 repo 여야 한다: {allowed}. "
        "레포에 Dockerfile 이 없으면 needs_build=true 와 dockerfile(초안)을 채워라.\n\n"
        f"[README]\n{readme}\n\n[package.json]\n{pkg_text}\n\n[compose]\n{compose_text}\n"
    )
    try:
        raw = llm_generate(prompt)
        data = BootRecipe.model_validate_json(raw).model_dump()
    except Exception:
        return None
    if not image_allowed(data.get("base_image")):
        return None
    if data.get("dockerfile") and not image_allowed(dockerfile_from_image(data["dockerfile"])):
        return None
    return data


def draft_recipe(root: str | Path, *, llm_generate=None) -> dict:
    """레포 루트 → 부팅 레시피 dict(BootRecipe 형상). 결정적 우선, 빈칸만 LLM, 최후엔 저신뢰 기본.

    항상 dict 를 반환한다(조용히 실패 없음) — confidence/notes 로 신뢰도를 전한다. 모달이 이걸
    채워 사람이 확인·수정한다.
    """
    root = Path(root)
    plan = detect.detect(root)
    host_alias = _backing_alias(root, plan)
    db_creds = _dummy_db_credentials(host_alias)

    recipe = None
    if plan.ok and plan.dockerfile:
        recipe = _from_dockerfile(root, plan)
    if recipe is None:
        recipe = _from_manifest(root, plan)
    if recipe is None and llm_generate is not None:
        recipe = _llm_fill(root, plan, llm_generate)
    if recipe is None:
        # 결정적으로도 LLM 으로도 못 정함 — 저신뢰 기본(사람이 모달에서 전부 채우도록).
        recipe = {"base_image": _BASE_IMAGE.get(plan.language or "", ""), "needs_build": True,
                  "dockerfile": None, "dockerfile_path": plan.dockerfile,
                  "start_command": plan.run_cmd, "app_port": _app_port(root, plan),
                  "confidence": 0.0, "notes": f"스택 미상({plan.reason or 'unknown'}) — 사람이 레시피를 채워야 함"}

    recipe["db_credentials"] = db_creds
    recipe["env"] = _app_env(root, db_creds, int(recipe["app_port"]))
    return recipe
