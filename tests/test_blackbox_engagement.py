"""블랙박스 engagement worker 글루(_run_blackbox) — Docker/LLM 없이 분기·영속·정리 배선 검증.

핵심 루프는 test_pentester.py 에서 검증했으므로, 여기서는 '표적 인가 호출 → 툴박스 기동/정리 →
run_pentester 호출 → 리포트 아티팩트 영속'이 제대로 엮였는지만 본다.
"""

from __future__ import annotations

import worker.handlers.engagement as eng
from app import pentester


class _Cur:
    def __init__(self):
        self.calls = []
        self._ret = {"seq": 1}

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._ret = {"id": "x1"} if "RETURNING id" in sql else {"seq": 1}

    def fetchone(self):
        return self._ret

    def fetchall(self):
        return []     # 이전 agent_action 없음(최초 실행)


class _FakeRunner:
    started = destroyed = False

    def start_toolbox(self, eid, *, network=None, image=None):
        _FakeRunner.started = True
        return object()

    def exec_toolbox(self, container, argv, *, timeout_s=120):
        raise AssertionError("run_pentester 가 모킹돼 exec 는 호출되면 안 됨")

    def destroy(self, eid):
        _FakeRunner.destroyed = True


def test_run_blackbox_wires_and_persists(monkeypatch):
    monkeypatch.setattr(eng.sandbox, "assert_target_allowed", lambda cur, target, actor: "test.internal")
    monkeypatch.setattr("worker.sandbox.dind.DindRunner", _FakeRunner)
    monkeypatch.setattr(eng.ai_providers, "resolve_requested_model", lambda x: "m")

    captured = {}

    def fake_run(cur, *, target, llm_generate, toolbox_exec, permission_mode, effective_role, actor,
                 approved_commands=None, **kw):
        captured.update(target=target, permission_mode=permission_mode, approved=approved_commands,
                        resume=kw.get("resume_commands"))
        return pentester.PentestOutcome(
            "succeeded", findings=[{"title": "open ssh", "severity": "low"}],
            history=[{"command": "nmap -sV test.internal", "result": {"ok": True, "exit_code": 0}}], steps=2)

    monkeypatch.setattr(eng.pentester, "run_pentester", fake_run)

    cur = _Cur()
    engagement = {"id": "e1", "project_id": "p1", "permission_mode": "ask"}
    result = eng._run_blackbox(cur, engagement, "test.internal")

    assert result["kind"] == "blackbox" and result["status"] == "succeeded"
    assert result["findings"] == 1 and result["steps"] == 2
    assert captured["target"] == "test.internal" and captured["permission_mode"] == "ask"
    assert _FakeRunner.started and _FakeRunner.destroyed      # 기동 + 정리(finally) 보장
    assert any("artifact" in sql.lower() for sql, _ in cur.calls)   # 리포트 영속


def test_run_blackbox_awaiting_approval_status(monkeypatch):
    monkeypatch.setattr(eng.sandbox, "assert_target_allowed", lambda cur, target, actor: "test.internal")
    monkeypatch.setattr("worker.sandbox.dind.DindRunner", _FakeRunner)
    monkeypatch.setattr(eng.ai_providers, "resolve_requested_model", lambda x: "m")
    monkeypatch.setattr(eng.pentester, "run_pentester", lambda *a, **k: pentester.PentestOutcome(
        "awaiting_approval", history=[], pending_command="nmap -sV test.internal", steps=0))

    result = eng._run_blackbox(_Cur(), {"id": "e2", "project_id": "p1", "permission_mode": "ask"}, "test.internal")
    assert result["status"] == "awaiting_approval"
    assert result["pending_command"] == "nmap -sV test.internal"
