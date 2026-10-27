"""engagement.run 핸들러.

phase-1 정적 흐름: StaticAnalyst(보안 finding + 엔트리포인트 해석) → Reporter. 샌드박스 무의존이라
빠르므로 여기서 동기로 돈다. 긴 Pentester/Verifier(샌드박스 앱 기동)는 phase-2 에서 subtask.run
잡으로 분리한다(그때 queue.heartbeat 필요).

멱등·재개형: 이미 종료 상태면 아무것도 안 한다(job_reap 로 재큐돼도 이중 실행 방지).
scan_id 가 없으면 검증할 정적 스냅샷이 없으므로 failed 로 끝낸다.
"""

from __future__ import annotations

import json
import logging

from app import agents, ai_providers, audit, pentester, sandbox
from app.config import settings
from app.db import tx
from app.engagements import set_status

log = logging.getLogger("worker.engagement")

_TERMINAL = ("succeeded", "failed", "cancelled")


def _run_blackbox(cur, engagement: dict, target: str) -> dict:
    """블랙박스 외부표적 engagement — 샌드박스 툴박스에서 LLM Pentester 로 인가 표적을 점검.

    assert_target_allowed(인가·fail-closed) → 툴박스 기동 → run_pentester → 리포트 아티팩트.
    permission_mode=ask(기본)면 첫 명령에서 awaiting_approval 로 멈춘다(HITL). Docker/LLM 이 있어야
    실제로 돌고, 없거나 실패하면 예외가 상위 except 로 올라가 failed 로 끝난다(조용히 실패 안 함).
    """
    actor = "system:worker"
    sandbox.assert_target_allowed(cur, target, actor=actor)   # 미인가 표적이면 예외(+audit)

    eid = str(engagement["id"])
    permission_mode = engagement.get("permission_mode", "ask")
    task_id = agents._add_task(cur, eid, 10, "pentester", f"블랙박스 점검: {target}")

    from worker.sandbox.dind import DindRunner
    runner = DindRunner()
    network = None  # 기본 bridge(표적 egress 허용). 스코프 egress 프록시는 후속(plans).
    container = runner.start_toolbox(eid, network=network)
    model = ai_providers.resolve_requested_model(None)

    def llm_generate(prompt: str) -> str:
        return ai_providers.generate(model, prompt, json_mode=True, system=pentester.SYSTEM,
                                     project_id=str(engagement["project_id"]), purpose="pentest_step")

    def toolbox_exec(argv):
        return runner.exec_toolbox(container, argv, timeout_s=settings().sandbox_boot_timeout_s)

    try:
        outcome = pentester.run_pentester(
            cur, target=target, llm_generate=llm_generate, toolbox_exec=toolbox_exec,
            permission_mode=permission_mode, effective_role="manager", actor=actor)
    finally:
        try:
            runner.destroy(eid)   # 툴박스 컨테이너·네트워크 정리(실패해도 best-effort)
        except Exception:
            log.warning("툴박스 정리 실패", exc_info=True)

    lines = [f"# 블랙박스 점검 리포트", f"- 표적: {target}", f"- 스텝: {outcome.steps}",
             f"- 상태: {outcome.status}", ""]
    for i, h in enumerate(outcome.history):
        if "rejected" in h:
            lines.append(f"- [{i}] 거부({h['rejected']}): `{h['command']}`")
            continue
        r = h.get("result") or {}
        timed = " (타임아웃)" if r.get("timed_out") else ""
        lines.append(f"- [{i}] `{h['command']}` → ok={r.get('ok')} exit={r.get('exit_code')}{timed}")
        out = (r.get("output") or r.get("error") or "").strip()
        if out:   # 명령 출력(마스킹됨) 또는 거부 사유 — 화면에서 원인이 바로 보이게 코드블록으로
            lines += ["", "```", out[:4000], "```", ""]
    if outcome.findings:
        lines.append("\n## 발견")
        for f in outcome.findings:
            lines.append(f"- [{f.get('severity','?')}] {f.get('title','')}: {f.get('evidence','')}")
    body = {"markdown": "\n".join(lines), "target": target, "status": outcome.status,
            "findings": outcome.findings, "steps": outcome.steps,
            "pending_command": outcome.pending_command}
    agents._add_artifact(cur, eid, task_id, "report", "블랙박스 점검 리포트", body)
    agents._finish_task(cur, task_id, "succeeded" if outcome.status == "succeeded" else "blocked",
                        {"status": outcome.status, "findings": len(outcome.findings)})
    return {"kind": "blackbox", "status": outcome.status, "findings": len(outcome.findings),
            "steps": outcome.steps, "pending_command": outcome.pending_command}


def _maybe_dynamic(cur, engagement: dict) -> dict | None:
    """sandbox_enabled 이고 repo 가 allowlist 에 있으면 앱을 기동해 동적 검증까지 한다.

    기본 off(sandbox_enabled=False) — 이 경우 None 을 반환해 정적 결과만 쓴다(동작 불변).
    repo 가 allowlist 밖이면 assert_repo_allowed 가 거부(외부 코드 실행 금지). 부팅 실패/미기동은
    graceful: 검증 카운트 없이 None 반환하고 정적 판정을 유지한다.
    """
    if not settings().sandbox_enabled or not engagement.get("repository_id"):
        return None
    actor = "system:worker"
    sandbox.assert_repo_allowed(cur, str(engagement["repository_id"]), actor=actor)  # 거부 시 예외
    try:
        from worker.sandbox import boot
    except Exception:
        log.warning("샌드박스 모듈 로드 실패 — 정적 판정 유지", exc_info=True)
        return None
    booted = boot.boot_engagement(engagement)   # SandboxBoot | None
    if booted is None:
        return None
    try:
        return agents.verifier(cur, engagement, app_client=booted.app_client, log_source=booted.log_source,
                               allow_instrumentation=engagement.get("allow_instrumentation", False),
                               iast_nonce=booted.iast_nonce)
    finally:
        try:
            booted.teardown()
        except Exception:
            log.warning("샌드박스 정리 실패", exc_info=True)


def handle(payload: dict) -> None:
    engagement_id = payload["engagement_id"]
    with tx() as cur:
        cur.execute(
            "SELECT id, status, project_id, repository_id, scan_id, goal, permission_mode, "
            "allow_instrumentation, params "
            "FROM engagement WHERE id=%s FOR UPDATE",
            (engagement_id,))
        row = cur.fetchone()
        if row is None:
            log.warning("engagement %s 없음 — 건너뜀", engagement_id)
            return
        if row["status"] in _TERMINAL:
            log.info("engagement %s 이미 %s — 건너뜀", engagement_id, row["status"])
            return
        engagement = dict(row)
        params = engagement.get("params") or {}
        target = params.get("target")
        if not target and not row["scan_id"]:
            # 화이트박스인데 스캔이 없다 — 검증할 정적 스냅샷이 없으므로 실패.
            set_status(cur, engagement_id, "failed", error="검증 대상 스캔이 없습니다")
            return
        set_status(cur, engagement_id, "running")

    try:
        if target:
            # --- 블랙박스 외부표적: 스캔 없이 샌드박스 도구로 인가 표적 점검 ---
            with tx() as cur:
                cur.execute("SELECT status FROM engagement WHERE id=%s", (engagement_id,))
                if cur.fetchone()["status"] == "cancelled":
                    return
                result = _run_blackbox(cur, engagement, target)
                final = "awaiting_approval" if result["status"] == "awaiting_approval" else "succeeded"
                set_status(cur, engagement_id, final, result=result)
                audit.record(cur, actor="system:worker", action="engagement.finished",
                             subject={"engagement_id": engagement_id}, payload=result)
            log.info("engagement %s(blackbox) %s: findings=%s steps=%s", engagement_id,
                     result["status"], result["findings"], result["steps"])
            return

        with tx() as cur:
            cur.execute("SELECT status FROM engagement WHERE id=%s", (engagement_id,))
            if cur.fetchone()["status"] == "cancelled":   # 협조적 취소 지점
                return
            static_summary = agents.static_analyst(cur, engagement)
            verify_counts = _maybe_dynamic(cur, engagement)   # None 이면 정적만(동작 불변)
            if verify_counts is not None:
                static_summary["dynamic"] = verify_counts
            agents.reporter(cur, engagement, static_summary)
            set_status(cur, engagement_id, "succeeded",
                       result={"phase": 2 if verify_counts is not None else 1,
                               "findings": static_summary["findings_total"],
                               "entrypoint_resolved": static_summary["entrypoint_resolved"],
                               "dynamic": verify_counts})
            audit.record(cur, actor="system:worker", action="engagement.finished",
                         subject={"engagement_id": engagement_id},
                         payload={"status": "succeeded", "findings": static_summary["findings_total"],
                                  "dynamic": verify_counts})
        log.info("engagement %s 완료: finding %s건 dynamic=%s", engagement_id,
                 static_summary["findings_total"], verify_counts)
    except Exception as exc:
        log.exception("engagement %s 실패", engagement_id)
        with tx() as cur:
            set_status(cur, engagement_id, "failed", error=str(exc)[:2000])
