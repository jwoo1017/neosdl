"""Security History API — Phase 1: 커밋 간 Security Diff + per-branch Timeline.

`NeoSDL_Security_History_Plan.md` §5, §7.1. 기존
`GET /repositories/{id}/security/risk-history`(app/main.py)는 그대로 둔다
(하위호환). 여기 두 엔드포인트가 그 위에 fingerprint / branch / 합성 점수를 얹는다.

Risk Identity 생애주기·전파 그래프·4뷰 UI 는 다음 라운드.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app import ai_providers
from app.auth import assert_repository_access, get_current_member
from app.db import tx
from app.risk_identity import ensure_risk_identities
from app.security_score import synthetic_score
from app.security_snapshot import ensure_security_snapshot

router = APIRouter(tags=["security-history"])

_SEVERITIES = ("critical", "high", "medium", "low")


def _materialize_history(cur, repository_id: str) -> None:
    """security 분석은 끝났는데 스냅샷/Risk Identity 가 아직 없는 스캔들을 lazy 처리.

    파이프라인(risk.build)이 이미 하지만, 그 전에 계산된 옛 스캔이나 마이그레이션
    직후를 메꾼다. 실패해도 조회는 계속(개별 스캔만 건너뜀).
    """
    cur.execute(
        """
        SELECT id, stats->>'snapshot_built' AS snap FROM scan
         WHERE repository_id = %s AND stats->>'security_analyzed' = 'true'
         ORDER BY created_at
        """,
        (str(repository_id),),
    )
    scans = cur.fetchall()
    for r in scans:
        try:
            if r["snap"] is None:
                ensure_security_snapshot(cur, str(r["id"]))
            ensure_risk_identities(cur, str(r["id"]))
        except Exception:
            pass


def _symbol_key_path(symbol_key: str | None) -> str | None:
    """{lang}:{repo}:{path}#{qname}(...) -> {path}."""
    if not symbol_key or ":" not in symbol_key or "#" not in symbol_key:
        return None
    return symbol_key.split(":", 2)[-1].split("#", 1)[0]


# whitelist(오탐/승인) 처리한 finding 을 제외 — app/main.py scan_security_findings 관례와 동일.
_NOT_WHITELISTED = """
    NOT EXISTS (SELECT 1 FROM finding_whitelist fw
                 WHERE fw.repository_id = sf.repository_id AND fw.category = sf.category
                   AND (fw.sink_symbol_key IS NULL OR fw.sink_symbol_key = sf.sink_symbol_key))
"""


def _load_findings(cur, scan_id: str) -> dict[str, dict]:
    """fingerprint -> 대표 finding 행. 같은 지문이 sink_line 만 다르게 중복될 수 있어 첫 행만."""
    cur.execute(
        f"""
        SELECT sf.fingerprint, sf.kind, sf.category, sf.severity, sf.sink_symbol_key, sf.sink_line,
               sf.source_symbol_key, sf.source_var, sf.path
          FROM security_finding sf
         WHERE sf.scan_id = %s AND sf.status = 'open' AND sf.fingerprint IS NOT NULL
           AND {_NOT_WHITELISTED}
        """,
        (str(scan_id),),
    )
    out: dict[str, dict] = {}
    for r in cur.fetchall():
        out.setdefault(r["fingerprint"], r)
    return out


def _severity_counts(cur, scan_id: str) -> dict[str, int]:
    cur.execute(
        f"SELECT sf.severity, count(*) AS n FROM security_finding sf "
        f"WHERE sf.scan_id = %s AND sf.status = 'open' AND {_NOT_WHITELISTED} GROUP BY sf.severity",
        (str(scan_id),),
    )
    return {r["severity"]: r["n"] for r in cur.fetchall()}


def _view(fp: str, row: dict) -> dict:
    return {
        "fingerprint": fp, "kind": row["kind"], "category": row["category"],
        "severity": row["severity"], "sink_symbol_key": row["sink_symbol_key"],
        "sink_line": row["sink_line"], "source_var": row["source_var"],
    }


def compute_diff(cur, base_scan_id: str, head_scan_id: str) -> dict:
    """두 스냅샷의 fingerprint 단위 델타(`NeoSDL_Security_History_Plan.md` §5)."""
    ensure_security_snapshot(cur, base_scan_id)
    ensure_security_snapshot(cur, head_scan_id)

    base = _load_findings(cur, base_scan_id)
    head = _load_findings(cur, head_scan_id)
    base_c = _severity_counts(cur, base_scan_id)
    head_c = _severity_counts(cur, head_scan_id)

    new_fps = head.keys() - base.keys()
    resolved_fps = base.keys() - head.keys()

    affected: set[str] = set()
    for row in [head[fp] for fp in new_fps] + [base[fp] for fp in resolved_fps]:
        for k in (row["sink_symbol_key"], row["source_symbol_key"]):
            p = _symbol_key_path(k)
            if p:
                affected.add(p)
        for hop in row["path"] or []:
            p = _symbol_key_path(hop.get("symbol_key") if isinstance(hop, dict) else None)
            if p:
                affected.add(p)

    _sev_order = {s: i for i, s in enumerate(_SEVERITIES)}
    return {
        "new": sorted((_view(fp, head[fp]) for fp in new_fps), key=lambda f: _sev_order.get(f["severity"], 9)),
        "resolved": sorted((_view(fp, base[fp]) for fp in resolved_fps), key=lambda f: _sev_order.get(f["severity"], 9)),
        "unchanged": [_view(fp, head[fp]) for fp in head.keys() & base.keys()],
        "severity_delta": {s: head_c.get(s, 0) - base_c.get(s, 0) for s in _SEVERITIES},
        "score_delta": round(synthetic_score(head_c) - synthetic_score(base_c), 2),
        "affected_paths": sorted(affected),
    }


@router.get("/repositories/{repository_id}/security/diff")
def security_diff(repository_id: UUID, head_scan: UUID, base_scan: UUID | None = None,
                  member: dict = Depends(get_current_member)):
    """head_scan 과 base_scan(생략 시 head 의 parent_scan_id) 사이 Security Diff."""
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        cur.execute(
            "SELECT id, parent_scan_id, created_at FROM scan WHERE id = %s AND repository_id = %s",
            (str(head_scan), str(repository_id)),
        )
        head = cur.fetchone()
        if head is None:
            raise HTTPException(404, "head scan not found")

        base_id = str(base_scan) if base_scan else (
            str(head["parent_scan_id"]) if head["parent_scan_id"] else None
        )
        if base_id is None:
            # 폴백: 같은 레포의 직전 스냅샷(커밋시각 기준)
            cur.execute(
                """
                SELECT s.id FROM security_snapshot ss JOIN scan s ON s.id = ss.scan_id
                 WHERE ss.repository_id = %s AND s.id <> %s AND ss.committed_at < %s
                 ORDER BY ss.committed_at DESC LIMIT 1
                """,
                (str(repository_id), str(head_scan), head["created_at"]),
            )
            row = cur.fetchone()
            base_id = str(row["id"]) if row else None
        if base_id is None:
            raise HTTPException(422, "no base scan available — pass base_scan=")

        diff = compute_diff(cur, base_id, str(head_scan))
        return {"base_scan": base_id, "head_scan": str(head_scan), **diff}


@router.get("/repositories/{repository_id}/security/timeline")
def security_timeline(repository_id: UUID, branch: str | None = None, limit: int = 100,
                       member: dict = Depends(get_current_member)):
    """브랜치별 Security Snapshot 시계열(커밋시각 정렬). §7.1."""
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        _materialize_history(cur, str(repository_id))

        params: list = [str(repository_id)]
        sql = """
            SELECT scan_id, commit_sha, branch, committed_at, score,
                   n_critical, n_high, n_medium, n_low, n_findings, n_evidence
              FROM security_snapshot WHERE repository_id = %s
        """
        if branch:
            sql += " AND branch = %s"
            params.append(branch)
        sql += " ORDER BY committed_at LIMIT %s"
        params.append(limit)
        cur.execute(sql, params)
        series = cur.fetchall()

        cur.execute(
            """
            SELECT b.name, b.is_default, b.deleted_at, b.head_scan_id,
                   (SELECT count(*) FROM security_snapshot ss
                     WHERE ss.repository_id = b.repository_id AND ss.branch = b.name) AS n_snapshots
              FROM branch b WHERE b.repository_id = %s
             ORDER BY b.is_default DESC, b.name
            """,
            (str(repository_id),),
        )
        branches = cur.fetchall()

    return {"branches": branches, "series": series}


# --------------------------------------------------------------------- Risk Identity (Phase 2)

@router.get("/repositories/{repository_id}/security/risks")
def list_risk_identities(repository_id: UUID, status: str | None = None, branch: str | None = None,
                          member: dict = Depends(get_current_member)):
    """Risk Identity 목록 — 지문 하나 = 여러 커밋/브랜치에서 관측된 하나의 위험. §2.3."""
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        _materialize_history(cur, str(repository_id))

        params: list = [str(repository_id)]
        sql = """
            SELECT ri.fingerprint, ri.kind, ri.category, ri.worst_severity, ri.status,
                   ri.first_seen_commit, ri.first_seen_branch, ri.first_seen_at,
                   ri.last_seen_at, ri.fixed_at, ri.resolved_at, ri.sample,
                   (SELECT count(*) FROM risk_observation ro
                     WHERE ro.repository_id = ri.repository_id AND ro.fingerprint = ri.fingerprint) AS n_observations,
                   (SELECT count(DISTINCT ro.branch) FROM risk_observation ro
                     WHERE ro.repository_id = ri.repository_id AND ro.fingerprint = ri.fingerprint) AS n_branches
              FROM risk_identity ri
             WHERE ri.repository_id = %s
        """
        if status:
            sql += " AND ri.status = %s"
            params.append(status)
        if branch:
            sql += """ AND EXISTS (SELECT 1 FROM risk_observation ro
                                    WHERE ro.repository_id = ri.repository_id
                                      AND ro.fingerprint = ri.fingerprint AND ro.branch = %s)"""
            params.append(branch)
        sql += """ ORDER BY CASE ri.status WHEN 'active' THEN 0 WHEN 'fixed' THEN 1 ELSE 2 END,
                           CASE ri.worst_severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                                                  WHEN 'medium' THEN 2 ELSE 3 END,
                           ri.first_seen_at DESC"""
        cur.execute(sql, params)
        rows = cur.fetchall()

        cur.execute(
            """
            SELECT status, count(*) n FROM risk_identity WHERE repository_id = %s GROUP BY status
            """,
            (str(repository_id),),
        )
        summary = {r["status"]: r["n"] for r in cur.fetchall()}

    return {"summary": summary, "risks": rows}


@router.get("/repositories/{repository_id}/security/risks/{fingerprint}")
def risk_identity_detail(repository_id: UUID, fingerprint: str,
                          member: dict = Depends(get_current_member)):
    """Risk Identity 하나의 생애주기 — Detected/Introduced/Propagated/Merged/Fixed/Resolved
    이벤트 + 관측된 커밋/브랜치. §7.4."""
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        _materialize_history(cur, str(repository_id))

        cur.execute(
            "SELECT * FROM risk_identity WHERE repository_id = %s AND fingerprint = %s",
            (str(repository_id), fingerprint),
        )
        identity = cur.fetchone()
        if identity is None:
            raise HTTPException(404, "risk identity not found")

        cur.execute(
            """
            SELECT kind, scan_id, pr_number, branch, detail, at
              FROM risk_lifecycle_event
             WHERE repository_id = %s AND fingerprint = %s ORDER BY at, id
            """,
            (str(repository_id), fingerprint),
        )
        events = cur.fetchall()

        cur.execute(
            """
            SELECT ro.scan_id, ro.branch, ro.commit_sha, ro.committed_at, ro.severity
              FROM risk_observation ro
             WHERE ro.repository_id = %s AND ro.fingerprint = %s
             ORDER BY ro.committed_at
            """,
            (str(repository_id), fingerprint),
        )
        observations = cur.fetchall()

    return {"identity": identity, "lifecycle": events, "observations": observations}


_EXPLAIN_PROMPT = """다음은 한 코드 취약점(Risk Identity)의 프로젝트 시간축 생애주기 데이터다.
이 데이터**만** 근거로, 아래 질문에 3~6문장 한국어로 답하라. 근거에 없는 내용은 지어내지 마라.

- 이 취약점은 언제, 어느 브랜치·커밋에서 처음 생겼나?
- 어떤 PR을 거쳐 어느 브랜치로 전파됐나?
- 현재 상태는? (해결됐다면 언제)
- 해당 기간에 같은 취약점을 막거나 고친 흔적이 있나?

취약점: {category} ({severity})
지문: {fingerprint}
현재 상태: {status}

생애주기 이벤트(시간순):
{lifecycle}

관측된 커밋(시간순):
{observations}

대표 데이터 흐름: {dataflow}

답변(자연어 문장만, 코드블록/JSON 금지):
"""


@router.post("/repositories/{repository_id}/security/risks/{fingerprint}/explain")
def explain_risk_identity(repository_id: UUID, fingerprint: str,
                           member: dict = Depends(get_current_member)):
    """LLM History Reasoning — 이 취약점의 생애주기를 자연어로 설명(§9). 근거는 DB 데이터만."""
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        cur.execute(
            "SELECT * FROM risk_identity WHERE repository_id = %s AND fingerprint = %s",
            (str(repository_id), fingerprint),
        )
        identity = cur.fetchone()
        if identity is None:
            raise HTTPException(404, "risk identity not found")
        cur.execute(
            "SELECT kind, branch, pr_number, at FROM risk_lifecycle_event "
            "WHERE repository_id = %s AND fingerprint = %s ORDER BY at, id",
            (str(repository_id), fingerprint),
        )
        events = cur.fetchall()
        cur.execute(
            "SELECT branch, commit_sha, committed_at, severity FROM risk_observation "
            "WHERE repository_id = %s AND fingerprint = %s ORDER BY committed_at",
            (str(repository_id), fingerprint),
        )
        obs = cur.fetchall()
        cur.execute(
            """
            SELECT p.id AS project_id, p.qa_model_name
              FROM project_repository pr JOIN project p ON p.id = pr.project_id
             WHERE pr.repository_id = %s LIMIT 1
            """,
            (str(repository_id),),
        )
        proj = cur.fetchone() or {}

    lifecycle_txt = "\n".join(
        f"- {e['kind']}"
        + (f" (PR #{e['pr_number']})" if e["pr_number"] else "")
        + (f" @ {e['branch']}" if e["branch"] else "")
        + f"  {e['at']}"
        for e in events
    ) or "(없음)"
    obs_txt = "\n".join(
        f"- {o['commit_sha'][:8]} @ {o['branch'] or '?'}  ({o['severity']})  {o['committed_at']}"
        for o in obs
    ) or "(없음)"
    sample = identity["sample"] or {}
    hops = sample.get("path") or []
    dataflow = " ▸ ".join(
        _symbol_key_path(h.get("symbol_key")) or "?" for h in hops if isinstance(h, dict)
    ) or (_symbol_key_path(sample.get("sink_symbol_key")) or "(경로 정보 없음)")

    prompt = _EXPLAIN_PROMPT.format(
        category=identity["category"], severity=identity["worst_severity"],
        fingerprint=fingerprint, status=identity["status"],
        lifecycle=lifecycle_txt, observations=obs_txt, dataflow=dataflow,
    )
    model = ai_providers.resolve_requested_model(None, project_default=proj.get("qa_model_name"))
    try:
        explanation = ai_providers.generate(
            model, prompt, json_mode=False,
            project_id=str(proj["project_id"]) if proj.get("project_id") else None,
            purpose="security_history_explain",
        ).strip()
    except Exception:
        raise HTTPException(503, "설명 생성에 실패했습니다 (LLM 게이트웨이)")
    return {"fingerprint": fingerprint, "explanation": explanation}
