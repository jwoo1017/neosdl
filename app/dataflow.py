"""보안 모드 source→sink 경로 탐색 — PDG 레이어 5.

`find_security_paths` 는 함수 하나 안(intra-procedural)만 본다.
`find_security_paths_interprocedural` 은 레이어 4(절차간 연결 — 호출 인자를
콜리의 파라미터로 잇는 것)까지 써서 함수 경계를 넘는 경로를 찾는다.
리턴값이 호출부로 되돌아오는 흐름(return backflow)은 아직 범위 밖이다.

source/sink 판정은 정규식 매칭. `find_domain_crossings`는 값이 도메인을
넘나드는 비즈니스 IP 모드 — 정규식 대신 symbol_semantic.domain 경계를 쓴다.
"""

from __future__ import annotations

import re

SOURCE_PATTERNS = [re.compile(p) for p in (
    r"req\.(body|query|params)",
    r"request\.(GET|POST|args|form|json|query)",
    r"\binput\(",
    r"os\.environ",
    r"process\.env",
    r"sys\.argv",
)]

# (category, pattern) — category는 security_finding.category / 심각도 매핑
# (app/security_severity.py) 에 그대로 쓰인다.
SINK_PATTERNS = [(cat, re.compile(p)) for cat, p in (
    ("sql_injection", r"\.execute\b"),
    ("command_injection", r"\bexec\("),
    ("command_injection", r"\beval\("),
    ("command_injection", r"os\.system"),
    ("command_injection", r"subprocess\."),
    ("command_injection", r"child_process"),
    ("xss", r"innerHTML"),
    ("xss", r"dangerouslySetInnerHTML"),
)]

# worker/handlers/cfg_extract.py 의 PARAMETER 와 같은 문자열이어야 한다 —
# 이 모듈은 DB(텍스트 컬럼)로만 cfg_extract 와 연결돼 있어 import로 공유하지
# 않는다(app -> worker 의존을 만들지 않기 위해).
PARAMETER_KIND = "PARAMETER"


def _matches_any(patterns: list[re.Pattern], text: str | None) -> bool:
    return any(p.search(text or "") for p in patterns)


def _match_sink_category(text: str | None) -> str | None:
    for category, pattern in SINK_PATTERNS:
        if pattern.search(text or ""):
            return category
    return None


def find_security_paths_interprocedural(
    data_edges: list[dict], calls: list[dict],
    call_links: list[dict], functions: dict[str, list[str]],
) -> list[dict]:
    """레이어 4 — 함수 경계를 넘는 source→sink 경로 탐색.

    call_links: [{caller_symbol_key, call_line, callee_symbol_key}] — 이미
    resolved=true 인 symbol_edge(calls) 를 pdg_call 의 (symbol_key, line) 과
    맞춰 조인한 결과(엔드포인트에서 만들어 넘긴다).
    functions: symbol_key -> 파라미터 이름(위치 순, self/this 제외).

    인자→파라미터 전파만 다룬다 — 콜백의 리턴값이 호출부 변수로 돌아오는
    흐름(return backflow)은 이번 범위 밖이다(다음 단계).
    """
    edges_by_symbol: dict[str, list[dict]] = {}
    for e in data_edges:
        edges_by_symbol.setdefault(e["symbol_key"], []).append(e)
    # 인자 슬롯의 "실제" 줄(arg_lines) -> (call, 위치) — 호출식이 여러 줄에
    # 걸치면(`pool.execute(sql,\n  [memo, id])`) 호출식 시작 줄(line)과
    # 인자 자신의 줄이 달라진다. pdg_data_edge.use_line은 인자 identifier의
    # 실제 줄로 기록되므로, 여기도 그 줄로 찾아야 매칭이 새지 않는다.
    arg_index: dict[tuple[str, int], list[tuple[dict, int]]] = {}
    for c in calls:
        arg_lines = c.get("arg_lines") or []
        for i, a in enumerate(c["arg_vars"]):
            line_i = arg_lines[i] if i < len(arg_lines) else c["line"]
            arg_index.setdefault((c["symbol_key"], line_i), []).append((c, i))
    callees_by_line: dict[tuple[str, int], list[str]] = {}
    for link in call_links:
        callees_by_line.setdefault((link["caller_symbol_key"], link["call_line"]), []).append(
            link["callee_symbol_key"])

    defs_at_line: dict[str, dict[int, list[tuple[str, str, str]]]] = {}
    for symbol_key, edges in edges_by_symbol.items():
        d: dict[int, list[tuple[str, str, str]]] = {}
        for e in edges:
            d.setdefault(e["def_line"], []).append((e["var"], e["evidence_kind"], e["def_text"]))
        defs_at_line[symbol_key] = d

    # (symbol_key, var, def_line) -> (거쳐온 전체 경유지 hops, evidence_kind 경로)
    # hops 를 통째로 들고 다녀야 "함수 호출 경로 확인" 스토리(중간 경유 함수
    # 전부 보여주기)가 가능하다 — origin/최종점만 남기면 경유지가 소실된다.
    Key = tuple[str, str, int]
    tainted: dict[Key, tuple[list[Key], list[str]]] = {}
    findings: list[dict] = []

    # 시드 1 — 변수 정의 텍스트가 source 패턴에 걸림(`y = req.query.id`).
    for e in data_edges:
        key = (e["symbol_key"], e["var"], e["def_line"])
        if key not in tainted and _matches_any(SOURCE_PATTERNS, e["def_text"]):
            tainted[key] = ([key], [e["evidence_kind"]])

    # 시드 2 — 변수 대입 없이 호출 인자로 바로 넘기는 경우(`svc.f(req.query.q)`).
    # 이게 실제 코드에서 훨씬 흔한 패턴이다 — 변수에 담아 쓰는 경우보다 많다.
    # (a) 그 호출 자체가 sink 면 즉시 발견(같은 줄, 함수 경계 안 넘어도 됨).
    # (b) resolve 되는 호출이면 콜리의 그 위치 파라미터를 오염 시드로 심는다.
    for c in calls:
        sink_category = _match_sink_category(c["target_text"])
        is_sink_call = sink_category is not None
        arg_lines = c.get("arg_lines") or []
        for i, text in enumerate(c.get("arg_texts", [])):
            if not _matches_any(SOURCE_PATTERNS, text):
                continue
            arg_line = arg_lines[i] if i < len(arg_lines) else c["line"]
            origin_key: Key = (c["symbol_key"], text, arg_line)
            if is_sink_call:
                findings.append({
                    "category": sink_category,
                    "sink_symbol_key": c["symbol_key"],
                    "sink_line": c["line"],
                    "sink_call": c["target_text"],
                    "source_symbol_key": c["symbol_key"],
                    "source_var": text,
                    "source_line": arg_line,
                    "cross_function": False,
                    "evidence_kinds": ["DIRECT_ARGUMENT"],
                    "path": [origin_key],
                })
            for callee in callees_by_line.get((c["symbol_key"], c["line"]), []):
                param_names = functions.get(callee, [])
                if i >= len(param_names) or param_names[i] == "?":
                    continue
                pname = param_names[i]
                for pe in edges_by_symbol.get(callee, []):
                    if pe["var"] == pname and pe["evidence_kind"] == PARAMETER_KIND:
                        pkey = (callee, pname, pe["def_line"])
                        if pkey not in tainted:
                            tainted[pkey] = ([origin_key, pkey], ["DIRECT_ARGUMENT", PARAMETER_KIND])
                        break

    worklist = list(tainted.keys())
    while worklist:
        symbol_key, var, def_line = worklist.pop()
        hops_so_far, path_so_far = tainted[(symbol_key, var, def_line)]
        for e in edges_by_symbol.get(symbol_key, []):
            if e["var"] != var or e["def_line"] != def_line:
                continue
            use_line = e["use_line"]

            # (a) 같은 함수 안 — 다른 변수의 정의로 이어짐(`z = y` 패턴).
            for w, wk, _wt in defs_at_line.get(symbol_key, {}).get(use_line, []):
                wkey = (symbol_key, w, use_line)
                if wkey not in tainted:
                    tainted[wkey] = (hops_so_far + [wkey], path_so_far + [e["evidence_kind"]])
                    worklist.append(wkey)

            # (b) 함수 경계 — use_line이 어느 호출식의 인자 "자신의" 줄과
            # 맞아떨어지면(호출식이 여러 줄에 걸쳐도 이 인덱스는 인자 자신의
            # 줄로 찾는다) 그 위치를 콜리(callee)의 파라미터로 이어 붙인다.
            for call, pos in arg_index.get((symbol_key, use_line), []):
                if call["arg_vars"][pos] != var:
                    continue
                for callee in callees_by_line.get((symbol_key, call["line"]), []):
                    param_names = functions.get(callee, [])
                    if pos >= len(param_names) or param_names[pos] == "?":
                        continue
                    pname = param_names[pos]
                    for pe in edges_by_symbol.get(callee, []):
                        if pe["var"] == pname and pe["evidence_kind"] == PARAMETER_KIND:
                            pkey = (callee, pname, pe["def_line"])
                            if pkey not in tainted:
                                tainted[pkey] = (hops_so_far + [pkey],
                                                  path_so_far + [e["evidence_kind"], PARAMETER_KIND])
                                worklist.append(pkey)
                            break

    for c in calls:
        sink_category = _match_sink_category(c["target_text"])
        if sink_category is None:
            continue
        symbol_key = c["symbol_key"]
        arg_lines = c.get("arg_lines") or []
        for idx, av in enumerate(c["arg_vars"]):
            av_line = arg_lines[idx] if idx < len(arg_lines) else c["line"]
            for e in edges_by_symbol.get(symbol_key, []):
                key = (symbol_key, av, e["def_line"])
                if e["var"] == av and e["use_line"] == av_line and key in tainted:
                    hops, kinds = tainted[key]
                    src_symbol, src_var, src_line = hops[0]
                    findings.append({
                        "category": sink_category,
                        "sink_symbol_key": symbol_key,
                        "sink_line": c["line"],
                        "sink_call": c["target_text"],
                        "source_symbol_key": src_symbol,
                        "source_var": src_var,
                        "source_line": src_line,
                        "cross_function": src_symbol != symbol_key,
                        "evidence_kinds": kinds + [e["evidence_kind"]],
                        "path": hops + [(symbol_key, av, av_line)],
                    })
    return findings


def find_domain_crossings(call_links: list[dict], calls: list[dict],
                           file_domain_by_symbol: dict[str, str]) -> list[dict]:
    """비즈니스 IP 모드 — 도메인 그래프(`/scans/{id}/graph/domains`)가 이미

    도메인 간 호출 수를 "집계"해서 보여준다. 이 함수는 그 집계 뒤에 숨은
    **개별 사례** — 어느 함수가 어느 인자를 들고 도메인 경계를 넘는지 —
    를 낸다. source/sink 정규식은 안 쓴다: "호출이 도메인 경계를 넘는가"
    자체가 판정 기준이다 — 위험한지 아닌지는 WBS 2.3(Risk Scoring) 몫이고
    여기서는 "이런 흐름이 존재한다"는 사실만 증명한다.
    """
    calls_by_line: dict[tuple[str, int], list[dict]] = {}
    for c in calls:
        calls_by_line.setdefault((c["symbol_key"], c["line"]), []).append(c)

    findings: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for link in call_links:
        caller, callee = link["caller_symbol_key"], link["callee_symbol_key"]
        src_domain = file_domain_by_symbol.get(caller)
        dst_domain = file_domain_by_symbol.get(callee)
        if not src_domain or not dst_domain or src_domain == dst_domain:
            continue
        key = (caller, callee, link["call_line"])
        if key in seen:
            continue
        seen.add(key)
        for c in calls_by_line.get((caller, link["call_line"]), []):
            findings.append({
                "category": f"domain_crossing:{dst_domain}",
                "caller_symbol_key": caller,
                "callee_symbol_key": callee,
                "call_line": link["call_line"],
                "source_domain": src_domain,
                "sink_domain": dst_domain,
                "target_call": c["target_text"],
                "arg_vars": [v for v in c["arg_vars"] if v != "?"],
            })
    return findings
