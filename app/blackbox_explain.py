"""블랙박스 발견을 비전문가가 읽을 수 있게 — 쉬운 설명·수정법·관련 소스·AI 요약.

두 종류를 다르게 다룬다:
- **주입류(SQL·XSS·SSTI·LFI·명령·eval·NoSQL)** — 판정(app/finding_verdict.py)이 sink_trace 로 찾은
  **실제 sink 줄**을 들고 온다. 설명·고치는 법은 그 sink 의 사실(호출·섞인 요청 값·쿼리 구성)로 만든다.
  예전엔 제목에 `sql inj` 만 있으면 고정 문구("입력값이 DB 쿼리에 그대로 이어 붙어…")를 붙였고, 그게
  쿼리가 없는 컨트롤러 선언 줄 옆에 나가 "코드를 안 보고 답한다"는 지적을 받았다(2026-11-02).
  LLM 수정 패치는 원문 줄과 대조해 맞을 때만 싣는다.
- **위생류(보안 헤더·CORS·노출 파일)** — nikto 영어 원문을 사전으로 한국어화하고 앱 생성/cors/정적 서빙
  줄로 앵커링한다(호스트 단위 설정이라 엔드포인트 sink 가 없다).

DB·소스 읽기는 주입받은 reader 로만(오프라인 단위 테스트). 소스는 로컬에만 머문다.
"""

from __future__ import annotations

import logging
import re

from app import sinks

log = logging.getLogger("app.blackbox_explain")

SNIPPET_RADIUS = 5

# (매칭 정규식, kind, label, plain, fix) — 위에서부터 첫 매칭. kind 는 소스 앵커 종류.
_GLOSSARY = [
    (re.compile(r"접근통제 결함|broken access|비인증 접근", re.I), "access", "접근통제 결함(비인증 접근)",
     "로그인이 필요한 엔드포인트가 인증 없이도 응답합니다. 남의 데이터 열람·변조(IDOR 포함) 위험이 있습니다.",
     "서버에서 모든 보호 라우트에 인증·인가 미들웨어를 강제하고, 객체 소유권(본인 자원인지)을 매 요청 확인하세요."),
    # nikto 가 영어 원문으로 내는 '웹에 노출된 파일/디렉터리'류 — 사전이 없으면 info 로 떨어져 영어 그대로
    # 나오고 소스 줄도 안 붙던 것(사용자 지적). 소스 엔드포인트가 아니라 정적 서빙·배포 설정 문제라
    # kind=exposure 로 묶어 한국어 설명 + 정적 서빙 줄 앵커를 붙인다.
    (re.compile(r"\.(?:bash|sh|zsh|ksh|mysql|psql|sqlite)_history|shell history|"
                r"home directory may be set to the web root|database sql", re.I), "exposure",
     "명령 기록 파일 노출(셸/DB 히스토리)",
     "서버의 셸·DB 접속 기록 파일(.bash_history, .mysql_history 등)이 웹에 그대로 열립니다. 과거 실행한 명령과, 때로는 평문 비밀번호까지 읽힙니다.",
     "홈 디렉터리를 웹 루트로 쓰지 말고, 정적 서빙 디렉터리에서 점(.)으로 시작하는 숨김 파일이 노출되지 않게 차단하세요."),
    (re.compile(r"\.htpasswd|authorization information", re.I), "exposure", "인증 정보 파일(.htpasswd) 노출",
     "HTTP 기본 인증의 사용자명·비밀번호 해시가 담긴 .htpasswd 파일이 웹에서 열립니다. 해시를 받아 크랙하면 계정이 뚫립니다.",
     "이 파일을 웹 루트 밖으로 옮기거나 웹 서버에서 접근을 차단(deny)하세요. 노출됐다면 해당 비밀번호를 즉시 교체하세요."),
    (re.compile(r"might be interesting|directory indexing|index of /", re.I), "exposure", "노출된 경로/디렉터리",
     "숨겨져 있어야 할 경로나 디렉터리 목록이 웹에 노출됩니다. 내부 파일 구조·업로드물·백업이 드러나 추가 공격의 발판이 됩니다.",
     "필요 없으면 해당 경로를 제거하고, 디렉터리 리스팅을 끄거나(autoindex off / express.static 의 자동 인덱싱 비활성) 접근을 제한하세요."),
    (re.compile(r"민감 경로 노출|민감파일|sensitive (?:path|file)", re.I), "exposure", "민감 경로/파일 노출",
     "소스·백업·설정·비밀 파일(.git, .env, *.sql 등)이 웹에서 바로 내려받아집니다. 그대로 자격증명·코드 유출로 이어집니다.",
     "해당 경로를 웹루트에서 제거하거나 접근 차단(deny)하고, 비밀은 환경변수·시크릿 매니저로 옮긴 뒤 노출된 값은 즉시 교체하세요."),
    (re.compile(r"x-powered-by", re.I), "header", "서버 기술 정보 노출",
     "응답 헤더에 'Express' 같은 서버 종류가 그대로 나갑니다. 바로 뚫리진 않지만 공격자가 맞춤 공격을 고르는 단서가 됩니다.",
     "`app.disable('x-powered-by')` 를 추가하거나 `app.use(helmet())` 을 적용하세요."),
    (re.compile(r"access-control-allow-origin", re.I), "cors", "CORS 전체 허용(*)",
     "어느 웹사이트에서든 브라우저로 이 API 를 호출할 수 있게 열려 있습니다. 인증과 엮이면 다른 사이트가 사용자 대신 데이터를 읽어갈 수 있습니다.",
     "`cors({ origin: ['https://우리-도메인'] })` 처럼 허용할 출처를 명시하세요."),
    (re.compile(r"content-security-policy|\bcsp\b", re.I), "header", "CSP(콘텐츠 보안 정책) 없음",
     "페이지가 어떤 스크립트를 실행해도 되는지 제한이 없어, XSS 가 하나라도 생기면 피해가 커집니다.",
     "`app.use(helmet())` 으로 기본 CSP 를 켜고, 필요한 출처만 허용하도록 조정하세요."),
    (re.compile(r"strict-transport-security", re.I), "header", "HTTPS 강제(HSTS) 없음",
     "브라우저가 이 사이트를 항상 HTTPS 로만 접속해야 한다는 걸 모릅니다. 공용 와이파이 등에서 HTTP 로 낮춰 가로챌 여지가 있습니다.",
     "HTTPS 로 운영한다면 `helmet()`(HSTS 포함)을 적용하세요."),
    (re.compile(r"x-content-type-options", re.I), "header", "파일 형식 추측(MIME 스니핑) 방지 없음",
     "브라우저가 응답 형식을 멋대로 추측해, 업로드 파일 등을 스크립트로 실행할 여지가 생깁니다.",
     "`helmet()` 을 적용하면 `X-Content-Type-Options: nosniff` 가 붙습니다."),
    (re.compile(r"referrer-policy", re.I), "header", "Referrer-Policy 없음",
     "다른 사이트로 이동할 때 현재 URL(토큰·ID 가 들어 있을 수 있음)이 그대로 넘어갈 수 있습니다.",
     "`helmet()` 을 적용하거나 `Referrer-Policy: no-referrer` 를 설정하세요."),
    (re.compile(r"permissions-policy", re.I), "header", "Permissions-Policy 없음",
     "카메라·마이크·위치 같은 브라우저 기능 사용 범위를 제한하지 않습니다. API 서버라면 영향은 작습니다.",
     "필요 없는 기능을 끄는 `Permissions-Policy` 헤더를 추가하세요."),
    (re.compile(r"x-frame-options|frame-ancestors|clickjack", re.I), "header", "클릭재킹 방지 설정 없음/구식",
     "다른 사이트가 이 페이지를 iframe 으로 몰래 감싸 사용자의 클릭을 가로챌 수 있습니다.",
     "`helmet()` 의 CSP `frame-ancestors 'self'` 로 막으세요."),
]

# kind → 소스에서 찾을 앵커 줄(위에서부터 우선). 헤더류는 앱 생성 지점에 미들웨어를 추가하게 된다.
# Express 뿐 아니라 Python(FastAPI/Flask) 앱도 — 예전엔 JS 패턴만 있어서 FastAPI 레포를 부팅해
# 점검하면 앵커를 못 찾아 "코드 위치"가 통째로 빠졌다(neosdl 자체 점검에서 발견).
_ANCHORS = {
    "cors": [re.compile(r"\bcors\s*\("), re.compile(r"\bCORSMiddleware\b"), re.compile(r"\bCORS\s*\(\s*app\b"),
             re.compile(r"Access-Control-Allow-Origin", re.I)],
    "header": [re.compile(r"\bhelmet\s*\("), re.compile(r"=\s*express\s*\(\s*\)"),
               re.compile(r"=\s*FastAPI\s*\("), re.compile(r"=\s*Flask\s*\("),
               re.compile(r"\.listen\s*\("), re.compile(r"\buvicorn\.run\s*\(")],
    # 노출 파일/디렉터리는 소스 엔드포인트가 없다 — '어디서 고치나'는 정적 서빙 설정 줄이다(express.static,
    # app.use('/public', …), serveStatic, FastAPI StaticFiles/mount). 없으면(=웹서버가 서빙) 앵커 없이 설명만.
    "exposure": [re.compile(r"express\.static\s*\("), re.compile(r"\bserveStatic\b"),
                 re.compile(r"\.use\(\s*['\"][^'\"]*(?:public|static|files|uploads|ftp)", re.I),
                 re.compile(r"\bStaticFiles\s*\("), re.compile(r"\.mount\s*\(")],
}

# Python 앱(앵커 파일이 .py)일 때의 고치는 법 — 사전의 fix 는 Express(helmet) 기준이라.
_PY_HEADER_FIX = ("앱 생성 직후 `@app.middleware(\"http\")` 로 응답 헤더 `{h}` 를 붙이세요"
                  "(또는 `secure` 패키지 같은 보안 헤더 미들웨어).")
_PY_FIX = {
    "CORS 전체 허용(*)": "`CORSMiddleware(allow_origins=[\"https://우리-도메인\"])` 처럼 허용할 출처를 명시하세요.",
    "서버 기술 정보 노출": "uvicorn 이면 `--no-server-header`(또는 `server_header=False`)로 서버 종류 헤더를 숨기세요.",
    "CSP(콘텐츠 보안 정책) 없음": _PY_HEADER_FIX.format(h="Content-Security-Policy: default-src 'self'"),
    "HTTPS 강제(HSTS) 없음": _PY_HEADER_FIX.format(h="Strict-Transport-Security: max-age=31536000"),
    "파일 형식 추측(MIME 스니핑) 방지 없음": _PY_HEADER_FIX.format(h="X-Content-Type-Options: nosniff"),
    "Referrer-Policy 없음": _PY_HEADER_FIX.format(h="Referrer-Policy: no-referrer"),
    "Permissions-Policy 없음": _PY_HEADER_FIX.format(h="Permissions-Policy: camera=(), microphone=(), geolocation=()"),
    "클릭재킹 방지 설정 없음/구식": _PY_HEADER_FIX.format(h="Content-Security-Policy: frame-ancestors 'self'"),
}
_SEV_KO = {"critical": "치명", "high": "높음", "medium": "중간", "low": "낮음"}
_SEV_ORDER = ("critical", "high", "medium", "low")
_SQLMAP_CLEAN = re.compile(r"do(?:es)? not (?:seem|appear) to be injectable", re.I)
_URL_PATH = re.compile(r"https?://[^/\s\"']+(/[^\s\"'?]*)")


# 주입류 — 영향 한 문장(무엇이 가능해지나). 원인·위치·수정은 sink 사실로 따로 만든다.
_IMPACT = {
    sinks.SQL: "공격자가 쿼리 구조를 바꿔 로그인 우회·데이터 열람/변조를 할 수 있습니다.",
    sinks.NOSQL: "`{\"$ne\": null}` 같은 연산자 객체로 조건을 무력화해 로그인 우회·데이터 열람이 가능합니다.",
    sinks.CMD: "공격자가 서버에서 임의 셸 명령을 실행할 수 있습니다(서버 장악).",
    sinks.CODE: "공격자가 서버에서 임의 코드를 실행할 수 있습니다(서버 장악).",
    sinks.SSTI: "템플릿 엔진이 입력을 코드로 평가해 원격 코드 실행까지 번질 수 있습니다.",
    sinks.LFI: "`../` 로 서버의 임의 파일(소스·설정·비밀)을 읽거나 쓸 수 있습니다.",
    sinks.XSS: "공격자가 심은 스크립트가 피해자 브라우저에서 실행돼 세션 탈취·피싱으로 이어집니다.",
}
_EVIDENCE_PARAM = re.compile(r"parameter=([^,;\s]+)")


def _py_file(path: str | None) -> bool:
    return str(path or "").endswith(".py")


def _fix_for_sink(cat: str, sink: dict, path: str | None) -> str:
    """sink 사실로 만든 고치는 법 — 실제 호출·변수 이름을 그대로 쓴다."""
    callee = sink.get("callee") or "호출"
    vs = ", ".join(f"`{v}`" for v in (sink.get("tainted") or [])[:3]) or "요청 값"
    vlist = ", ".join((sink.get("tainted") or ["value"])[:3])
    loc = f"`{_base(path)}:{sink.get('line')}`"
    if cat == sinks.SQL:
        if _py_file(path):
            return (f"{loc} 의 쿼리 문자열에서 {vs} 를 빼고 자리표시자로 바꾼 뒤 값은 따로 넘기세요: "
                    f"`{callee}(\"… = %s\", ({vlist},))` (SQLAlchemy 면 `text(\"… = :v\")` + 파라미터 dict).")
        return (f"{loc} 의 쿼리 문자열에서 {vs} 를 빼고 자리표시자(`?`, pg 는 `$1`)로 바꾼 뒤 값은 두 번째 인자 배열로 "
                f"넘기세요: `{callee}('… = ?', [{vlist}])`. 테이블·컬럼명처럼 바인딩이 안 되는 자리는 허용 목록으로 검증하세요.")
    if cat == sinks.NOSQL:
        return (f"{loc} 에서 요청 값을 필터 객체째 넘기지 말고 필요한 필드만 문자열로 강제하세요"
                f"(`String({vlist})`), 또는 `express-mongo-sanitize` 로 `$` 연산자를 제거하세요.")
    if cat == sinks.CMD:
        return (f"{loc} 의 `{callee}` 대신 인자 배열을 받는 `execFile`/`spawn`(shell: false)을 쓰고, {vs} 는 허용 목록으로 "
                f"검증하세요. 셸 문자열에 요청 값을 이어 붙이면 안 됩니다.")
    if cat == sinks.CODE:
        return f"{loc} 의 `{callee}` 를 제거하고 `JSON.parse`·명시적 분기 등 데이터로만 처리하세요."
    if cat == sinks.SSTI:
        return (f"{loc} 에서 {vs} 를 템플릿 **소스**에 넣지 말고 렌더 변수로만 넘기세요"
                f"(예: `res.render('view', {{ {vlist} }})`). 템플릿을 문자열 연결로 만들지 마세요.")
    if cat == sinks.LFI:
        return (f"{loc} 에서 {vs} 를 경로에 쓰기 전에 `path.basename` 으로 디렉터리 성분을 떼고 고정 디렉터리와 join 한 뒤, "
                f"결과가 그 디렉터리 안인지(`startsWith`) 확인하세요. 가능하면 ID→파일 매핑 표를 쓰세요.")
    if cat == sinks.XSS:
        return (f"{loc} 의 `{callee}` 로 HTML 을 직접 만들지 말고 `res.json` 으로 응답하거나 {vs} 를 HTML 이스케이프하세요"
                f"(템플릿 엔진 자동 이스케이프 + `helmet()` CSP).")
    return f"{loc} 의 `{callee}` 에 {vs} 가 그대로 들어가지 않게 검증·이스케이프하세요."


def _base(path: str | None) -> str:
    return str(path or "").replace("\\", "/").rsplit("/", 1)[-1]


def _explain_code(f: dict) -> dict:
    """주입류 발견 → sink 사실 기반 {kind, label, plain, fix}."""
    cat = f.get("category")
    name = sinks.CATEGORY_KO.get(cat, "주입")
    confirmed = f.get("status") != "suspected"
    label = f"{name} 확인" if confirmed else f"{name} 의심(코드 분석)"
    sink = f.get("sink") or {}
    parts = []
    if sink:
        vs = ", ".join(f"`{v}`" for v in (sink.get("tainted") or [])[:3]) or "요청 값"
        how = {"concat_input": "문자열 연결/보간으로 그대로 들어갑니다",
               "direct_input": "가공 없이 그대로 전달됩니다"}.get(sink.get("construction"), "섞여 들어갑니다")
        parts.append(f"`{_base(sink.get('file'))}:{sink.get('line')}` 의 `{sink.get('callee')}` 호출에 요청 값 {vs} 가 {how}.")
    if confirmed:
        param = _EVIDENCE_PARAM.search(f.get("evidence") or "")
        tool = f.get("tool") or f.get("source") or "도구"
        parts.append(f"{tool} 가 실제 요청으로 주입에 성공했습니다"
                     + (f"(파라미터 `{param.group(1)}`)." if param and param.group(1) != "?" else "."))
    else:
        parts.append("자동 공격으로는 아직 확인되지 않은, 코드 분석상의 위험입니다.")
    if f.get("code_note"):
        parts.append(f["code_note"])
    parts.append(_IMPACT.get(cat, ""))
    fix = _fix_for_sink(cat, sink, sink.get("file")) if sink else (
        "도구가 확인한 엔드포인트의 핸들러부터 요청 값이 쿼리/명령/템플릿/경로로 이어지는 곳을 찾아 바인딩·이스케이프로 바꾸세요.")
    return {"kind": "vuln", "label": label, "plain": " ".join(p for p in parts if p), "fix": fix}


def explain(f: dict) -> dict:
    """발견 1건 → {kind, label, plain, fix}. 주입류는 sink 사실로, 위생류는 사전으로, 그 외는 원문 제목."""
    if f.get("category") in sinks.CATEGORY_KO:
        return _explain_code(f)
    hay = f"{f.get('title', '')} {f.get('evidence', '')}"
    for rx, kind, label, plain, fix in _GLOSSARY:
        if rx.search(hay):
            return {"kind": kind, "label": label, "plain": plain, "fix": fix}
    title = re.sub(r"\s*See:\s*https?://\S*", "", f.get("title") or "").strip()
    return {"kind": "info", "label": title[:120] or "기타 발견", "plain": None, "fix": None}


def _find_anchor(reader, module_paths: list[str], entry_names: tuple, kind: str) -> tuple | None:
    """kind 에 맞는 앵커 줄을 엔트리 파일에서 찾는다 → (path, line) 또는 None."""
    entries = [p for p in module_paths if p.replace("\\", "/").rsplit("/", 1)[-1].lower() in entry_names]
    entries.sort(key=lambda p: p.count("/"))          # 루트에 가까운 엔트리 우선
    srcs = [(p, reader(p) or "") for p in entries]
    for rx in _ANCHORS.get(kind, []):
        for path, src in srcs:
            for i, line in enumerate(src.splitlines(), 1):
                if rx.search(line):
                    return path, i
    return None


def snippet(lines: list[str], path: str, focus: int, radius: int = SNIPPET_RADIUS) -> dict:
    """focus 줄 ±radius 구간 → {path, start, focus, lines}. 프런트가 줄번호·강조를 그린다."""
    start = max(1, focus - radius)
    end = min(len(lines), focus + radius)
    return {"path": path, "start": start, "focus": focus, "lines": lines[start - 1:end]}


_SEV_RANK = {s: i for i, s in enumerate(("critical", "high", "medium", "low", "info"))}


def _dedupe(findings: list) -> list:
    """쉬운 이름이 같은 발견은 하나로 — nikto 는 같은 문제를 문구만 달리해 여러 번 낸다
    (CSP 누락 + frame-ancestors 등). SQLi 는 엔드포인트마다 따로 봐야 하고, 사전에 없는 발견(info)은
    원문이 서로 달라 합치지 않는다. 합친 원문은 `also` 에 남기고 심각도는 더 높은 쪽을 쓴다."""
    out: list = []
    seen: dict[tuple, dict] = {}
    for f in findings:
        kind = f.get("kind")
        # 호스트 단위 위생(헤더·CORS)만 합친다 — nikto 가 같은 문제를 문구만 달리해 여러 번 낸다.
        # 엔드포인트 단위 취약점(sqli/xss/ssti/lfi/access/exposure)은 경로마다 따로 봐야 하므로 합치지 않는다.
        if kind not in ("header", "cors"):
            out.append(f)
            continue
        key = (kind, f.get("label"))
        first = seen.get(key)
        if first is None:
            seen[key] = f
            out.append(f)
            continue
        first.setdefault("also", []).append(f.get("title"))
        if _SEV_RANK.get(f.get("severity"), 9) < _SEV_RANK.get(first.get("severity"), 9):
            first["severity"] = f["severity"]
    return out


def _collapse_headers(findings: list) -> list:
    """helmet() 한 줄로 한꺼번에 고치는 보안 헤더 누락들을 **하나의 발견**으로 묶는다.

    HSTS·CSP·MIME·Referrer·Permissions·x-powered-by 등은 각각 따로 뜨면 전부 같은 줄(express() 생성
    지점)을 가리켜 "다 server.js" 처럼 보이고 목록만 길어진다. 실제 고침은 `helmet()` 한 줄이므로 묶어
    보여주되, 개별 항목은 evidence·also 에 남겨 아무 것도 숨기지 않는다. CORS 는 고침·위치가 달라 제외."""
    heads = [f for f in findings if f.get("kind") == "header"]
    if len(heads) < 2:
        return findings
    names = []
    for f in heads:
        m = re.search(r"(x-[\w-]+|strict-transport-security|content-security-policy|referrer-policy|"
                      r"permissions-policy|x-frame-options)", f"{f.get('title','')} {f.get('evidence','')}", re.I)
        names.append(m.group(1).lower() if m else (f.get("label") or "헤더"))
    sev = min((f.get("severity", "low") for f in heads), key=lambda s: _SEV_RANK.get(s, 9))
    merged = {
        "kind": "header", "severity": sev, "source": "merged",
        "title": f"보안 헤더 여러 개 누락 — helmet() 미적용 ({len(heads)}건)",
        "label": "보안 헤더 여러 개 누락",
        "plain": f"응답에 보안 헤더 {len(heads)}개({', '.join(dict.fromkeys(names))})가 빠져 있습니다. "
                 "각각 바로 뚫리는 취약점은 아니지만, 한 번에 보완하는 게 좋습니다.",
        "fix": "`app.use(helmet())` 한 줄이면 대부분이 한꺼번에 설정됩니다(필요 시 CSP 만 조정).",
        "evidence": "missing: " + ", ".join(dict.fromkeys(names)),
        "also": [f.get("title") for f in heads],
    }
    out, placed = [], False
    for f in findings:
        if f.get("kind") == "header":
            if not placed:          # 첫 헤더 자리에 묶음 1건을 넣고 나머지 헤더는 버린다(also 로 보존)
                out.append(merged)
                placed = True
            continue
        out.append(f)
    return out


def attach_code(findings: list, *, reader, module_paths: list[str], entry_names: tuple) -> list:
    """발견마다 explain 결과 + 소스 앵커(file/line) + ±5줄 스니펫을 붙인다. reader 가 없으면 설명만.

    같은 문제의 중복 발견은 합친다 — 리스트를 제자리에서 바꾼다(호출측이 outcome.findings 를 그대로 쓴다).
    """
    for f in findings:
        info = explain(f)
        f.update({k: v for k, v in info.items() if v is not None})
    findings[:] = _collapse_headers(_dedupe(findings))
    anchors: dict[str, tuple | None] = {}
    for f in findings:
        info = {"kind": f.get("kind")}
        if reader is None:
            continue
        if not f.get("file") and info["kind"] in _ANCHORS:
            if info["kind"] not in anchors:
                anchors[info["kind"]] = _find_anchor(reader, module_paths, entry_names, info["kind"])
                if anchors[info["kind"]] is None and info["kind"] == "cors":   # cors() 없으면 앱 생성 줄
                    anchors["cors"] = _find_anchor(reader, module_paths, entry_names, "header")
            if anchors[info["kind"]]:
                f["file"], f["line"] = anchors[info["kind"]]
        if str(f.get("file") or "").endswith(".py") and f.get("label") in _PY_FIX:
            f["fix"] = _PY_FIX[f["label"]]
        if f.get("file") and f.get("line"):
            src = reader(f["file"])
            if src:
                f["snippet"] = snippet(src.splitlines(), f["file"], int(f["line"]))
        _trace_snippets(f, reader)
    return findings


def _trace_snippets(f: dict, reader) -> None:
    """경로 체인(route → handler → … → sink)의 각 단계에 ±3줄 — UI 가 단계를 눌러 코드를 본다."""
    for hop in f.get("code_trace") or []:
        if not hop.get("file") or not hop.get("line") or hop.get("snippet"):
            continue
        try:
            src = reader(hop["file"])
        except Exception:
            src = None
        if src:
            hop["snippet"] = snippet(src.splitlines(), hop["file"], int(hop["line"]), radius=3)


def _clean_sqlmap_paths(history: list) -> list[str]:
    """sqlmap 이 끝까지 돌아 '주입 불가'로 판정한 엔드포인트 path — 요약에 '안전 확인'으로 쓴다."""
    out = []
    for h in history or []:
        cmd, r = h.get("command") or "", h.get("result") or {}
        if cmd.startswith("sqlmap") and r.get("ok") and _SQLMAP_CLEAN.search(r.get("output") or ""):
            m = _URL_PATH.search(cmd)
            if m and m.group(1) not in out:
                out.append(m.group(1))
    return out


def fallback_summary(findings: list, history: list, safe: list | None = None) -> str:
    """LLM 없이 만드는 요약 — 확인/의심 구분 + 가장 급한 것 + 코드·도구로 안전 확인된 것."""
    if not findings:
        text = "점검한 범위에서 확인된 취약점이 없습니다."
    else:
        counts = {s: sum(1 for f in findings if f.get("severity") == s) for s in _SEV_ORDER}
        dist = ", ".join(f"{_SEV_KO[s]} {n}건" for s, n in counts.items() if n)
        top = next((f for s in _SEV_ORDER for f in findings if f.get("severity") == s), findings[0])
        text = f"총 {len(findings)}건을 발견했습니다({dist}). 가장 먼저 볼 것은 '{top.get('label') or top.get('title')}' 입니다."
        suspected = sum(1 for f in findings if f.get("status") == "suspected")
        if suspected:
            text += (f" 이 중 {suspected}건은 코드 분석으로 찾은 '의심'(요청 값이 쿼리·명령 등에 직접 섞이는 줄)이며"
                     " 자동 공격으로는 아직 확인되지 않았습니다.")
        if not counts["critical"] and not counts["high"] and not suspected:
            text += " 대부분 보안 헤더 설정 누락으로, 바로 뚫리는 취약점은 아니지만 helmet 등으로 한 번에 정리할 수 있습니다."
    clean = _clean_sqlmap_paths(history)
    if clean:
        text += f" SQL 주입 점검({', '.join(clean)})에서는 취약점이 확인되지 않았습니다."
    coded = [s["endpoint"] for s in (safe or [])]
    if coded:
        shown = ", ".join(coded[:4]) + (f" 외 {len(coded) - 4}개" if len(coded) > 4 else "")
        text += f" 코드상으로도 {shown} 의 쿼리는 모두 바인딩/고정 문자열이라 안전합니다."
    return text


def summarize(findings: list, history: list, llm_generate=None, safe: list | None = None) -> str:
    """전체 결과를 비전문가용 3~4문장으로. llm_generate(prompt)->str 실패/빈 응답이면 결정적 요약."""
    base = fallback_summary(findings, history, safe)
    if llm_generate is None or not findings:
        return base
    items = "\n".join(f"- [{f.get('severity', '?')}·{'의심' if f.get('status') == 'suspected' else '확인'}] "
                      f"{f.get('label') or f.get('title')}: {f.get('plain') or ''}"
                      for f in findings[:15])
    prompt = ("아래는 웹 서비스 자동 보안 점검 결과다. 보안 비전문가 개발자가 읽을 수 있게 한국어 3~4문장으로 "
              "요약하라. 무엇이 가장 급한지, 실제로 위험한지(바로 뚫리는지/설정 정리 수준인지), 무엇부터 고치면 "
              "되는지를 쉬운 말로. '의심'은 코드상 위험일 뿐 공격으로 확인되진 않았다고 구분해 말하라. "
              "목록·마크다운·영어 전문용어 나열 금지. 없는 사실을 지어내지 말 것.\n\n"
              f"발견 목록:\n{items}\n\n참고(결정적 요약): {base}")
    try:
        text = (llm_generate(prompt) or "").strip()
    except Exception:
        log.warning("블랙박스 요약 LLM 실패 — 결정적 요약 사용", exc_info=True)
        return base
    text = re.sub(r"^```\w*|```$", "", text).strip()
    return text[:900] if len(text) >= 20 else base


# ───────────────────────────────────────────── 코드 수정 패치(LLM 제안 + 원문 대조 검증)
PATCH_LIMIT = 5
_PATCH_CONTEXT = 14
_PLACEHOLDER = re.compile(r"\?|\$\d|%s|:\w+|%\(\w+\)s")


def _patch_prompt(f: dict, lines: list[str], start: int, end: int) -> str:
    sink = f.get("sink") or {}
    code = "\n".join(f"{n:>4}| {lines[n - 1]}" for n in range(start, end + 1))
    trace = " → ".join(f"{h.get('name')}({_base(h.get('file'))}:{h.get('line')})" for h in f.get("code_trace") or [])
    return (
        f"다음 코드의 {sink.get('line')}번 줄에 {sinks.CATEGORY_KO.get(f.get('category'), '주입')} 취약점이 있다. "
        f"`{sink.get('callee')}` 호출에 요청 값 {', '.join(sink.get('tainted') or [])} 이(가) "
        f"{sink.get('construction_ko')}.\n호출 경로: {trace}\n파일: {sink.get('file')}\n\n{code}\n\n"
        "이 취약점만 고치는 최소 수정을 JSON 하나로만 답하라(설명 금지):\n"
        '{"line_start":<수정 시작 줄>,"line_end":<수정 끝 줄>,"before":"<그 줄들의 원문 그대로>",'
        '"after":"<바꿀 코드>","note":"<한국어 한 문장>"}\n'
        "규칙: before 는 위 코드의 해당 줄을 줄번호 없이 **한 글자도 바꾸지 말고** 복사. 수정 범위는 취약한 줄을 "
        "반드시 포함하고 12줄 이하. SQL 이면 자리표시자 + 바인딩 값 배열/튜플로. 기존 변수명·들여쓰기 유지."
    )


def _validate_patch(obj: dict, f: dict, lines: list[str]) -> dict | None:
    try:
        a, b = int(obj["line_start"]), int(obj["line_end"])
        before, after = str(obj["before"]), str(obj["after"])
    except (KeyError, TypeError, ValueError):
        return None
    sink_line = int((f.get("sink") or {}).get("line") or 0)
    if not (1 <= a <= b <= len(lines)) or b - a > 12 or not (a <= sink_line <= b):
        return None
    norm = lambda t: [ln.strip() for ln in t.strip("\n").splitlines() if ln.strip()]   # noqa: E731
    if norm(before) != norm("\n".join(lines[a - 1:b])):
        return None                                        # LLM 이 원문을 지어냄 — 버린다
    if not after.strip() or norm(after) == norm(before):
        return None
    if f.get("category") == sinks.SQL and not _PLACEHOLDER.search(after):
        return None                                        # SQL 인데 바인딩이 없는 '수정'
    return {"file": f["sink"]["file"], "line_start": a, "line_end": b,
            "before": "\n".join(lines[a - 1:b]), "after": after.rstrip("\n"),
            "note": str(obj.get("note") or "")[:200], "verified": "원문 일치"}


def suggest_patches(findings: list, reader, llm_generate, *, limit: int = PATCH_LIMIT) -> int:
    """sink 가 있는 주입류 발견에 검증된 before/after 패치를 붙인다(제자리). 붙인 개수 반환.

    LLM 이 낸 before 가 실제 소스 줄과 다르면(환각) 버리고 결정적 '고치는 법'만 남긴다.
    """
    if reader is None or llm_generate is None:
        return 0
    from app.pentester import parse_action
    done = 0
    for f in findings:
        if done >= limit:
            break
        sink = f.get("sink") or {}
        if f.get("category") not in sinks.CATEGORY_KO or not sink.get("file") or not sink.get("line"):
            continue
        try:
            src = reader(sink["file"])
        except Exception:
            src = None
        if not src:
            continue
        lines = src.splitlines()
        line = int(sink["line"])
        start, end = max(1, line - _PATCH_CONTEXT), min(len(lines), line + 6)
        try:
            obj = parse_action(llm_generate(_patch_prompt(f, lines, start, end)))
        except Exception:
            log.warning("수정 패치 LLM 실패 — 결정적 고치는 법만", exc_info=True)
            continue
        patch = _validate_patch(obj or {}, f, lines)
        if patch:
            f["patch"] = patch
            done += 1
    return done
