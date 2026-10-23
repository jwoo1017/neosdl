"""finding(sink) → HTTP 엔트리포인트 해석.

sink 심볼에서 call graph 를 거슬러 올라가(callers_within) `role='controller'` 심볼을 찾고,
그 소스에서 프레임워크별 정규식으로 method+path+param 을 뽑는다. 결정적 추출이 애매하면
LLM(RouteBinding)로 보완한다(결정적 우선, 신뢰도 cap). 코드 그래프가 이미 있으니 가능한 일 —
블랙박스 스캐너가 맹목적으로 퍼징하는 것과 다르다(화이트박스 차별점).

도달 가능한 엔트리포인트가 없으면 EntryPoint.reason 에 사유(no_http_entrypoint/needs_auth)를 담아
호출부가 not_attempted 로 처리하게 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.scan_queries import call_graph_paths
from app.source_reader import file_reader, read_symbol

_CONTROLLER_PATH_HINTS = ("controller", "/routes/", "/route/", "/api/", "/handlers/", "/views/")

# 프레임워크별 라우트 선언 — (정규식, method 추출기). path 는 group('path').
_ROUTE_PATTERNS = [
    # Express/Nest/Koa/Fastify: app.get('/x'...), router.post("/x"...)
    (re.compile(r"""\b(?:app|router|r)\.(?P<m>get|post|put|patch|delete)\(\s*['"](?P<path>[^'"]+)['"]""", re.I), "verb"),
    # Nest/Spring 데코레이터: @Get('/x'), @PostMapping("/x"), @RequestMapping(path="/x")
    (re.compile(r"""@(?P<m>Get|Post|Put|Patch|Delete)(?:Mapping)?\(\s*['"](?P<path>[^'"]*)['"]""", re.I), "verb"),
    (re.compile(r"""@RequestMapping\([^)]*(?:path|value)\s*=\s*['"](?P<path>[^'"]+)['"]""", re.I), "mapping"),
    # FastAPI/Flask: @app.get("/x"), @router.post("/x"), @app.route("/x", methods=[...])
    (re.compile(r"""@(?:app|router)\.(?P<m>get|post|put|patch|delete)\(\s*['"](?P<path>[^'"]+)['"]""", re.I), "verb"),
    (re.compile(r"""@(?:app|bp|blueprint)\.route\(\s*['"](?P<path>[^'"]+)['"]""", re.I), "flask"),
]

_PARAM_SOURCE_RE = re.compile(
    r"""req\.(?:body|query|params)\.(\w+)|request\.(?:args|form|json|GET|POST)(?:\.get\(['"]?(\w+)|\[['"](\w+))|"""
    r"""@RequestParam\s+\w+\s+(\w+)|@PathVariable\s+\w+\s+(\w+)""")


@dataclass(frozen=True)
class EntryPoint:
    ok: bool
    method: str | None = None
    path_template: str | None = None
    param_name: str | None = None
    param_in: str | None = None
    controller_symbol_key: str | None = None
    auth_required: bool = False
    confidence: float = 0.0
    resolved_by: str = "graph"       # graph | llm | mixed
    reason: str | None = None        # not ok 일 때 사유


def _param_from_source_var(source_var: str | None) -> str | None:
    """테인트 source_var 에서 요청 필드명을 뽑는다.

    (a) `req.query.id`/`request.args.get('q')` 등 명시 패턴이면 그 필드명.
    (b) 없으면, source_var 가 단순 식별자(`uid`)면 같은 이름의 요청 필드로 추정한다 —
        값이 같은 이름으로 흘러가는 흔한 경우(controller 가 `const {uid}=req.body` 후 전달). 추정이라
        resolve 에서 신뢰도를 낮춘다.
    """
    if not source_var:
        return None
    m = _PARAM_SOURCE_RE.search(source_var)
    if m:
        return next((g for g in m.groups() if g), None)
    sv = source_var.strip()
    if re.fullmatch(r"[A-Za-z_]\w{1,40}", sv) and sv not in ("req", "request", "self", "this"):
        return sv
    return None


def _find_controller(cur, scan_id: str, sink_symbol_key: str, depth: int = 6) -> dict | None:
    """sink 를 호출하는 체인에서 controller(또는 라우트 파일) 심볼을 찾는다. 최단 hop 우선."""
    paths = call_graph_paths(cur, scan_id, sink_symbol_key, depth=depth, direction="callers")
    # call_graph_paths 는 relation=target/caller 항목에 path/qualified_name/role 없이 오므로
    # caller 들의 symbol_key 로 role 을 조회한다.
    caller_keys = [p["symbol_key"] for p in paths if p.get("relation") == "caller" and p.get("symbol_key")]
    if not caller_keys:
        return None
    cur.execute(
        """
        SELECT s.symbol_key, s.path, s.qualified_name, sem.role
          FROM symbol s
          LEFT JOIN symbol mod_s ON mod_s.scan_id=s.scan_id AND mod_s.path=s.path AND mod_s.kind='module'
          LEFT JOIN symbol_semantic sem ON sem.scan_id=s.scan_id AND sem.symbol_key=mod_s.symbol_key
         WHERE s.scan_id=%s AND s.symbol_key = ANY(%s)
        """,
        (str(scan_id), caller_keys),
    )
    rows = {r["symbol_key"]: r for r in cur.fetchall()}
    # 우선순위: role=controller > 경로 힌트 매칭 > 그 외. 입력 caller_keys 순서(=최단 hop 순) 유지.
    for key in caller_keys:
        r = rows.get(key)
        if r and r.get("role") == "controller":
            return r
    for key in caller_keys:
        r = rows.get(key)
        if r and any(h in (r["path"] or "").lower() for h in _CONTROLLER_PATH_HINTS):
            return r
    return None


_AUTH_RE = re.compile(r"authenticate|requireRole|@PreAuthorize|login_required|IsAuthenticated|ensureAuth", re.I)


def _route_files(cur, scan_id: str) -> list[str]:
    """라우트 선언이 있을 법한 모듈 파일 경로들(Express 의 *.routes.js 등)."""
    cur.execute(
        "SELECT DISTINCT path FROM symbol WHERE scan_id=%s AND kind='module' "
        "AND (path ILIKE '%%rout%%' OR path ILIKE '%%/api/%%')",
        (str(scan_id),),
    )
    return [r["path"] for r in cur.fetchall()]


def _find_route_for_method(cur, scan_id: str, method_name: str) -> tuple[str, str, bool] | None:
    """라우트 파일에서 `router.VERB('path', ... <method_name>)` 를 찾는다.

    Express 는 경로→컨트롤러 매핑이 컨트롤러 파일이 아니라 별도 라우트 파일에 있다
    (예: admin.routes.js `router.get('/stats', adminController.getStats)`). 반환:
    (method, path, auth_required). 파일 전체에 auth 미들웨어(router.use(authenticate...))가 있으면 auth True.
    """
    if not method_name or len(method_name) < 3:
        return None
    reader = file_reader(cur, scan_id)
    if reader is None:
        return None
    rx = re.compile(
        r"""\.(?P<m>get|post|put|patch|delete)\(\s*['"](?P<path>[^'"]+)['"](?P<mw>[^)]*)\b"""
        + re.escape(method_name) + r"\b", re.I)
    for path in _route_files(cur, scan_id):
        src = reader(path)
        if not src or method_name not in src:
            continue
        m = rx.search(src)
        if not m:
            continue
        # auth 판정은 '이 라우트에 실제로 적용되는가'로 — 파일 아무데나 authenticate 가
        # 있다고 전부 auth 로 보지 않는다(login 처럼 공개 라우트 오탐 방지).
        #  (a) 이 라우트 선언 줄의 미들웨어 인자에 auth 가 있거나,
        #  (b) 이 라우트 앞에 router.use(...authenticate...) 전역 가드가 있으면 auth.
        on_line = bool(_AUTH_RE.search(m.group("mw") or ""))
        before = src[:m.start()]
        global_guard = bool(re.search(r"\.use\([^)]*(?:authenticate|requireRole|requireAuth|ensureAuth)", before, re.I))
        return m.group("m").upper(), m.group("path"), (on_line or global_guard)
    return None


def _extract_route(source: str) -> tuple[str, str] | None:
    """컨트롤러 소스에서 (method, path). 못 찾으면 None."""
    for rx, kind in _ROUTE_PATTERNS:
        m = rx.search(source)
        if not m:
            continue
        path = m.group("path")
        if kind == "verb":
            return m.group("m").upper(), path
        if kind == "mapping":
            mm = re.search(r"method\s*=\s*RequestMethod\.(\w+)", source)
            return (mm.group(1).upper() if mm else "GET"), path
        if kind == "flask":
            mm = re.search(r"methods\s*=\s*\[([^\]]*)\]", source)
            method = "POST" if (mm and "POST" in mm.group(1).upper()) else "GET"
            return method, path
    return None


def resolve(cur, scan_id: str, finding: dict) -> EntryPoint:
    """finding(security_finding 행 dict) → EntryPoint. 결정적 우선, 필요 시 호출부가 LLM 보완.

    finding 필수 키: sink_symbol_key, source_var. (category 는 호출부가 사용.)
    """
    controller = _find_controller(cur, scan_id, finding["sink_symbol_key"])
    if controller is None:
        return EntryPoint(ok=False, reason="no_http_entrypoint")

    param = _param_from_source_var(finding.get("source_var"))
    got = read_symbol(cur, scan_id, controller["symbol_key"])
    source = got["code"] if got and got.get("code") else ""

    # (1) 컨트롤러 파일 내부 인라인 라우트(FastAPI/Flask/Spring 데코레이터, Nest)
    route = _extract_route(source)
    auth_required = bool(_AUTH_RE.search(source))

    # (2) Express 패턴: 경로가 별도 라우트 파일에 있음 → 컨트롤러 메서드명으로 찾는다.
    if route is None:
        method_name = (controller.get("qualified_name") or "").replace("#", ".").split(".")[-1]
        found = _find_route_for_method(cur, scan_id, method_name)
        if found:
            route = (found[0], found[1])
            auth_required = auth_required or found[2]

    if route is None:
        # 결정적 실패 — 호출부가 RouteBinding LLM 보완을 시도할 수 있게 부분 정보 반환.
        return EntryPoint(ok=False, controller_symbol_key=controller["symbol_key"],
                          param_name=param, reason="route_undetermined", resolved_by="graph")

    method, path = route
    conf = 0.7 if param else 0.5
    return EntryPoint(ok=True, method=method, path_template=path, param_name=param,
                      param_in="query" if method == "GET" else "body",
                      controller_symbol_key=controller["symbol_key"], auth_required=auth_required,
                      confidence=conf, resolved_by="graph")
