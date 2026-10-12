"""Risk Evidence 수집 — 기존 NeoSDL 분석에서 근거를 끌어온다(phase2 plan §4, §25.1).

새 분석을 만들지 않는다. `symbol_semantic`(annotate), `symbol_edge`(call graph),
`security_finding`(dataflow taint), `cfg_edge`/`pdg_data_edge`(CFG/PDG), 그리고
deterministic 정규식(`secret_scan`/`pii_scan`)의 출력을 Risk 차원에 매핑한
`Evidence` 목록으로 정규화한다.

한 번의 스캔 전체를 훑는 몇 개의 집계 쿼리로 데이터를 모은 뒤 Python 에서
심볼별로 그룹핑한다(`app/dataflow.py` 와 같은 방식) — 심볼마다 쿼리를 날리지
않는다.

`source_reader(path) -> str | None` 는 선택적이다. 주어지면 함수 본문 구간을
잘라 secret/PII 정규식을 돌린다. 없으면(단위 테스트 등) 소스 기반 근거는 건너뛴다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from app.risk import pii_scan, secret_scan
from app.risk.policy import RiskPolicy

log = logging.getLogger("app.risk.evidence")

SourceReader = Callable[[str], "str | None"]

# 경쟁우위/핵심 IP 를 암시하는 어휘(§7). 임계값이 아니라 사전이므로 정책이 아닌
# 코드에 둔다. Business/IP 분류 LLM(classify.py)이 이 신호를 근거로 받아 정제한다(§8).
_IP_CORE_TERMS = (
    "pricing", "price", "discount", "fee", "rate", "quote", "billing",
    "recommend", "recommendation", "ranking", "rank", "matching", "match",
    "fraud", "risk score", "scoring", "credit", "underwrit",
    "optimization", "optimize", "allocation", "routing algorithm",
    "settlement", "clearing", "payout", "reconcil",
    "approval workflow", "authorization", "decision rule", "eligibility",
    "가격", "요금", "할인", "정산", "추천", "순위", "매칭", "승인", "심사",
    "부정", "이상거래", "한도", "수수료", "배분", "최적화",
)

_SOURCE_SENSITIVE_TERMS = (
    "proprietary", "internal only", "confidential", "trade secret",
    "internal service", "private api", "internal system",
    "사내", "내부 전용", "대외비", "영업비밀", "내부 시스템",
)

# 파일 도메인이 이 어휘를 포함하면 "민감 흐름의 상류"로 본다(§7.4).
_SENSITIVE_DOMAIN_TERMS = ("payment", "결제", "auth", "인증", "fraud", "부정", "정산", "settlement",
                            "billing", "요금", "pricing", "가격")


@dataclass
class Evidence:
    """`risk_evidence` 한 행에 대응. `id` 는 DB 저장 후 채워진다."""

    target_symbol_key: str
    kind: str                       # §6 evidence kind
    dimension: str                  # business_ip | source_sensitivity | security | pii | secret | none
    weight: float
    confidence: float
    description: str
    source: str
    related_symbol_key: str | None = None
    location: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    id: int | None = None

    @property
    def fingerprint(self) -> str:
        loc = self.location or {}
        return "|".join((
            self.kind,
            self.related_symbol_key or "",
            f"{loc.get('path') or ''}:{loc.get('line') or ''}",
            self.description[:80],
        ))

    def to_row(self, scan_id: str) -> tuple:
        return (
            scan_id, self.target_symbol_key, self.related_symbol_key, self.kind,
            self.dimension, self.weight, self.confidence, self.description, self.source,
            json.dumps(self.location, ensure_ascii=False),
            json.dumps(self.metadata, ensure_ascii=False, default=str),
            self.fingerprint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target_symbol_key,
            "related": self.related_symbol_key,
            "kind": self.kind,
            "dimension": self.dimension,
            "weight": round(self.weight, 4),
            "confidence": round(self.confidence, 4),
            "description": self.description,
            "source": self.source,
            "location": self.location,
            "metadata": self.metadata,
        }


def _contains_any(text: str | None, terms) -> str | None:
    if not text:
        return None
    low = text.lower()
    for t in terms:
        if t in low:
            return t
    return None


def collect_evidence(
    cur, scan_id: str, policy: RiskPolicy, *,
    targets: list[str] | None = None,
    source_reader: SourceReader | None = None,
) -> dict[str, list[Evidence]]:
    """scan 의 (선택된) function/method 심볼별 Evidence 목록.

    targets 가 None 이면 이 스캔의 모든 function/method 심볼이 대상이다.
    """
    cur.execute(
        """
        SELECT symbol_key, kind, qualified_name, path, start_line, end_line, param_types
          FROM symbol
         WHERE scan_id = %s AND kind IN ('function', 'method')
        """,
        (scan_id,),
    )
    symbols = {r["symbol_key"]: r for r in cur.fetchall()}
    if targets is not None:
        symbols = {k: v for k, v in symbols.items() if k in targets}
    if not symbols:
        return {}

    # 심볼 → 파일 경로(모든 심볼, caller 도메인 해석용)
    cur.execute("SELECT symbol_key, path FROM symbol WHERE scan_id = %s", (scan_id,))
    path_by_symbol = {r["symbol_key"]: r["path"] for r in cur.fetchall()}

    # 파일(module) 단위 role/domain/summary
    cur.execute(
        """
        SELECT s.path, sem.role, sem.domain, sem.summary
          FROM symbol s
          JOIN symbol_semantic sem ON sem.scan_id = s.scan_id AND sem.symbol_key = s.symbol_key
         WHERE s.scan_id = %s AND s.kind = 'module'
        """,
        (scan_id,),
    )
    file_sem = {r["path"]: r for r in cur.fetchall()}

    # 심볼 단위 summary
    cur.execute("SELECT symbol_key, summary FROM symbol_semantic WHERE scan_id = %s", (scan_id,))
    sym_summary = {r["symbol_key"]: r["summary"] for r in cur.fetchall()}

    # fan-in (call centrality) + caller 목록
    cur.execute(
        """
        SELECT dst_symbol_key, count(*) AS fan_in, array_agg(src_symbol_key) AS callers
          FROM symbol_edge
         WHERE scan_id = %s AND resolved AND edge_type = 'calls'
         GROUP BY dst_symbol_key
        """,
        (scan_id,),
    )
    fan_in = {r["dst_symbol_key"]: r for r in cur.fetchall()}

    cur.execute("SELECT * FROM security_finding WHERE scan_id = %s", (scan_id,))
    findings = cur.fetchall()

    cur.execute(
        "SELECT symbol_key, count(*) AS n FROM pdg_data_edge WHERE scan_id = %s GROUP BY symbol_key",
        (scan_id,),
    )
    pdg_edge_count = {r["symbol_key"]: r["n"] for r in cur.fetchall()}

    cur.execute(
        """
        SELECT symbol_key, count(*) AS n FROM cfg_edge
         WHERE scan_id = %s AND branch_label IN ('true', 'false', 'case', 'loop_back')
         GROUP BY symbol_key
        """,
        (scan_id,),
    )
    branch_count = {r["symbol_key"]: r["n"] for r in cur.fetchall()}

    cur.execute("SELECT symbol_key, param_names FROM pdg_function WHERE scan_id = %s", (scan_id,))
    param_names = {r["symbol_key"]: (r["param_names"] or []) for r in cur.fetchall()}

    fan_in_sat = float(policy.get("centrality", "fan_in_saturation", default=6) or 6)

    out: dict[str, list[Evidence]] = {}
    for key, sym in symbols.items():
        ev: list[Evidence] = []
        path = sym["path"]
        fsem = file_sem.get(path, {})
        summary = sym_summary.get(key) or ""
        haystack = " ".join(filter(None, [sym["qualified_name"], summary, fsem.get("summary"), fsem.get("domain")]))

        # --- Business/IP: 의미(§7, §8) ---
        term = _contains_any(haystack, _IP_CORE_TERMS)
        if term:
            ev.append(Evidence(
                key, "business_semantic", "business_ip",
                weight=policy.weight("business_semantic"), confidence=0.6,
                description=f"핵심 업무 로직 어휘 '{term}' 가 의미/이름에 나타남"
                            + (f" (도메인: {fsem['domain']})" if fsem.get("domain") else ""),
                source="semantic_search",
                location={"path": path, "line": sym["start_line"]},
                metadata={"term": term, "domain": fsem.get("domain"), "summary": summary or None},
            ))
        elif fsem.get("domain"):
            # 도메인 소속 자체는 위험이 아니다 — dimension="none" 으로 점수에는 안 들어가고
            # Evidence Graph 의 맥락 정보로만 남는다(§5). 여기에 점수를 주면 거의 모든
            # 심볼이 business_ip 를 갖게 되어 약한 신호들이 쌓여 과탐이 난다(§20).
            ev.append(Evidence(
                key, "semantic", "none",
                weight=0.0, confidence=0.35,
                description=f"업무 도메인 '{fsem['domain']}' 에 속한 로직",
                source="semantic_search",
                location={"path": path, "line": sym["start_line"]},
                metadata={"domain": fsem.get("domain")},
            ))

        # --- Source sensitivity: 의미 ---
        sterm = _contains_any(haystack, _SOURCE_SENSITIVE_TERMS)
        if sterm:
            ev.append(Evidence(
                key, "business_semantic", "source_sensitivity",
                weight=policy.weight("source_sensitivity_semantic"), confidence=0.55,
                description=f"대외 비공개를 암시하는 표현 '{sterm}'",
                source="semantic_search",
                location={"path": path, "line": sym["start_line"]},
                metadata={"term": sterm},
            ))

        # --- Call centrality (§4 Call Graph, §7.3) ---
        fi = fan_in.get(key)
        if fi and fi["fan_in"] >= 2:
            n = fi["fan_in"]
            norm = min(1.0, n / fan_in_sat)
            ev.append(Evidence(
                key, "centrality", "business_ip",
                weight=policy.weight("call_centrality"), confidence=min(0.85, 0.4 + 0.1 * n),
                description=f"여러 핵심 경로에서 호출됨 (fan-in {n})",
                source="callers_within",
                location={"path": path, "line": sym["start_line"]},
                metadata={"fan_in": n, "normalized": round(norm, 3)},
            ))
            # 민감 도메인 caller (§7.4)
            for caller in dict.fromkeys(fi["callers"] or []):
                cpath = path_by_symbol.get(caller)
                cdomain = (file_sem.get(cpath, {}) or {}).get("domain") if cpath else None
                if cdomain and _contains_any(cdomain, _SENSITIVE_DOMAIN_TERMS):
                    ev.append(Evidence(
                        key, "called_by", "business_ip",
                        weight=policy.weight("called_by_sensitive"), confidence=0.6,
                        description=f"민감 도메인 '{cdomain}' 흐름에서 호출됨",
                        source="callers_within", related_symbol_key=caller,
                        location={"path": path},
                        metadata={"caller_domain": cdomain, "caller_path": cpath},
                    ))

        # --- PDG core dependency / CFG complexity (§4) ---
        pdg_n = pdg_edge_count.get(key, 0)
        if pdg_n >= 8:
            ev.append(Evidence(
                key, "pdg", "business_ip",
                weight=policy.weight("pdg_core_dependency"), confidence=0.4,
                description=f"내부 데이터 의존이 조밀함 (PDG 데이터 엣지 {pdg_n}개) — 고유 계산 로직 가능성",
                source="pdg", location={"path": path},
                metadata={"data_edges": pdg_n},
            ))
        br_n = branch_count.get(key, 0)
        if br_n >= 6:
            ev.append(Evidence(
                key, "cfg", "business_ip",
                weight=policy.weight("cfg_complexity"), confidence=0.35,
                description=f"제어 흐름이 복잡함 (분기 {br_n}개) — 규칙/정책 로직 가능성",
                source="cfg", location={"path": path},
                metadata={"branches": br_n},
            ))

        # --- PII: 파라미터/식별자 (§6) ---
        for hit in pii_scan.scan_identifiers(list(param_names.get(key, [])) + [sym["qualified_name"]]):
            ev.append(Evidence(
                key, "pii_detection", "pii",
                weight=policy.weight("pii_detection"), confidence=hit["confidence"],
                description=hit["description"], source="pii_scan",
                location={"path": path}, metadata=hit["metadata"],
            ))

        # --- 소스 기반: secret / PII 리터럴 (§6) ---
        if source_reader is not None:
            body = _read_region(source_reader, path, sym["start_line"], sym["end_line"])
            if body:
                for hit in secret_scan.scan_text(body, path=path):
                    ev.append(_from_scan_hit(key, hit, policy, "secret", sym["start_line"]))
                for hit in pii_scan.scan_text(body, path=path):
                    ev.append(_from_scan_hit(key, hit, policy, "pii", sym["start_line"]))

        out[key] = ev

    _attach_security_findings(out, symbols, findings, path_by_symbol, file_sem, policy)
    return out


def _from_scan_hit(key: str, hit: dict, policy: RiskPolicy, dimension: str, base_line: int) -> Evidence:
    weight_key = "secret_detection" if dimension == "secret" else "pii_detection"
    loc = dict(hit["location"])
    if loc.get("line"):
        loc["line"] = base_line + max(0, loc["line"] - 1)
    return Evidence(
        key, hit["kind"], dimension,
        weight=policy.weight(weight_key), confidence=hit["confidence"],
        description=hit["description"], source=hit["kind"].split("_")[0] + "_scan",
        location=loc, metadata={**hit["metadata"], "rule": hit["rule"]},
    )


_SECURITY_WEIGHT_BY_SEVERITY = {
    "critical": ("security_pattern_critical", 0.9),
    "high": ("security_pattern_high", 0.85),
    "medium": ("security_pattern_medium", 0.7),
    "low": ("security_pattern_medium", 0.5),
}


def _attach_security_findings(out, symbols, findings, path_by_symbol, file_sem, policy: RiskPolicy) -> None:
    """security_finding(§4 Dataflow) → security / business_ip 차원 근거."""
    for f in findings:
        endpoints = {f["sink_symbol_key"], f.get("source_symbol_key")}
        for hop in (f["path"] or []):
            if isinstance(hop, dict) and hop.get("symbol_key"):
                endpoints.add(hop["symbol_key"])
        endpoints = {e for e in endpoints if e in symbols}
        if not endpoints:
            continue

        if f["kind"] == "business" or str(f["category"]).startswith("domain_crossing:"):
            dst_domain = str(f["category"]).removeprefix("domain_crossing:")
            for ep in endpoints:
                out.setdefault(ep, []).append(Evidence(
                    ep, "domain_crossing", "business_ip",
                    weight=policy.weight("domain_crossing"), confidence=0.5,
                    description=f"값이 도메인 경계('{dst_domain}')를 넘어 흐름",
                    source="security_finding",
                    related_symbol_key=(f.get("source_symbol_key") if f.get("source_symbol_key") != ep else f["sink_symbol_key"]),
                    location={"line": f["sink_line"]},
                    metadata={"category": f["category"], "finding_id": f["id"]},
                ))
            continue

        weight_key, base_conf = _SECURITY_WEIGHT_BY_SEVERITY.get(f["severity"], ("security_pattern_medium", 0.6))
        for ep in endpoints:
            is_sink = ep == f["sink_symbol_key"]
            out.setdefault(ep, []).append(Evidence(
                ep, "security_pattern", "security",
                weight=policy.weight(weight_key), confidence=base_conf,
                description=f"{f['category']} 취약 경로의 "
                            + ("sink" if is_sink else "경유 지점")
                            + f" (심각도 {f['severity']})",
                source="security_finding",
                related_symbol_key=(f["sink_symbol_key"] if not is_sink else f.get("source_symbol_key")),
                location={"line": f["sink_line"]},
                metadata={"category": f["category"], "severity": f["severity"],
                          "evidence_kinds": f["evidence_kinds"], "finding_id": f["id"]},
            ))


def _read_region(reader: SourceReader, path: str, start_line: int, end_line: int) -> str | None:
    try:
        text = reader(path)
    except Exception:
        log.debug("source_reader 실패: %s", path, exc_info=True)
        return None
    if not text:
        return None
    lines = text.splitlines()
    lo = max(0, (start_line or 1) - 1)
    hi = min(len(lines), end_line or len(lines))
    return "\n".join(lines[lo:hi])
