"""증분 스캔 DB 통합 — annotate/embed/risk.build 의 copy-forward 가 실제 스키마에서
안 바뀐 파일 결과를 parent 에서 가져오고, 변경 파일만 재분석하는지 검증
(phase2 plan §12).

Postgres(sql/001..021 적용) 필요 — 없으면 conftest 의 `db` 픽스처가 세션을 skip.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.risk import classify
from app.risk.synthesis import ensure_risk_objects
from worker.handlers import annotate as annotate_mod
from worker.handlers import embed as embed_mod

pytestmark = pytest.mark.integration

REPO_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")

# 2개 파일: pricing.py 는 안 바뀜(parent 재사용), order.py 는 변경(재분석)
_FILES = {
    "app/pricing.py": [
        ("module", "app.pricing", []),
        ("function", "calculate_discount", ["any", "any"]),
    ],
    "app/order.py": [
        ("module", "app.order", []),
        ("function", "place_order", ["any"]),
    ],
}


def _insert_scan(cur, *, parent_scan_id=None, changed_paths=None, risk_analyzed=True):
    sid = str(uuid.uuid4())
    stats = {}
    if risk_analyzed:
        stats["risk_analyzed"] = True
    if changed_paths is not None:
        stats.update({"incremental": True, "changed_paths": changed_paths})
    cur.execute(
        """
        INSERT INTO scan (id, repository_id, commit_sha, ref, parser_version, status,
                          parent_scan_id, stats)
        VALUES (%s, %s, %s, 'refs/heads/main', 'test', 'succeeded', %s, %s::jsonb)
        """,
        (sid, str(REPO_ID), uuid.uuid4().hex + uuid.uuid4().hex[:8], parent_scan_id,
         json.dumps(stats)),
    )
    keys: dict[str, str] = {}
    for path, syms in _FILES.items():
        cur.execute(
            "INSERT INTO code_file (scan_id, path, lang, size_bytes, content_hash)"
            " VALUES (%s, %s, 'python', 1, %s) RETURNING id",
            (sid, path, uuid.uuid4().hex + uuid.uuid4().hex),
        )
        file_id = cur.fetchone()["id"]
        for kind, qname, params in syms:
            cur.execute(
                """
                INSERT INTO symbol (scan_id, file_id, repository_id, lang, path, qualified_name,
                                    param_types, kind, start_line, end_line)
                VALUES (%s, %s, %s, 'python', %s, %s, %s, %s, 1, 5) RETURNING symbol_key
                """,
                (sid, file_id, str(REPO_ID), path, qname, params, kind),
            )
            keys[qname] = cur.fetchone()["symbol_key"]
    return sid, keys


@pytest.fixture()
def parent_and_child(db):
    with db() as cur:
        cur.execute(
            """
            INSERT INTO repository (id, provider, remote_id, namespace, name, clone_url)
            VALUES (%s, 'gitlab', 'incr-pipe', 'evaltest', 'incr', 'file:///dev/null')
            ON CONFLICT (provider, remote_id) DO NOTHING
            """,
            (str(REPO_ID),),
        )
        parent, pkeys = _insert_scan(cur)
        # parent 의 파생 결과 — 두 파일 모두
        for qname, key in pkeys.items():
            is_module = "." in qname
            cur.execute(
                """
                INSERT INTO symbol_semantic (scan_id, symbol_key, role, domain, summary, model)
                VALUES (%s, %s, %s, %s, %s, 'test')
                """,
                (parent, key, "service" if is_module else None,
                 "가격/주문" if is_module else None, f"{qname} 요약"),
            )
            if not is_module:
                cur.execute(
                    """
                    INSERT INTO symbol_embedding (scan_id, symbol_key, model, embedding)
                    VALUES (%s, %s, 'test-embed', %s::vector)
                    """,
                    (parent, key, "[" + ",".join(["0.1"] * 1024) + "]"),
                )
                cur.execute(
                    """
                    INSERT INTO risk_object (scan_id, target_symbol_key, target, risk, classification,
                                             exposure, rule_outcomes, provenance)
                    VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, '[]'::jsonb, '{}'::jsonb)
                    """,
                    (parent, key,
                     json.dumps({"type": "function", "symbol": qname, "file": "x", "lines": [1, 5],
                                 "symbol_key": key}),
                     json.dumps({"business_ip": {"score": 0.8, "confidence": 0.9, "evidence_refs": []}}),
                     json.dumps({"business_role": f"{qname} 로직", "business_ip_class": "business_core_logic",
                                 "classifier_confidence": 0.9, "classifier_evidence": ["x"]}),
                     json.dumps({"allowed_representation": "INTERNAL_ONLY", "external_raw_allowed": False,
                                 "specialized_model_required": False, "reasons": [], "query_adjusted": False})),
                )

        child, ckeys = _insert_scan(cur, parent_scan_id=parent,
                                    changed_paths=["app/order.py"], risk_analyzed=False)
    yield {"db": db, "parent": parent, "child": child, "pkeys": pkeys, "ckeys": ckeys}
    with db() as cur:
        cur.execute("DELETE FROM scan WHERE repository_id = %s", (str(REPO_ID),))


def test_annotate_carries_forward_unchanged_files(parent_and_child):
    db, child = parent_and_child["db"], parent_and_child["child"]
    parent_id, changed = annotate_mod._incremental_carry_forward(child)
    assert parent_id == parent_and_child["parent"]
    assert changed == ["app/order.py"]

    with db() as cur:
        cur.execute(
            "SELECT s.path FROM symbol_semantic ss JOIN symbol s"
            " ON s.scan_id=ss.scan_id AND s.symbol_key=ss.symbol_key WHERE ss.scan_id=%s",
            (child,),
        )
        paths = {r["path"] for r in cur.fetchall()}
    # pricing.py(안 바뀜) 는 복사됨, order.py(변경) 는 아직 없음(LLM 이 채울 것)
    assert paths == {"app/pricing.py"}


def test_embed_copies_forward_and_fills_gap(parent_and_child, monkeypatch):
    db, child = parent_and_child["db"], parent_and_child["child"]
    # order.py 심볼에도 summary 를 넣어 임베딩 대상이 되게 한다(annotate 가 했을 상황)
    with db() as cur:
        cur.execute(
            "INSERT INTO symbol_semantic (scan_id, symbol_key, summary, model) VALUES (%s, %s, '주문 요약', 'test')",
            (child, parent_and_child["ckeys"]["place_order"]),
        )
    # pricing.py 는 parent 에서 복사되어야 하므로 먼저 carry-forward
    annotate_mod._incremental_carry_forward(child)

    monkeypatch.setattr(embed_mod, "embed_text", lambda text: [0.2] * 1024)
    monkeypatch.setattr(embed_mod, "to_vector_literal", lambda v: "[" + ",".join(map(str, v)) + "]")
    embed_mod.handle({"scan_id": child})

    with db() as cur:
        cur.execute(
            "SELECT s.path, se.model FROM symbol_embedding se JOIN symbol s"
            " ON s.scan_id=se.scan_id AND s.symbol_key=se.symbol_key WHERE se.scan_id=%s",
            (child,),
        )
        rows = {(r["path"], r["model"]) for r in cur.fetchall()}
    assert ("app/pricing.py", "test-embed") in rows          # parent 에서 복사
    assert any(p == "app/order.py" for p, _m in rows)          # 갭 채움


def test_risk_incremental_reuses_parent_classification(parent_and_child, monkeypatch):
    db, child = parent_and_child["db"], parent_and_child["child"]
    calls: list[str] = []

    def fake_classify(symbol, path, evidence, **kw):
        calls.append(symbol)
        return {"classification": "generic_infrastructure", "business_role": "새 분류",
                "confidence": 0.8, "evidence": []}

    monkeypatch.setattr(classify, "classify_business_ip", fake_classify)
    # order.py 심볼이 business_semantic 근거를 갖도록 semantic 추가(→ _should_classify True)
    with db() as cur:
        cur.execute(
            "INSERT INTO symbol_semantic (scan_id, symbol_key, summary, model)"
            " VALUES (%s, %s, 'pricing discount 계산', 'test')",
            (child, parent_and_child["ckeys"]["place_order"]),
        )
        cur.execute(
            "INSERT INTO symbol_semantic (scan_id, symbol_key, role, domain, summary, model)"
            " VALUES (%s, %s, 'service', '가격', '모듈', 'test')",
            (child, parent_and_child["ckeys"]["app.order"]),
        )
        annotate_mod._incremental_carry_forward(child)

    with db() as cur:
        ensure_risk_objects(
            cur, child, model="m",
            parent_scan_id=parent_and_child["parent"], changed_paths=["app/order.py"],
        )
        cur.execute(
            "SELECT target_symbol_key, classification, provenance FROM risk_object WHERE scan_id=%s",
            (child,),
        )
        objs = {r["target_symbol_key"]: r for r in cur.fetchall()}

    pricing_key = parent_and_child["ckeys"]["calculate_discount"]
    order_key = parent_and_child["ckeys"]["place_order"]

    # pricing(안 바뀜): parent 분류 재사용, LLM 미호출
    assert objs[pricing_key]["classification"]["business_ip_class"] == "business_core_logic"
    assert "carried" in objs[pricing_key]["provenance"]["decision_source"]
    assert "calculate_discount" not in calls

    # order(변경): 새로 분류
    assert objs[order_key]["classification"]["business_ip_class"] == "generic_infrastructure"
    assert "place_order" in calls
