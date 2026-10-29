"""블랙박스 발견 설명·소스 앵커·요약(app/blackbox_explain.py) — 오프라인(주입 reader/LLM)."""

from __future__ import annotations

from app import blackbox_explain as bx

_ENTRY = ("server.js", "app.js")
_SERVER = "\n".join([
    "const express = require('express');",
    "const cors = require('cors');",
    "",
    "const app = express();",
    "app.use(cors());",
    "app.use(express.json());",
    "",
    "app.listen(4000);",
])
_SRC = {"src/server.js": _SERVER,
        "src/controllers/authController.js": "\n".join(f"line{i}" for i in range(1, 41))}


def _reader(path):
    return _SRC.get(path)


def _nikto(desc):
    return {"title": f"/ — {desc}", "severity": "low", "evidence": "nikto: /", "source": "nikto"}


def test_explain_maps_tool_jargon_to_plain_korean():
    e = bx.explain(_nikto("Suggested security header missing: referrer-policy. See: https://developer.mozilla.org/x"))
    assert e["kind"] == "header" and "Referrer" in e["label"] and e["plain"] and e["fix"]
    assert bx.explain(_nikto("Retrieved access-control-allow-origin header: *."))["kind"] == "cors"
    unknown = bx.explain(_nikto("Something odd. See: https://example.com/a"))
    assert unknown["kind"] == "info" and "See:" not in unknown["label"]


def test_header_findings_anchor_to_app_creation_and_cors_to_cors_call():
    fs = [_nikto("Retrieved x-powered-by header: Express."),
          _nikto("Retrieved access-control-allow-origin header: \\*.")]
    bx.attach_code(fs, reader=_reader, module_paths=list(_SRC), entry_names=_ENTRY)
    assert (fs[0]["file"], fs[0]["line"]) == ("src/server.js", 4)        # const app = express()
    assert (fs[1]["file"], fs[1]["line"]) == ("src/server.js", 5)        # app.use(cors())
    snip = fs[1]["snippet"]
    assert snip["start"] == 1 and snip["focus"] == 5 and snip["lines"][4] == "app.use(cors());"


def test_sqli_keeps_handler_location_and_gets_plus_minus_five_lines():
    f = {"title": "SQL 인젝션 확인 — sqlmap ...", "severity": "critical", "evidence": "sqlmap: parameter=email",
         "file": "src/controllers/authController.js", "line": 20}
    bx.attach_code([f], reader=_reader, module_paths=list(_SRC), entry_names=_ENTRY)
    assert f["label"] == "SQL 인젝션 확인"
    s = f["snippet"]
    assert (s["start"], s["focus"], len(s["lines"])) == (15, 20, 11) and s["lines"][5] == "line20"


def test_without_source_only_explanations_are_added():
    f = _nikto("Suggested security header missing: content-security-policy.")
    bx.attach_code([f], reader=None, module_paths=[], entry_names=_ENTRY)
    assert f["label"] and "file" not in f and "snippet" not in f


def test_summary_falls_back_when_llm_fails_and_mentions_clean_sqlmap():
    fs = [dict(_nikto("x-powered-by"), label="서버 기술 정보 노출")]
    hist = [{"command": 'sqlmap -u "http://172.18.0.3:4000/api/v1/auth/login" --data="email=1"',
             "result": {"ok": True, "output": "[WARNING] POST parameter 'email' does not seem to be injectable"}}]

    def boom(prompt):
        raise RuntimeError("gateway down")

    text = bx.summarize(fs, hist, boom)
    assert "총 1건" in text and "/api/v1/auth/login" in text
    assert bx.summarize(fs, hist, lambda p: "헤더 설정만 정리하면 되는 수준이며 바로 뚫리는 취약점은 없습니다.") \
        .startswith("헤더 설정만")
    assert bx.summarize([], [], None) == "점검한 범위에서 확인된 취약점이 없습니다."
