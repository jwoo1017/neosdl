"""attack_plan — PentAGI식 다단계 공격 플랜 + 결정적 발견 추출기(순수, Docker/LLM 무의존).

프로브 명령이 toolbox.validate_command 를 통과하는지(셸 메타·금지 서브스트링·스코프), 그리고 각
도구/프로브 출력에서 취약점을 결정적으로(오탐 없이) 뽑는지 검증한다.
"""

from __future__ import annotations

import pytest

from app import attack_plan, sandbox, toolbox
from app.config import Settings

HTTP = [{"port": 4000, "service": "http", "proto": "tcp", "http": True}]


@pytest.fixture(autouse=True)
def _allowlist(monkeypatch):
    s = Settings(sandbox_enabled=True, sandbox_target_allowlist="t")
    monkeypatch.setattr(toolbox, "settings", lambda: s)
    monkeypatch.setattr(sandbox, "settings", lambda: s)


# ─────────────────────────────── 플랜 순서 ───────────────────────────────
def _cmd(sugg: str) -> str:
    """추천 문자열에서 앞의 실행 명령만(설명 괄호 앞까지)."""
    return sugg.split("  (")[0].strip()


def test_plan_recon_then_broad_then_injection():
    """정찰(whatweb→nikto→nuclei)을 먼저 깔고, 그다음 엔드포인트 능동 주입으로 넘어간다."""
    eps = [{"method": "GET", "path": "/search", "params": ["q"], "auth": False}]
    seen: set[str] = set()
    order = []
    for _ in range(8):
        sugg = attack_plan.plan_next("t", HTTP, seen, eps)
        cmd = _cmd(sugg)
        if cmd.startswith("finish"):
            break
        order.append(cmd.split()[0])
        seen.add(cmd)
    # whatweb, nikto, nuclei 가 sqlmap/curl 주입보다 앞선다
    assert order[:3] == ["whatweb", "nikto", "nuclei"]
    assert "sqlmap" in order and "curl" in order and "ffuf" in order


def test_plan_covers_all_injection_classes_for_endpoint():
    """한 엔드포인트에 sqlmap(SQLi)+curl(XSS/SSTI/LFI) 네 종류 주입이 모두 계획된다."""
    eps = [{"method": "GET", "path": "/item", "params": ["id"], "auth": False}]
    seen = {"whatweb http://t:4000", "nikto -h http://t:4000", "nuclei -u http://t:4000"}
    issued = []
    for _ in range(12):
        cmd = _cmd(attack_plan.plan_next("t", HTTP, seen, eps))
        if cmd.startswith(("finish", "ffuf")):
            break
        issued.append(cmd)
        seen.add(cmd)
    assert any(c.startswith("sqlmap") for c in issued)
    assert any(attack_plan._XSS_PAYLOAD in c for c in issued)
    assert any(attack_plan._SSTI_PAYLOAD in c for c in issued)
    assert any(attack_plan._LFI_MARK in c.lower() for c in issued)


def test_plan_access_probe_for_auth_endpoint():
    """인증필요 엔드포인트는 비인증 접근통제 프로브가 계획된다(파라미터 없어도)."""
    eps = [{"method": "GET", "path": "/admin", "params": [], "auth": True}]
    # 정찰·주입·ffuf 를 모두 소진했다고 치면 접근통제 프로브가 남는다
    seen = {"whatweb http://t:4000", "nikto -h http://t:4000", "nuclei -u http://t:4000",
            "ffuf -u http://t:4000/FUZZ"}
    sugg = attack_plan.plan_next("t", HTTP, seen, eps)
    assert attack_plan._ACCESS_MARK in sugg and "/admin" in sugg


# ──────────────────────── 프로브가 안전검증을 통과한다 ────────────────────────
@pytest.mark.parametrize("build", [
    attack_plan.xss_probe, attack_plan.ssti_probe, attack_plan.lfi_probe, attack_plan.access_probe,
    lambda t, p, e: attack_plan.sqlmap_for(t, p, e),
])
def test_probe_commands_pass_toolbox_validation(build):
    ep = {"method": "GET", "path": "/x", "params": ["q"], "auth": True}
    cmd = build("localhost", 4000, ep)
    argv = toolbox.validate_command(cmd, "localhost", trusted=True)   # 거부되면 CommandRejected
    assert argv and argv[0] == cmd.split()[0]


def test_post_probe_passes_validation():
    ep = {"method": "POST", "path": "/login", "params": ["email", "password"], "auth": False}
    for cmd in (attack_plan.xss_probe("localhost", 4000, ep), attack_plan.ssti_probe("localhost", 4000, ep),
                attack_plan.lfi_probe("localhost", 4000, ep)):
        assert toolbox.validate_command(cmd, "localhost", trusted=True)


def test_lfi_payload_avoids_forbidden_substring():
    """LFI 페이로드는 /etc/passwd 를 URL 인코딩해 금지 서브스트링 거부를 피한다."""
    cmd = attack_plan.lfi_probe("localhost", 4000, {"method": "GET", "path": "/f", "params": ["file"]})
    assert "/etc/passwd" not in cmd.lower() and "etc%2fpasswd" in cmd.lower()
    toolbox.validate_command(cmd, "localhost", trusted=True)   # 통과해야 한다


# ────────────────────────────── 추출기(결정적) ──────────────────────────────
def test_nuclei_findings_parse_jsonl_and_drop_info():
    out = (
        '{"template-id":"CVE-2021-1234","info":{"name":"Remote Code Exec","severity":"critical"},'
        '"matched-at":"http://t:4000/api/upload"}\n'
        '{"template-id":"tech-detect","info":{"name":"Nginx","severity":"info"},"matched-at":"http://t:4000"}\n'
        'not json\n'
        '{"template-id":"missing-hsts","info":{"name":"HSTS missing","severity":"low"},"matched-at":"http://t:4000/"}\n')
    f = attack_plan.nuclei_findings(out)
    titles = " ".join(x["title"] for x in f)
    assert "Remote Code Exec" in titles and "/api/upload" in titles
    assert "Nginx" not in titles, "info 심각도는 버린다"
    sev = {x["severity"] for x in f}
    assert sev == {"critical", "low"}


def test_xss_finding_only_on_unescaped_reflection():
    cmd = attack_plan.xss_probe("t", 4000, {"method": "GET", "path": "/s", "params": ["q"]})
    # 디코드돼 그대로 반사되면 취약
    hit = attack_plan.curl_probe_findings("<div>neoxss<img/src=x></div>", cmd)
    assert hit and hit[0]["severity"] == "high" and "/s" in hit[0]["title"]
    # 인코드된 채 반사되면(=이스케이프됨) 취약 아님
    assert attack_plan.curl_probe_findings("neoxss%3Cimg%2Fsrc%3Dx%3E", cmd) == []


def test_ssti_finding_on_evaluated_expression():
    cmd = attack_plan.ssti_probe("t", 4000, {"method": "GET", "path": "/hello", "params": ["name"]})
    assert attack_plan.curl_probe_findings("Hello neossti49 world", cmd)[0]["severity"] == "critical"
    # 평가 안 되고 그대로면 취약 아님
    assert attack_plan.curl_probe_findings("Hello neossti{{7*7}}", cmd) == []


def test_lfi_finding_on_passwd_contents():
    cmd = attack_plan.lfi_probe("t", 4000, {"method": "GET", "path": "/download", "params": ["file"]})
    hit = attack_plan.curl_probe_findings("root:x:0:0:root:/root:/bin/bash\n", cmd)
    assert hit and hit[0]["severity"] == "critical" and "/download" in hit[0]["title"]
    assert attack_plan.curl_probe_findings("file not found", cmd) == []


def test_access_finding_on_2xx_without_auth():
    cmd = attack_plan.access_probe("t", 4000, {"method": "GET", "path": "/admin", "auth": True})
    assert attack_plan.curl_probe_findings("NEOACCESS:200", cmd)[0]["severity"] == "medium"
    # 401/403 이면 접근통제가 정상 — 발견 아님
    assert attack_plan.curl_probe_findings("NEOACCESS:401", cmd) == []
    assert attack_plan.curl_probe_findings("NEOACCESS:403", cmd) == []


def test_ffuf_only_flags_sensitive_paths():
    out = ("index.html             [Status: 200, Size: 100, Words: 1, Lines: 1]\n"
           ".git/config            [Status: 200, Size: 92, Words: 1, Lines: 1]\n"
           "backup.sql             [Status: 200, Size: 999, Words: 1, Lines: 1]\n"
           "about                  [Status: 200, Size: 50, Words: 1, Lines: 1]\n")
    f = attack_plan.ffuf_findings(out)
    paths = " ".join(x["title"] for x in f)
    assert ".git/config" in paths and "backup.sql" in paths
    assert "index.html" not in paths and "about" not in paths, "일반 200 은 노이즈라 제외"
    assert all(x["severity"] == "high" for x in f)


def test_observed_findings_dispatches_by_command():
    hist = [
        {"command": 'sqlmap -u "http://t:4000/login" --data="email=1"',
         "result": {"ok": True, "output": "injection point(s)\nParameter: email\nback-end DBMS: MySQL"}},
        {"command": attack_plan.lfi_probe("t", 4000, {"method": "GET", "path": "/dl", "params": ["f"]}),
         "result": {"ok": True, "output": "root:x:0:0:root:/root:/bin/bash"}},
        {"command": "nuclei -u http://t:4000 -jsonl",
         "result": {"ok": True, "output": '{"template-id":"x","info":{"name":"Open Redirect","severity":"medium"},"matched-at":"http://t:4000/go"}'}},
        {"command": "whatweb http://t:4000", "result": {"ok": True, "output": "nginx"}},  # 발견 없음
    ]
    f = attack_plan.observed_findings(hist)
    sources = {x["source"] for x in f}
    assert sources == {"sqlmap", "lfi", "nuclei"}
    assert len(f) == 3
