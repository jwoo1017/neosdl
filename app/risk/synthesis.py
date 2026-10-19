"""Risk Synthesis — static evidence + Rule Engine + 내부 LLM 결합(phase2 plan §8) +
Contextual/Query-aware Risk(§11) + 영속화.

`security_finding` 과 같은 "최초 조회 시 1회 계산 + 캐싱" 방식이다
(`ensure_risk_objects`, scan.stats 의 `risk_analyzed` 플래그). scan 은 불변
스냅샷이므로 재계산이 아니라 새 scan 이 새 행을 만든다.

결합 순서(§8):
    NeoSDL 분석 → Evidence 수집 → Risk Vector 집계 → (게이트 통과 시) Business/IP LLM 분류
    → LLM 분류로 business_ip 하한 보정 → Rule Engine(결정론적) → Exposure Boundary → Risk Object

Query-aware(`evaluate_query`)는 영속화하지 않는다 — `/scans/{id}/ask` 가 질문마다
상태를 남기지 않는 것과 같은 이유. base Risk Object 를 가져와(없으면 즉석 계산)
질문 의도만 반영해 Exposure 를 다시 산출한다(§11, §12: 전체 재분석 금지).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.risk import classify
from app.risk.evidence import Evidence, SourceReader, collect_evidence
from app.risk.exposure import derive_exposure
from app.risk.graph import build_evidence_graph
from app.risk.policy import RiskPolicy, load_policy
from app.risk.rules import apply_rules, score_floors
from app.risk.schema import ExposureBoundary, RiskDimensionScore, RiskObject, RiskVector, RuleOutcome
from app.risk.vector import compute_vector

log = logging.getLogger("app.risk.synthesis")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------- persistence

def _persist_evidence(cur, scan_id: str, target: str, evidence: list[Evidence]) -> list[Evidence]:
    """(scan_id, target, fingerprint) 유니크로 upsert 하고 id 를 채워 돌려준다."""
    seen: dict[str, Evidence] = {}
    for e in evidence:
        seen.setdefault(e.fingerprint, e)
    stored: list[Evidence] = []
    for e in seen.values():
        cur.execute(
            """
            INSERT INTO risk_evidence
                (scan_id, target_symbol_key, related_symbol_key, kind, dimension, weight,
                 confidence, description, source, location, metadata, fingerprint)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (scan_id, target_symbol_key, fingerprint) DO UPDATE
                SET confidence = EXCLUDED.confidence, weight = EXCLUDED.weight,
                    description = EXCLUDED.description, metadata = EXCLUDED.metadata
            RETURNING id
            """,
            e.to_row(scan_id),
        )
        e.id = cur.fetchone()["id"]
        stored.append(e)
    return stored


def _persist_risk_object(cur, scan_id: str, obj: RiskObject) -> None:
    cur.execute(
        """
        INSERT INTO risk_object
            (scan_id, target_symbol_key, target, risk, classification, exposure,
             rule_outcomes, provenance)
        VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
        ON CONFLICT (scan_id, target_symbol_key) DO UPDATE
            SET target = EXCLUDED.target, risk = EXCLUDED.risk,
                classification = EXCLUDED.classification, exposure = EXCLUDED.exposure,
                rule_outcomes = EXCLUDED.rule_outcomes, provenance = EXCLUDED.provenance,
                generated_at = now()
        """,
        (
            scan_id, obj.target["symbol_key"],
            json.dumps(obj.target, ensure_ascii=False),
            json.dumps(obj.risk.to_dict(), ensure_ascii=False),
            json.dumps(obj.classification, ensure_ascii=False, default=str),
            json.dumps(obj.exposure.to_dict(), ensure_ascii=False),
            json.dumps([r.to_dict() for r in obj.rule_outcomes], ensure_ascii=False),
            json.dumps(obj.provenance, ensure_ascii=False, default=str),
        ),
    )


# --------------------------------------------------------------------- core

def _target_meta(cur, scan_id: str, symbol_key: str) -> dict[str, Any] | None:
    cur.execute(
        "SELECT kind, qualified_name, path, start_line, end_line FROM symbol WHERE scan_id=%s AND symbol_key=%s",
        (scan_id, symbol_key),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "type": row["kind"], "symbol": row["qualified_name"], "file": row["path"],
        "lines": [row["start_line"], row["end_line"]], "symbol_key": symbol_key,
    }


_BUSINESS_IP_PRIMARY_KINDS = ("business_semantic", "domain_crossing")


def _should_classify(vector: RiskVector, evidence: list[Evidence], policy: RiskPolicy) -> bool:
    """Business/IP LLM 을 부를지 결정 — 비용/지연 통제(§8: 전체 코드가 아니라 evidence 기반, §12).

    primary IP 신호(핵심 어휘 / 도메인 경계 흐름)가 있거나, static business_ip 가
    이미 높으면(≥ static-only 상한) 부른다. amplifier 신호만 있는 심볼은 부르지
    않는다 — 그건 이미 상한에 눌려 DENY 티어에 못 가므로 LLM 비용이 아깝다.
    """
    if any(e.kind in _BUSINESS_IP_PRIMARY_KINDS and e.dimension == "business_ip" for e in evidence):
        return True
    gate = float(policy.get("thresholds", "business_ip_static_only_ceiling", default=0.65) or 0.65)
    return vector.score("business_ip") >= gate


def _reconcile_business_ip(vector: RiskVector, classification: dict | None,
                            policy: RiskPolicy, *, has_primary: bool = False) -> tuple[RiskVector, str | None]:
    """LLM 분류로 business_ip 점수를 검증/조정(§8 Evidence Validation). classification 이
    없어도 static-only 상한은 항상 적용한다."""
    new_score, note = classify.reconcile_business_ip(
        vector.score("business_ip"), classification, policy, has_primary_evidence=has_primary,
    )
    if abs(new_score - vector.score("business_ip")) < 1e-9:
        return vector, note
    scores = dict(vector.scores)
    prev = scores.get("business_ip")
    conf = max(prev.confidence if prev else 0.0, float((classification or {}).get("confidence", 0.5)))
    scores["business_ip"] = RiskDimensionScore(
        score=new_score, confidence=conf, evidence_refs=prev.evidence_refs if prev else [],
    )
    return RiskVector(scores=scores), note


def _apply_score_floors(vector: RiskVector, outcomes: list[RuleOutcome]) -> RiskVector:
    floors = score_floors(outcomes)
    if not floors:
        return vector
    scores = dict(vector.scores)
    for dim, floor in floors.items():
        prev = scores.get(dim)
        if not prev or floor > prev.score:
            scores[dim] = RiskDimensionScore(
                score=floor, confidence=max(prev.confidence if prev else 0.0, 0.95),
                evidence_refs=prev.evidence_refs if prev else [],
            )
    return RiskVector(scores=scores)


def assemble_risk(
    evidence: list[Evidence], policy: RiskPolicy, *,
    llm_classification: dict | None = None, query_context: dict | None = None,
) -> tuple[RiskVector, dict[str, Any], list[RuleOutcome], ExposureBoundary]:
    """근거 목록 → (Risk Vector, classification, rule outcomes, Exposure Boundary).

    DB/LLM 의존이 전혀 없는 **순수** 결합 로직(§8 Risk Synthesis 의 계산부).
    `compute_risk_object` 는 여기에 evidence 수집·LLM 호출·영속화를 두른 것이고,
    평가 하니스(tests/test_risk_scenarios.py)는 이 함수를 직접 부른다.

    `llm_classification` 은 `classify.classify_business_ip` 의 반환 형태
    (`{"classification","business_role","confidence","evidence"}`) 또는 None.
    """
    vector = compute_vector(evidence, policy)

    classification: dict[str, Any] = {}
    if llm_classification:
        classification = {
            "business_role": llm_classification.get("business_role"),
            "business_ip_class": llm_classification.get("classification"),
            "classifier_confidence": llm_classification.get("confidence"),
            "classifier_evidence": llm_classification.get("evidence", []),
        }

    has_primary = any(
        e.kind in _BUSINESS_IP_PRIMARY_KINDS and e.dimension == "business_ip" for e in evidence
    )
    vector, reconcile_note = _reconcile_business_ip(
        vector, llm_classification, policy, has_primary=has_primary,
    )
    if reconcile_note:
        classification["business_ip_reconciliation"] = reconcile_note

    sec_types = sorted({
        e.metadata.get("category") for e in evidence
        if e.kind == "security_pattern" and e.metadata.get("category")
    })
    if sec_types:
        classification["security_types"] = sec_types

    outcomes = apply_rules(vector, classification, policy)
    vector = _apply_score_floors(vector, outcomes)
    exposure = derive_exposure(vector, outcomes, policy, query_context=query_context)
    return vector, classification, outcomes, exposure


def _override_to_raw(classification_override: dict | None) -> dict | None:
    """영속화된 classification dict → `classify.classify_business_ip` 반환 형태로 역변환.

    증분 스캔에서 parent 의 Business/IP 분류를 LLM 재호출 없이 그대로 쓰기 위함(§12).
    """
    if not classification_override or not classification_override.get("business_ip_class"):
        return None
    return {
        "classification": classification_override["business_ip_class"],
        "business_role": classification_override.get("business_role"),
        "confidence": classification_override.get("classifier_confidence", 0.5),
        "evidence": classification_override.get("classifier_evidence", []),
    }


def compute_risk_object(
    cur, scan_id: str, symbol_key: str, *,
    policy: RiskPolicy, model: str,
    project_id: str | None = None, source_reader: SourceReader | None = None,
    query_context: dict | None = None,
    evidence: list[Evidence] | None = None,
    allow_llm: bool = True,
    persist_evidence: bool = True,
    classification_override: dict | None = None,
) -> RiskObject | None:
    """한 심볼의 Risk Object 를 계산한다(risk_object 자체는 영속화하지 않음).

    `evidence` 를 넘기면 재수집을 생략한다. `persist_evidence=False` 면 `risk_evidence`
    행도 쓰지 않는다(즉석 조회/query 평가 — `ensure_risk_objects` 배치에서만 영속화).
    `classification_override`(영속화된 classification dict)를 넘기면 Business/IP LLM 을
    호출하지 않고 그 분류를 재사용한다(증분 스캔, §12) — 벡터/규칙/exposure 는
    fresh evidence 로 결정론적으로 다시 계산한다.
    """
    target = _target_meta(cur, scan_id, symbol_key)
    if target is None:
        return None

    if evidence is None:
        by_target = collect_evidence(cur, scan_id, policy, targets=[symbol_key], source_reader=source_reader)
        evidence = by_target.get(symbol_key, [])
    if persist_evidence:
        evidence = _persist_evidence(cur, scan_id, symbol_key, evidence)

    carried = _override_to_raw(classification_override)
    llm: dict | None = None
    if carried is not None:
        llm = carried
    elif allow_llm and _should_classify(compute_vector(evidence, policy), evidence, policy):
        llm = classify.classify_business_ip(
            target["symbol"], target["file"], evidence,
            model=model, project_id=project_id, scan_id=scan_id,
        )

    vector, classification, outcomes, exposure = assemble_risk(
        evidence, policy, llm_classification=llm, query_context=query_context,
    )
    if carried is not None:
        decision_source = "static+rules+llm(carried)"
    elif llm:
        decision_source = "static+rules+llm"
    else:
        decision_source = "static+rules"

    provenance = {
        "decision_source": decision_source,
        "model": model if (llm and carried is None) else None,
        "policy_version": policy.version,
        "generated_at": _now(),
        "query_context": query_context or None,
    }

    return RiskObject(
        target=target, risk=vector, classification=classification, exposure=exposure,
        rule_outcomes=outcomes, evidence_refs=[e.id for e in evidence if e.id is not None],
        provenance=provenance,
    )


# --------------------------------------------------------------------- scan-level

def ensure_risk_objects(
    cur, scan_id: str, *, model: str,
    project_id: str | None = None, source_reader: SourceReader | None = None,
    parent_scan_id: str | None = None, changed_paths: list[str] | None = None,
) -> None:
    """이 스캔의 Risk 분석이 없으면 계산해서 채운다(있으면 아무것도 안 함).

    근거가 하나도 없는 심볼(순수 CRUD/유틸)은 영속화하지 않는다 — 노이즈.
    그런 심볼도 `/scans/{id}/risk/symbol` 로 물으면 즉석 계산해 LOW/RAW 를 돌려준다.

    증분(§12): `parent_scan_id` + `changed_paths` 가 주어지면, evidence 는 항상
    전체 재수집하고 벡터/규칙/exposure 도 전 심볼 재계산하되(=구조 정확성 유지),
    **변경 파일 심볼 + 그 caller** 외에는 Business/IP LLM 분류를 parent 것으로
    재사용한다 — 577개 → ~수십 개로 LLM 호출을 줄인다.
    """
    cur.execute("SELECT stats FROM scan WHERE id=%s", (scan_id,))
    row = cur.fetchone()
    if row is None or (row["stats"] or {}).get("risk_analyzed"):
        return

    policy = load_policy(cur)
    by_target = collect_evidence(cur, scan_id, policy, source_reader=source_reader)

    affected: set[str] = set()
    parent_cls: dict[str, dict] = {}
    if parent_scan_id:
        from app import incremental

        affected = incremental.affected_symbols(cur, scan_id, changed_paths or [])
        cur.execute(
            "SELECT target_symbol_key, classification FROM risk_object WHERE scan_id = %s",
            (parent_scan_id,),
        )
        parent_cls = {r["target_symbol_key"]: (r["classification"] or {}) for r in cur.fetchall()}
        log.info("scan %s: 증분 Risk — 재분류 대상 %d개 (변경/caller), parent 재사용 %d개",
                 scan_id, len(affected), len(parent_cls))

    built = reused = 0
    for symbol_key, evidence in by_target.items():
        if not evidence:
            continue
        override = None
        if parent_scan_id and symbol_key not in affected and symbol_key in parent_cls:
            override = parent_cls[symbol_key]
            reused += 1
        obj = compute_risk_object(
            cur, scan_id, symbol_key, policy=policy, model=model,
            project_id=project_id, source_reader=source_reader, evidence=evidence,
            classification_override=override,
        )
        if obj is None:
            continue
        _persist_risk_object(cur, scan_id, obj)
        built += 1

    stats_patch = {"risk_analyzed": True}
    if parent_scan_id:
        stats_patch["reused_classifications"] = reused
    cur.execute(
        "UPDATE scan SET stats = stats || %s::jsonb WHERE id=%s",
        (json.dumps(stats_patch), scan_id),
    )
    log.info("scan %s: Risk Object %d개 생성 (LLM 재분류 %d, parent 재사용 %d)",
             scan_id, built, built - reused, reused)


def _row_to_object_dict(row: dict) -> dict[str, Any]:
    return {
        "target": row["target"],
        "risk": row["risk"],
        "classification": row["classification"],
        "exposure": row["exposure"],
        "rule_outcomes": row["rule_outcomes"],
        "evidence": _evidence_ref_ids(row["risk"]),
        "provenance": row["provenance"],
        "generated_at": row["generated_at"],
    }


def _evidence_ref_ids(risk: dict) -> list[int]:
    seen: list[int] = []
    for entry in (risk or {}).values():
        for ref in entry.get("evidence_refs", []):
            if ref not in seen:
                seen.append(ref)
    return seen


def get_risk_objects(cur, scan_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT target_symbol_key, target, risk, classification, exposure, rule_outcomes,
               provenance, generated_at
          FROM risk_object WHERE scan_id = %s
        """,
        (scan_id,),
    )
    return [_row_to_object_dict(r) for r in cur.fetchall()]


def get_risk_object_with_graph(
    cur, scan_id: str, symbol_key: str, *, model: str,
    project_id: str | None = None, source_reader: SourceReader | None = None,
) -> dict[str, Any] | None:
    """영속화된 Risk Object + Evidence Graph. 없으면 즉석 계산(영속화 안 함)."""
    cur.execute(
        """
        SELECT target, risk, classification, exposure, rule_outcomes, provenance, generated_at
          FROM risk_object WHERE scan_id = %s AND target_symbol_key = %s
        """,
        (scan_id, symbol_key),
    )
    row = cur.fetchone()
    policy = load_policy(cur)

    if row is not None:
        obj_dict = _row_to_object_dict(row)
        cur.execute(
            """
            SELECT id, target_symbol_key, related_symbol_key, kind, dimension, weight,
                   confidence, description, source, location, metadata
              FROM risk_evidence WHERE scan_id = %s AND target_symbol_key = %s
            """,
            (scan_id, symbol_key),
        )
        evs = [
            Evidence(
                target_symbol_key=r["target_symbol_key"], kind=r["kind"], dimension=r["dimension"],
                weight=r["weight"], confidence=r["confidence"], description=r["description"],
                source=r["source"], related_symbol_key=r["related_symbol_key"],
                location=r["location"], metadata=r["metadata"], id=r["id"],
            )
            for r in cur.fetchall()
        ]
        label = obj_dict["target"].get("symbol", symbol_key)
        graph = build_evidence_graph(symbol_key, label, evs, _symbol_labels(cur, scan_id, evs))
        return {**obj_dict, "evidence_detail": [e.to_dict() for e in evs], "evidence_graph": graph}

    # 영속본 없음 — 즉석 계산(risk_evidence/risk_object 둘 다 쓰지 않는다).
    # 일반 CRUD/유틸 심볼은 배치가 영속화하지 않으므로 이 경로가 Scenario A(LOW/RAW)를 답한다.
    by_target = collect_evidence(cur, scan_id, policy, targets=[symbol_key], source_reader=source_reader)
    evs = by_target.get(symbol_key, [])
    obj = compute_risk_object(
        cur, scan_id, symbol_key, policy=policy, model=model,
        project_id=project_id, evidence=evs, persist_evidence=False,
    )
    if obj is None:
        return None
    d = obj.to_dict()
    d["provenance"]["computed_on_demand"] = True
    label = d["target"].get("symbol", symbol_key)
    d["evidence_detail"] = [e.to_dict() for e in evs]
    d["evidence_graph"] = build_evidence_graph(symbol_key, label, evs, _symbol_labels(cur, scan_id, evs))
    return d


def _symbol_labels(cur, scan_id: str, evidence: list[Evidence]) -> dict[str, str]:
    keys = {e.related_symbol_key for e in evidence if e.related_symbol_key}
    if not keys:
        return {}
    cur.execute(
        "SELECT symbol_key, qualified_name FROM symbol WHERE scan_id=%s AND symbol_key = ANY(%s)",
        (scan_id, list(keys)),
    )
    return {r["symbol_key"]: r["qualified_name"] for r in cur.fetchall()}


def _vector_from_dict(risk: dict) -> RiskVector:
    return RiskVector(scores={
        dim: RiskDimensionScore(
            score=float(entry.get("score", 0.0)),
            confidence=float(entry.get("confidence", 0.0)),
            evidence_refs=list(entry.get("evidence_refs", [])),
        )
        for dim, entry in (risk or {}).items()
    })


def _rule_from_dict(d: dict) -> RuleOutcome:
    return RuleOutcome(
        rule=d.get("rule", ""), effect=d.get("effect", ""), dimension=d.get("dimension"),
        reason=d.get("reason", ""), detail=d.get("detail") or {},
    )


def _load_persisted_base(cur, scan_id: str, symbol_key: str) -> dict[str, Any] | None:
    """영속화된 risk_object 행을 Contextual 재산출에 쓸 형태로 로드.

    vector/classification/rule_outcomes 는 그대로 재사용하고, Exposure 만 질문
    맥락으로 다시 산출한다 — symbol 당 LLM 재호출을 피한다(§8, §12).
    """
    cur.execute(
        """
        SELECT target, risk, classification, rule_outcomes, provenance
          FROM risk_object WHERE scan_id = %s AND target_symbol_key = %s
        """,
        (scan_id, symbol_key),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "target": row["target"],
        "vector": _vector_from_dict(row["risk"]),
        "classification": row["classification"] or {},
        "rule_outcomes": [_rule_from_dict(d) for d in (row["rule_outcomes"] or [])],
        "evidence_refs": _evidence_ref_ids(row["risk"]),
        "provenance": {**(row["provenance"] or {}), "reused_persisted": True},
    }


def evaluate_query(
    cur, scan_id: str, query: str, targets: list[str], *,
    model: str, project_id: str | None = None,
    source_reader: SourceReader | None = None,
) -> dict[str, Any]:
    """Query-aware Contextual Risk(§11). 영속화하지 않는다.

    각 target 의 base Risk(영속본 우선, 없으면 즉석 계산)를 가져와 질문 의도만
    반영해 Exposure Boundary 를 다시 산출한다. `base_exposure`(맥락 없는 경계)도
    함께 돌려줘서 UI 가 "질문 때문에 달라진 부분"을 보여줄 수 있게 한다.
    """
    policy = load_policy(cur)
    intent = classify.classify_query_intent(
        query, targets, model=model, project_id=project_id, scan_id=scan_id,
    )

    results: list[dict[str, Any]] = []
    for symbol_key in targets:
        base = _load_persisted_base(cur, scan_id, symbol_key)
        if base is None:
            obj = compute_risk_object(
                cur, scan_id, symbol_key, policy=policy, model=model,
                project_id=project_id, source_reader=source_reader, persist_evidence=False,
            )
            if obj is None:
                results.append({"target": symbol_key, "error": "symbol not found in scan"})
                continue
            base = {
                "target": obj.target, "vector": obj.risk, "classification": obj.classification,
                "rule_outcomes": obj.rule_outcomes, "evidence_refs": obj.evidence_refs,
                "provenance": obj.provenance,
            }

        base_exposure = derive_exposure(base["vector"], base["rule_outcomes"], policy)
        adjusted = derive_exposure(base["vector"], base["rule_outcomes"], policy, query_context=intent)
        results.append({
            "risk_object": {
                "target": base["target"],
                "risk": base["vector"].to_dict(),
                "classification": base["classification"],
                "exposure": adjusted.to_dict(),
                "rule_outcomes": [r.to_dict() for r in base["rule_outcomes"]],
                "evidence": base["evidence_refs"],
                "provenance": {**base["provenance"], "query_context": intent},
            },
            "exposure_boundary": adjusted.to_dict(),
            "base_exposure": base_exposure.to_dict(),
        })

    return {"query": query, "query_intent": intent, "results": results}
