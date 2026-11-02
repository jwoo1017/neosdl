"""finding_verdict — 도구 증거 × 코드 증거 판정표(오프라인)."""

from app import finding_verdict as fv


def _sink(line=9, dangerous=True, construction="concat_input", category="sql_injection"):
    return {"category": category, "label": "SQL 쿼리 실행", "callee": "db.query", "file": "src/repo.js",
            "line": line, "code": "db.query(sql)", "arg": "sql", "construction": construction,
            "construction_ko": "…", "dangerous": dangerous, "tainted": ["email"] if dangerous else [],
            "function": "findByEmail",
            "chain": [{"file": "src/routes/r.js", "line": 3, "name": "POST /login", "kind": "route"},
                      {"file": "src/ctrl.js", "line": 8, "name": "login", "kind": "handler"},
                      {"file": "src/repo.js", "line": line, "name": "db.query", "kind": "sink"}]}


def _ep(path="/login", sinks_=None, method="POST"):
    sinks_ = sinks_ if sinks_ is not None else [_sink()]
    return {"method": method, "path": path, "file": "src/ctrl.js", "line": 8, "route_file": "src/routes/r.js",
            "route_line": 3,
            "sink_profile": {"handler": {"file": "src/ctrl.js", "line": 8, "name": "login", "kind": "handler"},
                             "sinks": sinks_, "dangerous": [s for s in sinks_ if s["dangerous"]], "resolved": True}}


def _sqlmap(path="/login"):
    return {"title": f"SQL 인젝션 확인 — {path}", "severity": "critical", "source": "sqlmap",
            "evidence": f"sqlmap: parameter=email, back-end DBMS=MySQL; path={path}"}


def _hist(path="/login", clean=True):
    out = "[WARNING] POST parameter 'email' does not seem to be injectable" if clean else "is vulnerable"
    return [{"command": f'sqlmap -u "http://t:4000{path}" --data="email=1" --batch',
             "result": {"ok": True, "output": out}}]


def test_tool_confirmed_with_dangerous_sink_anchors_to_sink_line():
    r = fv.reconcile([_sqlmap()], [_ep()], _hist(clean=False))
    [f] = r["findings"]
    assert f["status"] == "confirmed" and f["severity"] == "critical"
    assert (f["file"], f["line"]) == ("src/repo.js", 9)
    assert f["sink"]["callee"] == "db.query" and f["code_trace"][-1]["kind"] == "sink"


def test_tool_confirmed_without_code_sink_anchors_handler_with_note():
    r = fv.reconcile([_sqlmap()], [_ep(sinks_=[])], [])
    [f] = r["findings"]
    assert f["status"] == "confirmed" and (f["file"], f["line"]) == ("src/ctrl.js", 8)
    assert "코드에서 위험한 구성을 찾지 못했" in f["code_note"]


def test_code_only_dangerous_sink_is_suspected_medium():
    r = fv.reconcile([], [_ep()], _hist(clean=True))
    [f] = r["findings"]
    assert f["status"] == "suspected" and f["severity"] == "medium" and f["source"] == "code"
    assert (f["file"], f["line"]) == ("src/repo.js", 9) and f["tool_tested"] is True


def test_safe_sinks_and_clean_tool_produce_no_finding_but_safe_note():
    r = fv.reconcile([], [_ep(sinks_=[_sink(5, False, "parameterized")])], _hist(clean=True))
    assert r["findings"] == []
    [s] = r["safe"]
    assert s["endpoint"] == "POST /login" and s["tested"] is True


def test_llm_only_claim_is_discarded_even_if_it_pretends_tool_source():
    # 사건 재현: LLM 이 'SQL Injection Vulnerability'(high) + signup URL 을 적었다. sqlmap 은 signup 을 안 돌렸다.
    llm = {"title": "SQL Injection Vulnerability", "severity": "high", "source": "llm",
           "evidence": "http://172.18.0.3:4000/api/v1/auth/signup"}
    r = fv.reconcile([llm], [_ep("/api/v1/auth/signup", [_sink(5, False, "parameterized")])], [])
    assert r["findings"] == [] and r["discarded"][0]["title"] == "SQL Injection Vulnerability"
    no_source = {"title": "XSS", "severity": "high"}
    assert fv.reconcile([no_source], [], [])["findings"] == []


def test_same_sink_reached_from_two_endpoints_is_one_suspect():
    r = fv.reconcile([], [_ep("/a"), _ep("/b")], [])
    [f] = r["findings"]
    assert f["endpoints"] == ["POST /a", "POST /b"]


def test_hygiene_findings_pass_through_confirmed():
    nikto = {"title": "/ — x-powered-by", "severity": "low", "source": "nikto", "evidence": "nikto: /"}
    [f] = fv.reconcile([nikto], [], [])["findings"]
    assert f["status"] == "confirmed" and f["source"] == "nikto"
