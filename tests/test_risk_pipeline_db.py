"""Risk 파이프라인 DB 통합 — `evidence.py` 의 SQL 을 실제 스키마로 검증
(phase2 plan §24 Step 13). 순수 테스트로는 닿지 않는 부분이다.

실제 소스 스니펫 3개를 파서(ast/edge/cfg/pdg 추출기)로 처리해 DB에 적재하고,
`ensure_security_findings` → `collect_evidence` → `compute_risk_object(allow_llm=False)`
까지 돌려 근거 종류와 Exposure 를 확인한다.

Postgres(sql/001..020 적용) 필요 — 없으면 conftest 의 `db` 픽스처가 세션을 skip 한다.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.risk.evidence import collect_evidence
from app.risk.policy import default_policy
from app.risk.synthesis import compute_risk_object
from worker.handlers.ast_extract import extract_symbols
from worker.handlers.cfg_extract import analyze, build_function_cfgs
from worker.handlers.edge_extract import build_scan_index, extract_edges

pytestmark = pytest.mark.integration

REPO_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

_PRICING = b'''
def calculate_discount(price, tier):
    rate = 0.15 if tier == "gold" else 0.05
    adjusted = price - price * rate
    return round(adjusted, 2)
'''

_SQLI = b'''
def lookup_user():
    name = input()
    query = "SELECT * FROM users WHERE name = '" + name + "'"
    conn.execute(query)
'''

_SECRET = b'''
def build_client():
    api_key = "AKIAIOSFODNN7EXAMPLE"
    return connect(api_key)
'''

_SNIPPETS = {
    "app/pricing.py": (_PRICING, {"role": "service", "domain": "가격 정책",
                                   "summary": "티어별 할인율을 적용해 최종 가격을 계산한다"}),
    "app/userq.py": (_SQLI, {"role": "repository", "domain": "회원 관리",
                              "summary": "이름으로 사용자를 조회한다"}),
    "app/clients.py": (_SECRET, {"role": "util", "domain": "외부 연동",
                                  "summary": "외부 서비스 클라이언트를 만든다"}),
}


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows


@pytest.fixture(scope="module")
def ingested(db):
    """3개 스니펫을 파서로 처리해 scan 하나에 적재한다. 반환: (scan_id, {name: symbol_key})."""
    sid = str(uuid.uuid4())
    sources = {p: s for p, (s, _m) in _SNIPPETS.items()}

    with db() as cur:
        cur.execute(
            """
            INSERT INTO repository (id, provider, remote_id, namespace, name, clone_url)
            VALUES (%s, 'gitlab', 'risk-pipeline', 'evaltest', 'pipe', 'file:///dev/null')
            ON CONFLICT (provider, remote_id) DO NOTHING
            """,
            (str(REPO_ID),),
        )
        cur.execute(
            """
            INSERT INTO scan (id, repository_id, commit_sha, ref, parser_version, status)
            VALUES (%s, %s, %s, 'refs/heads/main', 'test', 'succeeded')
            """,
            (sid, str(REPO_ID), uuid.uuid4().hex + uuid.uuid4().hex[:8]),
        )

        index_rows: list[dict] = []
        want: dict[str, str] = {}
        for path, (src, meta) in _SNIPPETS.items():
            cur.execute(
                "INSERT INTO code_file (scan_id, path, lang, size_bytes, content_hash)"
                " VALUES (%s, %s, 'python', %s, %s) RETURNING id",
                (sid, path, len(src), uuid.uuid4().hex + uuid.uuid4().hex),
            )
            file_id = cur.fetchone()["id"]
            module_key = None
            for s in extract_symbols("python", ".py", path, src):
                cur.execute(
                    """
                    INSERT INTO symbol (scan_id, file_id, repository_id, lang, path, qualified_name,
                                        param_types, kind, start_line, end_line)
                    VALUES (%s, %s, %s, 'python', %s, %s, %s, %s, %s, %s) RETURNING symbol_key
                    """,
                    (sid, file_id, str(REPO_ID), path, s["qualified_name"], s["param_types"],
                     s["kind"], s["start_line"], s["end_line"]),
                )
                key = cur.fetchone()["symbol_key"]
                index_rows.append({"path": path, "lang": "python", "kind": s["kind"],
                                   "qualified_name": s["qualified_name"],
                                   "param_types": s["param_types"], "symbol_key": key})
                if s["kind"] == "module":
                    module_key = key
                else:
                    want[s["qualified_name"]] = key
            # 의미 계층(annotate 대역)
            cur.execute(
                """
                INSERT INTO symbol_semantic (scan_id, symbol_key, role, domain, summary, model)
                VALUES (%s, %s, %s, %s, %s, 'test')
                """,
                (sid, module_key, meta["role"], meta["domain"], meta["summary"]),
            )

        idx = build_scan_index(_FakeCursor(index_rows), sid, sources)

        for path, src in sources.items():
            for e in extract_edges("python", ".py", path, src, idx):
                cur.execute(
                    """
                    INSERT INTO symbol_edge (scan_id, src_symbol_key, dst_symbol_key, dst_hint,
                                             edge_type, resolved, confidence, call_line)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (sid, e["src_symbol_key"], e.get("dst_symbol_key"), e.get("dst_hint"),
                     e["edge_type"], e["resolved"], e.get("confidence", 0.0), e.get("call_line")),
                )

            for symbol_key, builder in build_function_cfgs("python", ".py", path, src, idx).items():
                data_edges, control_edges = analyze(builder)
                cur.execute(
                    "INSERT INTO pdg_function (scan_id, symbol_key, param_names) VALUES (%s, %s, %s)",
                    (sid, symbol_key, builder.params),
                )
                for bid, blk in builder.blocks.items():
                    cur.execute(
                        "INSERT INTO cfg_block (scan_id, symbol_key, block_id, start_line, end_line)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        (sid, symbol_key, bid, blk.start_line, blk.end_line),
                    )
                for src_b, dst_b, label in builder.edges:
                    cur.execute(
                        "INSERT INTO cfg_edge (scan_id, symbol_key, src_block_id, dst_block_id, branch_label)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        (sid, symbol_key, src_b, dst_b, label),
                    )
                for var, def_line, kind, def_block, use_line, def_text in data_edges:
                    cur.execute(
                        """
                        INSERT INTO pdg_data_edge (scan_id, symbol_key, var_name, def_line, def_block_id,
                                                   use_line, evidence_kind, def_text)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (sid, symbol_key, var, def_line, def_block, use_line, kind, def_text),
                    )
                for bid, calls in builder.calls.items():
                    for cline, target_text, arg_vars, arg_texts, arg_lines in calls:
                        cur.execute(
                            """
                            INSERT INTO pdg_call (scan_id, symbol_key, block_id, line, target_text,
                                                  arg_vars, arg_texts, arg_lines)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (sid, symbol_key, bid, cline, target_text, arg_vars, arg_texts, arg_lines),
                        )
                for branch_b, dep_b, label in control_edges:
                    cur.execute(
                        """
                        INSERT INTO pdg_control_edge (scan_id, symbol_key, branch_block_id,
                                                      dependent_block_id, branch_label)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (sid, symbol_key, branch_b, dep_b, label),
                    )

    yield sid, want

    with db() as cur:
        cur.execute("DELETE FROM scan WHERE id = %s", (sid,))


def _reader(path: str) -> str | None:
    entry = _SNIPPETS.get(path)
    return entry[0].decode() if entry else None


def test_sqli_snippet_yields_security_evidence_and_sanitize(db, ingested):
    from app.scan_queries import ensure_security_findings

    scan_id, want = ingested
    with db() as cur:
        ensure_security_findings(cur, scan_id)
        cur.execute("SELECT category FROM security_finding WHERE scan_id = %s", (scan_id,))
        cats = {r["category"] for r in cur.fetchall()}
        assert "sql_injection" in cats

        obj = compute_risk_object(cur, scan_id, want["lookup_user"], policy=default_policy(),
                                  provider="gateway", model="m", allow_llm=False,
                                  source_reader=_reader, persist_evidence=False)
    assert obj.risk.score("security") >= 0.5
    assert obj.exposure.allowed_representation in ("SANITIZED", "MASKED", "PSEUDOCODE",
                                                    "STRUCTURED", "SUMMARY", "INTERNAL_ONLY", "BLOCK")
    assert obj.exposure.external_raw_allowed is False


def test_secret_snippet_blocks(db, ingested):
    scan_id, want = ingested
    with db() as cur:
        obj = compute_risk_object(cur, scan_id, want["build_client"], policy=default_policy(),
                                  provider="gateway", model="m", allow_llm=False,
                                  source_reader=_reader, persist_evidence=False)
    assert obj.risk.score("secret") >= 0.9
    assert obj.exposure.allowed_representation == "BLOCK"
    assert obj.exposure.external_raw_allowed is False


def test_pricing_snippet_has_business_semantic_evidence(db, ingested):
    scan_id, want = ingested
    with db() as cur:
        by_target = collect_evidence(cur, scan_id, default_policy(),
                                     targets=[want["calculate_discount"]], source_reader=_reader)
    kinds = {e.kind for e in by_target[want["calculate_discount"]]}
    assert "business_semantic" in kinds


def test_collect_evidence_runs_against_real_schema(db, ingested):
    """모든 집계 쿼리가 실제 스키마에서 에러 없이 도는지 — 회귀 방지."""
    scan_id, _want = ingested
    with db() as cur:
        by_target = collect_evidence(cur, scan_id, default_policy(), source_reader=_reader)
    assert by_target  # 최소 하나의 function 심볼
    assert all(isinstance(v, list) for v in by_target.values())
