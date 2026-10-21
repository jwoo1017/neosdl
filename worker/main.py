"""워커 루프.

한 프로세스가 한 번에 한 작업. 병렬이 필요하면 프로세스를 더 띄운다 —
SKIP LOCKED 가 중복 처리를 막아준다.
"""

import logging
import shutil
import signal
import time
from pathlib import Path

from app import analysis, queue
from app.config import settings
from app.db import tx
from worker.handlers import (
    annotate, clone, embed, fix_generate, git_history, parse, pdg_build, project, risk_build, scan_fetch,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("worker")

HANDLERS = {
    "repo.clone": clone.handle,
    "scan.fetch": scan_fetch.handle,
    "scan.parse": parse.handle,
    "graph.project": project.handle,
    "symbol.annotate": annotate.handle,
    "git.sync_history": git_history.handle,
    "symbol.embed": embed.handle,
    "pdg.build": pdg_build.handle,
    "risk.build": risk_build.handle,
    "fix.generate": fix_generate.handle,
}

_stop = False


def _on_signal(signum, _frame):
    global _stop
    log.info("signal %s — 현재 작업을 끝내고 종료한다", signum)
    _stop = True


def run() -> None:
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    worker = settings().worker_name
    log.info("worker %s 시작 · graph_backend=%s", worker, settings().graph_backend)
    idle = 0

    while not _stop:
        with tx() as cur:
            job = queue.dequeue(cur, worker, list(HANDLERS))

        if job is None:
            idle += 1
            # 죽은 워커가 물고 있던 작업 회수 — 약 1분마다
            if idle % 60 == 0:
                with tx() as cur:
                    cur.execute("SELECT job_reap()")
            # 파이프라인이 끝난(또는 오래된) 스캔 worktree 정리 — 약 5분마다
            if idle % 300 == 0:
                _sweep_stale_worktrees()
            time.sleep(1)
            continue

        idle = 0
        log.info("job %s %s 시작 (attempt %s)", job["id"], job["kind"], job["attempts"])
        _notify_stage(job)
        try:
            HANDLERS[job["kind"]](job["payload"])
        except Exception as exc:                    # noqa: BLE001
            log.exception("job %s 실패", job["id"])
            error = f"{type(exc).__name__}: {exc}"
            with tx() as cur:
                status = queue.fail(cur, job["id"], error)
            if status == "dead":
                _mark_dead(job, error)
        else:
            with tx() as cur:
                queue.finish(cur, job["id"])
            log.info("job %s 완료", job["id"])

    log.info("worker %s 종료", worker)


def _notify_stage(job: dict) -> None:
    """핵심 분석 단계 시작을 화면에 알린다(app/analysis.py). 알림 실패가 작업을 막으면 안 된다."""
    try:
        with tx() as cur:
            analysis.notify_stage(cur, job["kind"], job["payload"])
    except Exception:
        log.warning("job %s: scan_stage 알림 실패 — 무시", job["id"], exc_info=True)


def _mark_dead(job: dict, error: str) -> None:
    """핵심 분석 단계가 최종 실패하면 스캔에 실패를 남긴다 — 화면이 "분석 중"에 머물지 않게."""
    try:
        with tx() as cur:
            analysis.mark_stage_dead(cur, job["kind"], job["payload"], error)
    except Exception:
        log.exception("job %s: 분석 실패 기록 실패", job["id"])


def _sweep_stale_worktrees() -> None:
    """scans/<scan_id> 중 파이프라인이 끝났거나(risk_analyzed) 없어진 스캔의 것을 지운다.

    risk_build.handle 이 정상 종료 시 자기 worktree 를 지우지만, 실패/중단 시
    남을 수 있어 backstop 으로 둔다.
    """
    root = Path(settings().neosdl_workdir) / "scans"
    if not root.exists():
        return
    try:
        dirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return
    if not dirs:
        return
    ids = [p.name for p in dirs]
    try:
        with tx() as cur:
            cur.execute(
                """
                SELECT id::text AS id, repository_id::text AS repo,
                       (stats->>'risk_analyzed' = 'true') AS done
                  FROM scan WHERE id::text = ANY(%s)
                """,
                (ids,),
            )
            state = {r["id"]: r for r in cur.fetchall()}
    except Exception:
        log.debug("worktree 스윕: scan 조회 실패", exc_info=True)
        return

    for p in dirs:
        row = state.get(p.name)
        if row is None:  # 스캔이 사라짐
            shutil.rmtree(p, ignore_errors=True)
        elif row["done"]:  # 파이프라인 끝남
            log.info("stale worktree 정리: %s", p.name)
            clone.remove_scan_worktree(row["repo"], p.name)


if __name__ == "__main__":
    run()
