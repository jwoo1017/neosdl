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


# ------------------------------------------------------------- Python(FastAPI) 레포 · 중복 합치기

_PY_MAIN = "\n".join([
    "from fastapi import FastAPI",
    "from fastapi.middleware.cors import CORSMiddleware",
    "",
    'app = FastAPI(title="x")',
    'app.add_middleware(CORSMiddleware, allow_origins=["*"])',
])
_PY_SRC = {"app/main.py": _PY_MAIN}


def test_fastapi_repo_gets_anchors_and_python_fix():
    # neosdl 자체(FastAPI)를 부팅해 점검했을 때 코드 위치가 통째로 빠지던 회귀
    fs = [_nikto("Suggested security header missing: content-security-policy."),
          _nikto("Retrieved access-control-allow-origin header: *.")]
    bx.attach_code(fs, reader=_PY_SRC.get, module_paths=list(_PY_SRC), entry_names=("main.py",))
    assert (fs[0]["file"], fs[0]["line"]) == ("app/main.py", 4)        # app = FastAPI(
    assert (fs[1]["file"], fs[1]["line"]) == ("app/main.py", 2)        # CORSMiddleware 첫 등장
    assert "helmet" not in fs[0]["fix"] and "@app.middleware" in fs[0]["fix"]
    assert "CORSMiddleware" in fs[1]["fix"]
    assert fs[0]["snippet"]["focus"] == 4


def test_express_repo_keeps_express_fix():
    fs = [_nikto("Suggested security header missing: content-security-policy.")]
    bx.attach_code(fs, reader=_reader, module_paths=list(_SRC), entry_names=_ENTRY)
    assert "helmet" in fs[0]["fix"]


def test_header_hygiene_findings_collapse_to_one_helmet_finding():
    """보안 헤더 누락들은 helmet() 한 건으로 묶인다(= 전부 같은 줄을 가리켜 '다 server.js' 되던 것 해소).
    개별 항목은 also·evidence 로 보존하고, 사전에 없는 발견(info)은 묶지 않는다."""
    fs = [_nikto("Suggested security header missing: content-security-policy."),
          _nikto("Retrieved x-content-type-options header is missing."),
          {**_nikto("CSP header missing on /api"), "severity": "medium"},
          _nikto("X-Content-Type-Options header not set."),
          _nikto("Something odd A"), _nikto("Something odd B")]
    bx.attach_code(fs, reader=None, module_paths=[], entry_names=_ENTRY)
    heads = [f for f in fs if f.get("kind") == "header"]
    assert len(heads) == 1                                           # 헤더 위생은 한 건으로
    merged = heads[0]
    assert merged["label"] == "보안 헤더 여러 개 누락" and "helmet" in merged["fix"]
    assert merged["severity"] == "medium"                           # 묶음은 더 높은 심각도를 따른다
    assert len(merged["also"]) == 2                                  # CSP·MIME 원문 보존(dedupe 후)
    assert sum(f["kind"] == "info" for f in fs) == 2                 # 사전에 없는 발견은 안 묶는다
    assert len(fs) == 3                                             # 묶음1 + info2


def test_sqli_findings_are_never_merged():
    fs = [{"title": "SQL 인젝션 확인 — /login", "severity": "critical", "evidence": "", "file": "a.js", "line": 1},
          {"title": "SQL 인젝션 확인 — /signup", "severity": "critical", "evidence": "", "file": "a.js", "line": 9}]
    bx.attach_code(fs, reader=None, module_paths=[], entry_names=_ENTRY)
    assert len(fs) == 2
