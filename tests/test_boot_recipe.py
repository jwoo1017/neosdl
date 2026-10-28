"""부팅 레시피 결정적 추출 — Docker·LLM 없이 순수 파일시스템만으로 검증.

실측 교훈(exchange-sample-backend): compose 가 mysql 만 담고 앱 서비스·Dockerfile 이 없어서
자동 감지가 `no_web_service` 로 실패했다. 레시피는 그 레포를 **결정적으로** 띄울 수 있어야 한다:
node 베이스 + `node server.js` + PORT 4000 + mysql 백킹 + DB env 통일.
"""

from __future__ import annotations

import json

from worker.sandbox import boot_recipe as br
from worker.sandbox import repo_target


_COMPOSE = """\
version: "3.8"
services:
    mysql:
        image: mysql:8.4
        ports: ["3306:3306"]
        environment:
            MYSQL_ROOT_PASSWORD: ${DB_ROOT_PASSWORD}
            MYSQL_DATABASE: ${DB_NAME}
            MYSQL_USER: ${DB_USER}
            MYSQL_PASSWORD: ${DB_PASSWORD}
volumes:
    mysql_data:
"""

_PKG = {
    "name": "exchange-sample-backend", "main": "index.js", "type": "module",
    "scripts": {"dev": "node --watch server.js", "prod": "node server.js"},
    "dependencies": {"express": "^5.2.1", "mysql2": "^3.22.5", "sequelize": "^6.37.8"},
}

_ENV_EXAMPLE = (
    "PORT=4000\nDB_HOST=localhost\nDB_PORT=3306\nDB_NAME=exchange\nDB_USER=root\nDB_PASSWORD=secret\n"
    # 앱 전용 DB 네임스페이스(실제 exchange-sample-backend 가 쓰는 solfin 커넥션) — localhost 로 박혀 있음
    "SOLFIN_DB_HOST=127.0.0.1\nSOLFIN_DB_NAME=finble\nSOLFIN_DB_USER=finble\nSOLFIN_DB_PASSWORD=change-me\n")


def _exchange_like(tmp_path):
    (tmp_path / "docker-compose.yaml").write_text(_COMPOSE, encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps(_PKG), encoding="utf-8")
    (tmp_path / ".env.example").write_text(_ENV_EXAMPLE, encoding="utf-8")
    (tmp_path / "server.js").write_text("import express from 'express'\n", encoding="utf-8")
    return tmp_path


def test_draft_recipe_exchange_backend_deterministic(tmp_path):
    """compose=mysql만 + Dockerfile 없음 + node 앱 → 결정적으로 node 합성 레시피(LLM 없이)."""
    recipe = br.draft_recipe(_exchange_like(tmp_path))   # llm_generate 없음 → 결정적 경로만

    assert recipe["needs_build"] is True
    assert recipe["app_port"] == 4000                       # .env.example PORT 에서 결정적으로
    assert recipe["start_command"] == "node server.js"      # 관례 엔트리(server.js 존재)
    assert recipe["base_image"] == "node:20-bookworm-slim"  # 언어 기본(allowlist repo 일치)
    assert br.image_allowed(recipe["base_image"])            # allowlist 통과

    df = recipe["dockerfile"]
    assert df and "FROM node:20-bookworm-slim" in df and "EXPOSE 4000" in df
    assert br.image_allowed(br.dockerfile_from_image(df))    # 합성 Dockerfile FROM 도 allowlist


def test_draft_recipe_wires_db_env_to_backing_alias(tmp_path):
    """app env 의 DB_HOST 가 compose 백킹서비스명(mysql)으로, DB 크리덴셜이 통일되는지."""
    recipe = br.draft_recipe(_exchange_like(tmp_path))

    assert recipe["env"]["DB_HOST"] == "mysql"               # 백킹 서비스 별칭(IP 조회 불필요)
    assert recipe["env"]["PORT"] == "4000"
    # backing(${DB_PASSWORD})과 app 이 같은 더미값을 쓰도록 db_credentials 로 묶임
    assert recipe["db_credentials"]["DB_PASSWORD"] == recipe["env"]["DB_PASSWORD"]
    assert recipe["db_credentials"]["DB_NAME"] == recipe["env"]["DB_NAME"]
    # 앱 전용 네임스페이스(SOLFIN_DB_*)도 같은 backing/크리덴셜로 통일 — 127.0.0.1 → mysql
    assert recipe["env"]["SOLFIN_DB_HOST"] == "mysql"
    assert recipe["env"]["SOLFIN_DB_USER"] == recipe["db_credentials"]["DB_USER"]
    assert recipe["env"]["SOLFIN_DB_PASSWORD"] == recipe["db_credentials"]["DB_PASSWORD"]


def test_image_allowed_repo_level(tmp_path):
    """태그는 유연하되 repo 는 allowlist 통제 — node 는 OK, 임의 이미지는 거부(fail-closed)."""
    assert br.image_allowed("node:20")                       # allowlist node:20-bookworm-slim 과 같은 repo
    assert br.image_allowed("python:3.12-slim")
    assert not br.image_allowed("evil/backdoor:latest")
    assert not br.image_allowed(None)


def test_draft_recipe_unknown_stack_low_confidence(tmp_path):
    """매니페스트·Dockerfile·compose 다 없으면 저신뢰 기본(조용히 실패 아님 — 사람이 채움)."""
    recipe = br.draft_recipe(tmp_path)   # 빈 디렉터리
    assert recipe["confidence"] == 0.0
    assert "env" in recipe and "db_credentials" in recipe   # 항상 형상은 완성


def test_expand_compose_vars_with_recipe_creds():
    """repo_target._expand: compose 의 ${VAR}/${VAR:-기본} 을 더미 크리덴셜로 치환."""
    subs = {"DB_PASSWORD": "pw123", "DB_NAME": "db1"}
    assert repo_target._expand("${DB_PASSWORD}", subs) == "pw123"
    assert repo_target._expand("${MISSING:-fallback}", subs) == "fallback"
    assert repo_target._expand("$DB_NAME", subs) == "db1"
    assert repo_target._expand("plain", subs) == "plain"
