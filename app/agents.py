"""에이전트 역할 — Planner / StaticAnalyst / Reporter (Phase 1, 정적·샌드박스 무의존).

Phase 1 정적 흐름은 빠르므로 engagement.run 핸들러 안에서 동기로 돈다(긴 Pentester/Verifier는
Phase 2 에서 subtask.run 잡으로 분리 — 그때 heartbeat 가 필요). task/agent_action 행은 기록으로 남겨
작업 계층·감사·재현이 지금부터 성립하게 한다.

StaticAnalyst 가 내는 산출물은 "이 스캔의 보안 finding 들과 각자의 HTTP 엔트리포인트(도달 경로·
인증 요구)" — 동적 검증(Phase 2)의 입력이자, 그 자체로 바로 쓸 수 있는 화이트박스 분석 결과다.
"""

from __future__ import annotations

import json
import logging

from app.dynamic import entrypoint

log = logging.getLogger("app.agents")


def _add_task(cur, engagement_id: str, seq: int, role: str, title: str) -> str:
    cur.execute(
        "INSERT INTO task (engagement_id, seq, title, assigned_role, status) "
        "VALUES (%s,%s,%s,%s::agent_role,'running') RETURNING id",
        (engagement_id, seq, title, role),
    )
    return str(cur.fetchone()["id"])


def _finish_task(cur, task_id: str, status: str, result: dict | None = None) -> None:
    cur.execute("UPDATE task SET status=%s::work_status, result=%s::jsonb, updated_at=now() WHERE id=%s",
                (status, json.dumps(result) if result is not None else None, task_id))


def _add_artifact(cur, engagement_id: str, task_id: str | None, kind: str, title: str, body: dict) -> str:
    cur.execute(
        "INSERT INTO artifact (engagement_id, task_id, kind, title, body) "
        "VALUES (%s,%s,%s::artifact_kind,%s,%s::jsonb) RETURNING id",
        (engagement_id, task_id, kind, title, json.dumps(body, default=str)),
    )
    return str(cur.fetchone()["id"])


def static_analyst(cur, engagement: dict) -> dict:
    """스캔의 보안 finding 을 모으고 각자의 HTTP 엔트리포인트를 해석한다 → finding 아티팩트.

    반환 요약 dict(리포터가 사용). 각 candidate: finding 메타 + 엔트리포인트 해석 결과.
    """
    scan_id = engagement["scan_id"]
    eid = str(engagement["id"])
    task_id = _add_task(cur, eid, 1, "static_analyst", "보안 finding 수집 및 엔트리포인트 해석")

    cur.execute(
        "SELECT id, category, severity, sink_symbol_key, sink_line, source_symbol_key, source_var "
        "FROM security_finding WHERE scan_id=%s AND kind='security' AND status='open' ORDER BY id",
        (scan_id,),
    )
    findings = cur.fetchall()

    candidates = []
    reachable = 0
    for f in findings:
        ep = entrypoint.resolve(cur, scan_id, f)
        if ep.ok:
            reachable += 1
        candidates.append({
            "finding_id": f["id"], "category": f["category"], "severity": f["severity"],
            "sink": f["sink_symbol_key"].split("#")[-1], "source_var": f["source_var"],
            "entrypoint": {
                "resolved": ep.ok, "method": ep.method, "path": ep.path_template,
                "param": ep.param_name, "auth_required": ep.auth_required,
                "confidence": ep.confidence, "reason": ep.reason,
            },
        })

    summary = {
        "findings_total": len(findings),
        "entrypoint_resolved": reachable,
        "by_category": _count_by(findings, "category"),
        "candidates": candidates,
    }
    _add_artifact(cur, eid, task_id, "finding", "보안 finding + 엔트리포인트", summary)
    _finish_task(cur, task_id, "succeeded",
                 {"findings": len(findings), "reachable": reachable})
    return summary


def verifier(cur, engagement: dict, *, app_client, log_source=None, allow_instrumentation=False,
             limit: int = 50) -> dict:
    """열린 보안 finding 을 동적으로 검증(confirmed/false_positive/…)하고 판정을 반영한다.

    app_client/log_source 는 샌드박스(booted app)가 제공하거나 테스트가 주입. 각 finding 은
    runner.verify_finding → feedback.write_verdict. 결과 카운트 요약 반환.
    """
    from app.dynamic import feedback, runner

    scan_id = engagement["scan_id"]
    eid = str(engagement["id"])
    task_id = _add_task(cur, eid, 50, "verifier", "동적 검증(무해 프로브)")
    cur.execute(
        "SELECT id, category, severity, sink_symbol_key, sink_line, source_symbol_key, source_var "
        "FROM security_finding WHERE scan_id=%s AND kind='security' AND status='open' ORDER BY id LIMIT %s",
        (scan_id, limit),
    )
    findings = cur.fetchall()
    counts: dict[str, int] = {}
    for f in findings:
        outcome = runner.verify_finding(cur, scan_id, dict(f), app_client=app_client,
                                        log_source=log_source, allow_instrumentation=allow_instrumentation)
        feedback.write_verdict(cur, engagement=engagement, finding=dict(f), verdict=outcome.verdict,
                               entry_route=outcome.entry_route, oracle=outcome.oracle_kind,
                               artifacts=outcome.artifacts)
        counts[outcome.verdict.verdict] = counts.get(outcome.verdict.verdict, 0) + 1
    _finish_task(cur, task_id, "succeeded", counts)
    return counts


def reporter(cur, engagement: dict, static_summary: dict) -> str:
    """StaticAnalyst 결과를 사람이 읽을 리포트 아티팩트로 집계한다. artifact_id 반환."""
    eid = str(engagement["id"])
    task_id = _add_task(cur, eid, 99, "reporter", "검증 리포트 생성")
    n = static_summary.get("findings_total", 0)
    reach = static_summary.get("entrypoint_resolved", 0)
    lines = [
        f"# 보안 검증 리포트 (정적, phase-1)",
        f"- 목표: {engagement.get('goal') or '(없음)'}",
        f"- 보안 finding: {n}건, HTTP 엔트리포인트 해석됨: {reach}건",
        f"- 카테고리: {static_summary.get('by_category')}",
        "",
        "## 후보(동적 검증 대상)",
    ]
    for c in static_summary.get("candidates", []):
        ep = c["entrypoint"]
        loc = (f"{ep['method']} {ep['path']}" + (f" (param={ep['param']})" if ep.get("param") else "")
               + (" [auth]" if ep.get("auth_required") else "")) if ep["resolved"] else f"미해석({ep.get('reason')})"
        lines.append(f"- [{c['category']}] {c['sink']} ← {c['source_var']} → {loc}")
    body = {"markdown": "\n".join(lines), "static_summary": static_summary}
    artifact_id = _add_artifact(cur, eid, task_id, "report", "검증 리포트", body)
    _finish_task(cur, task_id, "succeeded")
    return artifact_id


def _count_by(rows: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return out
