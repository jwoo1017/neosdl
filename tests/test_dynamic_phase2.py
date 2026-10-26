"""Phase 2 검증 두뇌 단위 테스트 — runner/instrumentation/detect/dind/feedback(순수부).

실제 Docker·앱 기동 없이 가짜(app_client/log_source) 주입으로 전 경로를 검증한다.
FP 제거 end-to-end(DB)는 scripts 의 스모크로 별도 확인.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.dynamic import entrypoint, feedback, instrumentation, runner
from worker.sandbox import ContainerSpec, detect
from worker.sandbox.dind import _container_kwargs


@dataclass
class _Resp:
    status_code: int
    text: str


def _ep(**kw):
    base = dict(ok=True, method="GET", path_template="/x", param_name="id", param_in="query",
                controller_symbol_key="c", auth_required=False, confidence=0.7, resolved_by="graph")
    base.update(kw)
    return entrypoint.EntryPoint(**base)


# --------------------------------------------------------------- runner
def test_runner_iast_not_reached_is_false_positive(monkeypatch):
    """바인딩 쿼리: IAST 가 sink 미도달 → false_positive (이번 9건 패턴)."""
    monkeypatch.setattr(entrypoint, "resolve", lambda cur, s, f: _ep())
    f = {"id": 1, "category": "sql_injection", "source_var": "req.query.id", "sink_symbol_key": "k"}
    out = runner.verify_finding(
        None, "scan", f,
        app_client=lambda *a: _Resp(200, "same rows"),
        log_source=lambda nonce: {"app_log": "", "iast_reached": False},
        allow_instrumentation=True, iast_nonce="neosdl-xx")
    assert out.verdict.verdict == "false_positive"
    assert out.oracle_kind == "iast"


def test_runner_iast_reached_is_confirmed(monkeypatch):
    monkeypatch.setattr(entrypoint, "resolve", lambda cur, s, f: _ep())
    f = {"id": 2, "category": "sql_injection", "source_var": "req.query.id", "sink_symbol_key": "k"}
    out = runner.verify_finding(
        None, "scan", f, app_client=lambda *a: _Resp(500, "boom"),
        log_source=lambda nonce: {"iast_reached": True}, allow_instrumentation=True, iast_nonce="neosdl-xx")
    assert out.verdict.verdict == "confirmed"


def test_runner_iast_falls_back_to_blackbox_when_no_signal(monkeypatch):
    """IAST 켜졌지만 해당 sink 에 신호 없음(reached=None) → 블랙박스 경로로 폴백."""
    monkeypatch.setattr(entrypoint, "resolve", lambda cur, s, f: _ep())
    f = {"id": 9, "category": "xss", "source_var": "req.query.q", "sink_symbol_key": "k"}
    out = runner.verify_finding(
        None, "scan", f,
        app_client=lambda m, p, name, pin, val: _Resp(200, f"<div>{val}</div>"),
        log_source=lambda nonce: {"iast_reached": None}, allow_instrumentation=True, iast_nonce="neosdl-xx")
    assert out.oracle_kind == "blackbox" and out.verdict.verdict == "confirmed"


def test_runner_not_attempted_paths(monkeypatch):
    f = {"id": 3, "category": "sql_injection", "source_var": "req.query.id", "sink_symbol_key": "k"}
    # 엔트리포인트 없음
    monkeypatch.setattr(entrypoint, "resolve", lambda cur, s, f: _ep(ok=False, reason="no_http_entrypoint"))
    out = runner.verify_finding(None, "s", f, app_client=lambda *a: _Resp(200, ""))
    assert out.verdict.verdict == "not_attempted" and out.verdict.signals["reason"] == "no_http_entrypoint"
    # 인증 필요
    monkeypatch.setattr(entrypoint, "resolve", lambda cur, s, f: _ep(auth_required=True))
    out = runner.verify_finding(None, "s", f, app_client=lambda *a: _Resp(200, ""))
    assert out.verdict.signals["reason"] == "needs_auth"
    # 앱 미기동
    monkeypatch.setattr(entrypoint, "resolve", lambda cur, s, f: _ep())
    out = runner.verify_finding(None, "s", f, app_client=lambda *a: None)
    assert out.verdict.signals["reason"] == "app_not_running"


def test_runner_xss_reflection_confirmed(monkeypatch):
    monkeypatch.setattr(entrypoint, "resolve", lambda cur, s, f: _ep())
    f = {"id": 4, "category": "xss", "source_var": "req.query.q", "sink_symbol_key": "k"}
    # probe_value(마커)를 그대로 반사
    out = runner.verify_finding(None, "s", f,
                                app_client=lambda m, p, name, pin, val: _Resp(200, f"<div>{val}</div>"))
    assert out.verdict.verdict == "confirmed"


# --------------------------------------------------------------- instrumentation
def test_nonce_reached_sink():
    assert instrumentation.nonce_reached_sink("N", "SELECT * FROM t WHERE x='N'", ["a"]) is True
    assert instrumentation.nonce_reached_sink("N", "SELECT * FROM t WHERE x=?", ["N"]) is False
    assert instrumentation.nonce_reached_sink("N", "SELECT 1", None) is False


# --------------------------------------------------------------- detect
def test_detect_dockerfile(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM node:20\nEXPOSE 8080\n")
    p = detect.detect(tmp_path)
    assert p.ok and p.dockerfile == "Dockerfile" and p.port == 8080


def test_detect_node_express(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts":{"start":"node ."},"dependencies":{"express":"4","mysql2":"3"}}')
    p = detect.detect(tmp_path)
    assert p.ok and p.framework == "express" and "mysql" in p.required_services and p.run_cmd == "npm start"


def test_detect_django(tmp_path):
    (tmp_path / "manage.py").write_text("# django")
    (tmp_path / "requirements.txt").write_text("django\n")
    p = detect.detect(tmp_path)
    assert p.ok and p.framework == "django" and "runserver" in p.run_cmd


def test_detect_unknown(tmp_path):
    (tmp_path / "README.md").write_text("nothing")
    assert detect.detect(tmp_path).ok is False


# --------------------------------------------------------------- dind security kwargs
def test_dind_kwargs_enforce_security_defaults():
    kw = _container_kwargs(ContainerSpec(image="node:20-bookworm-slim", mem_limit="2g"), "neosdl-sbx-net-x")
    assert kw["privileged"] is False
    assert kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges:true"]
    assert kw["user"] == "1000:1000"
    assert kw["network"] == "neosdl-sbx-net-x"
    assert kw["pids_limit"] == 512 and kw["mem_limit"] == "2g" and kw["memswap_limit"] == "2g"
    assert "volumes" not in kw and "binds" not in kw   # 바인드마운트 없음


# --------------------------------------------------------------- feedback 마스킹(순수)
def test_feedback_mask_redacts_flagged_lines():
    text = "line ok\nAKIAIOSFODNN7EXAMPLE secret here\nanother ok"
    masked, n = feedback._mask(text)
    assert "AKIA" not in masked and "***REDACTED***" in masked
    assert n >= 1 and "line ok" in masked
