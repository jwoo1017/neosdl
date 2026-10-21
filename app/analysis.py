"""프로젝트 분석 상태 — "채팅 가능" 판단, 진행 단계 표시, 완료/실패 알림의 기준.

분석 파이프라인의 핵심 단계는 scan.fetch → scan.parse → symbol.annotate → risk.build 다
(graph.project·pdg.build·symbol.embed 는 곁가지라 사용자 진행 표시에서 뺀다).
  - 완료: risk.build 가 끝나며 scan.analysis_completed_at 을 기록한다(sql/032).
  - 실패: 핵심 단계 작업이 재시도를 다 써서 dead 가 되면 워커(worker/main.py)가
    mark_stage_dead 로 scan.analysis_error 를 남긴다.
  - 진행 단계: 그 스캔의 대기/실행 중 작업 중 가장 앞 단계 — 실시간 이벤트를 놓쳐도
    다시 조회하면 맞는 값이 나온다.

채팅은 프로젝트의 대표 저장소(가장 최근에 연결한 것, app/chat.py _resolve_chat_ctx 와 같은 기준)에
완료된 스캔이 하나라도 있으면 열린다. 재분석 중에는 이전 완료 결과로 계속 답한다.
"""

from __future__ import annotations

import logging

from app import repo_activity_bus

log = logging.getLogger("app.analysis")

# 작업 종류 → 사용자에게 보이는 단계(화면의 4단계 진행 표시와 같은 순서)
STAGE_BY_KIND = {
    "scan.fetch": "fetch",
    "scan.parse": "parse",
    "symbol.annotate": "annotate",
    "risk.build": "risk",
}
STAGES = ["fetch", "parse", "annotate", "risk"]


def primary_repository(cur, project_id: str) -> dict | None:
    cur.execute(
        """
        SELECT r.id, r.namespace, r.name
          FROM project_repository pr JOIN repository r ON r.id = pr.repository_id
         WHERE pr.project_id = %s
         ORDER BY pr.attached_at DESC LIMIT 1
        """,
        (str(project_id),),
    )
    return cur.fetchone()


def latest_ready_scan_id(cur, repository_id) -> str | None:
    cur.execute(
        """
        SELECT id FROM scan
         WHERE repository_id = %s AND analysis_completed_at IS NOT NULL
         ORDER BY analysis_completed_at DESC LIMIT 1
        """,
        (str(repository_id),),
    )
    row = cur.fetchone()
    return str(row["id"]) if row else None


def _current_stage(cur, scan_id: str) -> str | None:
    cur.execute(
        """
        SELECT kind FROM job
         WHERE status IN ('queued', 'running') AND kind = ANY(%s)
           AND payload->>'scan_id' = %s
        """,
        (list(STAGE_BY_KIND), scan_id),
    )
    stages = {STAGE_BY_KIND[r["kind"]] for r in cur.fetchall()}
    for stage in STAGES:
        if stage in stages:
            return stage
    return None


def project_analysis(cur, project_id: str) -> dict:
    """GET /projects/{id}/analysis 응답 본문.

    state: no_repository | analyzing | failed | ready | ready_reanalyzing
    """
    repo = primary_repository(cur, project_id)
    if repo is None:
        return {"state": "no_repository", "chat_ready": False, "ready_scan_id": None, "latest": None}

    ready_scan_id = latest_ready_scan_id(cur, repo["id"])
    cur.execute(
        """
        SELECT id, status, analysis_completed_at, analysis_error, created_at, findings_count
          FROM (
            SELECT s.*, (SELECT count(*) FROM security_finding f
                          WHERE f.scan_id = s.id AND f.status = 'open') AS findings_count
              FROM scan s WHERE s.repository_id = %s
             ORDER BY s.created_at DESC LIMIT 1
          ) t
        """,
        (str(repo["id"]),),
    )
    latest = cur.fetchone()
    repo_info = {"id": str(repo["id"]), "full_name": f"{repo['namespace']}/{repo['name']}"}

    if latest is None:
        # 연결은 됐는데 스캔이 한 번도 안 만들어졌다(연결 직후 GitHub 조회 실패 등) — 다시 시도 대상.
        state = "ready" if ready_scan_id else "failed"
        return {
            "state": state, "chat_ready": bool(ready_scan_id), "ready_scan_id": ready_scan_id,
            "latest": {"scan_id": None, "repository": repo_info, "stage": None, "started_at": None,
                       "error": None if ready_scan_id else "분석이 아직 시작되지 않았어요.",
                       "findings_count": None},
        }

    latest_id = str(latest["id"])
    failed = latest["analysis_error"] is not None or latest["status"] == "failed"
    done = latest["analysis_completed_at"] is not None
    stage = None if (done or failed) else _current_stage(cur, latest_id)

    if ready_scan_id is None:
        state = "failed" if failed else ("ready" if done else "analyzing")
    elif done or failed:
        state = "ready"
    else:
        state = "ready_reanalyzing"

    return {
        "state": state,
        "chat_ready": ready_scan_id is not None,
        "ready_scan_id": ready_scan_id,
        "latest": {
            "scan_id": latest_id,
            "repository": repo_info,
            "stage": stage,
            "started_at": latest["created_at"],
            "error": latest["analysis_error"] if failed else None,
            "findings_count": latest["findings_count"] if done else None,
        },
    }


def chat_ready(cur, project_id: str) -> bool:
    repo = primary_repository(cur, project_id)
    return repo is not None and latest_ready_scan_id(cur, repo["id"]) is not None


def _scan_repository(cur, scan_id: str):
    cur.execute("SELECT repository_id FROM scan WHERE id = %s", (scan_id,))
    row = cur.fetchone()
    return row["repository_id"] if row else None


def notify_stage(cur, kind: str, payload: dict) -> None:
    """핵심 단계 작업이 시작될 때 저장소 실시간 소켓으로 단계를 알린다(fail-open 은 호출부)."""
    stage = STAGE_BY_KIND.get(kind)
    scan_id = (payload or {}).get("scan_id")
    if stage is None or not scan_id:
        return
    repository_id = _scan_repository(cur, str(scan_id))
    if repository_id is None:
        return
    repo_activity_bus.notify_repo_activity(
        cur, repository_id, {"type": "scan_stage", "scan_id": str(scan_id), "stage": stage},
    )


def mark_stage_dead(cur, kind: str, payload: dict, error: str) -> None:
    """핵심 단계 작업이 최종 실패(dead)했을 때 스캔에 실패를 기록하고 알린다.

    fetch/parse 단계면 스캔 자체가 만들어지지 못한 것이므로 status 도 failed 로 내린다.
    annotate/risk 단계면 파싱 결과는 남아 있어 status 는 그대로 둔다.
    """
    stage = STAGE_BY_KIND.get(kind)
    scan_id = (payload or {}).get("scan_id")
    if stage is None or not scan_id:
        return
    message = f"{stage} 단계 실패: {error}"[:2000]
    if stage in ("fetch", "parse"):
        cur.execute(
            """
            UPDATE scan SET analysis_error = %s, status = 'failed', finished_at = coalesce(finished_at, now())
             WHERE id = %s AND analysis_completed_at IS NULL RETURNING repository_id
            """,
            (message, str(scan_id)),
        )
    else:
        cur.execute(
            "UPDATE scan SET analysis_error = %s WHERE id = %s AND analysis_completed_at IS NULL "
            "RETURNING repository_id",
            (message, str(scan_id)),
        )
    row = cur.fetchone()
    if row is None:
        return
    repo_activity_bus.notify_repo_activity(
        cur, row["repository_id"],
        {"type": "scan_failed", "scan_id": str(scan_id), "stage": stage, "error": message},
    )
