"""app/dynamic/probes.py + oracle.py + entrypoint 정규식 — 순수 단위 테스트(DB/LLM 없음)."""

from __future__ import annotations

from app.dynamic import oracle, probes
from app.dynamic.entrypoint import _extract_route, _param_from_source_var


# --------------------------------------------------------------- probes
def test_probes_are_benign_and_shaped():
    for cat in probes.CATEGORIES:
        p = probes.synthesize(cat, finding_id=7)
        assert p is not None and p.category == cat
        assert p.nonce.startswith("neosdl-7-")
        # 안전 문자 클래스 통과(파괴 토큰 없음)
        assert probes._check(cat, p.probe_value)
        assert probes._check(cat, p.control_value)


def test_sqli_forbidden_tokens_rejected():
    assert probes._check("sql_injection", "1'") is True       # 읽기전용 따옴표 차분은 허용
    for bad in ["1; DROP TABLE x", "1 OR 1=1", "1 UNION SELECT", "1--", "a'/*"]:
        assert probes._check("sql_injection", bad) is False


def test_xss_probe_has_no_script_or_handler():
    p = probes.synthesize("xss", finding_id=1)
    assert "script" not in p.probe_value.lower()
    assert "onerror" not in p.probe_value.lower() and "=" not in p.probe_value


def test_validate_llm_fill_rejects_unknown_template_and_bad_value():
    assert probes.validate_llm_fill("sql_injection", "sqli_quote_error", "1", "1'") is True
    assert probes.validate_llm_fill("sql_injection", "evil_tpl", "1", "1'") is False
    assert probes.validate_llm_fill("sql_injection", "sqli_quote_error", "1", "1; DROP") is False


# --------------------------------------------------------------- oracle
def test_oracle_iast_reached_is_decisive():
    v = oracle.decide("sql_injection", probe_body="", iast_reached=True, entry_confidence=0.8)
    assert v.verdict == "confirmed" and v.confidence > 0.6
    v = oracle.decide("sql_injection", probe_body="", iast_reached=False, entry_confidence=0.8)
    assert v.verdict == "false_positive" and v.confidence > 0.6


def test_oracle_sqli_error_differential():
    # probe 에서만 DB 파싱 에러 → confirmed
    v = oracle.decide("sql_injection", control_body="ok rows", probe_body="You have an error in your SQL syntax near \"'\"")
    assert v.verdict == "confirmed"
    # 따옴표 넣어도 응답 동일(바인딩) → false_positive  (이번 9건 패턴)
    v = oracle.decide("sql_injection", control_body="same", probe_body="same")
    assert v.verdict == "false_positive"


def test_oracle_xss_reflection():
    marker = "<neo-abc123>"   # probes 가 만드는 실제 마커 모양(꺾쇠 포함)
    # 미이스케이프 반사 → confirmed
    v = oracle.decide("xss", probe_body=f"<div>{marker}</div>", nonce=marker)
    assert v.verdict == "confirmed"
    # 이스케이프되어 반사 → false_positive
    v = oracle.decide("xss", probe_body=f"<div>{oracle._html_escaped(marker)}</div>", nonce=marker)
    assert v.verdict == "false_positive"
    # 아예 반사 안 됨 → inconclusive
    v = oracle.decide("xss", probe_body="<div>nothing</div>", nonce=marker)
    assert v.verdict == "inconclusive"


def test_oracle_command_injection_echo_and_timing():
    v = oracle.decide("command_injection", probe_body="out: neosdl_1_aa", nonce="neosdl_1_aa")
    assert v.verdict == "confirmed"
    v = oracle.decide("command_injection", probe_body="no marker", nonce="neosdl_1_aa", timing_delta_s=5.0)
    assert v.verdict == "confirmed"
    v = oracle.decide("command_injection", probe_body="no marker", nonce="neosdl_1_aa", timing_delta_s=0.1)
    assert v.verdict == "inconclusive"


# --------------------------------------------------------------- entrypoint regex
def test_extract_route_frameworks():
    assert _extract_route("router.get('/users/:id', h)") == ("GET", "/users/:id")
    assert _extract_route("@PostMapping(\"/login\")") == ("POST", "/login")
    assert _extract_route("@app.get('/search')") == ("GET", "/search")
    assert _extract_route("@app.route('/submit', methods=['POST'])") == ("POST", "/submit")
    assert _extract_route("def helper(): pass") is None


def test_param_from_source_var():
    assert _param_from_source_var("req.query.id") == "id"
    assert _param_from_source_var("req.body.email") == "email"
    assert _param_from_source_var("request.args.get('q')") == "q"
    assert _param_from_source_var("uid") is None
