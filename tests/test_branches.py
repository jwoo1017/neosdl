"""브랜치 생애주기 (app/branches.py) — 순수 파싱 + DB upsert.

Security History: 웹훅으로 관측한 브랜치, 삭제돼도 행 유지(deleted_at).
"""

from __future__ import annotations

import uuid

import pytest

from app import branches


# --------------------------------------------------------------------- 순수

def test_parse_branch_name():
    assert branches.parse_branch_name("refs/heads/feature/x") == "feature/x"
    assert branches.parse_branch_name("refs/heads/main") == "main"
    assert branches.parse_branch_name("refs/pull/5/head") is None
    assert branches.parse_branch_name("refs/tags/v1.0") is None
    assert branches.parse_branch_name(None) is None
    assert branches.parse_branch_name("refs/heads/") is None


# --------------------------------------------------------------------- DB (integration)

REPO_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")


@pytest.fixture
def repo(db):
    with db() as cur:
        cur.execute(
            """
            INSERT INTO repository (id, provider, remote_id, namespace, name, clone_url)
            VALUES (%s, 'gitlab', 'branch-test', 'evaltest', 'br', 'file:///dev/null')
            ON CONFLICT (provider, remote_id) DO NOTHING
            """,
            (str(REPO_ID),),
        )
    yield str(REPO_ID)
    with db() as cur:
        cur.execute("DELETE FROM repository WHERE id = %s", (str(REPO_ID),))


@pytest.mark.integration
def test_branch_lifecycle(db, repo):
    with db() as cur:
        branches.record_branch_event(cur, repo, "refs/heads/main", "a" * 40,
                                     default_branch="main")
        branches.record_branch_event(cur, repo, "refs/heads/feature/pay", "b" * 40,
                                     default_branch="main")
        cur.execute("SELECT name, head_sha, is_default, deleted_at FROM branch "
                    "WHERE repository_id = %s ORDER BY name", (repo,))
        rows = {r["name"]: r for r in cur.fetchall()}
    assert rows["main"]["is_default"] is True
    assert rows["feature/pay"]["is_default"] is False
    assert rows["feature/pay"]["head_sha"] == "b" * 40

    # 새 head 로 재push → 갱신, last_seen_at 전진
    with db() as cur:
        cur.execute("SELECT last_seen_at FROM branch WHERE repository_id=%s AND name='feature/pay'", (repo,))
        before = cur.fetchone()["last_seen_at"]
        branches.record_branch_event(cur, repo, "refs/heads/feature/pay", "c" * 40)
        cur.execute("SELECT head_sha, last_seen_at FROM branch WHERE repository_id=%s AND name='feature/pay'", (repo,))
        r = cur.fetchone()
    assert r["head_sha"] == "c" * 40
    assert r["last_seen_at"] >= before

    # 삭제 → deleted_at 세팅, 행 유지
    with db() as cur:
        branches.record_branch_event(cur, repo, "refs/heads/feature/pay", None, deleted=True)
        cur.execute("SELECT deleted_at, head_sha FROM branch WHERE repository_id=%s AND name='feature/pay'", (repo,))
        r = cur.fetchone()
    assert r["deleted_at"] is not None
    assert r["head_sha"] == "c" * 40   # 마지막 head 는 남아 있다

    # 재생성 → deleted_at 복구
    with db() as cur:
        branches.record_branch_event(cur, repo, "refs/heads/feature/pay", "d" * 40)
        cur.execute("SELECT deleted_at FROM branch WHERE repository_id=%s AND name='feature/pay'", (repo,))
        assert cur.fetchone()["deleted_at"] is None


@pytest.mark.integration
def test_resolve_scan_branch(db, repo):
    with db() as cur:
        # 브랜치 ref 는 그대로
        assert branches.resolve_scan_branch(cur, repo, "refs/heads/develop", "e" * 40) == "develop"
        # PR ref 는 pull_request.head_ref 폴백
        cur.execute(
            """
            INSERT INTO pull_request (repository_id, number, title, state, base_ref, head_ref,
                                      head_sha, merged, updated_at)
            VALUES (%s, 7, 't', 'open', 'main', 'feature/auth', %s, false, now())
            ON CONFLICT (repository_id, number) DO NOTHING
            """,
            (repo, "f" * 40),
        )
        assert branches.resolve_scan_branch(cur, repo, "refs/pull/7/head", "f" * 40) == "feature/auth"
        # 아무 것도 없으면 None
        assert branches.resolve_scan_branch(cur, repo, "refs/pull/9/head", "0" * 40) is None
