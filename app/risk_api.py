"""Risk Intelligence API — Phase 2 산출물 조회(phase2 plan §15, §24 Step 11).

    GET  /scans/{scan_id}/risk              — 스캔 전체 Risk Object 목록 + 요약
    GET  /scans/{scan_id}/risk/symbol       — 심볼 하나의 Risk Object + Evidence Graph
    POST /scans/{scan_id}/risk/evaluate     — Query-aware Contextual Risk(§11)

명명은 기존 컨벤션을 따른다 — 심볼 지정은 `/scans/{id}/callers` 처럼 쿼리
파라미터 `symbol_key`, 접근 체크는 `assert_scan_access`.

스캔 전체 계산은 내부 LLM 을 여러 번 부르므로 `risk.build` 워커 잡으로 돌린다
(annotate/embed 와 동일). 아직 안 됐으면 이 엔드포인트가 잡을 넣고 `analyzing`
상태를 돌려준다(릴리즈 페이지의 "스캔하기" CTA 와 같은 패턴). 심볼 단위/쿼리
평가는 대상이 좁아 요청 안에서 즉석 계산한다(`/scans/{id}/ask` 와 같은 절충).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import ai_providers, source_reader
from app.auth import assert_scan_access, get_current_member
from app.db import tx
from app.queue import enqueue
from app.risk import synthesis
from app.risk.schema import REPRESENTATIONS

router = APIRouter(tags=["risk"])


def _llm_ctx(cur, scan_id: UUID) -> tuple[str, str | None]:
    """(model, project_id) — Business/IP 분류에 쓸 게이트웨이 별칭."""
    cur.execute(
        """
        SELECT p.id AS project_id, p.qa_model_name
          FROM scan s
          LEFT JOIN project_repository pr ON pr.repository_id = s.repository_id
          LEFT JOIN project p ON p.id = pr.project_id
         WHERE s.id = %s LIMIT 1
        """,
        (str(scan_id),),
    )
    row = cur.fetchone() or {}
    model = ai_providers.resolve_requested_model(None, project_default=row.get("qa_model_name"))
    return model, (str(row["project_id"]) if row.get("project_id") else None)


def _source_reader(cur, scan_id: UUID):
    # 레포 캐시의 작업 트리는 기본 브랜치 tip이라 스캔 커밋과 다를 수 있다 — 스캔 커밋의
    # blob을 읽어야 symbol의 줄 범위와 맞는다(app/source_reader.py).
    return source_reader.file_reader(cur, str(scan_id))


def _summarize(objects: list[dict]) -> dict:
    by_repr = {name: 0 for name in REPRESENTATIONS}
    raw_denied = 0
    specialized = 0
    top_dims: dict[str, int] = {}
    for o in objects:
        exp = o["exposure"]
        by_repr[exp["allowed_representation"]] = by_repr.get(exp["allowed_representation"], 0) + 1
        if not exp["external_raw_allowed"]:
            raw_denied += 1
        if exp["specialized_model_required"]:
            specialized += 1
        for dim, entry in (o["risk"] or {}).items():
            if entry.get("score", 0) >= 0.45:
                top_dims[dim] = top_dims.get(dim, 0) + 1
    return {
        "total": len(objects),
        "external_raw_denied": raw_denied,
        "specialized_model_required": specialized,
        "by_representation": {k: v for k, v in by_repr.items() if v},
        "elevated_dimensions": top_dims,
    }


@router.get("/scans/{scan_id}/risk")
def scan_risk(scan_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        cur.execute("SELECT stats FROM scan WHERE id=%s", (str(scan_id),))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "scan not found")

        if not (row["stats"] or {}).get("risk_analyzed"):
            job_id = enqueue(cur, "risk.build", {"scan_id": str(scan_id)},
                             priority=120, dedupe_key=f"risk:{scan_id}")
            return {"status": "analyzing", "scan_id": str(scan_id),
                    "queued": job_id is not None,
                    "detail": "Risk 분석이 백그라운드에서 진행 중입니다. 잠시 후 다시 조회하세요."}

        objects = synthesis.get_risk_objects(cur, str(scan_id))

    objects.sort(key=lambda o: -max((e.get("score", 0) for e in (o["risk"] or {}).values()), default=0))
    return {"status": "ready", "scan_id": str(scan_id),
            "summary": _summarize(objects), "risk_objects": objects}


@router.get("/scans/{scan_id}/risk/symbol")
def symbol_risk(scan_id: UUID, symbol_key: str, member: dict = Depends(get_current_member)):
    """심볼 하나의 Risk Object + Evidence Graph. 영속본이 없으면 즉석 계산한다
    (일반 CRUD/유틸 심볼은 영속화하지 않으므로 — Scenario A: LOW/RAW)."""
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        model, project_id = _llm_ctx(cur, scan_id)
        result = synthesis.get_risk_object_with_graph(
            cur, str(scan_id), symbol_key, model=model,
            project_id=project_id, source_reader=_source_reader(cur, scan_id),
        )
    if result is None:
        raise HTTPException(404, "symbol not found in scan")
    return result


class RiskEvaluateIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    targets: list[str] = Field(min_length=1, max_length=20)


@router.post("/scans/{scan_id}/risk/evaluate")
def evaluate_risk(scan_id: UUID, body: RiskEvaluateIn, member: dict = Depends(get_current_member)):
    """Query-aware Contextual Risk(§11). 결과는 영속화하지 않는다."""
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        model, project_id = _llm_ctx(cur, scan_id)
        return synthesis.evaluate_query(
            cur, str(scan_id), body.query, body.targets,
            model=model, project_id=project_id,
            source_reader=_source_reader(cur, scan_id),
        )
