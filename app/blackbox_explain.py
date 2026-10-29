"""블랙박스 발견을 비전문가가 읽을 수 있게 — 쉬운 설명·수정법·관련 소스 ±5줄·AI 요약.

nikto/sqlmap 발견 제목은 "Suggested security header missing: referrer-policy. See: …" 같은 도구
원문이라 사원이 무엇이 문제인지 알 수 없다. 여기서:
- **결정적 사전**으로 발견마다 label(쉬운 이름)·plain(무엇이 위험한지)·fix(고치는 법)를 붙이고,
- 발견을 **소스 위치**로 앵커링한다 — SQLi 는 `_attach_source` 의 핸들러 file:line, 보안 헤더류는
  앱 생성 줄(`express()`/`helmet(`), CORS 는 `cors(` 줄 — 그리고 그 줄 ±5 스니펫을 싣는다.
- 전체 결과를 **LLM 이 3~4문장으로 요약**한다(실패하면 결정적 요약으로 폴백 — 조용히 비지 않게).

DB·소스 읽기는 주입받은 reader 로만(오프라인 단위 테스트). 소스는 로컬에만 머문다.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("app.blackbox_explain")

SNIPPET_RADIUS = 5

# (매칭 정규식, kind, label, plain, fix) — 위에서부터 첫 매칭. kind 는 소스 앵커 종류.
_GLOSSARY = [
    (re.compile(r"sql 인젝션|sql inj", re.I), "sqli", "SQL 인젝션 확인",
     "입력값이 DB 쿼리에 그대로 이어 붙어, 공격자가 로그인 우회·데이터 열람/변조를 할 수 있습니다. 즉시 수정이 필요합니다.",
     "문자열 연결 대신 파라미터 바인딩(prepared statement, `?` 자리표시자)이나 ORM 쿼리 빌더를 쓰세요."),
    (re.compile(r"반영 xss|reflected xss|\bxss 확인", re.I), "xss", "반영 XSS(교차 사이트 스크립팅) 확인",
     "입력값이 페이지에 이스케이프 없이 그대로 출력돼, 공격자가 심은 스크립트가 피해자 브라우저에서 실행됩니다. 세션 탈취·피싱으로 이어집니다.",
     "출력 시 HTML 이스케이프(템플릿 자동 이스케이프 사용), 입력 검증, 그리고 `helmet()` CSP 로 스크립트 출처를 제한하세요."),
    (re.compile(r"ssti|템플릿 주입", re.I), "ssti", "서버사이드 템플릿 주입(SSTI) 확인",
     "입력값이 서버 템플릿 엔진에서 코드로 평가됩니다(예: {{7*7}}→49). 원격 코드 실행까지 번질 수 있는 치명적 결함입니다.",
     "사용자 입력을 템플릿 소스에 넣지 말고 **데이터로만** 전달하세요(render 변수 바인딩). 템플릿을 문자열 연결로 만들지 마세요."),
    (re.compile(r"경로순회|path traversal|\blfi\b|파일읽기", re.I), "lfi", "경로순회/로컬 파일 읽기(LFI) 확인",
     "파일 경로 파라미터에 ../ 를 넣어 서버의 임의 파일(/etc/passwd 등)을 읽어갈 수 있습니다. 소스·설정·비밀이 유출됩니다.",
     "사용자 입력을 파일 경로에 직접 쓰지 말고, 화이트리스트/인덱스로 매핑하거나 `path.basename` 후 고정 디렉터리에 join 하세요."),
    (re.compile(r"접근통제 결함|broken access|비인증 접근", re.I), "access", "접근통제 결함(비인증 접근)",
     "로그인이 필요한 엔드포인트가 인증 없이도 응답합니다. 남의 데이터 열람·변조(IDOR 포함) 위험이 있습니다.",
     "서버에서 모든 보호 라우트에 인증·인가 미들웨어를 강제하고, 객체 소유권(본인 자원인지)을 매 요청 확인하세요."),
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


def explain(f: dict) -> dict:
    """발견 1건 → {kind, label, plain, fix}. 사전에 없으면 원문 제목을 다듬어 label 로."""
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
    return findings


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


def fallback_summary(findings: list, history: list) -> str:
    """LLM 없이 만드는 요약 — 심각도 분포 + 가장 급한 것 + 안전 확인된 것."""
    if not findings:
        text = "점검한 범위에서 확인된 취약점이 없습니다."
    else:
        counts = {s: sum(1 for f in findings if f.get("severity") == s) for s in _SEV_ORDER}
        dist = ", ".join(f"{_SEV_KO[s]} {n}건" for s, n in counts.items() if n)
        top = next((f for s in _SEV_ORDER for f in findings if f.get("severity") == s), findings[0])
        text = f"총 {len(findings)}건을 발견했습니다({dist}). 가장 먼저 볼 것은 '{top.get('label') or top.get('title')}' 입니다."
        if not counts["critical"] and not counts["high"]:
            text += " 대부분 보안 헤더 설정 누락으로, 바로 뚫리는 취약점은 아니지만 helmet 등으로 한 번에 정리할 수 있습니다."
    clean = _clean_sqlmap_paths(history)
    if clean:
        text += f" SQL 주입 점검({', '.join(clean)})에서는 취약점이 확인되지 않았습니다."
    return text


def summarize(findings: list, history: list, llm_generate=None) -> str:
    """전체 결과를 비전문가용 3~4문장으로. llm_generate(prompt)->str 실패/빈 응답이면 결정적 요약."""
    base = fallback_summary(findings, history)
    if llm_generate is None or not findings:
        return base
    items = "\n".join(f"- [{f.get('severity', '?')}] {f.get('label') or f.get('title')}: {f.get('plain') or ''}"
                      for f in findings[:15])
    prompt = ("아래는 웹 서비스 자동 보안 점검 결과다. 보안 비전문가 개발자가 읽을 수 있게 한국어 3~4문장으로 "
              "요약하라. 무엇이 가장 급한지, 실제로 위험한지(바로 뚫리는지/설정 정리 수준인지), 무엇부터 고치면 "
              "되는지를 쉬운 말로. 목록·마크다운·영어 전문용어 나열 금지. 없는 사실을 지어내지 말 것.\n\n"
              f"발견 목록:\n{items}\n\n참고(결정적 요약): {base}")
    try:
        text = (llm_generate(prompt) or "").strip()
    except Exception:
        log.warning("블랙박스 요약 LLM 실패 — 결정적 요약 사용", exc_info=True)
        return base
    text = re.sub(r"^```\w*|```$", "", text).strip()
    return text[:900] if len(text) >= 20 else base
