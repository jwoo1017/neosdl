"""compose 파싱(worker/sandbox/compose.py) — 순수 함수라 Docker 없이 전 경로 검증.

웹(표적) 서비스 선택, 컨테이너 포트 추출, env(dict/list), depends_on 기동순서, 백킹서비스 제외.
"""

from __future__ import annotations

from worker.sandbox.compose import parse_compose

APP_DB = """
services:
  backend:
    build: .
    ports:
      - "8080:3000"
    environment:
      DATABASE_URL: postgres://db:5432/app
    depends_on:
      - db
  db:
    image: postgres:16-alpine
    environment:
      - POSTGRES_PASSWORD=secret
      - POSTGRES_DB=app
"""


def test_picks_web_service_and_container_port():
    plan = parse_compose(APP_DB)
    assert plan.ok
    assert plan.web_service == "backend"
    assert plan.web_port == 3000           # host:container 에서 컨테이너 포트(3000)
    names = [s.name for s in plan.services]
    assert names.index("db") < names.index("backend")   # depends_on → db 먼저


def test_backing_service_detected_and_excluded_from_web():
    plan = parse_compose(APP_DB)
    db = next(s for s in plan.services if s.name == "db")
    be = next(s for s in plan.services if s.name == "backend")
    assert db.is_backing and not be.is_backing
    assert db.env["POSTGRES_DB"] == "app"            # list 형식 env 파싱
    assert be.env["DATABASE_URL"].startswith("postgres://")  # dict 형식 env 파싱
    assert be.build_context == "."


def test_long_syntax_ports_and_expose():
    y = """
services:
  web:
    image: myapp
    ports:
      - target: 8000
        published: 9000
    expose:
      - "9100"
"""
    plan = parse_compose(y)
    assert plan.ok and plan.web_service == "web"
    assert plan.web_port == 8000                      # long syntax target
    assert 8000 in parse_compose(y).services[0].ports and 9100 in plan.services[0].ports


def test_build_dict_with_dockerfile():
    y = """
services:
  api:
    build:
      context: ./svc
      dockerfile: Dockerfile.prod
    ports: ["5000"]
"""
    plan = parse_compose(y)
    api = plan.services[0]
    assert api.build_context == "./svc" and api.dockerfile == "Dockerfile.prod"
    assert plan.web_port == 5000


def test_no_web_service_when_only_backing():
    y = """
services:
  db:
    image: postgres:16
  cache:
    image: redis:7
"""
    plan = parse_compose(y)
    assert not plan.ok and plan.reason == "no_web_service"


def test_unparsable_and_empty():
    assert parse_compose("::: not yaml :::").ok is False
    assert parse_compose("version: '3'").reason == "no_services"


def test_web_name_priority_over_frontend():
    """backend 와 frontend 가 모두 있으면 backend(우선순위 높음)를 표적으로."""
    y = """
services:
  frontend:
    build: ./fe
    ports: ["3000"]
  backend:
    build: ./be
    ports: ["8080"]
"""
    plan = parse_compose(y)
    assert plan.web_service == "backend" and plan.web_port == 8080
