"""docker-compose 파일을 부팅 계획으로 파싱한다 — 순수 함수(파일시스템·Docker 무의존, 오프라인 테스트).

compose 를 **그대로 `docker compose up` 하지 않는다**(호스트 바인드마운트·포트 publish·임의 옵션은
보안상 위험). 대신 서비스 정의에서 필요한 것만 뽑아 worker/sandbox 의 하드닝된 SDK 경로로 재구성한다:
각 서비스의 image/build/env/의존성/컨테이너 포트. 웹(표적) 서비스는 휴리스틱으로 고른다.

블랙박스 점검 목적이므로 IAST 계측·소스 bind 는 쓰지 않고, 빌드 이미지를 engagement 네트워크에
올린 뒤 **웹 서비스 컨테이너 IP** 를 표적으로 삼는다(사람이 IP 를 볼 필요 없음).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 백킹서비스로 인식하는 이미지 접두(이게 웹 표적이 되면 안 됨).
_BACKING_IMAGE_PREFIX = (
    "postgres", "mysql", "mariadb", "redis", "mongo", "memcached", "rabbitmq",
    "elasticsearch", "opensearch", "kafka", "zookeeper", "minio", "nginx",
    "traefik", "haproxy", "clickhouse", "cassandra", "etcd", "consul", "vault",
)
# 웹 표적 서비스 이름 우선순위(높을수록 먼저).
_WEB_NAME_PRIORITY = ("backend", "api", "app", "web", "server", "service", "www", "frontend")
# 웹으로 그럴듯한 컨테이너 포트(앞에 있을수록 우선).
_WEB_PORTS = (80, 8080, 8000, 3000, 5000, 4000, 8888, 9000)


@dataclass(frozen=True)
class ComposeService:
    name: str
    image: str | None = None
    build_context: str | None = None     # compose 파일 기준 상대 경로(빌드 디렉터리)
    dockerfile: str | None = None
    env: dict = field(default_factory=dict)
    ports: list = field(default_factory=list)      # 컨테이너 포트(정수)
    depends_on: list = field(default_factory=list)

    @property
    def is_backing(self) -> bool:
        img = (self.image or "").lower().split("/")[-1]
        return self.build_context is None and any(img.startswith(p) for p in _BACKING_IMAGE_PREFIX)


@dataclass(frozen=True)
class ComposePlan:
    ok: bool
    services: list = field(default_factory=list)   # 기동 순서(의존성 먼저)로 정렬됨
    web_service: str | None = None
    web_port: int | None = None
    reason: str | None = None


def _parse_env(raw) -> dict:
    """compose environment(dict 또는 ["K=V",...]) → dict. 비밀값은 그대로(샌드박스 더미 전제)."""
    if isinstance(raw, dict):
        return {str(k): "" if v is None else str(v) for k, v in raw.items()}
    out = {}
    for item in raw or []:
        if isinstance(item, str) and "=" in item:
            k, _, v = item.partition("=")
            out[k.strip()] = v.strip()
    return out


def _parse_container_port(entry) -> int | None:
    """ports 한 항목에서 컨테이너 포트를 뽑는다. 'host:container', 'ip:host:container',
    'container', '3000/tcp', 또는 long syntax({target: ...}) 지원."""
    if isinstance(entry, dict):
        t = entry.get("target")
        return int(t) if t is not None and str(t).isdigit() else None
    s = str(entry)
    last = s.split(":")[-1]          # 컨테이너 포트는 항상 마지막
    last = last.split("/")[0]        # /tcp /udp 제거
    return int(last) if last.isdigit() else None


def _collect_ports(svc: dict) -> list:
    ports = []
    for entry in (svc.get("ports") or []):
        p = _parse_container_port(entry)
        if p and p not in ports:
            ports.append(p)
    for entry in (svc.get("expose") or []):
        s = str(entry).split("/")[0]
        if s.isdigit() and int(s) not in ports:
            ports.append(int(s))
    return ports


def _parse_depends(raw) -> list:
    if isinstance(raw, dict):
        return list(raw.keys())
    return list(raw or [])


def _build_of(svc: dict) -> tuple[str | None, str | None]:
    """build 섹션 → (context, dockerfile). 문자열이면 context 만."""
    b = svc.get("build")
    if b is None:
        return None, None
    if isinstance(b, str):
        return b, None
    return b.get("context", "."), b.get("dockerfile")


def _topo_order(services: list) -> list:
    """depends_on 을 존중한 기동 순서(간단 위상정렬, 사이클은 원래 순서 폴백)."""
    by_name = {s.name: s for s in services}
    ordered, visiting, done = [], set(), set()

    def visit(name):
        if name in done or name not in by_name:
            return
        if name in visiting:       # 사이클 — 더 파고들지 않음
            return
        visiting.add(name)
        for dep in by_name[name].depends_on:
            visit(dep)
        visiting.discard(name)
        done.add(name)
        ordered.append(by_name[name])

    for s in services:
        visit(s.name)
    return ordered


def _pick_web(services: list) -> tuple[str | None, int | None]:
    """표적(웹) 서비스와 포트를 고른다. 백킹서비스 제외 → 이름 우선순위 → 빌드 보유 → 웹 포트."""
    cands = [s for s in services if not s.is_backing]
    if not cands:
        return None, None

    def score(s: ComposeService) -> tuple:
        name = s.name.lower()
        name_rank = next((len(_WEB_NAME_PRIORITY) - i for i, n in enumerate(_WEB_NAME_PRIORITY)
                          if n in name), 0)
        has_build = 1 if s.build_context else 0
        has_port = 1 if s.ports else 0
        return (name_rank, has_build, has_port)

    best = max(cands, key=score)
    port = _pick_web_port(best.ports)
    return best.name, port


def _pick_web_port(ports: list) -> int | None:
    for wp in _WEB_PORTS:
        if wp in ports:
            return wp
    return ports[0] if ports else None


def parse_compose(text: str) -> ComposePlan:
    """compose YAML 문자열 → ComposePlan. 파싱 실패·서비스 없음·웹 서비스 없음이면 ok=False."""
    try:
        doc = yaml.safe_load(text)
    except Exception as exc:
        return ComposePlan(ok=False, reason=f"compose_unparsable: {exc}")
    if not isinstance(doc, dict) or not isinstance(doc.get("services"), dict):
        return ComposePlan(ok=False, reason="no_services")

    services = []
    for name, svc in doc["services"].items():
        if not isinstance(svc, dict):
            continue
        ctx, dockerfile = _build_of(svc)
        services.append(ComposeService(
            name=str(name), image=svc.get("image"), build_context=ctx, dockerfile=dockerfile,
            env=_parse_env(svc.get("environment")), ports=_collect_ports(svc),
            depends_on=_parse_depends(svc.get("depends_on"))))
    if not services:
        return ComposePlan(ok=False, reason="no_services")

    web, port = _pick_web(services)
    if web is None:
        return ComposePlan(ok=False, reason="no_web_service", services=services)
    return ComposePlan(ok=True, services=_topo_order(services), web_service=web, web_port=port)


def load_compose(path: str | Path) -> ComposePlan:
    p = Path(path)
    if not p.exists():
        return ComposePlan(ok=False, reason="compose_missing")
    return parse_compose(p.read_text("utf-8", "replace"))
