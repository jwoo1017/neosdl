"""레포 부팅 블랙박스(_run_repo_blackbox) 글루 — Docker/LLM 없이 배선·영속·정리·인가 검증.

부팅/펜테스터 핵심은 compose·pentester 테스트에서 봤으므로, 여기서는 '워크스페이스 인가 →
표적 부팅 → 툴박스 재사용 → run_pentester(trusted) → 리포트 영속 → 정리'가 엮였는지 본다.
"""

from __future__ import annotations

import worker.handlers.engagement as eng
from app import pentester
from worker.sandbox import repo_target


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
        return []


class _FakeRunner:
    started = destroyed = False

    def start_toolbox(self, eid, *, network=None, image=None):
        _FakeRunner.started = True
        return object()

    def exec_toolbox(self, container, argv, *, timeout_s=120):
        class _R:
            exit_code = 0
            logs = ""
            timed_out = False
        return _R()

    def destroy(self, eid):
        _FakeRunner.destroyed = True


def test_run_repo_blackbox_wires_persists_and_cleans(monkeypatch):
    monkeypatch.setattr(eng.sandbox, "assert_repo_allowed", lambda cur, rid, actor: "finble/exchange")
    monkeypatch.setattr(eng.ai_providers, "resolve_requested_model", lambda x: "m")
    monkeypatch.setattr("worker.sandbox.dind.DindRunner", _FakeRunner)
    _FakeRunner.started = _FakeRunner.destroyed = False

    def fake_boot(engagement, runner, factory):
        factory("neosdl-sbx-net-e1")       # 툴박스를 네트워크에 기동(state["exec"] 채움)
        return repo_target.BootedTarget(web_ip="172.18.0.3", web_port=3000,
                                        network="neosdl-sbx-net-e1", teardown=lambda: None), None

    monkeypatch.setattr(repo_target, "boot_repo_target", fake_boot)

    captured = {}

    def fake_run(cur, *, target, llm_generate, toolbox_exec, permission_mode, effective_role, actor,
                 approved_commands=None, trusted_target=False, **kw):
        captured.update(target=target, trusted=trusted_target, mode=permission_mode)
        return pentester.PentestOutcome(
            "succeeded", findings=[{"title": "exposed /admin", "severity": "high"}],
            history=[{"command": "nmap -Pn 172.18.0.3", "result": {"ok": True, "exit_code": 0}}], steps=3)

    monkeypatch.setattr(eng.pentester, "run_pentester", fake_run)

    cur = _Cur()
    engagement = {"id": "e1", "project_id": "p1", "repository_id": "r1", "scan_id": "s1",
                  "permission_mode": "auto", "params": {}}
    result = eng._run_repo_blackbox(cur, engagement)

    assert result["kind"] == "repo_blackbox" and result["status"] == "succeeded"
    assert result["findings"] == 1 and result["steps"] == 3
    assert captured["target"] == "172.18.0.3"      # 표적 = 부팅된 앱 컨테이너 IP(사람이 IP 안 봄)
    assert captured["trusted"] is True             # self-boot → trusted(host allowlist 우회)
    assert _FakeRunner.started and _FakeRunner.destroyed     # 툴박스 기동 + finally 정리
    assert any("artifact" in sql.lower() for sql, _ in cur.calls)   # 리포트 영속


def test_run_repo_blackbox_boot_failure_is_blocked_report(monkeypatch):
    monkeypatch.setattr(eng.sandbox, "assert_repo_allowed", lambda cur, rid, actor: "finble/exchange")
    monkeypatch.setattr(eng.ai_providers, "resolve_requested_model", lambda x: "m")
    monkeypatch.setattr("worker.sandbox.dind.DindRunner", _FakeRunner)
    monkeypatch.setattr(repo_target, "boot_repo_target",
                        lambda *a, **k: (None, "detect_failed: stack_undetermined"))   # 부팅 실패

    called = {"run": False}
    monkeypatch.setattr(eng.pentester, "run_pentester",
                        lambda *a, **k: called.__setitem__("run", True))

    result = eng._run_repo_blackbox(_Cur(), {"id": "e2", "project_id": "p1", "repository_id": "r1",
                                             "scan_id": "s1", "permission_mode": "auto", "params": {}})
    assert result["status"] == "failed" and result["steps"] == 0
    assert called["run"] is False      # 부팅 실패 시 펜테스터 안 돌린다(조용히 실패 아님: blocked 리포트)
