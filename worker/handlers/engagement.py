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

from app import agents, ai_providers, audit, pentest_routes, pentester, sandbox
from app.config import settings
from app.db import tx
from app.engagements import set_status

log = logging.getLogger("worker.engagement")

_TERMINAL = ("succeeded", "failed", "cancelled")


def _attach_source(findings: list, endpoints: list) -> list:
    """발견에 소스 위치를 붙인다 — title/evidence 에 엔드포인트 path 가 있으면 그 엔드포인트의 file:line.

    블랙박스 발견(sqlmap 등)은 URL 을 때린 결과라 소스 링크가 없다. 우린 코드그래프로 엔드포인트→
    핸들러 소스를 알므로, "어떤 파일 몇 번째 줄" 을 발견에 역매핑해 사원이 바로 고칠 수 있게 한다.
    """
    if not endpoints:
        return findings
    eps = sorted(endpoints, key=lambda e: len(e.get("path") or ""), reverse=True)  # 긴 path 우선
    for f in findings:
        hay = f"{f.get('title', '')} {f.get('evidence', '')}"
        for e in eps:
            p = e.get("path")
            if p and p in hay and e.get("file"):
                f["file"], f["line"] = e["file"], e.get("line")
                f["endpoint"] = f"{e['method']} {p}"
                break
    return findings


def _blackbox_report_body(target: str, outcome, *, title: str, endpoints: list | None = None) -> dict:
    """pentester outcome → 리포트 아티팩트 body(markdown + 구조화). 외부/레포 블랙박스 공용.

    endpoints(소스 포함)를 주면 '점검 범위' 섹션으로 사원이 확인할 파일 범위를 특정한다.
    """
    lines = [f"# {title}", f"- 표적: {target}", f"- 스텝: {outcome.steps}",
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
            loc = f"  →  `{f['file']}:{f.get('line')}`" if f.get("file") else ""
            lines.append(f"- [{f.get('severity','?')}] {f.get('title','')}: {f.get('evidence','')}{loc}")
    if endpoints:
        lines.append("\n## 점검 범위 (엔드포인트 → 소스)")
        lines.append("사원이 확인할 파일 범위입니다 — 발견 유무와 무관하게 점검한 엔드포인트의 소스 위치:")
        for e in endpoints[:20]:
            src = f"{e['file']}:{e.get('line')}" if e.get("file") else "(소스 미상)"
            h = f" — {e['handler']}" if e.get("handler") else ""
            flag = " [인증필요]" if e.get("auth") else ""
            lines.append(f"- `{e['method']} {e['path']}`{flag}  →  `{src}`{h}")
    return {"markdown": "\n".join(lines), "target": target, "status": outcome.status,
            "findings": outcome.findings, "steps": outcome.steps,
            "pending_command": outcome.pending_command, "endpoints": endpoints or []}


def _add_subtask(cur, task_id: str, engagement_id: str, role: str) -> str:
    cur.execute("INSERT INTO subtask (task_id, engagement_id, seq, assigned_role, status) "
                "VALUES (%s,%s,%s,%s::agent_role,'running') RETURNING id",
                (task_id, engagement_id, 1, role))
    return str(cur.fetchone()["id"])


def _add_agent_action(cur, subtask_id: str, engagement_id: str, seq: int, rec: dict) -> None:
    """pentester 1스텝을 agent_action 에 영속(상태 저장 — 재개 시 이걸로 복원)."""
    cmd = rec.get("command")
    if "rejected" in rec:
        output, status = {"rejected": rec["rejected"]}, "error"
    else:
        r = rec.get("result") or {}
        output = {"ok": r.get("ok"), "exit_code": r.get("exit_code"),
                  "timed_out": r.get("timed_out"), "output": (r.get("output") or r.get("error") or "")[:8000]}
        status = "done" if r.get("ok") else "error"
    cur.execute(
        "INSERT INTO agent_action (subtask_id, engagement_id, seq, agent_role, tool, input, output, "
        "status, finished_at) VALUES (%s,%s,%s,'pentester','terminal',%s::jsonb,%s::jsonb,%s::action_status, now())",
        (subtask_id, engagement_id, seq, json.dumps({"command": cmd}), json.dumps(output), status))


def _load_prior_state(cur, engagement_id: str):
    """이전 실행의 terminal agent_action 을 불러와 (history, open_ports, 실행된 명령 set) 복원."""
    cur.execute("SELECT input, output FROM agent_action WHERE engagement_id=%s AND tool='terminal' "
                "ORDER BY started_at, seq", (engagement_id,))
    history, open_ports, executed = [], [], set()
    for row in cur.fetchall():
        inp, outp = (row["input"] or {}), (row["output"] or {})
        cmd = inp.get("command")
        if not cmd:
            continue
        if outp.get("rejected"):
            history.append({"command": cmd, "rejected": outp["rejected"]})
            continue
        result = {"ok": outp.get("ok"), "exit_code": outp.get("exit_code"),
                  "timed_out": outp.get("timed_out"), "output": outp.get("output", "")}
        history.append({"command": cmd, "result": result})
        executed.add(cmd)
        if outp.get("ok"):
            pentester._merge_ports(open_ports, outp.get("output", ""))
    return history, open_ports, executed


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
        return runner.exec_toolbox(container, argv, timeout_s=settings().sandbox_exec_timeout_s)

    # 상태 영속/재개(PentAGI식): 이전 실행의 agent_action 을 불러와 이미 실행한 명령은 다시 안 돈다.
    # 승인된 명령(params.approved_commands)만 재개로 바로 실행하고, 매 스텝을 agent_action 에 저장한다.
    approved = (engagement.get("params") or {}).get("approved_commands") or []
    prior_history, prior_open_ports, prior_executed = _load_prior_state(cur, eid)
    resume_commands = [c for c in approved if c not in prior_executed]
    subtask_id = _add_subtask(cur, task_id, eid, "pentester")
    _seq = [len(prior_history)]

    def persist(rec):
        _add_agent_action(cur, subtask_id, eid, _seq[0], rec)
        _seq[0] += 1

    try:
        outcome = pentester.run_pentester(
            cur, target=target, llm_generate=llm_generate, toolbox_exec=toolbox_exec,
            permission_mode=permission_mode, effective_role="manager", actor=actor,
            approved_commands=approved, resume_commands=resume_commands,
            prior_history=prior_history, prior_open_ports=prior_open_ports, on_step=persist)
    finally:
        try:
            runner.destroy(eid)   # 툴박스 컨테이너·네트워크 정리(실패해도 best-effort)
        except Exception:
            log.warning("툴박스 정리 실패", exc_info=True)

    body = _blackbox_report_body(target, outcome, title="블랙박스 점검 리포트")
    agents._add_artifact(cur, eid, task_id, "report", "블랙박스 점검 리포트", body)
    agents._finish_task(cur, task_id, "succeeded" if outcome.status == "succeeded" else "blocked",
                        {"status": outcome.status, "findings": len(outcome.findings)})
    return {"kind": "blackbox", "status": outcome.status, "findings": len(outcome.findings),
            "steps": outcome.steps, "pending_command": outcome.pending_command}


def _run_repo_blackbox(cur, engagement: dict) -> dict:
    """레포 부팅 블랙박스 — 워크스페이스 레포를 샌드박스에 띄우고 그 앱을 표적으로 점검한다.

    외부표적(_run_blackbox)과 달리 표적을 우리가 만든다: assert_repo_allowed(워크스페이스 멤버십
    인가) → 레포(Dockerfile/compose) 부팅 → 웹 컨테이너 IP 가 표적 → 같은 네트워크 툴박스로 점검.
    표적이 self-boot(trusted)라 host allowlist·IP 조회가 필요 없고, 일회용이라 파괴 도구도 auto 에서
    자동 허용된다(ask 면 건건 승인). 부팅 실패는 blocked 리포트로 명시(조용히 실패 안 함).
    """
    actor = "system:worker"
    sandbox.assert_repo_allowed(cur, str(engagement["repository_id"]), actor=actor)  # 워크스페이스 인가

    eid = str(engagement["id"])
    permission_mode = engagement.get("permission_mode", "auto")
    task_id = agents._add_task(cur, eid, 10, "pentester", "레포 부팅 블랙박스 점검")

    from worker.sandbox import repo_target
    from worker.sandbox.dind import DindRunner
    runner = DindRunner()
    model = ai_providers.resolve_requested_model(None)

    def llm_generate(prompt: str) -> str:
        return ai_providers.generate(model, prompt, json_mode=True, system=pentester.SYSTEM,
                                     project_id=str(engagement["project_id"]), purpose="pentest_step")

    # 툴박스는 부팅이 만든 engagement 네트워크에 올라가야 한다(표적 앱과 같은 네트워크). 부팅의
    # readiness 체크에 쓴 그 툴박스를 펜테스터가 그대로 재사용한다(컨테이너 1개).
    state: dict = {}

    def exec_toolbox_factory(net):
        tb = runner.start_toolbox(eid, network=net)

        def _exec(argv):
            return runner.exec_toolbox(tb, argv, timeout_s=settings().sandbox_exec_timeout_s)
        state["exec"] = _exec
        return _exec

    # 부팅 레시피(모달에서 사람이 확인/수정) — 있으면 결정적 부팅, 없으면 자동 감지 폴백.
    recipe = (engagement.get("params") or {}).get("boot_recipe") or None

    booted = None
    try:
        booted, boot_reason = repo_target.boot_repo_target(engagement, runner, exec_toolbox_factory,
                                                           recipe=recipe)
        if booted is None or "exec" not in state:
            reason = boot_reason or ("no_toolbox" if "exec" not in state else "unknown")
            md = (
                "# 레포 부팅 블랙박스 — 부팅 실패\n\n"
                "표적 앱을 **띄우지 못했습니다.** 아래 사유·컨테이너 로그를 보고 레시피"
                "(베이스 이미지·시작 명령·포트·env·Dockerfile)를 고쳐 다시 실행하세요.\n\n"
                "## 사유 / 컨테이너 진단\n```\n" + str(reason)[:6000] + "\n```\n\n"
                "### 흔한 원인\n"
                "- 앱 `status=exited` + 로그에 `ECONNREFUSED`/`getaddrinfo <db>`/`Access denied` → "
                "DB 연결 실패(기동 지연·크리덴셜). 잠시 후 재실행하거나 env 의 `DB_*`/`DB_HOST`(=DB 서비스명) 확인.\n"
                "- 앱 `status=running`인데 미응답 → 포트 불일치 또는 `127.0.0.1` 바인드. `app_port`·시작 명령 확인.\n"
                "- DB 컨테이너 `status=exited` → DB 자체가 못 뜸(env·디스크)."
            )
            agents._add_artifact(cur, eid, task_id, "report", "레포 부팅 블랙박스 리포트",
                                 {"markdown": md, "status": "boot_failed", "reason": reason,
                                  "findings": [], "steps": 0})
            agents._finish_task(cur, task_id, "blocked", {"status": "boot_failed", "reason": reason})
            return {"kind": "repo_blackbox", "status": "failed", "findings": 0, "steps": 0,
                    "pending_command": None, "reason": reason}

        target = booted.web_ip
        subtask_id = _add_subtask(cur, task_id, eid, "pentester")
        _seq = [0]

        def persist(rec):
            _add_agent_action(cur, subtask_id, eid, _seq[0], rec)
            _seq[0] += 1

        approved = (engagement.get("params") or {}).get("approved_commands") or []
        # 코드그래프 유도: 스캔한 소스에서 실제 엔드포인트를 뽑아 도구를 겨눈다(홈 크롤 대신).
        endpoints = pentest_routes.enumerate_endpoints(cur, str(engagement.get("scan_id") or ""))
        if endpoints:
            log.info("engagement %s 유도 엔드포인트 %d개(예: %s)", eid, len(endpoints),
                     f"{endpoints[0]['method']} {endpoints[0]['path']}")
        outcome = pentester.run_pentester(
            cur, target=target, llm_generate=llm_generate, toolbox_exec=state["exec"],
            permission_mode=permission_mode, effective_role="manager", actor=actor,
            approved_commands=approved, trusted_target=True, on_step=persist, endpoints=endpoints)
    finally:
        try:
            runner.destroy(eid)   # 표적 컨테이너·툴박스·네트워크 정리(실패해도 best-effort)
        except Exception:
            log.warning("레포 블랙박스 정리 실패", exc_info=True)

    _attach_source(outcome.findings, endpoints)   # 발견 → 어떤 파일 몇 번째 줄
    body = _blackbox_report_body(target, outcome, title="레포 부팅 블랙박스 리포트", endpoints=endpoints)
    body["web_port"] = booted.web_port
    agents._add_artifact(cur, eid, task_id, "report", "레포 부팅 블랙박스 리포트", body)
    agents._finish_task(cur, task_id, "succeeded" if outcome.status == "succeeded" else "blocked",
                        {"status": outcome.status, "findings": len(outcome.findings)})
    return {"kind": "repo_blackbox", "status": outcome.status, "findings": len(outcome.findings),
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
        kind = params.get("engagement_kind")
        if kind == "repo_blackbox" and not row["scan_id"]:
            # 레포 부팅 블랙박스는 부팅할 소스 스냅샷(스캔)이 필요하다.
            set_status(cur, engagement_id, "failed", error="부팅할 소스 스냅샷(스캔)이 없습니다")
            return
        if not target and kind != "repo_blackbox" and not row["scan_id"]:
            # 화이트박스인데 스캔이 없다 — 검증할 정적 스냅샷이 없으므로 실패.
            set_status(cur, engagement_id, "failed", error="검증 대상 스캔이 없습니다")
            return
        set_status(cur, engagement_id, "running")

    try:
        if kind == "repo_blackbox":
            # --- 레포 부팅 블랙박스: 워크스페이스 레포를 띄워 그 앱을 표적으로 점검 ---
            with tx() as cur:
                cur.execute("SELECT status FROM engagement WHERE id=%s", (engagement_id,))
                if cur.fetchone()["status"] == "cancelled":
                    return
                result = _run_repo_blackbox(cur, engagement)
                status = result["status"]
                final = status if status in ("awaiting_approval", "failed") else "succeeded"
                set_status(cur, engagement_id, final, result=result)
                audit.record(cur, actor="system:worker", action="engagement.finished",
                             subject={"engagement_id": engagement_id}, payload=result)
            log.info("engagement %s(repo_blackbox) %s: findings=%s steps=%s", engagement_id,
                     result["status"], result["findings"], result["steps"])
            return

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
