"""스캔 하나에 대한 시맨틱 검색/Dataflow 조회 로직.

`app/main.py`의 `/scans/{id}/search`, `/scans/{id}/dataflow` 라우트와
`app/orchestrator.py`(LLM이 고른 도구 실행)가 둘 다 이 함수들을 호출한다.
main.py 안에 두면 orchestrator.py가 main을 import해야 해서 순환 임포트가
생기므로 별도 모듈로 뺐다. 접근 권한 확인은 호출자 책임(여기서는 안 함).
"""

from __future__ import annotations

import json
import re
from uuid import UUID

from fastapi import HTTPException

from app.config import settings
from app.dataflow import find_domain_crossings, find_security_paths_interprocedural
from app.embeddings import embed_text, to_vector_literal
from app.graph import make_graph_store
from app.security_fingerprint import compute_fingerprint
from app.security_severity import severity_for


def _filter_by_keywords(findings: list[dict], keywords: list[str] | None) -> list[dict]:
    """finding을 통째로 문자열화해서 키워드가 하나라도 있으면 남긴다.

    security/business 모드의 필드 구조가 서로 달라서(qualified_name/path/
    sink_call vs domain/arg_vars 등) 필드별로 따로 다루지 않고 JSON
    문자열 전체를 대상으로 검사한다 — 오케스트레이터가 "이 질문의 구체적
    관심사"로 좁혀 부를 때만 쓰는 후처리 필터이고, `app/dataflow.py`의
    탐색 알고리즘 자체는 건드리지 않는다.
    """
    lowered = [k.lower() for k in (keywords or []) if k]
    if not lowered:
        return findings
    kept = []
    for f in findings:
        blob = json.dumps(f, ensure_ascii=False, default=str).lower()
        if any(k in blob for k in lowered):
            kept.append(f)
    return kept


def _enable_iterative_scan(cur) -> None:
    """pgvector ≥0.8 반복 스캔 — HNSW 인덱스는 모든 스캔의 임베딩을 한데 담고 있어서,
    `scan_id`/`domain` 필터가 인덱스 탐색 *후에* 적용되면 ef_search(기본 40) 후보가
    대부분 다른 스캔 것이라 결과가 limit보다 적거나 0건이 된다. relaxed_order는 필터를
    통과한 결과가 limit만큼 찰 때까지 인덱스를 계속 훑는다. 지원 안 하는 버전이면
    savepoint로 감싸 트랜잭션을 깨지 않고 무시한다."""
    try:
        with cur.connection.transaction():
            cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
    except Exception:
        pass


def search_symbols(cur, scan_id: UUID, q: str, limit: int, domain: str | None = None,
                   min_similarity: float | None = None) -> list[dict]:
    """symbol_embedding(symbol.embed 잡이 채움)에 대한 코사인 유사도 검색.

    자연어 질의를 같은 임베딩 모델로 변환해서 pgvector HNSW 인덱스로
    최근접 이웃을 찾는다 — "로그인 처리하는 함수" 같은 질의가 심볼 이름에
    그 단어가 없어도 요약(summary) 의미로 매칭되는 게 핵심.

    `domain`을 주면 그 도메인으로 태깅된 파일(symbol_semantic.domain, WBS 2.2
    파일 단위 태깅)에 속한 심볼로만 후보를 좁힌 뒤 유사도 순위를 매긴다 —
    `app/orchestrator.py`의 `codebase_structure` 도구가 찾은 도메인 이름을
    그대로 넘기면 코드베이스 전체가 아니라 그 도메인 안에서만 검색해서,
    관련 없는 도메인의 파일이 우연히 높은 유사도로 걸리는 오탐(예: 이미지/폰트
    유틸이 "회원가입 정책" 질문에 최상위로 hit)을 줄인다. 도메인 태깅은
    심볼이 아니라 파일(모듈) 단위이므로, 각 심볼을 자신의 모듈 심볼에 조인해
    그 파일의 domain을 가져온다(`app/main.py GET /scans/{id}/graph`와 같은
    module-join 패턴 재사용).

    `domain`이 이 스캔에 실제로 존재하지 않는 값이면(라이브 관측: 로컬 7B가
    "codebase_structure의 결과 도메인" 같은 플레이스홀더 문자열을 그대로 넣은
    사례) 조용히 무시하고 스코프 없이 검색한다 — 존재하지 않는 도메인으로
    필터링하면 결과가 통째로 0건이 되어, 스코핑이 검색을 도와주기는커녕
    망치는 역효과가 나기 때문(fail-open).
    """
    model = f"ollama/{settings().ollama_embed_model}"
    try:
        qvec = to_vector_literal(embed_text(q))
    except Exception as exc:
        raise HTTPException(503, f"embedding model unreachable: {exc}") from exc

    if domain:
        cur.execute(
            "SELECT 1 FROM symbol_semantic WHERE scan_id=%s AND domain=%s LIMIT 1",
            (str(scan_id), domain),
        )
        if cur.fetchone() is None:
            domain = None

    _enable_iterative_scan(cur)
    cur.execute(
        """
        SELECT s.symbol_key, s.qualified_name, s.kind, s.path, s.start_line, s.end_line, sem.summary,
               1 - (se.embedding <=> %s::vector) AS similarity
          FROM symbol_embedding se
          JOIN symbol s ON s.symbol_key = se.symbol_key AND s.scan_id = se.scan_id
          LEFT JOIN symbol_semantic sem
                 ON sem.scan_id = se.scan_id AND sem.symbol_key = se.symbol_key
          LEFT JOIN symbol mod_s
                 ON mod_s.scan_id = se.scan_id AND mod_s.path = s.path AND mod_s.kind = 'module'
          LEFT JOIN symbol_semantic mod_sem
                 ON mod_sem.scan_id = se.scan_id AND mod_sem.symbol_key = mod_s.symbol_key
         WHERE se.scan_id = %s AND se.model = %s
           AND (%s::text IS NULL OR mod_sem.domain = %s)
         ORDER BY se.embedding <=> %s::vector
         LIMIT %s
        """,
        (qvec, str(scan_id), model, domain, domain, qvec, limit),
    )
    # relaxed_order는 순서를 느슨하게 보장하므로 유사도로 다시 정렬한다.
    rows = sorted(cur.fetchall(), key=lambda r: r["similarity"], reverse=True)
    if min_similarity is not None:
        rows = [r for r in rows if r["similarity"] >= min_similarity]
    return rows


# 검색어에서 뺄 일반어 — 어느 심볼 요약에나 걸려서 키워드 점수를 흐린다.
_STOP_TERMS = {
    "함수", "코드", "로직", "관련", "처리", "어디", "어디서", "어디에", "있어", "있나", "하는", "방법",
    "구현", "기능", "파일", "어떻게", "뭐야", "무엇", "찾아", "알려줘", "그리고", "부분", "곳",
    "the", "and", "for", "code", "function", "logic",
}
_KO_PARTICLE_RE = re.compile(r"(에서|으로|하는|되는|하고|이나|에게|까지|부터|은|는|이|가|을|를|의|에|로|와|과|도|만|나)$")


def _query_terms(q: str) -> list[str]:
    """키워드 매칭용 검색어 — 영문 식별자 조각과 조사를 뗀 한국어 낱말."""
    out = []
    for t in re.findall(r"[A-Za-z0-9_$]+|[가-힣]+", q or ""):
        if re.match(r"[가-힣]", t) and len(t) >= 3:
            t = _KO_PARTICLE_RE.sub("", t)
        t = t.lower()
        if len(t) >= 2 and t not in _STOP_TERMS:
            out.append(t)
    return list(dict.fromkeys(out))[:10]


def hybrid_search_symbols(cur, scan_id: UUID, q: str, limit: int, domain: str | None = None) -> list[dict]:
    """채팅용 검색 — 임베딩 유사도 + 이름/경로/요약 키워드 매칭을 섞어 순위를 매긴다.

    임베딩 모델(mxbai-embed-large)은 영어 전용인데 symbol 요약과 질문은 한국어라,
    실측 유사도가 관련·무관 가릴 것 없이 0.75~1.0에 몰린다("날씨 API" 같은 무관한
    질의도 0.8). 그래서 임베딩만으로는 "금지어 필터링"에 Projects/App이 1위로 올라왔다.
    키워드 점수(질의 낱말이 함수명·경로에 있으면 1, 요약에만 있으면 0.6)를 섞으면
    textMatcher.matchBannedWord처럼 이름·요약에 낱말이 실제로 있는 심볼이 올라온다.
    유사도는 후보 집합 안에서 0~1로 정규화해서(절대값이 의미 없으므로) 더한다.

    REST `/scans/{id}/search`(순수 의미 검색 화면)는 계속 `search_symbols`를 쓴다.
    """
    vector_rows = search_symbols(cur, scan_id, q, limit=40, domain=domain)
    terms = _query_terms(q)
    lexical_rows: list[dict] = []
    if terms:
        patterns = [f"%{_like_escape(t)}%" for t in terms]
        qvec = to_vector_literal(embed_text(q))
        model = f"ollama/{settings().ollama_embed_model}"
        cur.execute(
            """
            SELECT s.symbol_key, s.qualified_name, s.kind, s.path, s.start_line, s.end_line, sem.summary,
                   1 - (se.embedding <=> %s::vector) AS similarity
              FROM symbol s
              LEFT JOIN symbol_semantic sem ON sem.scan_id = s.scan_id AND sem.symbol_key = s.symbol_key
              LEFT JOIN symbol_embedding se
                     ON se.scan_id = s.scan_id AND se.symbol_key = s.symbol_key AND se.model = %s
              LEFT JOIN symbol mod_s
                     ON mod_s.scan_id = s.scan_id AND mod_s.path = s.path AND mod_s.kind = 'module'
              LEFT JOIN symbol_semantic mod_sem
                     ON mod_sem.scan_id = s.scan_id AND mod_sem.symbol_key = mod_s.symbol_key
             WHERE s.scan_id = %s
               AND (%s::text IS NULL OR mod_sem.domain = %s)
               AND (s.qualified_name ILIKE ANY(%s) OR s.path ILIKE ANY(%s) OR sem.summary ILIKE ANY(%s))
             LIMIT 80
            """,
            (qvec, model, str(scan_id), domain, domain, patterns, patterns, patterns),
        )
        lexical_rows = cur.fetchall()

    by_key: dict[str, dict] = {}
    for r in [*vector_rows, *lexical_rows]:
        by_key.setdefault(r["symbol_key"], dict(r))
    if not by_key:
        return []
    sims = [r["similarity"] for r in by_key.values() if r.get("similarity") is not None]
    lo, hi = (min(sims), max(sims)) if sims else (0.0, 1.0)

    def lexical(r: dict) -> float:
        if not terms:
            return 0.0
        name = f"{r.get('qualified_name') or ''} {r.get('path') or ''}".lower()
        summary = (r.get("summary") or "").lower()
        return sum(1.0 if t in name else 0.6 if t in summary else 0.0 for t in terms) / len(terms)

    for r in by_key.values():
        sim = r.get("similarity")
        sim_norm = (sim - lo) / (hi - lo) if sim is not None and hi > lo else 0.0
        r["score"] = round(0.45 * sim_norm + 0.55 * lexical(r), 3)
    return sorted(by_key.values(), key=lambda r: r["score"], reverse=True)[:limit]


def domain_overview(cur, scan_id: UUID) -> list[dict]:
    """의미 태깅(symbol.annotate)이 만든 도메인/역할 개요 + 파일 수. 아직 안 돌았으면 빈 리스트.

    `search_symbols`/`call_graph_paths`는 질문에 맞는 이름/키워드를 이미 안다는
    전제로 동작하는 "핀포인트" 조회다 — 그 추측이 틀리면(엉뚱한 도메인 매칭)
    되돌릴 방법이 없다. 이 함수는 반대로 "이 코드베이스에 애초에 어떤
    도메인/모듈이 있나"를 먼저 보여줘서, 오케스트레이터(app/orchestrator.py
    codebase_structure 도구)가 핀포인트 검색이 빗나갔을 때 방향을 다시 잡는
    데 쓴다. `app/main.py GET /scans/{id}/domains`와 같은 쿼리 — 라우트가
    이 함수를 호출한다(단일 소스).
    """
    cur.execute(
        """
        SELECT domain, role, count(*) AS file_count
          FROM symbol_semantic
         WHERE scan_id=%s AND domain IS NOT NULL
         GROUP BY domain, role
         ORDER BY file_count DESC
        """,
        (str(scan_id),),
    )
    return cur.fetchall()


# ASCII 식별자만 — `\w`는 한글도 잡아서 "auth.service의"가 한 토큰이 되면 파일 힌트가 깨진다.
_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*(?:[./#][A-Za-z_$][A-Za-z0-9_$]*)*")


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resolve_symbols(cur, scan_id: UUID, query: str, limit: int = 3) -> list[dict]:
    """함수/메서드 이름(또는 `auth.service.login`·`UserService#login` 같은 경로 힌트 포함
    이름)을 symbol 행으로 해석한다. 이름으로 못 찾으면 [].

    질문 문장이 통째로 들어와도(예: "auth.service의 login 함수") 식별자 토큰만 뽑아
    뒤에서부터(보통 함수명이 뒤에 온다) 시도한다. 토큰의 마지막 세그먼트가 이름이고,
    앞부분과 다른 토큰들은 파일 경로 힌트다 — 같은 이름(login)이 controller/service/api에
    다 있을 때 힌트에 맞는 파일만 남긴다(맞는 게 없으면 전부). LIKE 와일드카드는 이스케이프.
    """
    tokens = _IDENT_RE.findall(query or "")
    for pos in range(len(tokens) - 1, -1, -1):
        parts = re.split(r"[./#]", tokens[pos])
        name = parts[-1]
        if len(name) < 3:
            continue
        hints = ["/".join(parts[:-1])] + [t.replace(".", "/") for i, t in enumerate(tokens) if i != pos]
        hints = [h.lower() for h in hints if len(h) >= 3]
        cur.execute(
            r"""
            SELECT symbol_key, path, qualified_name, kind, start_line, end_line
              FROM symbol
             WHERE scan_id=%s AND kind <> 'module'
               AND (qualified_name = %s OR qualified_name ILIKE %s ESCAPE '\')
             ORDER BY (qualified_name = %s) DESC, length(qualified_name), path
             LIMIT 20
            """,
            (str(scan_id), name, f"%.{_like_escape(name)}", name),
        )
        rows = cur.fetchall()
        if not rows:
            continue
        if hints:
            hinted = [r for r in rows if any(h in r["path"].lower().replace(".", "/") for h in hints)]
            rows = hinted or rows
        return rows[:limit]
    return []


def _callees_within(cur, scan_id: UUID, symbol_key: str, depth: int) -> list[dict]:
    """symbol_key가 depth 단계 안에서 호출하는 심볼들(심볼당 최단 경로 하나).

    `callers_within`(sql/001_init.sql)의 반대 방향이다. GraphStore 계약은 callers만
    정의하므로 정본(symbol_edge)에서 바로 순회한다 — Neo4j 동등성 요구가 없는 채팅 전용.
    """
    cur.execute(
        """
        WITH RECURSIVE down AS (
            SELECT e.dst_symbol_key AS symbol_key, 1 AS hops,
                   ARRAY[e.src_symbol_key, e.dst_symbol_key] AS path, e.call_line
              FROM symbol_edge e
             WHERE e.scan_id = %s AND e.resolved AND e.edge_type = 'calls' AND e.src_symbol_key = %s
            UNION ALL
            SELECT e.dst_symbol_key, d.hops + 1, d.path || e.dst_symbol_key, d.call_line
              FROM symbol_edge e JOIN down d ON e.src_symbol_key = d.symbol_key
             WHERE e.scan_id = %s AND e.resolved AND e.edge_type = 'calls'
               AND d.hops < %s AND NOT e.dst_symbol_key = ANY(d.path)
        )
        SELECT DISTINCT ON (symbol_key) symbol_key, hops, path, call_line
          FROM down ORDER BY symbol_key, hops, call_line
        """,
        (str(scan_id), symbol_key, str(scan_id), depth),
    )
    return sorted(cur.fetchall(), key=lambda r: (r["hops"], r["call_line"] or 0))


def call_graph_paths(cur, scan_id: UUID, symbol_query: str, depth: int = 3, candidates: int = 3,
                     direction: str = "both", max_items: int = 30) -> list[dict]:
    """`symbol_query`(함수/메서드 이름 또는 자연어 설명)를 심볼로 해석한 뒤 호출 관계를 순회한다.

    - callers: 그 심볼을 depth 단계 안에서 호출하는 경로(`GraphStore.callers_within`,
      `/scans/{id}/callers`와 동일 — path가 그대로 Risk Evidence다).
    - callees: 그 심볼이 내부에서 호출하는 함수(최대 2단계, call_line 순).
    - 결과 맨 앞에 해석된 대상 심볼 자체(relation=target)를 넣는다 — "어느 login을
      말하는지"와 그 위치(path/줄 범위)가 근거에 남아야 답변이 정확히 인용할 수 있다.

    이름 매칭(`resolve_symbols`)을 먼저 시도하고, 실패할 때만 의미 검색으로 후보를 찾는다.
    모든 항목에 path/qualified_name/start_line/end_line을 붙인다 — 예전엔 symbol_key만
    돌려줘서 LLM이 파일 위치를 추측해야 했다.
    """
    targets = resolve_symbols(cur, scan_id, symbol_query, limit=candidates)
    if not targets:
        targets = hybrid_search_symbols(cur, scan_id, symbol_query, limit=candidates)
    if not targets:
        return []

    want_callers = direction in ("callers", "both")
    want_callees = direction in ("callees", "both")
    raw: list[tuple[str, dict, dict]] = []  # (relation, target_row, edge_row)

    if want_callers:
        store = make_graph_store()
        if not store.health():
            # Neo4j가 설정돼 있지만 죽어 있으면 정본(Postgres)으로 — 두 구현은 같은 결과를
            # 내도록 보장된다(tests/test_graph_equivalence.py). 예전엔 빈 결과를 돌려줘서
            # "호출하는 곳 없음"으로 잘못 답했다.
            from app.graph.postgres import PostgresGraphStore

            close = getattr(store, "close", None)
            if close:
                close()
            store = PostgresGraphStore()
        try:
            if store.health():
                for t in targets:
                    try:
                        for p in store.callers_within(str(scan_id), t["symbol_key"], depth):
                            raw.append(("caller", t, p.as_dict()))
                    except ValueError:
                        continue
        finally:
            close = getattr(store, "close", None)
            if close:
                close()
    if want_callees:
        for t in targets:
            for r in _callees_within(cur, scan_id, t["symbol_key"], min(depth, 2)):
                raw.append(("callee", t, r))

    keys = {k for _, _, r in raw for k in r["path"]} | {t["symbol_key"] for t in targets}
    cur.execute(
        "SELECT symbol_key, path, qualified_name, start_line, end_line FROM symbol "
        "WHERE scan_id=%s AND symbol_key = ANY(%s)",
        (str(scan_id), list(keys)),
    )
    meta = {r["symbol_key"]: r for r in cur.fetchall()}

    def label(key: str) -> str:
        m = meta.get(key)
        return f"{m['qualified_name']} ({m['path']})" if m else key

    results: list[dict] = []
    for t in targets:
        m = meta.get(t["symbol_key"], t)
        results.append({"relation": "target", "symbol_key": t["symbol_key"],
                        "qualified_name": m.get("qualified_name"), "path": m.get("path"),
                        "start_line": m.get("start_line"), "end_line": m.get("end_line")})
    for relation, t, r in raw:
        m = meta.get(r["symbol_key"], {})
        item = {"relation": relation, "target": label(t["symbol_key"]), "hops": r["hops"],
                "symbol_key": r["symbol_key"], "qualified_name": m.get("qualified_name"),
                "path": m.get("path"), "start_line": m.get("start_line"), "end_line": m.get("end_line"),
                "chain": [label(k) for k in r["path"]]}
        if relation == "callee" and r.get("call_line"):
            item["call_line"] = r["call_line"]
        results.append(item)
    return results[:max_items]


def run_dataflow(cur, scan_id: UUID, mode: str, limit: int, keywords: list[str] | None = None) -> dict:
    """PDG(레이어 1~3) + 절차간 연결(레이어 4) 기반 source→sink 경로 탐색.

    레이어 4는 인자→파라미터 오염 전파까지만 다룬다 — 콜백의 리턴값이
    호출부 변수로 되돌아오는 흐름(return backflow)은 아직 범위 밖이다.
    `business` 모드는 정규식 source/sink 대신 symbol_semantic.domain 경계를
    쓴다 — 도메인 그래프가 이미 보여주는 "도메인 간 호출 수" 집계 뒤에 숨은
    개별 사례(어느 함수가 어느 인자를 들고 넘는지)를 보여주는 역할이다.

    `keywords`는 오케스트레이터(`app/orchestrator.py`)가 질문의 구체적
    관심사로 결과를 좁힐 때만 넘긴다 — `/scans/{id}/dataflow` 라우트(그래프
    화면의 "위험 탐지" 버튼)는 이 인자를 안 넘기므로 동작이 그대로다.
    symbol_key 자체에 이미 path/qualified_name이 들어있어서(예:
    `javascript:<repo>:ai-moderation/pii/x.js#checkPii(any)`) enrichment
    전에 걸러도 키워드가 파일 경로/함수명에 매칭된다.
    """
    cur.execute(
        "SELECT symbol_key, line, target_text, arg_vars, arg_texts, arg_lines FROM pdg_call WHERE scan_id=%s",
        (str(scan_id),),
    )
    calls = cur.fetchall()
    # pdg_call(symbol_key, line) 을 이미 resolve 된 call graph(symbol_edge)와
    # 맞춰서 "이 호출 지점이 실제로 어느 함수로 이어지는가"를 얻는다 —
    # 레이어 4가 새로 만드는 정보가 아니라 WBS 1.3 산출물을 그대로 조인한 것.
    # 두 모드(security/business) 다 이 조인이 필요하다.
    cur.execute(
        """
        SELECT DISTINCT pc.symbol_key AS caller_symbol_key, pc.line AS call_line,
               e.dst_symbol_key AS callee_symbol_key
          FROM pdg_call pc
          JOIN symbol_edge e
            ON e.scan_id = %s AND e.src_symbol_key = pc.symbol_key AND e.call_line = pc.line
           AND e.edge_type = 'calls' AND e.resolved
         WHERE pc.scan_id = %s
        """,
        (str(scan_id), str(scan_id)),
    )
    call_links = cur.fetchall()

    if mode == "business":
        cur.execute(
            """
            SELECT s.symbol_key, sem.domain
              FROM symbol s
              JOIN symbol mod_s
                ON mod_s.scan_id = s.scan_id AND mod_s.path = s.path AND mod_s.kind = 'module'
              JOIN symbol_semantic sem
                ON sem.scan_id = s.scan_id AND sem.symbol_key = mod_s.symbol_key
             WHERE s.scan_id = %s AND sem.domain IS NOT NULL
            """,
            (str(scan_id),),
        )
        file_domain_by_symbol = {r["symbol_key"]: r["domain"] for r in cur.fetchall()}

        findings = find_domain_crossings(call_links, calls, file_domain_by_symbol)
        findings = _filter_by_keywords(findings, keywords)[:limit]
        if findings:
            keys = list({f[k] for f in findings for k in ("caller_symbol_key", "callee_symbol_key")})
            cur.execute(
                "SELECT symbol_key, path, qualified_name FROM symbol WHERE scan_id=%s AND symbol_key = ANY(%s)",
                (str(scan_id), keys),
            )
            meta = {r["symbol_key"]: r for r in cur.fetchall()}
            for f in findings:
                caller_m = meta.get(f["caller_symbol_key"], {})
                callee_m = meta.get(f["callee_symbol_key"], {})
                f["caller_path"] = caller_m.get("path")
                f["caller_qualified_name"] = caller_m.get("qualified_name")
                f["callee_path"] = callee_m.get("path")
                f["callee_qualified_name"] = callee_m.get("qualified_name")
        return {"mode": "business", "findings": findings}

    cur.execute(
        """
        SELECT symbol_key, var_name AS var, def_line, def_block_id AS def_block,
               use_line, evidence_kind, def_text
          FROM pdg_data_edge WHERE scan_id=%s
        """,
        (str(scan_id),),
    )
    edges = cur.fetchall()
    cur.execute("SELECT symbol_key, param_names FROM pdg_function WHERE scan_id=%s", (str(scan_id),))
    functions = {r["symbol_key"]: r["param_names"] for r in cur.fetchall()}

    findings = find_security_paths_interprocedural(edges, calls, call_links, functions)
    findings = _filter_by_keywords(findings, keywords)[:limit]
    for f in findings:
        # path 는 (symbol_key, var, line) 튜플 리스트로 나온다 — JSON 응답에
        # 실을 수 있게 dict 로, 화면에서 "함수 호출 경로"를 그릴 수 있게
        # symbol_key마다 path/qualified_name 을 붙인다.
        f["path"] = [{"symbol_key": s, "var": v, "line": ln} for s, v, ln in f["path"]]
    if findings:
        keys = list({f[k] for f in findings for k in ("source_symbol_key", "sink_symbol_key")}
                    | {hop["symbol_key"] for f in findings for hop in f["path"]})
        cur.execute(
            "SELECT symbol_key, path, qualified_name FROM symbol WHERE scan_id=%s AND symbol_key = ANY(%s)",
            (str(scan_id), keys),
        )
        meta = {r["symbol_key"]: r for r in cur.fetchall()}
        for f in findings:
            src_m = meta.get(f["source_symbol_key"], {})
            sink_m = meta.get(f["sink_symbol_key"], {})
            f["source_path"] = src_m.get("path")
            f["source_qualified_name"] = src_m.get("qualified_name")
            f["sink_path"] = sink_m.get("path")
            f["sink_qualified_name"] = sink_m.get("qualified_name")
            for hop in f["path"]:
                m = meta.get(hop["symbol_key"], {})
                hop["path"] = m.get("path")
                hop["qualified_name"] = m.get("qualified_name")

    return {"mode": "security", "findings": findings}


def ensure_security_findings(cur, scan_id: UUID) -> None:
    """security_finding에 이 scan 분석 결과가 없으면 계산해서 채운다(있으면 아무것도 안 함).

    `/scans/{id}/security/findings`와 릴리즈 드릴다운(`app/releases.py`)
    양쪽이 이 함수를 쓴다 — 둘 다 "최초 조회 시 계산+캐싱" 이 필요해서.
    `scan.stats`에 남기는 security_analyzed 플래그로, findings가 0건인
    "정상" 스캔도 매번 다시 계산하지 않는다.
    """
    cur.execute("SELECT repository_id, stats FROM scan WHERE id=%s", (str(scan_id),))
    scan_row = cur.fetchone()
    if scan_row is None or (scan_row["stats"] or {}).get("security_analyzed"):
        return
    repository_id = scan_row["repository_id"]

    for mode in ("security", "business"):
        result = run_dataflow(cur, scan_id, mode, limit=500)
        for f in result["findings"]:
            category = f["category"]
            severity = severity_for(category)
            if mode == "security":
                sink_symbol_key, sink_line = f["sink_symbol_key"], f["sink_line"]
                source_symbol_key, source_var = f.get("source_symbol_key"), f.get("source_var") or ""
                path, evidence_kinds = f.get("path") or [], f.get("evidence_kinds") or []
            else:
                sink_symbol_key, sink_line = f["callee_symbol_key"], f["call_line"]
                source_symbol_key, source_var = f["caller_symbol_key"], ""
                path, evidence_kinds = [], []
            # Security History — 스캔 간 동일 위험(Risk Identity) 식별용 결정적 지문.
            # 실패해도 finding 자체는 저장한다(security_snapshot.backfill_fingerprints 가 나중에 채움).
            try:
                fingerprint = compute_fingerprint({
                    "kind": mode, "category": category, "sink_symbol_key": sink_symbol_key,
                    "source_var": source_var, "path": path,
                })
            except Exception:
                fingerprint = None
            cur.execute(
                """
                INSERT INTO security_finding (scan_id, repository_id, kind, category, severity,
                                              sink_symbol_key, sink_line, source_symbol_key,
                                              source_var, path, evidence_kinds, fingerprint)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (scan_id, kind, category, sink_symbol_key, sink_line, source_var)
                DO NOTHING
                """,
                (str(scan_id), str(repository_id), mode, category, severity,
                 sink_symbol_key, sink_line, source_symbol_key, source_var,
                 json.dumps(path), evidence_kinds, fingerprint),
            )
    cur.execute(
        "UPDATE scan SET stats = stats || '{\"security_analyzed\": true}'::jsonb WHERE id=%s",
        (str(scan_id),),
    )
