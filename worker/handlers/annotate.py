"""symbol.annotate — 로컬 LLM(Ollama)으로 구조 그래프 위에 의미 계층을 얹는다.

WBS 2.2("LLM 기반 Business Logic 분석") 조기 프로토타입. 파일(module) 단위로
배치해서 호출한다 — 심볼 하나하나 부르면 느리고 비싸다(95개 파일 기준
실측 3~6분, 심볼 440개를 개별로 부르면 그보다 훨씬 오래 걸린다).

프롬프트에는 이미 확보된 구조 정보(symbol_edge의 import/calls)를 먼저
준다 — 구조 없이 의미만 묻지 않는다 (블로그가 지적한, 그래프만 봐서는
이해가 안 된다는 문제를 구조 컨텍스트를 줘서 완화).

scan.parse/graph.project와 분리된 별도 잡이다 — Ollama가 꺼져 있거나
느려도 스캔 자체(구조 추출)는 이미 성공한 뒤라 이 잡의 실패가 스캔을
되돌리지 않는다.
"""

from __future__ import annotations

import json
import logging

from app import ai_providers, incremental, llm_schemas, queue
from app.config import settings
from app.db import tx

log = logging.getLogger("worker.annotate")

_MEMBER_KINDS = ("function", "method", "class", "interface")

_PROMPT_TEMPLATE = """다음은 코드 파일 하나의 구조 정보다. 이 파일의 역할(role), 소속 도메인(domain), 한 줄 요약(summary)을 JSON으로 답하라.

파일 경로: {path}

이 파일이 정의한 함수/메서드/클래스들:
{symbols_block}

이 파일이 의존하는 모듈(import):
{imports_block}

이 파일 내부 호출 관계(일부):
{calls_block}

이 스캔에서 이미 쓰인 도메인 태그:
{known_domains_block}

domain 작성 규칙:
- 반드시 한국어 업무 도메인/기능 명사구로 쓴다(예: "주문 관리", "결제 처리", "회원 인증", "재고 관리"). 영어 단어를 쓰지 않는다.
- 위 "이미 쓰인 도메인 태그" 중 의미가 같거나 비슷한 게 있으면 새로 만들지 말고 그 표현을 그대로 재사용한다.

아래 JSON 스키마로만 답하라 (다른 설명 금지, 코드블록 금지):
{{"role": "controller|service|repository|util|test|config|other 중 하나", "domain": "한국어 업무 도메인 명사구", "summary": "한국어 1문장", "symbols": [{{"name": "함수명", "summary": "한국어 5~10단어 요약"}}, ...]}}
"""


def handle(payload: dict) -> None:
    scan_id = payload["scan_id"]

    with tx() as cur:
        # 여러 project에 attach된 repo일 수 있으므로 그중 하나의 CI/CD용 모델
        # 설정을 쓴다(project_repository 첫 매칭 — app/ai_providers.py의
        # 자리 마련 설계와 동일한 실용적 단순화). 값이 비어있으면 전역 기본값.
        cur.execute(
            """
            SELECT p.id AS project_id, p.ci_model_provider, p.ci_model_name
              FROM scan s
              JOIN project_repository pr ON pr.repository_id = s.repository_id
              JOIN project p ON p.id = pr.project_id
             WHERE s.id=%s LIMIT 1
            """,
            (scan_id,),
        )
        proj = cur.fetchone()
        project_id = str(proj["project_id"]) if proj else None
        # provider 는 라우팅에 안 쓰인다(게이트웨이가 model 별칭으로 결정) — 로깅용.
        provider = (proj or {}).get("ci_model_provider") or "gateway"
        model_name = (proj or {}).get("ci_model_name") or settings().default_chat_model

        cur.execute(
            """
            SELECT symbol_key, kind, path, qualified_name, param_types
              FROM symbol WHERE scan_id=%s
             ORDER BY path, start_line
            """,
            (scan_id,),
        )
        rows = cur.fetchall()

        cur.execute(
            """
            SELECT s1.path AS src_path, s1.qualified_name AS src_name,
                   s2.qualified_name AS dst_name, e.edge_type
              FROM symbol_edge e
              JOIN symbol s1 ON s1.symbol_key = e.src_symbol_key AND s1.scan_id = e.scan_id
              LEFT JOIN symbol s2 ON s2.symbol_key = e.dst_symbol_key AND s2.scan_id = e.scan_id
             WHERE e.scan_id=%s AND e.resolved
            """,
            (scan_id,),
        )
        edge_rows = cur.fetchall()

    by_path: dict[str, dict] = {}
    for r in rows:
        group = by_path.setdefault(r["path"], {"module": None, "members": []})
        if r["kind"] == "module":
            group["module"] = r
        elif r["kind"] in _MEMBER_KINDS:
            group["members"].append(r)

    edges_by_path: dict[str, list] = {}
    for e in edge_rows:
        edges_by_path.setdefault(e["src_path"], []).append(e)

    # 파일마다 독립적으로 LLM을 부르지만, 같은 스캔 안에서는 지금까지 쓰인
    # 도메인 태그를 프롬프트에 계속 실어줘서 "auth"/"authentication"/"인증"처럼
    # 같은 의미가 매번 다른 이름으로 갈라지지 않고 뒤로 갈수록 수렴하게 한다.
    known_domains: list[str] = []

    # --- 증분: parent 에서 안 바뀐 파일의 의미 태깅을 그대로 가져오고, 변경 파일만 LLM ---
    parent_scan_id, changed_paths = _incremental_carry_forward(scan_id)
    if parent_scan_id is not None:
        changed_set = set(changed_paths or [])
        by_path = {p: g for p, g in by_path.items() if p in changed_set}
        with tx() as cur:
            cur.execute(
                "SELECT DISTINCT domain FROM symbol_semantic WHERE scan_id=%s AND domain IS NOT NULL",
                (scan_id,),
            )
            known_domains = [r["domain"] for r in cur.fetchall()]
        log.info("scan %s: 증분 태깅 — 변경 파일 %d개만 LLM, 나머지는 parent(%s)에서 복사",
                 scan_id, len(by_path), parent_scan_id)

    annotated = 0
    for path, group in by_path.items():
        module = group["module"]
        members = group["members"]
        if module is None or not members:
            continue  # 함수/클래스가 하나도 없는 파일(설정 파일 등)은 태깅할 의미가 적음

        try:
            result = _annotate_file(path, members, edges_by_path.get(path, []), known_domains,
                                     provider=provider, model=model_name,
                                     project_id=project_id, scan_id=scan_id)
        except Exception:
            log.exception("scan %s: %s 의미 태깅 실패 — 건너뜀", scan_id, path)
            continue

        domain = result.get("domain")
        if domain and domain not in known_domains:
            known_domains.append(domain)

        with tx() as cur:
            _store(cur, scan_id, module, members, result, provider=provider, model_name=model_name)
        annotated += 1

    log.info("scan %s: 파일 %d개 의미 태깅 완료", scan_id, annotated)

    # 임베딩은 이 잡이 만든 summary 가 있어야 의미가 있으므로, scan.parse가
    # 아니라 annotate 완료 시점에 체이닝한다(annotate가 Ollama 태깅 없이도
    # 스캔 자체를 막지 않는 것과 같은 이유로 독립된 잡으로 분리돼 있다).
    with tx() as cur:
        queue.enqueue(cur, "symbol.embed", {"scan_id": scan_id},
                      priority=160, dedupe_key=f"embed:{scan_id}")
        # Risk Intelligence(WBS 2.3)도 이 잡이 만든 role/domain/summary 를 근거로
        # 쓰므로 여기서 체이닝한다. 임베딩과 달리 서로 의존이 없어 순서는 무관.
        queue.enqueue(cur, "risk.build", {"scan_id": scan_id},
                      priority=170, dedupe_key=f"risk:{scan_id}")


def _incremental_carry_forward(scan_id: str) -> tuple[str | None, list[str] | None]:
    """증분 스캔이면 parent 의 symbol_semantic 중 안 바뀐 파일 것을 이 스캔으로 복사.

    반환: (parent_scan_id, changed_paths) 또는 (None, None) = 전체 태깅.
    """
    with tx() as cur:
        parent_scan_id, changed_paths = incremental.read_incremental_context(cur, scan_id)
        if parent_scan_id is None:
            return None, None
        cur.execute(
            """
            INSERT INTO symbol_semantic (scan_id, symbol_key, role, domain, summary, confidence,
                                         model, generated_at)
            SELECT %s, ss.symbol_key, ss.role, ss.domain, ss.summary, ss.confidence,
                   ss.model, ss.generated_at
              FROM symbol_semantic ss
              JOIN symbol s ON s.scan_id = %s AND s.symbol_key = ss.symbol_key
             WHERE ss.scan_id = %s AND s.path <> ALL(%s)
            ON CONFLICT (scan_id, symbol_key) DO NOTHING
            """,
            (scan_id, scan_id, parent_scan_id, list(changed_paths or [])),
        )
    return parent_scan_id, changed_paths


def _annotate_file(path: str, members: list[dict], edges: list[dict], known_domains: list[str],
                    *, provider: str, model: str, project_id: str | None, scan_id: str) -> dict:
    symbols_block = "\n".join(
        f"- {m['qualified_name']}({','.join(m['param_types'] or [])})" for m in members
    ) or "(없음)"

    imports = sorted({e["dst_name"] for e in edges if e["edge_type"] == "imports" and e["dst_name"]})
    imports_block = "\n".join(f"- {i}" for i in imports) or "(없음)"

    calls_by_src: dict[str, list[str]] = {}
    for e in edges:
        if e["edge_type"] == "calls" and e["dst_name"]:
            calls_by_src.setdefault(e["src_name"], []).append(e["dst_name"])
    calls_block = "\n".join(
        f"- {src} → {', '.join(dsts[:8])}" for src, dsts in list(calls_by_src.items())[:10]
    ) or "(없음)"

    known_domains_block = ", ".join(known_domains) if known_domains else "(아직 없음)"

    prompt = _PROMPT_TEMPLATE.format(
        path=path, symbols_block=symbols_block, imports_block=imports_block, calls_block=calls_block,
        known_domains_block=known_domains_block,
    )

    raw = ai_providers.generate(provider, model, prompt, response_schema=llm_schemas.AnnotateResult,
                                 timeout=120, project_id=project_id, purpose="ci_annotate", scan_id=scan_id)
    return json.loads(raw)


def _store(cur, scan_id: str, module: dict, members: list[dict], result: dict,
           *, provider: str, model_name: str) -> None:
    model = f"{provider}/{model_name}"

    cur.execute(
        """
        INSERT INTO symbol_semantic (scan_id, symbol_key, role, domain, summary, model)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (scan_id, symbol_key) DO UPDATE
            SET role = EXCLUDED.role, domain = EXCLUDED.domain,
                summary = EXCLUDED.summary, model = EXCLUDED.model, generated_at = now()
        """,
        (scan_id, module["symbol_key"], result.get("role"), result.get("domain"),
         result.get("summary"), model),
    )

    # LLM이 qualified_name 그대로 쓸 수도, 마지막 단순 이름만 쓸 수도 있어 둘 다로 찾는다.
    by_name: dict[str, dict] = {}
    for m in members:
        by_name[m["qualified_name"]] = m
        by_name[m["qualified_name"].rsplit(".", 1)[-1]] = m

    for entry in (result.get("symbols") or []):
        name = entry.get("name")
        summary = entry.get("summary")
        target = by_name.get(name)
        if target is None:
            continue
        cur.execute(
            """
            INSERT INTO symbol_semantic (scan_id, symbol_key, summary, model)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (scan_id, symbol_key) DO UPDATE
                SET summary = EXCLUDED.summary, model = EXCLUDED.model, generated_at = now()
            """,
            (scan_id, target["symbol_key"], summary, model),
        )
