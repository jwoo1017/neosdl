"""security_finding 지문 순수 로직 — DB 없이 (app/security_fingerprint.py).

Risk Identity 는 이 지문으로 정의된다: 라인 이동/로컬 변수명엔 불변, 파일·함수
rename 엔 새 지문(문서화된 Phase 1 한계).
"""

from __future__ import annotations

from app.security_fingerprint import compute_fingerprint

_BASE = {
    "kind": "security",
    "category": "sql_injection",
    "sink_symbol_key": "python:11111111-1111-1111-1111-111111111111:app/db.py#run_query(any)",
    "source_var": "req.body.userId",
    "path": [
        {"symbol_key": "python:11111111-1111-1111-1111-111111111111:app/api.py#handle(any)",
         "var": "name", "line": 12},
        {"symbol_key": "python:11111111-1111-1111-1111-111111111111:app/db.py#run_query(any)",
         "var": "q", "line": 40},
    ],
}


def _fp(**over):
    return compute_fingerprint({**_BASE, **over})


def test_deterministic():
    assert _fp() == _fp()


def test_stable_under_line_move():
    moved = {
        "path": [
            {"symbol_key": _BASE["path"][0]["symbol_key"], "var": "name", "line": 999},
            {"symbol_key": _BASE["path"][1]["symbol_key"], "var": "q", "line": 1234},
        ],
    }
    assert _fp(**moved) == _fp()


def test_stable_under_local_var_rename():
    # source_var 이 같은 소스 클래스(http.req)면 이름이 달라도 같은 지문
    assert _fp(source_var="req.body.email") == _fp()
    # hop 의 var 도 지문에 안 들어감
    renamed_hops = {"path": [
        {"symbol_key": _BASE["path"][0]["symbol_key"], "var": "userInput", "line": 12},
        {"symbol_key": _BASE["path"][1]["symbol_key"], "var": "sql", "line": 40},
    ]}
    assert _fp(**renamed_hops) == _fp()


def test_changes_on_file_rename():
    renamed = _BASE["sink_symbol_key"].replace("app/db.py", "app/database.py")
    assert _fp(sink_symbol_key=renamed) != _fp()


def test_changes_on_function_rename():
    renamed_path = [
        _BASE["path"][0],
        {"symbol_key": _BASE["path"][1]["symbol_key"].replace("#run_query(", "#exec_query("),
         "var": "q", "line": 40},
    ]
    assert compute_fingerprint({**_BASE, "path": renamed_path}) != _fp()


def test_changes_on_category():
    assert _fp(category="xss") != _fp()


def test_repo_id_stripped():
    other_repo = _BASE["sink_symbol_key"].replace(
        "11111111-1111-1111-1111-111111111111", "99999999-9999-9999-9999-999999999999")
    other_path = [
        {"symbol_key": h["symbol_key"].replace(
            "11111111-1111-1111-1111-111111111111", "99999999-9999-9999-9999-999999999999"),
         "var": h["var"], "line": h["line"]}
        for h in _BASE["path"]
    ]
    assert compute_fingerprint({**_BASE, "sink_symbol_key": other_repo, "path": other_path}) == _fp()


def test_business_finding_stable():
    biz = {
        "kind": "business", "category": "domain_crossing:결제",
        "sink_symbol_key": "js:22222222-2222-2222-2222-222222222222:pay/ledger.js#post(any)",
        "source_var": "", "path": [],
    }
    fp = compute_fingerprint(biz)
    assert fp and fp == compute_fingerprint(dict(biz))


def test_source_class_folding():
    # process.env.X 와 os.environ["Y"] 는 둘 다 env 클래스
    a = _fp(source_var="process.env.SECRET")
    b = _fp(source_var="os.environ.get('TOKEN')")
    assert a == b
    # 알 수 없는 소스는 숫자만 제거한 소문자 — arg1/arg2 를 합침
    assert _fp(source_var="arg1") == _fp(source_var="arg2")
