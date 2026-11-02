"""블랙박스 발견 판정 — **도구 증거 × 코드 증거**. LLM 단독 주장은 발견이 될 수 없다.

사건(2026-11-02): LLM 이 finish 응답에 "SQL Injection Vulnerability"(high)를 적었고, 그 문장에 들어 있던
signup 경로가 문자열 매칭으로 AuthController.js:3(핸들러 선언 줄)에 붙어 "SQL 인젝션 확인"으로 나갔다.
sqlmap 은 signup 을 때린 적도 없었다. 여기서 발견의 자격을 결정적으로 정한다:

| 도구 증거            | 코드 증거(sink_trace)       | 결과                                         |
|----------------------|-----------------------------|----------------------------------------------|
| 확인(sqlmap/프로브)  | 같은 종류 위험 sink         | confirmed · 도구 심각도 · 위치=sink 줄       |
| 확인                 | 없음/안전                   | confirmed · 위치=핸들러 + "코드 위치 미확정" |
| 없음/미검출          | 위험 sink                   | suspected · medium · 위치=sink 줄            |
| 미검출               | 안전(바인딩·상수)           | 발견 없음 → 요약의 '안전 확인'               |
| LLM 만 주장          | —                           | 폐기(discarded 로 투명하게 남김)             |

헤더·CORS·노출·nuclei·ffuf·접근통제처럼 도구가 직접 관측한 위생 발견은 그대로 통과한다.
순수 함수 — 오프라인 단위 테스트.
"""

from __future__ import annotations

import re

from app import sinks

# 도구 발견 source → sink 카테고리
_TOOL_CATEGORY = {"sqlmap": sinks.SQL, "xss": sinks.XSS, "ssti": sinks.SSTI, "lfi": sinks.LFI}
_TOOL_NAME = {"sqlmap": "sqlmap", "xss": "XSS 반사 프로브", "ssti": "SSTI 평가 프로브", "lfi": "경로순회 프로브"}
# 이 도구가 해당 엔드포인트를 이 카테고리로 '때려 봤는지' — 명령 접두/마커로 판별
_PROBE_OF = {sinks.SQL: lambda c: c.startswith("sqlmap"),
             sinks.XSS: lambda c: c.startswith("curl") and "neoxss" in c,
             sinks.SSTI: lambda c: c.startswith("curl") and "neossti" in c,
             sinks.LFI: lambda c: c.startswith("curl") and "etc%2fpasswd" in c.lower()}
_SUSPECT_SEVERITY = {sinks.SQL: "medium", sinks.CMD: "medium", sinks.CODE: "medium", sinks.SSTI: "medium",
                     sinks.LFI: "medium", sinks.NOSQL: "medium", sinks.XSS: "low"}
_URL_PATH = re.compile(r"https?://[^/\s\"']+(/[^\s\"'?]*)")
_PATH_FIELD = re.compile(r"path=(/\S*?)(?:[;,\s]|$)")


def _evidence_path(f: dict) -> str | None:
    m = _PATH_FIELD.search(f.get("evidence") or "")
    return m.group(1) if m else None


def _endpoint_for(path: str | None, endpoints: list) -> dict | None:
    if not path:
        return None
    for e in endpoints or []:
        if e.get("path") == path:
            return e
    return None


def _tested(history: list, category: str, path: str) -> bool:
    probe = _PROBE_OF.get(category)
    if probe is None:
        return False
    for h in history or []:
        cmd = h.get("command") or ""
        r = h.get("result") or {}
        m = _URL_PATH.search(cmd)
        if r.get("ok") and probe(cmd) and m and m.group(1) == path:
            return True
    return False


def _sink_fields(s: dict) -> dict:
    return {"file": s["file"], "line": s["line"],
            "sink": {k: s.get(k) for k in ("category", "label", "callee", "file", "line", "code", "arg",
                                           "construction", "construction_ko", "tainted", "function")},
            "code_trace": s.get("chain") or []}


def _handler_fields(ep: dict) -> dict:
    prof = ep.get("sink_profile") or {}
    h = prof.get("handler") or {}
    out = {}
    if h.get("file"):
        out = {"file": h["file"], "line": h.get("line")}
    elif ep.get("file"):
        out = {"file": ep["file"], "line": ep.get("line")}
    chain = [x for x in [{"file": ep.get("route_file"), "line": ep.get("route_line"),
                          "name": f"{ep.get('method')} {ep.get('path')}", "kind": "route"}, h or None] if x and x.get("file")]
    if chain:
        out["code_trace"] = chain
    return out


def reconcile(findings: list, endpoints: list | None, history: list | None) -> dict:
    """→ {findings, discarded, safe}. findings 는 판정·앵커가 끝난 최종 목록."""
    endpoints = endpoints or []
    out, discarded = [], []
    confirmed_keys: set[tuple[str, str]] = set()

    for f in findings or []:
        src = f.get("source")
        if not src or src == "llm":
            discarded.append({"title": f.get("title"), "severity": f.get("severity"),
                              "reason": "도구 출력·코드 근거 없음(LLM 단독 주장)"})
            continue
        category = _TOOL_CATEGORY.get(src)
        if category is None:
            g = dict(f, status="confirmed")
            ep = _endpoint_for(_evidence_path(f), endpoints)
            if ep is not None and not g.get("file"):
                g.update(_handler_fields(ep))
                g["endpoint"] = f"{ep['method']} {ep['path']}"
            out.append(g)
            continue
        # 도구가 주입을 확인한 발견 — 코드 sink 로 위치를 정한다
        path = _evidence_path(f)
        ep = _endpoint_for(path, endpoints)
        g = dict(f, status="confirmed", category=category, tool=_TOOL_NAME.get(src, src))
        g.pop("file", None)
        g.pop("line", None)
        if ep is not None:
            g["endpoint"] = f"{ep['method']} {ep['path']}"
            prof = ep.get("sink_profile") or {}
            hit = next((s for s in prof.get("dangerous", []) if s["category"] == category), None)
            if hit is not None:
                g.update(_sink_fields(hit))
            else:
                g.update(_handler_fields(ep))
                g["code_note"] = "도구는 주입을 확인했지만 코드에서 위험한 구성을 찾지 못했습니다 — 핸들러부터 직접 확인하세요."
            confirmed_keys.add((ep["path"], category))
        out.append(g)

    # 코드상 위험 sink — 도구가 확인하지 못한 것은 '의심'으로. 같은 sink 줄은 하나로 합친다.
    by_sink: dict[tuple, dict] = {}
    for ep in endpoints:
        prof = ep.get("sink_profile") or {}
        for s in prof.get("dangerous", []):
            if (ep.get("path"), s["category"]) in confirmed_keys:
                continue
            key = (s["file"], s["line"], s["category"])
            tested = _tested(history, s["category"], ep.get("path"))
            if key in by_sink:
                by_sink[key]["endpoints"].append(f"{ep['method']} {ep['path']}")
                continue
            name = sinks.CATEGORY_KO.get(s["category"], s["category"])
            g = {"title": f"{name} 의심(코드 분석) — {ep['path']}", "severity": _SUSPECT_SEVERITY.get(s["category"], "medium"),
                 "status": "suspected", "category": s["category"], "source": "code",
                 "evidence": f"{s['callee']} @ {s['file']}:{s['line']} — {s['construction_ko']}; path={ep['path']}",
                 "endpoint": f"{ep['method']} {ep['path']}", "endpoints": [f"{ep['method']} {ep['path']}"],
                 "tool_tested": tested}
            g.update(_sink_fields(s))
            if tested:
                g["code_note"] = ("자동 공격 도구로는 뚫리지 않았습니다(입력 검증·인증 등으로 막혔을 수 있음). "
                                  "코드 구성 자체는 위험하므로 바인딩/이스케이프로 고치는 것을 권합니다.")
            by_sink[key] = g
    out.extend(by_sink.values())

    # 안전 확인 — 이 엔드포인트의 SQL sink 가 전부 바인딩/상수이고 도구도 못 뚫음
    safe = []
    for ep in endpoints:
        prof = ep.get("sink_profile") or {}
        sql = [s for s in prof.get("sinks", []) if s["category"] == sinks.SQL]
        if sql and not any(s["dangerous"] for s in sql) and (ep.get("path"), sinks.SQL) not in confirmed_keys:
            safe.append({"endpoint": f"{ep['method']} {ep['path']}", "category": sinks.SQL,
                         "tested": _tested(history, sinks.SQL, ep.get("path")),
                         "sinks": [{"file": s["file"], "line": s["line"], "construction": s["construction"]}
                                   for s in sql]})
    return {"findings": out, "discarded": discarded, "safe": safe}
