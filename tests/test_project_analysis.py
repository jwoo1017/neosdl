"""저장소 연결 → 자동 분석 → 분석 전 채팅 잠금 → 완료/실패 상태(app/analysis.py, sql/032).

실제 Postgres 가 필요하다(없으면 skip). GitHub 최신 커밋 조회는 monkeypatch 로 대신한다.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app import analysis, auth, queue, scans
from app.config import settings
from worker import main as worker_main


def _project(owner):
    c = owner.client
    ws = c.post("/api/workspaces", json={"name": f"{owner.nickname}-ws", "type": "company"}).json()
    ts = c.post(f"/api/workspaces/{ws['id']}/teamspaces", json={"name": "분석팀"}).json()
    return c.post(f"/api/teamspaces/{ts['id']}/projects", json={"name": "분석검증"}).json()["id"]


@pytest.fixture
def repo(db, api_users):
    """alice 의 GitHub 설치 + 저장소 하나. teardown 에서 스캔·작업·저장소를 지운다."""
    alice = api_users.new("alice")
    repo_id = str(uuid.uuid4())
    with db() as cur:
        cur.execute(
            "INSERT INTO github_account (member_id, github_user_id, github_login) VALUES (%s, %s, 'alice-gh') "
            "RETURNING id",
            (alice.id, int(uuid.uuid4().int % 10**12)),
        )
        account = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO installation (github_installation_id, github_account_id, account_login, account_type)
            VALUES (%s, %s, 'alice-org', 'Organization') RETURNING id
            """,
            (int(uuid.uuid4().int % 10**12), account),
        )
        inst = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO repository (id, provider, remote_id, namespace, name, clone_url, default_branch,
                                    installation_id)
            VALUES (%s, 'github', %s, 'alice-org', 'svc', 'https://example.invalid/svc.git', 'main', %s)
            """,
            (repo_id, f"test-{repo_id}", inst),
        )
    yield alice, repo_id
    with db() as cur:
        cur.execute("SELECT id::text FROM scan WHERE repository_id=%s", (repo_id,))
        scan_ids = [r["id"] for r in cur.fetchall()]
        cur.execute("DELETE FROM job WHERE payload->>'scan_id' = ANY(%s)", (scan_ids,))
        cur.execute("DELETE FROM scan WHERE repository_id=%s", (repo_id,))
        cur.execute("DELETE FROM repository WHERE id=%s", (repo_id,))
        cur.execute("DELETE FROM installation WHERE id=%s", (inst,))


def _sha() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex[:8]


def _attach(alice, project_id, repo_id):
    r = alice.client.post(f"/api/projects/{project_id}/repositories", json={"repository_id": repo_id})
    assert r.status_code == 201, r.text
    return r.json()


def _analysis(alice, project_id):
    r = alice.client.get(f"/api/projects/{project_id}/analysis")
    assert r.status_code == 200, r.text
    return r.json()


def _complete(db, scan_id):
    """risk.build 완료와 같은 효과 — 파싱 성공 + 분석 완료 시각 기록, 남은 작업 정리."""
    with db() as cur:
        cur.execute("UPDATE scan SET status='succeeded', analysis_completed_at=now() WHERE id=%s", (scan_id,))
        cur.execute("UPDATE job SET status='done' WHERE payload->>'scan_id' = %s", (scan_id,))


@pytest.mark.integration
def test_attach_starts_analysis_and_locks_chat_until_complete(repo, db, monkeypatch):
    alice, repo_id = repo
    project_id = _project(alice)
    assert _analysis(alice, project_id)["state"] == "no_repository"

    sha = _sha()
    monkeypatch.setattr(scans, "get_latest_commit_sha", lambda *a: sha)
    body = _attach(alice, project_id, repo_id)
    assert body["scan_error"] is None and body["scan"]["reused"] is False
    scan_id = body["scan"]["id"]

    a = _analysis(alice, project_id)
    assert a["state"] == "analyzing" and a["chat_ready"] is False
    assert a["latest"]["scan_id"] == scan_id and a["latest"]["stage"] == "fetch"
    assert a["latest"]["repository"]["full_name"] == "alice-org/svc"

    # 단계는 남은 작업 중 가장 앞 단계로 계산한다 — 이벤트를 놓쳐도 다시 조회하면 맞다.
    with db() as cur:
        cur.execute("UPDATE job SET status='done' WHERE payload->>'scan_id'=%s", (scan_id,))
        queue.enqueue(cur, "symbol.annotate", {"scan_id": scan_id})
        queue.enqueue(cur, "risk.build", {"scan_id": scan_id})
    assert _analysis(alice, project_id)["latest"]["stage"] == "annotate"

    _complete(db, scan_id)
    a = _analysis(alice, project_id)
    assert a["state"] == "ready" and a["chat_ready"] is True and a["ready_scan_id"] == scan_id
    assert a["latest"]["findings_count"] == 0

    # 새 커밋 재분석 중에도 채팅은 이전 완료 결과로 계속 열려 있다.
    new_scan = scans.start_repository_scan(repo_id, actor="test", commit_sha=_sha())
    a = _analysis(alice, project_id)
    assert a["state"] == "ready_reanalyzing" and a["chat_ready"] is True
    assert a["ready_scan_id"] == scan_id and a["latest"]["scan_id"] == str(new_scan["id"])
    with db() as cur:
        assert analysis.latest_ready_scan_id(cur, repo_id) == scan_id


@pytest.mark.integration
def test_attach_succeeds_even_when_github_lookup_fails(repo, monkeypatch):
    alice, repo_id = repo
    project_id = _project(alice)

    def boom(*_a):
        raise RuntimeError("github down")

    monkeypatch.setattr(scans, "get_latest_commit_sha", boom)
    body = _attach(alice, project_id, repo_id)
    assert body["scan"] is None and "github down" in body["scan_error"]
    a = _analysis(alice, project_id)
    assert a["state"] == "failed" and a["chat_ready"] is False

    # 화면의 "다시 분석" = 기존 수동 스캔 API
    monkeypatch.setattr(scans, "get_latest_commit_sha", lambda *a: _sha())
    r = alice.client.post(f"/api/repositories/{repo_id}/scans", json={})
    assert r.status_code in (200, 201, 202), r.text
    assert _analysis(alice, project_id)["state"] == "analyzing"

    # 이미 연결된 저장소를 다시 연결하면 분석을 새로 시작하지 않는다.
    again = _attach(alice, project_id, repo_id)
    assert again["already_attached"] is True and again["scan"] is None


@pytest.mark.integration
@pytest.mark.parametrize("kind,expect_status", [("scan.parse", "failed"), ("risk.build", "succeeded")])
def test_dead_core_job_marks_analysis_failed(repo, db, monkeypatch, kind, expect_status):
    alice, repo_id = repo
    project_id = _project(alice)
    monkeypatch.setattr(scans, "get_latest_commit_sha", lambda *a: _sha())
    scan_id = _attach(alice, project_id, repo_id)["scan"]["id"]
    if kind == "risk.build":
        with db() as cur:
            cur.execute("UPDATE scan SET status='succeeded' WHERE id=%s", (scan_id,))

    sent = []
    monkeypatch.setattr(analysis.repo_activity_bus, "notify_repo_activity",
                        lambda cur, rid, event: sent.append((str(rid), event)))

    with db() as cur:
        job_id = queue.enqueue(cur, kind, {"scan_id": scan_id})
        cur.execute("UPDATE job SET attempts = max_attempts, status='running' WHERE id=%s", (job_id,))
        cur.execute("SELECT * FROM job WHERE id=%s", (job_id,))
        job = cur.fetchone()
    with db() as cur:
        assert queue.fail(cur, job_id, "ValueError: boom") == "dead"
    worker_main._mark_dead(job, "ValueError: boom")

    with db() as cur:
        cur.execute("SELECT status, analysis_error FROM scan WHERE id=%s", (scan_id,))
        row = cur.fetchone()
    assert row["status"] == expect_status and "boom" in row["analysis_error"]
    assert sent and sent[-1][0] == repo_id and sent[-1][1]["type"] == "scan_failed"

    a = _analysis(alice, project_id)
    assert a["state"] == "failed" and "boom" in a["latest"]["error"] and a["chat_ready"] is False


@pytest.mark.integration
def test_queue_fail_returns_queued_while_retries_remain(db):
    with db() as cur:
        job_id = queue.enqueue(cur, "graph.project", {"scan_id": str(uuid.uuid4())})
    try:
        with db() as cur:
            assert queue.fail(cur, job_id, "x") == "queued"
    finally:
        with db() as cur:
            cur.execute("DELETE FROM job WHERE id=%s", (job_id,))


@pytest.mark.integration
def test_stage_notification_for_core_jobs_only(repo, db, monkeypatch):
    alice, repo_id = repo
    project_id = _project(alice)
    monkeypatch.setattr(scans, "get_latest_commit_sha", lambda *a: _sha())
    scan_id = _attach(alice, project_id, repo_id)["scan"]["id"]

    sent = []
    monkeypatch.setattr(analysis.repo_activity_bus, "notify_repo_activity",
                        lambda cur, rid, event: sent.append(event))
    worker_main._notify_stage({"id": 1, "kind": "symbol.annotate", "payload": {"scan_id": scan_id}})
    worker_main._notify_stage({"id": 2, "kind": "graph.project", "payload": {"scan_id": scan_id}})
    assert sent == [{"type": "scan_stage", "scan_id": scan_id, "stage": "annotate"}]


@pytest.mark.integration
def test_chat_socket_rejects_questions_until_analysis_ready(repo, db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import chat
    from app.main import app

    alice, repo_id = repo
    project_id = _project(alice)
    monkeypatch.setattr(scans, "get_latest_commit_sha", lambda *a: _sha())
    scan_id = _attach(alice, project_id, repo_id)["scan"]["id"]
    session_id = alice.client.post(f"/api/projects/{project_id}/chat/sessions", json={}).json()["id"]

    handled = []

    async def fake_handle(sid, pid, question, client_msg_id, q, *rest):
        handled.append(question)
        await q.put({"type": "test_handled", "client_msg_id": client_msg_id})

    monkeypatch.setattr(chat, "_handle_question", fake_handle)
    origin = auth._origin_of(settings().frontend_url)
    path = f"/api/ws/projects/{project_id}/chat/sessions/{session_id}"

    with TestClient(app) as c:
        c.cookies = alice.client.cookies
        with c.websocket_connect(path, headers={"origin": origin}) as ws:
            ws.send_json({"type": "message", "question": "이 코드 뭐야?", "client_msg_id": "m1"})
            frame = ws.receive_json()
            assert frame["type"] == "error" and frame["error"] == "analysis_not_ready"
            assert frame["client_msg_id"] == "m1" and handled == []

            _complete(db, scan_id)
            ws.send_json({"type": "message", "question": "이제는?", "client_msg_id": "m2"})
            frame = ws.receive_json()
            assert frame == {"type": "test_handled", "client_msg_id": "m2"} and handled == ["이제는?"]


@pytest.mark.integration
def test_backfill_marks_existing_succeeded_scans_complete(db):
    """032 백필 — 이 기능 이전에 파싱까지 끝난 스캔은 분석 완료로 본다(기존 채팅이 잠기지 않게)."""
    with db() as cur:
        cur.execute("SELECT count(*) AS n FROM scan WHERE status='succeeded' AND analysis_completed_at IS NULL "
                    "AND created_at < (SELECT applied_at FROM schema_migrations WHERE version='032')")
        assert cur.fetchone()["n"] == 0


def test_stage_mapping_is_ordered():
    assert analysis.STAGES == ["fetch", "parse", "annotate", "risk"]
    assert set(analysis.STAGE_BY_KIND.values()) == set(analysis.STAGES)
    assert json.dumps(analysis.STAGE_BY_KIND)
