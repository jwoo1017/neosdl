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

from app import (agents, ai_providers, audit, blackbox_explain, engagement_bus, finding_verdict, pentest_routes,
                 pentester, queue, sandbox, sink_trace)
from app.config import settings
from app.db import tx
from app.engagements import set_status

log = logging.getLogger("worker.engagement")

_TERMINAL = ("succeeded", "failed", "cancelled")


def _trace_sinks(cur, scan_id: str | None, endpoints: list) -> None:
    """엔드포인트마다 핸들러→서비스→sink 를 소스로 추적해 `sink_profile` 을 붙인다(best-effort).

    공격 계획(위험 sink 우선)과 판정(발견 위치 = 실제 sink 줄)이 이걸 쓴다. 실패해도 점검은 계속.
    """
    if not scan_id or not endpoints:
        return
    try:
        reader = pentest_routes.file_reader(cur, scan_id)
        if reader is None:
            return
        sink_trace.trace_endpoints(reader, pentest_routes._module_paths(cur, scan_id), endpoints)
        n = sum(len((e.get("sink_profile") or {}).get("dangerous") or []) for e in endpoints)
        log.info("sink 추적: 엔드포인트 %d개, 위험 sink %d개", len(endpoints), n)
    except Exception:
        log.warning("sink 추적 실패 — 코드 근거 없이 진행", exc_info=True)


def _judge(outcome, endpoints: list | None) -> dict:
    """도구 증거 × 코드 증거로 발견을 판정한다 — LLM 단독 주장은 여기서 폐기(outcome.findings 제자리 교체)."""
    verdict = finding_verdict.reconcile(outcome.findings, endpoints or [], outcome.history)
    if verdict["discarded"]:
        log.info("근거 없는 LLM 발견 %d건 폐기: %s", len(verdict["discarded"]),
                 [d.get("title") for d in verdict["discarded"]][:5])
    outcome.findings[:] = verdict["findings"]
    return verdict


def _explain(cur, outcome, *, project_id: str, scan_id: str | None = None, safe: list | None = None) -> str:
    """발견에 쉬운 설명·수정법·소스(체인 스니펫)·검증된 패치를 붙이고 AI 요약을 돌려준다(best-effort)."""
    if outcome.status != "succeeded":
        return ""
    model = ai_providers.resolve_requested_model(None)

    def summary_generate(prompt: str) -> str:
        return ai_providers.generate(model, prompt, project_id=project_id, purpose="pentest_summary")

    def patch_generate(prompt: str) -> str:
        return ai_providers.generate(model, prompt, json_mode=True, project_id=project_id, purpose="pentest_fix")

    try:
        reader, paths = None, []
        if scan_id and outcome.findings:
            reader = pentest_routes.file_reader(cur, scan_id)
            paths = pentest_routes._module_paths(cur, scan_id) if reader else []
        blackbox_explain.attach_code(outcome.findings, reader=reader, module_paths=paths,
                                     entry_names=pentest_routes._ENTRY_NAMES)
        try:
            blackbox_explain.suggest_patches(outcome.findings, reader, patch_generate)
        except Exception:
            log.warning("수정 패치 생성 실패 — 결정적 고치는 법만", exc_info=True)
        return blackbox_explain.summarize(outcome.findings, outcome.history, summary_generate, safe)
    except Exception:
        log.warning("블랙박스 발견 설명/요약 실패 — 결정적 요약만", exc_info=True)
        return blackbox_explain.fallback_summary(outcome.findings, outcome.history, safe)


def _blackbox_report_body(target: str, outcome, *, title: str, endpoints: list | None = None,
                          summary: str = "", verdict: dict | None = None) -> dict:
    """pentester outcome → 리포트 아티팩트 body(markdown + 구조화). 외부/레포 블랙박스 공용.

    endpoints(소스 포함)를 주면 '점검 범위' 섹션으로 사원이 확인할 파일 범위를 특정한다.
    """
    lines = [f"# {title}", f"- 표적: {target}", f"- 스텝: {outcome.steps}",
             f"- 상태: {outcome.status}", ""]
    if summary:
        lines += ["## 요약", summary, ""]
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
    verdict = verdict or {}
    if outcome.findings:
        lines.append("\n## 발견")
        for f in outcome.findings:
            loc = f"  →  `{f['file']}:{f.get('line')}`" if f.get("file") else ""
            tag = " (코드상 의심)" if f.get("status") == "suspected" else ""
            lines.append(f"- [{f.get('severity','?')}]{tag} {f.get('label') or f.get('title','')}: "
                         f"{f.get('evidence','')}{loc}")
            if f.get("code_trace"):
                lines.append("  - 경로: " + " → ".join(
                    f"{h.get('name')} `{h.get('file')}:{h.get('line')}`" for h in f["code_trace"]))
    if verdict.get("safe"):
        lines.append("\n## 코드로 안전 확인")
        for sfe in verdict["safe"]:
            locs = ", ".join(f"`{x['file']}:{x['line']}`" for x in sfe["sinks"][:4])
            lines.append(f"- `{sfe['endpoint']}` — SQL 은 전부 바인딩/고정 문자열 ({locs})")
    if verdict.get("discarded"):
        lines.append("\n## 근거 없어 제외한 AI 주장")
        lines.append("도구 출력·코드 어디에도 근거가 없어 발견으로 올리지 않았습니다:")
        for d in verdict["discarded"][:10]:
            lines.append(f"- {d.get('title')}")
    if endpoints:
        lines.append("\n## 점검 범위 (엔드포인트 → 소스)")
        lines.append("사원이 확인할 파일 범위입니다 — 발견 유무와 무관하게 점검한 엔드포인트의 소스 위치:")
        for e in endpoints[:20]:
            src = f"{e['file']}:{e.get('line')}" if e.get("file") else "(소스 미상)"
            h = f" — {e['handler']}" if e.get("handler") else ""
            flag = " [인증필요]" if e.get("auth") else ""
            prof = e.get("sink_profile") or {}
            sk = ""
            if prof.get("sinks"):
                d = len(prof.get("dangerous") or [])
                sk = f" · sink {len(prof['sinks'])}개" + (f"(위험 {d})" if d else "(안전)")
            lines.append(f"- `{e['method']} {e['path']}`{flag}  →  `{src}`{h}{sk}")
    # 구조화 history — UI 가 마크다운 덤프 대신 스텝 카드(아이콘·상태·출력)로 렌더한다.
    hist = []
    for h in outcome.history:
        if "rejected" in h:
            hist.append({"command": h.get("command"), "rejected": h["rejected"]})
            continue
        r = h.get("result") or {}
        hist.append({"command": h.get("command"), "ok": r.get("ok"),
                     "exit_code": r.get("exit_code"), "timed_out": r.get("timed_out"),
                     "output": (r.get("output") or r.get("error") or "")[:1800]})
    return {"markdown": "\n".join(lines), "target": target, "status": outcome.status,
            "findings": outcome.findings, "steps": outcome.steps,
            "pending_command": outcome.pending_command, "endpoints": endpoints or [],
            "history": hist, "discarded": verdict.get("discarded") or [], "safe": verdict.get("safe") or []}


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
                  "timed_out": r.get("timed_out"), "output": (r.get("output") or r.get("error") or "")[:8000],
                  "reason": rec.get("reason")}
        status = "done" if r.get("ok") else "error"
    cur.execute(
        "INSERT INTO agent_action (subtask_id, engagement_id, seq, agent_role, tool, input, output, "
        "status, finished_at) VALUES (%s,%s,%s,'pentester','terminal',%s::jsonb,%s::jsonb,%s::action_status, now())",
        (subtask_id, engagement_id, seq, json.dumps({"command": cmd}), json.dumps(output), status))


def _step_event(seq: int, rec: dict) -> dict:
    """pentester 스텝 rec → 라이브 버스 이벤트(채팅/EngageTab 타임라인용). 출력은 잘라 싣는다."""
    if "rejected" in rec:
        return {"kind": "step", "seq": seq, "command": rec.get("command"), "rejected": rec["rejected"]}
    r = rec.get("result") or {}
    return {"kind": "step", "seq": seq, "command": rec.get("command"), "ok": r.get("ok"),
            "exit_code": r.get("exit_code"), "timed_out": r.get("timed_out"),
            "output": (r.get("output") or r.get("error") or "")[:800], "reason": rec.get("reason")}


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
        engagement_bus.notify_now(eid, _step_event(_seq[0], rec))   # 라이브 스트리밍(채팅/EngageTab)
        queue.heartbeat_current()   # 깊은 점검(30스텝)은 10분 초과 — 스텝마다 리스 갱신(이중 실행 방지)
        _seq[0] += 1

    try:
        outcome = pentester.run_pentester(
            cur, target=target, llm_generate=llm_generate, toolbox_exec=toolbox_exec,
            permission_mode=permission_mode, effective_role="manager", actor=actor,
            approved_commands=approved, resume_commands=resume_commands,
            prior_history=prior_history, prior_open_ports=prior_open_ports, on_step=persist,
            max_steps=settings().pentest_max_steps, sqlmap_flags=settings().pentest_sqlmap_flags,
            nuclei_flags=settings().pentest_nuclei_flags, ffuf_wordlist=settings().pentest_ffuf_wordlist)
    finally:
        try:
            runner.destroy(eid)   # 툴박스 컨테이너·네트워크 정리(실패해도 best-effort)
        except Exception:
            log.warning("툴박스 정리 실패", exc_info=True)

    verdict = _judge(outcome, [])   # 외부 표적은 소스가 없다 — 도구 관측만 발견, LLM 단독 주장은 폐기
    summary = _explain(cur, outcome, project_id=str(engagement["project_id"]))
    body = _blackbox_report_body(target, outcome, title="블랙박스 점검 리포트", summary=summary, verdict=verdict)
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
        # 상태 영속/재개: 이전 실행의 agent_action 을 복원해 이미 실행한 명령은 다시 안 돈다(_run_blackbox
        # 와 동형). 이게 없으면 승인(ask)마다 재부팅+전부 재실행하는 O(n²) 루프가 돈다. 표적은 매번
        # 새로 부팅되지만 같은 앱이라 이미 점검한 엔드포인트를 다시 때릴 필요가 없다.
        prior_history, prior_open_ports, prior_executed = _load_prior_state(cur, eid)
        approved = (engagement.get("params") or {}).get("approved_commands") or []
        resume_commands = [c for c in approved if c not in prior_executed]
        _seq = [len(prior_history)]

        def persist(rec):
            _add_agent_action(cur, subtask_id, eid, _seq[0], rec)
            engagement_bus.notify_now(eid, _step_event(_seq[0], rec))   # 라이브 스트리밍(채팅/EngageTab)
            queue.heartbeat_current()   # 깊은 점검(30스텝)은 10분 초과 — 스텝마다 리스 갱신(이중 실행 방지)
            _seq[0] += 1

        # 코드그래프 유도: 스캔한 소스에서 실제 엔드포인트를 뽑아 도구를 겨눈다(홈 크롤 대신).
        endpoints = pentest_routes.enumerate_endpoints(cur, str(engagement.get("scan_id") or ""))
        _trace_sinks(cur, str(engagement.get("scan_id") or "") or None, endpoints)   # 핸들러→sink 코드 경로
        if endpoints:
            log.info("engagement %s 유도 엔드포인트 %d개(예: %s)", eid, len(endpoints),
                     f"{endpoints[0]['method']} {endpoints[0]['path']}")
        outcome = pentester.run_pentester(
            cur, target=target, llm_generate=llm_generate, toolbox_exec=state["exec"],
            permission_mode=permission_mode, effective_role="manager", actor=actor,
            approved_commands=approved, resume_commands=resume_commands,
            prior_history=prior_history, prior_open_ports=prior_open_ports,
            trusted_target=True, on_step=persist, endpoints=endpoints,
            max_steps=settings().pentest_max_steps, sqlmap_flags=settings().pentest_sqlmap_flags,
            nuclei_flags=settings().pentest_nuclei_flags, ffuf_wordlist=settings().pentest_ffuf_wordlist)
    finally:
        try:
            runner.destroy(eid)   # 표적 컨테이너·툴박스·네트워크 정리(실패해도 best-effort)
        except Exception:
            log.warning("레포 블랙박스 정리 실패", exc_info=True)

    verdict = _judge(outcome, endpoints)   # 도구 × 코드 판정 — 위치는 실제 sink 줄, LLM 단독 주장 폐기
    summary = _explain(cur, outcome, project_id=str(engagement["project_id"]),
                       scan_id=str(engagement.get("scan_id") or "") or None,
                       safe=verdict["safe"])   # sink 사실 기반 설명·체인 스니펫·검증 패치·AI 요약
    body = _blackbox_report_body(target, outcome, title="레포 부팅 블랙박스 리포트", endpoints=endpoints,
                                 summary=summary, verdict=verdict)
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
