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
# 테스트 계정은 @example.com 이다 — 가입 정책(app/signup_policy.py)에서 회사 도메인으로 취급해
# 기존 테스트가 도메인 제한에 걸리지 않게 한다. 정책 자체는 tests/test_signup_policy.py 가 검증한다.
os.environ["ALLOWED_SIGNUP_DOMAINS"] = "aeonlab.kr,finble.io,zerokleek.com,example.com"

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
    def generate(model, prompt, *, json_mode=False, timeout=120,
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


# --------------------------------------------------------------------- API 사용자(가입·인증 완료)

@pytest.fixture
def api_users(db, monkeypatch):
    """인증까지 끝난 테스트 계정을 만들어 TestClient 로 돌려준다. teardown 에서 그 계정들이
    만든 워크스페이스(→ 팀스페이스/프로젝트/채팅 CASCADE)와 계정을 지운다.

    사용: `alice = api_users.new("alice")` → alice.client(쿠키 세션), alice.id, alice.email
    """
    from fastapi.testclient import TestClient

    from app import email_verification
    from app.main import app

    codes: list[str] = []
    monkeypatch.setattr(email_verification, "_send_code_email",
                        lambda email, nickname, code, purpose="signup": codes.append(code) or True)
    made: list[str] = []

    class User:
        pass

    def new(nickname: str):
        client = TestClient(app)
        email = f"{nickname}-{uuid.uuid4().hex[:8]}@example.com"
        r = client.post("/api/auth/signup",
                        json={"email": email, "password": "pw-12345678", "name": f"{nickname} 실명",
                              "nickname": nickname, "privacy_agreed": True})
        assert r.status_code == 201, r.text
        assert client.post("/api/auth/email-verification/verify", json={"code": codes[-1]}).status_code == 204
        u = User()
        u.client, u.email, u.nickname = client, email, nickname
        u.id = client.get("/api/me").json()["id"]
        made.append(u.id)
        return u

    class Users:
        pass

    users = Users()
    users.new = new
    yield users

    with db() as cur:
        disable_last_admin_triggers(cur)
        for mid in made:
            cur.execute("DELETE FROM workspace WHERE created_by = %s", (mid,))
        for mid in made:
            cur.execute("DELETE FROM member WHERE id = %s", (mid,))


def disable_last_admin_triggers(cur) -> None:
    """테스트 정리용 — 마지막 admin/owner 보호 트리거만 이 트랜잭션 동안 끈다(로컬 슈퍼유저).

    session_replication_role=replica 는 FK CASCADE(이것도 트리거)까지 꺼서 고아 행이 남으므로 쓰지 않는다.
    ALTER TABLE 은 트랜잭션 안이라 커밋/롤백과 함께 원상복구된다.
    """
    for table, trg in (("workspace_member", "workspace_member_protect_last_admin_trg"),
                       ("teamspace_member", "teamspace_member_protect_last_admin_trg"),
                       ("project_member", "project_member_protect_last_owner_trg")):
        cur.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trg}")

