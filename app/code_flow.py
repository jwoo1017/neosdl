"""채팅 답변 근거(tool_results)에서 "코드 흐름"을 뽑는다 — 흐름 탭 애니메이션 재생용.

- dataflow_security finding → taint 흐름 (source 함수 → … → sink 호출, hop 마다 오염 변수)
- call_graph caller/callee 경로 → 호출 흐름 (호출자 → … → 대상)

같은 함수가 연속되는 hop 은 하나로 접는다(프론트 GraphView 의 collapsePath 와 같은 규칙).
"""

from __future__ import annotations

from app.graphify_adapter import short_label

MAX_FLOWS = 3
MIN_HOPS = 2  # 한 함수 안에서 끝나는 경로는 "흐름"으로 보여줄 게 없다


def _hop(symbol_key: str, qualified_name: str | None, path: str | None, line, var: str | None = None) -> dict:
    return {"symbol_key": symbol_key, "label": short_label(qualified_name or symbol_key),
            "qualified_name": qualified_name, "path": path, "line": line, "var": var}


def _collapse(hops: list[dict]) -> list[dict]:
    out: list[dict] = []
    for h in hops:
        if out and out[-1]["symbol_key"] == h["symbol_key"]:
            if h.get("var") and not out[-1].get("var"):
                out[-1]["var"] = h["var"]
            continue
        out.append(h)
    return out


def flows_from_evidence(evidence: dict | None) -> list[dict]:
    if not evidence:
        return []
    flows: list[dict] = []
    seen: set[tuple] = set()

    def add(flow: dict) -> None:
        sig = tuple(h["symbol_key"] for h in flow["hops"])
        if len(flow["hops"]) < MIN_HOPS or sig in seen:
            return
        seen.add(sig)
        flows.append(flow)

    for f in evidence.get("dataflow_security") or []:
        hops = _collapse([_hop(h["symbol_key"], h.get("qualified_name"), h.get("path"), h.get("line"), h.get("var"))
                          for h in f.get("path") or [] if h.get("symbol_key")])
        add({"kind": "taint", "title": f"{f.get('category') or '오염 경로'} · {hops[-1]['label'] if hops else ''}",
             "category": f.get("category"), "sink": f.get("sink_call"), "sink_line": f.get("sink_line"),
             "hops": hops})

    callers = [p for p in evidence.get("call_graph") or [] if p.get("chain_hops")]
    # 긴 경로가 흐름으로서 더 많은 걸 보여준다
    for p in sorted(callers, key=lambda p: (-len(p["chain_hops"]), p.get("symbol_key") or "")):
        hops = _collapse([_hop(h["symbol_key"], h.get("qualified_name"), h.get("path"), h.get("line"))
                          for h in p["chain_hops"]])
        if not hops:
            continue
        verb = "호출 경로" if p.get("relation") == "caller" else "내부 호출"
        add({"kind": "call", "title": f"{verb} · {hops[0]['label']} → {hops[-1]['label']}",
             "category": None, "sink": None, "sink_line": None, "hops": hops})

    return flows[:MAX_FLOWS]
