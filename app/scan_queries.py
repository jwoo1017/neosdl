"""스캔 하나에 대한 시맨틱 검색/Dataflow 조회 로직.

`app/main.py`의 `/scans/{id}/search`, `/scans/{id}/dataflow` 라우트와
`app/orchestrator.py`(LLM이 고른 도구 실행)가 둘 다 이 함수들을 호출한다.
main.py 안에 두면 orchestrator.py가 main을 import해야 해서 순환 임포트가
생기므로 별도 모듈로 뺐다. 접근 권한 확인은 호출자 책임(여기서는 안 함).
"""

from __future__ import annotations

import json
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


def search_symbols(cur, scan_id: UUID, q: str, limit: int, domain: str | None = None) -> list[dict]:
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

    cur.execute(
        """
        SELECT s.symbol_key, s.qualified_name, s.kind, s.path, sem.summary,
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
    return cur.fetchall()


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


def call_graph_paths(cur, scan_id: UUID, symbol_query: str, depth: int = 3, candidates: int = 3) -> list[dict]:
    """`symbol_query`(함수/메서드 이름 또는 자연어 설명)를 심볼로 해석한 뒤,
    그 심볼을 depth 단계 안에서 호출하는 경로를 그래프에서 순회한다.

    이름 매칭을 먼저 시도한다 — 호출 그래프 질문은 보통 질문에 함수명이
    그대로 들어있어서(예: "runPiiForPost를 누가 호출해?") ILIKE 가 임베딩
    검색보다 정확하다. 매칭이 없을 때만 `search_symbols`의 의미 검색으로
    후보를 좁힌다. `GraphStore.callers_within`이 반환하는 path 가 그대로
    Risk Evidence 다([[app.graph.CallerPath]], `/scans/{id}/callers`와 동일).
    """
    cur.execute(
        "SELECT symbol_key FROM symbol WHERE scan_id=%s AND qualified_name ILIKE %s LIMIT %s",
        (str(scan_id), f"%{symbol_query}%", candidates),
    )
    rows = cur.fetchall()
    if rows:
        target_keys = [r["symbol_key"] for r in rows]
    else:
        target_keys = [r["symbol_key"] for r in search_symbols(cur, scan_id, symbol_query, limit=candidates)]

    store = make_graph_store()
    try:
        if not store.health():
            return []
        results: list[dict] = []
        for symbol_key in target_keys:
            try:
                paths = store.callers_within(str(scan_id), symbol_key, depth)
            except ValueError:
                continue
            results.extend(p.as_dict() for p in paths)
        return results
    finally:
        close = getattr(store, "close", None)
        if close:
            close()


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
