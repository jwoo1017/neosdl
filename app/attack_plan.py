"""블랙박스 공격 플랜 — PentAGI식 다단계 결정적 공격 오케스트레이션 + 도구 출력 발견 추출기.

왜 분리하나(2026-10-29): 예전 pentester 는 결정 체인이 **nmap → nikto → sqlmap** 뿐이라 실제
능동 공격이 SQL 인젝션 하나였고, 발견 대부분이 nikto 의 보안헤더 위생(= 전부 앱 생성 줄 하나로
앵커링돼 "다 똑같아" 보임)이었다. 로컬 LLM(7B)이 약해 추천만 따라가므로, allowlist 에 nuclei·ffuf·
whatweb 가 다 있어도 안 돌았다.

그래서 **결정적 공격 플랜**을 넓힌다(PentAGI 처럼 넓은 도구 그물 + 결과 추론). HTTP 표적마다:
  1. whatweb  — 기술 스택 핑거프린트(정찰)
  2. nikto    — 패시브 웹 점검(기존 유지)
  3. nuclei   — 템플릿 스캔(CVE·노출·설정오류·기본자격·SSRF·cmdi 등 넓은 그물)
  4. 엔드포인트별 능동 주입(소스 유도, 2패스: 비인증 먼저):
       - sqlmap   : SQL 인젝션
       - curl XSS : 반영 XSS(마커 반영으로 결정적 확인)
       - curl SSTI: 서버사이드 템플릿 주입({{7*7}}→49, 오탐 거의 0)
       - curl LFI : 경로순회/파일읽기(/etc/passwd → root:…:0:0, 오탐 거의 0)
  5. ffuf      — 콘텐츠 디스커버리(숨은 경로·민감파일 .git/.env/백업/admin)
  6. 접근통제  — 인증필요 엔드포인트를 **비인증**으로 때려 2xx 면 broken access control
  7. finish

핵심 원칙(기존과 동일): **결정적 우선**. LLM 은 추천 명령을 베껴 쓰기만 하면 되고, 발견은 도구
출력에서 정규식으로 뽑으므로 LLM 판단과 독립이다. 모든 프로브는 toolbox.validate_command 를
통과하도록 설계한다 — 셸 메타문자(`; | < > $( ${`)·금지 서브스트링(`/etc/passwd`)은 **URL 인코딩**으로
피한다(예: `..%2F..%2Fetc%2Fpasswd`, `%3Cimg…%3E`). 순수·결정적이라 Docker/LLM 없이 단위 테스트한다.
"""

from __future__ import annotations

import re

# ── 기본 플래그(운영은 settings 로 덮어씀) ─────────────────────────────────────
DEFAULT_SQLMAP_FLAGS = "--batch --level=2 --risk=2 --threads=4 --technique=BEUS"
# nuclei: jsonl 로 파싱, info 는 뺀다(= 보안헤더 등 노이즈 억제 — 사용자가 싫어하던 바로 그것).
DEFAULT_NUCLEI_FLAGS = "-silent -jsonl -severity low,medium,high,critical -timeout 8 -retries 1"
# ffuf 워드리스트 — Kali(vxcontrol/kali-linux)에 기본 존재하는 dirb common 사용. 운영서 교체 가능.
DEFAULT_FFUF_WORDLIST = "/usr/share/wordlists/dirb/common.txt"
DEFAULT_FFUF_FLAGS = "-mc 200,204,301,307,401,403 -t 40 -ac"

# 프로브 마커(명령에 박아 추출기가 '어떤 프로브인지' 결정적으로 구분한다). 응답에서 이 마커가
# **디코드된 형태**로 되돌아오면 취약점 — 입력이 그대로(인코드·리터럴) 반사된 것과 구분된다.
_XSS_MARK = "neoxss"
_XSS_PAYLOAD = "neoxss%3Cimg%2Fsrc%3Dx%3E"          # 디코드: neoxss<img/src=x>
_XSS_HIT = re.compile(r"neoxss<img", re.I)           # 태그가 이스케이프 안 되고 그대로 반사
_SSTI_PAYLOAD = "neossti%7B%7B7%2A7%7D%7D"           # 디코드: neossti{{7*7}}
_SSTI_HIT = re.compile(r"neossti49")                 # 7*7 이 49 로 평가되면 템플릿 주입
_SSTI_MARK = "neossti"
_LFI_PAYLOAD = "..%2F..%2F..%2F..%2F..%2Fetc%2Fpasswd"   # 디코드: ../../../../../etc/passwd
_LFI_MARK = "etc%2fpasswd"
_LFI_HIT = re.compile(r"root:[^:\n]*:0:0:")          # /etc/passwd 의 root 엔트리
_ACCESS_MARK = "NEOACCESS"
_ACCESS_HIT = re.compile(r"NEOACCESS:(2\d\d)")        # 인증필요인데 2xx → 접근통제 결함

_URL_PATH = re.compile(r"https?://[^/\s\"']+(/[^\s\"'?]*)")


# ── 엔드포인트별 능동 주입 프로브(명령 문자열 빌더) ─────────────────────────────
def sqlmap_for(target: str, port: int, ep: dict, flags: str = DEFAULT_SQLMAP_FLAGS) -> str:
    """소스 엔드포인트에 겨눈 sqlmap — 홈 크롤이 아니라 실제 파라미터를 주입."""
    data = "&".join(f"{p}=1" for p in (ep.get("params") or [])[:6])
    base = f'http://{target}:{port}{ep["path"]}'
    if ep.get("method") == "GET":
        return f'sqlmap -u "{base}?{data}" {flags}'
    return f'sqlmap -u "{base}" --data="{data}" {flags}'


def _inject_query(target: str, port: int, ep: dict, payload: str) -> str:
    """첫 파라미터에 payload 를, 나머지는 1 로 채운 쿼리스트링/‑‑data 를 만들어 curl 명령으로."""
    params = (ep.get("params") or ["q"])[:6]
    pairs = [f"{params[0]}={payload}"] + [f"{p}=1" for p in params[1:]]
    body = "&".join(pairs)
    base = f'http://{target}:{port}{ep["path"]}'
    if ep.get("method") == "GET":
        return f'curl -s -i "{base}?{body}"'
    return f'curl -s -i "{base}" --data "{body}"'


def xss_probe(target: str, port: int, ep: dict) -> str:
    return _inject_query(target, port, ep, _XSS_PAYLOAD)


def ssti_probe(target: str, port: int, ep: dict) -> str:
    return _inject_query(target, port, ep, _SSTI_PAYLOAD)


def lfi_probe(target: str, port: int, ep: dict) -> str:
    return _inject_query(target, port, ep, _LFI_PAYLOAD)


def access_probe(target: str, port: int, ep: dict) -> str:
    """인증필요 엔드포인트를 비인증으로 요청하고 HTTP 코드만 받아온다(-w 로 코드 태깅)."""
    base = f'http://{target}:{port}{ep["path"]}'
    return f'curl -s -o /dev/null -w "{_ACCESS_MARK}:%{{http_code}}" "{base}"'


# ── 결정적 다음 행동 플랜 ────────────────────────────────────────────────────
def _ran(seen: set[str], prefix: str, needle: str = "") -> bool:
    return any(c.startswith(prefix) and (needle in c) for c in seen)


def _injection_step(target: str, port: int, seen: set[str], endpoints, sqlmap_flags: str):
    """엔드포인트 2패스(비인증→인증) 능동 주입. 각 엔드포인트에서 sqlmap→XSS→SSTI→LFI 순.
    아직 안 돌린 첫 프로브의 명령+태그를 반환, 전부 돌렸으면 None."""
    for want_auth in (False, True):
        for ep in (endpoints or []):
            if not ep.get("params") or bool(ep.get("auth")) != want_auth:
                continue
            path = ep["path"]
            tag_auth = "(인증 라우트·비인증 표면) " if want_auth else ""
            probes = (
                (sqlmap_for(target, port, ep, sqlmap_flags), "SQL 인젝션"),
                (xss_probe(target, port, ep), "반영 XSS"),
                (ssti_probe(target, port, ep), "템플릿 주입(SSTI)"),
                (lfi_probe(target, port, ep), "경로순회/파일읽기(LFI)"),
            )
            for cmd, label in probes:
                # 각 프로브 명령은 path+페이로드 마커까지 포함해 결정적으로 유일하므로 seen 비교만으로 충분.
                if cmd in seen:
                    continue
                return f'{cmd}  ({label} {tag_auth}소스 유도: {ep["method"]} {path})'
    return None


def plan_next(target: str, open_ports, seen: set[str], endpoints=None,
              sqlmap_flags: str = DEFAULT_SQLMAP_FLAGS, nuclei_flags: str = DEFAULT_NUCLEI_FLAGS,
              ffuf_wordlist: str = DEFAULT_FFUF_WORDLIST, ffuf_flags: str = DEFAULT_FFUF_FLAGS) -> str:
    """결정적 다음 행동 추천. LLM 은 앞의 명령을 베껴 쓰기만 하면 된다."""
    if not open_ports:
        if not _ran(seen, "nmap"):
            return f'nmap -Pn -T4 --version-light -sV {target}  (포트/서비스 정찰 — 빠르게)'
        return "finish (포트가 안 열려 있으면 종료)"

    http_ports = [p["port"] for p in open_ports if p.get("http")]
    for port in http_ports:
        tp = f"http://{target}:{port}"
        # 1) 핑거프린트
        if not _ran(seen, "whatweb", f":{port}"):
            return f'whatweb {tp}  (기술 스택 핑거프린트 — HTTP {port})'
        # 2) 패시브 웹 점검(기존)
        if not _ran(seen, "nikto", f":{port}"):
            return f'nikto -h {tp}  (웹 취약점 패시브 점검 — 기본 80 아님!)'
        # 3) 템플릿 스캔(넓은 그물)
        if not _ran(seen, "nuclei", f":{port}"):
            return f'nuclei -u {tp} {nuclei_flags}  (템플릿 스캔: CVE·노출·설정오류·기본자격·SSRF)'
        # 4) 엔드포인트별 능동 주입(2패스)
        step = _injection_step(target, port, seen, endpoints, sqlmap_flags)
        if step:
            return step
        # 5) 콘텐츠 디스커버리(숨은 경로·민감파일)
        if not _ran(seen, "ffuf", f":{port}"):
            return (f'ffuf -u {tp}/FUZZ -w {ffuf_wordlist} {ffuf_flags}  '
                    f'(콘텐츠 디스커버리 — 숨은 경로·민감파일)')
        # 6) 접근통제(인증필요 엔드포인트를 비인증으로)
        for ep in (endpoints or []):
            if not ep.get("auth"):
                continue
            cmd = access_probe(target, port, ep)
            if cmd not in seen:
                return f'{cmd}  (접근통제 점검: 인증필요 {ep["method"]} {ep["path"]} 를 비인증 접근)'
        # 7) 엔드포인트 미상 → 루트 크롤 1회 폴백
        if not endpoints and not _ran(seen, "sqlmap"):
            return f'sqlmap -u "{tp}/" --batch --crawl=2  (엔드포인트 미상 — 루트 크롤)'
    return "finish (웹 점검까지 끝났으면 종료)"


# ── 도구 출력 → 발견(결정적 추출기) ──────────────────────────────────────────
_NIKTO_LINE = re.compile(r"^\+\s*(?:\[\d+\]\s*)?(/\S*):?\s+(.+)$", re.MULTILINE)


def nikto_findings(output: str) -> list[dict]:
    """nikto 출력(`+ [코드] /경로: 설명`)에서 발견 추출. 참조 URL 은 분류에서 제외(오분류 방지)."""
    findings = []
    for m in _NIKTO_LINE.finditer(output or ""):
        path, desc = m.group(1), m.group(2).strip()
        core = re.sub(r"see:\s*https?://\S+", "", desc, flags=re.I)
        low = f"{path} {core}".lower()
        if "header" in low and any(k in low for k in ("not set", "missing", "deprecated", "security header")):
            sev = "low"
        elif any(k in low for k in ("cve-", "xss", "sql inj", "injection")):
            sev = "high"
        elif any(k in low for k in (".htpasswd", ".bash_history", ".mysql_history", ".sh_history",
                                     "authorization information", "shell history", "password")):
            sev = "high"
        elif ("might be interesting" in low or path.endswith(".json")
              or path.rstrip("/").endswith(("/ftp", "/public")) or "indexing" in low):
            sev = "medium"
        else:
            sev = "low"
        findings.append({"title": f"{path} — {desc[:140]}", "severity": sev,
                         "evidence": f"nikto: {path}", "source": "nikto"})
    return findings


_SQLMAP_HIT = re.compile(r"is vulnerable|injection point\(s\)|identified the following injection", re.I)


def sqlmap_findings(output: str, command: str = "") -> list[dict]:
    """sqlmap 출력에서 SQLi 확인 추출. 주입 확인 시 critical 1건(엔드포인트 path 포함 → 소스 앵커)."""
    out = output or ""
    if not _SQLMAP_HIT.search(out):
        return []
    pm = re.search(r"Parameter:\s*(\S+)", out)
    dm = re.search(r"back-end DBMS:\s*([^\n]+)", out, re.I)
    param = pm.group(1) if pm else "?"
    dbms = dm.group(1).strip() if dm else "?"
    path = _path_of(command)
    where = f" — {path}" if path else f" — {command[:120]}"
    return [{"title": f"SQL 인젝션 확인{where}", "severity": "critical",
             "evidence": f"sqlmap: parameter={param}, back-end DBMS={dbms}; path={path or '?'}",
             "source": "sqlmap"}]


def _nuclei_sev(sev: str) -> str:
    s = (sev or "").lower()
    return s if s in ("critical", "high", "medium", "low") else "low"


def nuclei_findings(output: str) -> list[dict]:
    """nuclei -jsonl 출력에서 발견 추출. info 심각도는 버린다(보안헤더 등 노이즈 억제)."""
    import json
    findings = []
    for line in (output or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        info = obj.get("info") or {}
        sev = (info.get("severity") or "").lower()
        if sev in ("", "info", "unknown"):
            continue
        name = info.get("name") or obj.get("template-id") or "nuclei finding"
        matched = obj.get("matched-at") or obj.get("host") or ""
        path = _path_of(matched)
        findings.append({"title": f"{name}{(' — ' + path) if path else ''}",
                         "severity": _nuclei_sev(sev),
                         "evidence": f"nuclei: {obj.get('template-id', '?')} @ {matched}",
                         "source": "nuclei"})
    return findings


# ffuf 기본 출력: `path   [Status: 200, Size: 1234, ...]`. 민감한 경로만 발견으로(그 외 200 은 노이즈).
_FFUF_LINE = re.compile(r"^(\S+)\s*\[Status:\s*(\d{3})", re.MULTILINE)
_SENSITIVE = ("/.git", "/.env", "/.svn", "/.hg", "/backup", "/dump", "/.htpasswd", "/.htaccess",
              "/config", "/admin", "/phpinfo", "/.well-known/security.txt", "/swagger", "/actuator",
              "/server-status", "/wp-config", "/.ssh", "/id_rsa", "/.aws", "/credentials")
_SENSITIVE_EXT = (".sql", ".bak", ".old", ".zip", ".tar", ".gz", ".log", ".env", ".pem", ".key")


def ffuf_findings(output: str, command: str = "") -> list[dict]:
    """ffuf 발견 중 **민감한 경로/확장자**만 올린다(일반 200 은 노이즈라 제외)."""
    findings = []
    for m in _FFUF_LINE.finditer(output or ""):
        word, code = m.group(1), m.group(2)
        low = ("/" + word.lstrip("/")).lower()
        sensitive = any(s in low for s in _SENSITIVE) or low.endswith(_SENSITIVE_EXT)
        if not sensitive:
            continue
        sev = "high" if (low.endswith(_SENSITIVE_EXT) or any(s in low for s in
                         ("/.git", "/.env", "/.ssh", "/id_rsa", "/.aws", "/credentials",
                          "/.htpasswd", "/wp-config", "/backup", "/dump"))) else "medium"
        findings.append({"title": f"민감 경로 노출 — /{word.lstrip('/')} (HTTP {code})",
                         "severity": sev, "evidence": f"ffuf: /{word.lstrip('/')} → {code}",
                         "source": "ffuf"})
    return findings


def _path_of(s: str) -> str:
    m = _URL_PATH.search(s or "")
    return m.group(1) if m else ""


def curl_probe_findings(output: str, command: str) -> list[dict]:
    """curl 능동 프로브(XSS/SSTI/LFI/접근통제) 결과를 명령의 마커로 구분해 결정적으로 판정."""
    cmd, out = command or "", output or ""
    path = _path_of(cmd)
    loc = f" — {path}" if path else ""
    where = f"path={path or '?'}"
    if _XSS_MARK in cmd and _XSS_PAYLOAD in cmd and _XSS_HIT.search(out):
        return [{"title": f"반영 XSS 확인{loc}", "severity": "high",
                 "evidence": f"반사된 페이로드가 이스케이프되지 않음(neoxss<img…); {where}", "source": "xss"}]
    if _SSTI_MARK in cmd and _SSTI_HIT.search(out):
        return [{"title": f"서버사이드 템플릿 주입(SSTI) 확인{loc}", "severity": "critical",
                 "evidence": f"{{{{7*7}}}} 이 49 로 평가됨(neossti49); {where}", "source": "ssti"}]
    if _LFI_MARK in cmd.lower() and _LFI_HIT.search(out):
        return [{"title": f"경로순회/파일읽기(LFI) 확인{loc}", "severity": "critical",
                 "evidence": f"응답에 /etc/passwd 의 root 엔트리가 노출됨; {where}", "source": "lfi"}]
    if _ACCESS_MARK in cmd:
        m = _ACCESS_HIT.search(out)
        if m:
            return [{"title": f"접근통제 결함 — 인증필요 자원이 비인증 접근에 응답{loc}",
                     "severity": "medium",
                     "evidence": f"인증 없이 HTTP {m.group(1)}; 이 엔드포인트는 인증필요로 분류됨; {where}",
                     "source": "access"}]
    return []


def observed_findings(history: list[dict]) -> list[dict]:
    """도구 출력에서 결정적으로 뽑는 발견(nikto·sqlmap·nuclei·ffuf·curl 프로브). LLM 판단과 독립."""
    out: list[dict] = []
    for h in history:
        cmd, r = h.get("command", ""), (h.get("result") or {})
        if not r.get("ok"):
            continue
        output = r.get("output", "")
        if cmd.startswith("nikto"):
            out.extend(nikto_findings(output))
        elif cmd.startswith("sqlmap"):
            out.extend(sqlmap_findings(output, cmd))
        elif cmd.startswith("nuclei"):
            out.extend(nuclei_findings(output))
        elif cmd.startswith("ffuf"):
            out.extend(ffuf_findings(output, cmd))
        elif cmd.startswith("curl"):
            out.extend(curl_probe_findings(output, cmd))
    return out
