"""Risk Evidence Graph — 근거 역추적용 그래프 구조(phase2 plan §5, §16).

"business_ip = 0.94" 만 있는 게 아니라, 그 값이 어떤 근거들에서 나왔는지를
사용자가 되짚어볼 수 있어야 한다(§16: "왜 이 코드가 위험하다고 판단되는가?"의
역추적). 이 모듈은 `risk_evidence` 행들을 그런 그래프 형태로 조립한다.

    target(symbol)
      ├─ [kind: business_semantic]
      │     └─ evidence: "핵심 업무 로직 어휘 'pricing' ..."
      ├─ [kind: called_by]
      │     └─ evidence ── related_symbol: OrderService.checkout
      └─ [kind: security_pattern]
            └─ evidence ── related_symbol: <sink>

프론트(Step 12, 이번 범위 밖)가 이 구조를 그대로 그려서 Risk → Evidence →
Call Graph/Dataflow → Source 로 드릴다운한다.
"""

from __future__ import annotations

from typing import Any

from app.risk.evidence import Evidence


def build_evidence_graph(target_symbol_key: str, target_label: str,
                          evidence: list[Evidence],
                          symbol_labels: dict[str, str] | None = None) -> dict[str, Any]:
    symbol_labels = symbol_labels or {}
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def sym_node(key: str) -> str:
        nid = f"sym:{key}"
        nodes.setdefault(nid, {
            "id": nid, "type": "symbol", "symbol_key": key,
            "label": symbol_labels.get(key, key.split("#")[-1] if "#" in key else key),
        })
        return nid

    root = sym_node(target_symbol_key)
    nodes[root]["label"] = target_label
    nodes[root]["is_target"] = True

    groups: dict[str, list[Evidence]] = {}
    for e in evidence:
        groups.setdefault(e.kind, []).append(e)

    for kind, items in groups.items():
        gid = f"grp:{kind}"
        nodes[gid] = {"id": gid, "type": "group", "kind": kind, "label": kind,
                       "count": len(items),
                       "dimensions": sorted({i.dimension for i in items})}
        edges.append({"source": root, "target": gid, "rel": "has_evidence"})
        for e in items:
            eid = f"ev:{e.id}" if e.id is not None else f"ev:{kind}:{abs(hash(e.fingerprint)) % 10**8}"
            nodes[eid] = {
                "id": eid, "type": "evidence", "evidence_id": e.id,
                "kind": e.kind, "dimension": e.dimension,
                "weight": round(e.weight, 3), "confidence": round(e.confidence, 3),
                "description": e.description, "source": e.source,
                "location": e.location,
            }
            edges.append({"source": gid, "target": eid, "rel": "item"})
            if e.related_symbol_key:
                edges.append({"source": eid, "target": sym_node(e.related_symbol_key), "rel": e.kind})

    return {"target": target_symbol_key, "nodes": list(nodes.values()), "edges": edges}
