"""코드 지도(Code Map) — graphify(Graphify-Labs, Apache-2.0)로 심볼 그래프를 모듈 단위로 묶는다.

추출은 우리 파이프라인(tree-sitter → symbol / symbol_edge)을 그대로 쓰고, 그 위에
graphify 의 클러스터링·분석을 라이브러리로 직접 호출한다:
    graphify.cluster.cluster / label_communities_by_hub / score_all  → 모듈(커뮤니티)
    graphify.analyze.god_nodes / surprising_connections → 요약 카드(추천 질문은 한국어로 직접 생성)
graphify 내부 API 는 0.9.x 에서 빠르게 바뀌므로 호출은 이 파일 한 곳에만 둔다
(requirements 에서 버전 고정). graphify 가 없으면 디렉터리 단위 묶음으로 폴백해
화면은 그대로 동작한다.

전체 노드 헤어볼 대신 "모듈 지도 → 모듈 안 함수 → 코드" 순서로 내려가게 하는 게 목적.
"""

from __future__ import annotations

import json
import posixpath
from collections import Counter, defaultdict

# 심볼 한두 개짜리 모듈은 지도에서 잡음이다 — 이보다 작으면 같은 파일/디렉터리의
# 큰 모듈로 흡수한다.
MIN_COMMUNITY_SIZE = 3
OTHER_ID = -1
OTHER_NAME = "기타"
FALLBACK_ENGINE = "fallback:directory"
# 모듈 이름·추천 질문 규칙의 버전 — 바꾸면 올려서 code_map 캐시를 다시 만들게 한다.
REPORT_VERSION = "r2"
HUB_MIN_DEGREE = 10  # 근거 서브그래프에서 이보다 연결이 적으면 허브로 보지 않는다


def short_label(qualified_name: str | None) -> str:
    """`src.controllers.order.cancelOrder` → `cancelOrder`. 지도 라벨은 짧아야 겹치지 않는다."""
    name = (qualified_name or "").strip()
    for sep in ("::", "#", "."):
        if sep in name:
            name = name.rsplit(sep, 1)[-1]
    return name or (qualified_name or "")


def _confidence_tag(resolved: bool, confidence: float | None) -> str:
    # graphify 는 EXTRACTED/INFERRED/AMBIGUOUS 를 본다(AMBIGUOUS 는 클러스터 가중치 절반).
    c = float(confidence or 0.0)
    if resolved and c >= 0.9:
        return "EXTRACTED"
    if c >= 0.5:
        return "INFERRED"
    return "AMBIGUOUS"


def load_graph_rows(cur, scan_id) -> tuple[list[dict], list[dict]]:
    """코드 지도에 쓰는 심볼 + 해석된 엣지. 외부(미해석) 호출은 모듈 구분에 잡음이라 뺀다."""
    cur.execute(
        """
        SELECT s.symbol_key, s.kind, s.qualified_name, s.path, s.start_line,
               mod_sem.domain AS domain
          FROM symbol s
          LEFT JOIN symbol mod_s
                 ON mod_s.scan_id = s.scan_id AND mod_s.path = s.path AND mod_s.kind = 'module'
          LEFT JOIN symbol_semantic mod_sem
                 ON mod_sem.scan_id = s.scan_id AND mod_sem.symbol_key = mod_s.symbol_key
         WHERE s.scan_id = %s
        """,
        (str(scan_id),),
    )
    symbols = cur.fetchall()
    cur.execute(
        """
        SELECT src_symbol_key, dst_symbol_key, edge_type::text AS edge_type, resolved, confidence
          FROM symbol_edge
         WHERE scan_id = %s AND resolved AND dst_symbol_key IS NOT NULL
        """,
        (str(scan_id),),
    )
    return symbols, cur.fetchall()


def build_nx_graph(symbols: list[dict], edges: list[dict]):
    """graphify 가 읽는 속성 이름(label/source_file/relation/confidence)으로 DiGraph 를 만든다."""
    import networkx as nx

    G = nx.DiGraph()
    for s in symbols:
        G.add_node(
            s["symbol_key"],
            label=short_label(s["qualified_name"]),
            norm_label=(s["qualified_name"] or "").lower(),
            source_file=s["path"],
            source_location=f"L{s['start_line']}" if s.get("start_line") else None,
            file_type="code",
            kind=s["kind"],
        )
    for e in edges:
        src, dst = e["src_symbol_key"], e["dst_symbol_key"]
        if src not in G or dst not in G or src == dst:
            continue
        if G.has_edge(src, dst):
            G[src][dst]["weight"] += 1.0
            continue
        G.add_edge(src, dst, relation=e["edge_type"], weight=1.0,
                   confidence=_confidence_tag(e["resolved"], e["confidence"]))
    return G


def _fallback_partition(symbols: list[dict]) -> dict[int, list[str]]:
    """graphify/networkx 없이 — 파일의 상위 디렉터리 단위로 묶는다."""
    groups: dict[str, list[str]] = defaultdict(list)
    for s in symbols:
        groups[posixpath.dirname(s["path"] or "") or "/"].append(s["symbol_key"])
    ordered = sorted(groups.values(), key=lambda m: (-len(m), sorted(m)[0]))
    return {i: sorted(m) for i, m in enumerate(ordered)}


def _absorb_small(partition: dict[int, list[str]], symbols: list[dict]) -> dict[int, list[str]]:
    """작은 커뮤니티(고립 심볼 포함)를 같은 파일 → 같은 디렉터리의 큰 커뮤니티로 흡수, 없으면 '기타'."""
    path_of = {s["symbol_key"]: s["path"] or "" for s in symbols}
    big = {cid: m for cid, m in partition.items() if len(m) >= MIN_COMMUNITY_SIZE}
    small = [k for cid, m in partition.items() if len(m) < MIN_COMMUNITY_SIZE for k in m]
    assigned = {k for m in partition.values() for k in m}
    small += [s["symbol_key"] for s in symbols if s["symbol_key"] not in assigned]

    by_file: dict[str, Counter] = defaultdict(Counter)
    by_dir: dict[str, Counter] = defaultdict(Counter)
    for cid, members in big.items():
        for k in members:
            by_file[path_of.get(k, "")][cid] += 1
            by_dir[posixpath.dirname(path_of.get(k, ""))][cid] += 1

    result = {cid: list(m) for cid, m in big.items()}
    for k in small:
        p = path_of.get(k, "")
        votes = by_file.get(p) or by_dir.get(posixpath.dirname(p))
        cid = min(votes, key=lambda c: (-votes[c], c)) if votes else OTHER_ID
        result.setdefault(cid, []).append(k)
    return {cid: sorted(m) for cid, m in result.items()}


def _top_dir(paths: list[str]) -> str | None:
    dirs = Counter(posixpath.dirname(p) for p in paths if p)
    return dirs.most_common(1)[0][0] if dirs else None


def compute_code_map(symbols: list[dict], edges: list[dict]) -> dict:
    """심볼/엣지 → {engine, communities, membership, links, report}. 순수 함수(테스트 가능)."""
    try:
        from graphify.analyze import god_nodes, surprising_connections
        from graphify.cluster import cluster, label_communities_by_hub, score_all
    except ImportError:
        cluster = None
    engine = current_engine() if cluster is not None else FALLBACK_ENGINE

    sym_by_key = {s["symbol_key"]: s for s in symbols}
    G = None
    if cluster is not None:
        G = build_nx_graph(symbols, edges)
        connected = G.subgraph([n for n in G if G.degree(n) > 0]).copy()
        raw = cluster(connected, exclude_hubs_percentile=99) if connected.number_of_nodes() else {}
        partition = _absorb_small(raw, symbols)
        hub_names = label_communities_by_hub(G, {c: m for c, m in partition.items() if c != OTHER_ID})
        cohesion = score_all(G, {c: m for c, m in partition.items() if c != OTHER_ID})
    else:
        partition = _absorb_small(_fallback_partition(symbols), symbols)
        hub_names, cohesion = {}, {}

    membership = {k: cid for cid, members in partition.items() for k in members}

    communities = []
    for cid, members in sorted(partition.items(), key=lambda kv: (kv[0] == OTHER_ID, -len(kv[1]), kv[0])):
        paths = [sym_by_key[k]["path"] for k in members if k in sym_by_key]
        domains = Counter(sym_by_key[k].get("domain") for k in members if sym_by_key.get(k, {}).get("domain"))
        domain = domains.most_common(1)[0][0] if domains else None
        top_dir = _top_dir(paths)
        hub = hub_names.get(cid) or (posixpath.basename(top_dir) if top_dir else None)
        # 이름은 사람이 읽는 단위(도메인 > 디렉터리)로. 허브 함수명(fail/sleep 같은 유틸)은
        # 이름으로 쓰면 오해를 사서, 같은 이름이 겹칠 때 구분용으로만 붙인다(아래).
        if cid == OTHER_ID:
            name = OTHER_NAME
        else:
            name = domain or (posixpath.basename(top_dir) if top_dir else None) or hub or f"모듈 {cid + 1}"
        communities.append({
            "id": cid, "name": name, "hub": hub, "domain": domain, "size": len(members),
            "cohesion": round(float(cohesion.get(cid, 0.0)), 3), "top_dir": top_dir,
            "file_count": len(set(paths)),
        })

    name_count = Counter(c["name"] for c in communities)
    for c in communities:
        if name_count[c["name"]] > 1 and c["id"] != OTHER_ID:
            extra = c["hub"] if c["hub"] and c["hub"] != c["name"] else posixpath.basename(c["top_dir"] or "")
            c["name"] = f"{c['name']} · {extra}" if extra else f"{c['name']} {c['id'] + 1}"

    # 모듈 간 연결 — 지도 뷰의 엣지(가중치 = 호출/의존 수)
    link_w: Counter = Counter()
    for e in edges:
        a, b = membership.get(e["src_symbol_key"]), membership.get(e["dst_symbol_key"])
        if a is None or b is None or a == b:
            continue
        link_w[(a, b)] += 1
    links = [{"source": a, "target": b, "weight": w} for (a, b), w in sorted(link_w.items())]

    report = {"key_symbols": [], "surprising": [], "questions": []}
    if G is not None:
        report = _graphify_report(G, partition, god_nodes, surprising_connections)
        report["questions"] = korean_questions(report, communities, links)
    return {"engine": engine, "communities": communities, "membership": membership,
            "links": links, "report": report}


def _graphify_report(G, partition, god_nodes, surprising_connections) -> dict:
    """graphify GRAPH_REPORT 의 핵심 개념 / 의외의 연결 / 추천 질문을 카드용 JSON 으로."""
    comms = {c: m for c, m in partition.items() if c != OTHER_ID}
    und = G.to_undirected()
    out = {"key_symbols": [], "surprising": [], "questions": []}
    try:
        out["key_symbols"] = [{"symbol_key": g["id"], "label": g["label"], "degree": g["degree"]}
                              for g in god_nodes(und, top_n=5)]
    except Exception:  # noqa: BLE001 — 요약 카드는 부가 정보, 실패해도 지도는 보여준다
        pass
    try:
        out["surprising"] = [
            {"source": s["source"], "target": s["target"], "relation": s.get("relation"),
             "why": s.get("why") or s.get("note")}
            for s in surprising_connections(und, comms, top_n=5)
        ]
    except Exception:  # noqa: BLE001
        pass
    return out


MAX_QUESTIONS = 4
LOW_COHESION = 0.15  # 이보다 낮고 큰 모듈은 "여러 기능이 섞였다"는 신호
MIN_SPLIT_SIZE = 8


def korean_questions(report: dict, communities: list[dict], links: list[dict]) -> list[str]:
    """요약 카드의 추천 질문 — graphify 신호(핵심 함수·의외의 연결·응집도)로 한국어 질문을 만든다.

    graphify suggest_questions 는 영어 템플릿 문장만 내놓아서 쓰지 않는다. 질문은 그대로
    "AI에게 질문"에 들어가므로 우리 에이전트가 답할 수 있는 형태(함수 역할·호출 관계·모듈 연결)로.
    """
    names = {c["id"]: c["name"] for c in communities}
    out: list[str] = []

    def add(q: str) -> None:
        if q not in out:
            out.append(q)

    if report.get("key_symbols"):
        add(f"{report['key_symbols'][0]['label']} 함수는 어떤 역할을 하고 어디서 호출돼?")
    cross = [l for l in links if l["source"] != OTHER_ID and l["target"] != OTHER_ID]
    if cross:
        top = max(cross, key=lambda l: (l["weight"], -l["source"], -l["target"]))
        add(f"{names[top['source']]} 모듈은 {names[top['target']]} 모듈을 어떻게 사용해?")
    for s in report.get("surprising") or []:
        if s.get("source") and s.get("target") and s["source"] != s["target"]:
            add(f"{s['source']} 함수가 {s['target']} 함수를 호출하는 이유는 뭐야?")
            break
    loose = [c for c in communities
             if c["id"] != OTHER_ID and c["size"] >= MIN_SPLIT_SIZE and c["cohesion"] < LOW_COHESION]
    if loose:
        c = min(loose, key=lambda c: (c["cohesion"], -c["size"]))
        add(f"{c['name']} 모듈에는 어떤 기능들이 섞여 있어?")
    return out[:MAX_QUESTIONS]


def current_engine() -> str:
    """캐시 무효화 키 — graphify 버전이 바뀌면 code_map 을 다시 계산한다."""
    try:
        import graphify  # noqa: F401
    except ImportError:
        return FALLBACK_ENGINE
    try:
        from importlib.metadata import version

        return f"graphify {version('graphifyy')}/{REPORT_VERSION}"
    except Exception:  # noqa: BLE001 — 메타데이터 없는 설치(소스 체크아웃 등)
        return "graphify"


def get_or_build_code_map(cur, scan_id) -> dict:
    """code_map 캐시를 읽고, 없거나 engine 이 바뀌었으면 계산해 저장한다."""
    cur.execute("SELECT engine, communities, membership, links, report FROM code_map WHERE scan_id=%s",
                (str(scan_id),))
    row = cur.fetchone()
    if row and row["engine"] == current_engine():
        return dict(row)
    symbols, edges = load_graph_rows(cur, scan_id)
    cm = compute_code_map(symbols, edges)
    cur.execute(
        """
        INSERT INTO code_map (scan_id, engine, communities, membership, links, report)
        VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
        ON CONFLICT (scan_id) DO UPDATE
           SET engine=EXCLUDED.engine, communities=EXCLUDED.communities,
               membership=EXCLUDED.membership, links=EXCLUDED.links,
               report=EXCLUDED.report, created_at=now()
        """,
        (str(scan_id), cm["engine"], json.dumps(cm["communities"]), json.dumps(cm["membership"]),
         json.dumps(cm["links"]), json.dumps(cm["report"])),
    )
    return cm


def query_subgraph(symbols: list[dict], edges: list[dict], seeds: list[str],
                   depth: int = 1, max_nodes: int = 60) -> list[str]:
    """graphify `query` 와 같은 방식의 근거 서브그래프 — 시드에서 BFS 로 depth 만큼 넓히되
    허브(차수 상위 1%)는 거쳐 가지 않고, max_nodes 에서 자른다.

    graphify 는 질문 단어로 시드를 고르지만, 여기선 AI 답변이 실제로 쓴 근거 심볼이
    시드다(더 정확하다). 허브를 건너뛰는 이유도 같다 — logger/util 같은 허브 하나가
    서브그래프를 다시 헤어볼로 만든다.
    """
    keys = {s["symbol_key"] for s in symbols}
    adj: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        a, b = e["src_symbol_key"], e["dst_symbol_key"]
        if a in keys and b in keys and a != b:
            adj[a].add(b)
            adj[b].add(a)
    degrees = sorted(len(v) for v in adj.values())
    # 허브 = 차수가 상위 1% 값과 HUB_MIN_DEGREE 둘 다를 넘는 노드. 리프가 대부분인 그래프에선
    # p99 가 1까지 내려가 모든 노드가 허브가 되므로 바닥값을 둔다.
    p99 = degrees[min(len(degrees) - 1, int(len(degrees) * 0.99))] if degrees else 0
    hub_over = max(p99, HUB_MIN_DEGREE)

    def is_hub(n: str) -> bool:
        return len(adj[n]) > hub_over

    kept: list[str] = []
    seen: set[str] = set()
    for s in seeds:
        if s in keys and s not in seen:
            seen.add(s)
            kept.append(s)
    frontier = list(kept)
    for _ in range(max(0, depth)):
        nxt = []
        for n in frontier:
            if is_hub(n) and n not in seeds:
                continue
            for m in sorted(adj[n]):
                if len(kept) >= max_nodes:
                    return kept
                if m in seen or is_hub(m):
                    continue
                seen.add(m)
                kept.append(m)
                nxt.append(m)
        frontier = nxt
    return kept[:max_nodes]


def shortest_path(symbols: list[dict], edges: list[dict], src: str, dst: str) -> list[str] | None:
    """graphify `path A B` 와 같은 결정적 최단 경로 — 호출 방향 우선, 없으면 방향 무시.

    BFS 이웃을 정렬해서 돌아 동률 경로가 매번 같게 나온다(graphify 가 정렬 그래프를 쓰는 이유와 같음).
    networkx 없이 동작하도록 직접 구현.
    """
    keys = {s["symbol_key"] for s in symbols}
    if src not in keys or dst not in keys:
        return None
    fwd: dict[str, set[str]] = defaultdict(set)
    both: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        a, b = e["src_symbol_key"], e["dst_symbol_key"]
        if a in keys and b in keys and a != b:
            fwd[a].add(b)
            both[a].add(b)
            both[b].add(a)
    return _bfs(fwd, src, dst) or _bfs(both, src, dst)


def _bfs(adj: dict[str, set[str]], src: str, dst: str) -> list[str] | None:
    if src == dst:
        return [src]
    prev = {src: None}
    frontier = [src]
    while frontier:
        nxt = []
        for n in frontier:
            for m in sorted(adj.get(n, ())):
                if m in prev:
                    continue
                prev[m] = n
                if m == dst:
                    path = [m]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])
                    return path[::-1]
                nxt.append(m)
        frontier = nxt
    return None
