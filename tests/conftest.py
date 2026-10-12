"""공용 픽스처 + 마커 등록.

이 레포엔 원래 conftest.py 가 없었다(test_orchestrator/test_risk 는 순수 로직,
test_graph_equivalence 는 픽스처를 자기 파일에 둠). Risk 평가 하니스(Steps 13–15)가
DB/LLM 픽스처를 공유하므로 여기로 뺀다.

마커:
    integration  — 실제 Postgres(sql/001..020 적용) 필요. 없으면 skip.
    live_llm     — 실제 LiteLLM 게이트웨이 필요. RISK_EVAL_LIVE 로 opt-in.
"""

from __future__ import annotations

import os
import uuid

import pytest

# app.config.settings() 는 SESSION_SECRET 에 기본값이 없다 — .env 가 있으면 거기서
# 읽지만, .env 없는 CI/샌드박스에서도 import 가 죽지 않도록 더미를 채운다(테스트는
# 세션 서명을 검증하지 않는다).
os.environ.setdefault("SESSION_SECRET", "test-" + "0" * 32)

_MARKERS = {
    "integration": "실제 Postgres(sql/001..020 적용) 필요 — 없으면 skip",
    "live_llm": "실제 LiteLLM 게이트웨이 필요 — RISK_EVAL_LIVE=1 로 opt-in",
}


def pytest_configure(config):
    for name, desc in _MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {desc}")


# --------------------------------------------------------------------- LLM

def scripted_generate(responses: dict[str, str], default: str | None = None):
    """`ai_providers.generate` 대역. 프롬프트에 포함된 키워드로 응답을 고른다.

    responses = {"substring in prompt": '{"json": "response"}'} — 첫 매칭을 반환.
    매칭 없고 default 도 없으면 예외(테스트가 놓친 프롬프트를 조용히 넘기지 않게).
    """
    def generate(provider, model, prompt, *, json_mode=False, timeout=120,
                 project_id=None, purpose="qa", scan_id=None):
        for needle, resp in responses.items():
            if needle in prompt:
                return resp
        if default is not None:
            return default
        raise AssertionError(f"scripted_generate: 매칭되는 응답 없음 (purpose={purpose})")

    return generate


@pytest.fixture
def patch_risk_llm(monkeypatch):
    """사용법: `patch_risk_llm({"pricing": '{"classification": "business_core_logic", ...}'})`."""
    from app.risk import classify

    def apply(responses: dict[str, str], default: str | None = None):
        monkeypatch.setattr(classify.ai_providers, "generate", scripted_generate(responses, default))

    return apply


# --------------------------------------------------------------------- DB

def _db_ready() -> bool:
    try:
        from app.db import tx

        with tx() as cur:
            cur.execute(
                "SELECT to_regclass('public.risk_object') a, to_regclass('public.security_snapshot') b"
            )
            r = cur.fetchone()
            return r["a"] is not None and r["b"] is not None
    except Exception:
        return False


@pytest.fixture(scope="session")
def db():
    """Postgres 연결 가능 + sql/001..022 스키마 적용 확인. 아니면 전체 세션 skip."""
    if not _db_ready():
        pytest.skip("Postgres(sql/001..022 적용) 없음 — integration 테스트 생략")
    from app.db import tx

    return tx


@pytest.fixture
def scan_factory(db):
    """빈 scan 하나를 만들고 teardown 에서 CASCADE 로 지운다.

    반환: `make(repo_suffix="risk-eval", ref="refs/heads/main", commit_sha=None,
    stats=None) -> scan_id`. 호출부가 symbol/edge/cfg/pdg/finding 을 직접 INSERT 한다.
    """
    created: list[str] = []
    _NS = uuid.UUID("22222222-2222-2222-2222-222222222222")

    def make(repo_suffix: str = "risk-eval", *, ref: str = "refs/heads/main",
             commit_sha: str | None = None, stats: dict | None = None) -> str:
        import json

        repo_id = uuid.uuid5(_NS, repo_suffix)   # suffix 별 결정적 repo id — pk 충돌 방지
        sid = str(uuid.uuid4())
        sha = commit_sha or (uuid.uuid4().hex + uuid.uuid4().hex[:8])
        with db() as cur:
            cur.execute(
                """
                INSERT INTO repository (id, provider, remote_id, namespace, name, clone_url)
                VALUES (%s, 'gitlab', %s, 'evaltest', 'risk', 'file:///dev/null')
                ON CONFLICT (provider, remote_id) DO NOTHING
                """,
                (str(repo_id), repo_suffix),
            )
            cur.execute(
                """
                INSERT INTO scan (id, repository_id, commit_sha, ref, parser_version, status, stats)
                VALUES (%s, %s, %s, %s, 'test', 'succeeded', %s::jsonb)
                """,
                (sid, str(repo_id), sha, ref, json.dumps(stats or {})),
            )
        created.append(sid)
        return sid

    yield make

    with db() as cur:
        for sid in created:
            cur.execute("DELETE FROM scan WHERE id = %s", (sid,))
