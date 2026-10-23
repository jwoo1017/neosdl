"""채팅 파이프라인 오프라인 평가 — tests/eval/golden_chat.yaml 기준.

실제 채팅과 같은 경로(`app.chat._run_pipeline_sync` → `orchestrator.synthesize_answer`)를
골든 질문마다 돌려 결정적 지표만 계산한다(LLM 채점 없음).

지표(케이스별 → 유형별/전체 집계)
- turn_error        파이프라인 예외 또는 답변 단계의 오류 문구(사용자가 "오류"를 본 턴)
- false_claim       must_not_contain 문구가 답변에 나온 턴 수(없는 취약점 단정 등)
- tool_hit          expect_tools 중 하나라도 실행(비어 있으면 "도구를 안 썼는가")
- evidence_recall   expect_paths 중 최종 근거(relevance 통과)에 들어간 비율
- answer_recall     expect_paths(basename 허용) + expect_terms 중 답변에 등장한 비율
- halluc_paths      답변이 언급한 파일 경로 중 스캔에 존재하지 않는 것의 수
- cited_lines / unsupported_lines
                    답변이 인용한 줄 번호 수 / 그 중 근거(read_code·심볼 범위)로 뒷받침 안 되는 수
- llm 호출별 prompt_tokens·error(용도별) — 컨텍스트 초과/절단 진단용

사용:
    python scripts/eval_chat.py --model local-qwen-coder --out eval_before.json
    python scripts/eval_chat.py --model local-qwen-coder --model claude-sonnet-5 --only lo-1,se-1
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from app import ai_providers, chat, orchestrator  # noqa: E402
from app.db import tx  # noqa: E402

_ERROR_MARKERS = ("오류가 발생했습니다", "답변을 생성하는 중 오류")
_PATH_RE = re.compile(r"[\w./-]*[\w-]+\.(?:jsx?|tsx?|py|java)\b")
_LINE_RE = re.compile(
    r"(?P<path>[\w./-]+\.(?:jsx?|tsx?|py|java))\s*[:#(]\s*(?:L|line\s*)?(?P<a>\d+)(?:\s*[-~]\s*(?P<b>\d+))?"
)


def _load_cases(path: Path, only: set[str] | None) -> tuple[str, list[dict]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = [c for c in doc["cases"] if not only or c["id"] in only]
    return doc["scan_id"], cases


def _scan_ctx(scan_id: str, project_id: str | None) -> tuple[dict, str, dict[str, int]]:
    with tx() as cur:
        cur.execute("SELECT repository_id FROM scan WHERE id=%s", (scan_id,))
        repository_id = str(cur.fetchone()["repository_id"])
        if project_id is None:
            cur.execute(
                "SELECT project_id FROM project_repository WHERE repository_id=%s ORDER BY attached_at DESC LIMIT 1",
                (repository_id,),
            )
            project_id = str(cur.fetchone()["project_id"])
        cur.execute("SELECT name, description FROM project WHERE id=%s", (project_id,))
        p = cur.fetchone()
        cur.execute("SELECT path, max(end_line) AS n FROM symbol WHERE scan_id=%s GROUP BY path", (scan_id,))
        file_lines = {r["path"]: r["n"] for r in cur.fetchall()}
    ctx = {"repository_id": repository_id, "scan_id": scan_id,
           "project_info": {"name": p["name"], "description": p["description"]}}
    return ctx, project_id, file_lines


def _walk_paths(obj, out: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and (k == "path" or k.endswith("_path")):
                out.add(v)
            else:
                _walk_paths(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_paths(v, out)


def _walk_ranges(obj, out: dict[str, list[tuple[int, int]]]) -> None:
    """근거 안의 (path, start_line, end_line) 범위 — 줄 번호 인용의 뒷받침 판정용."""
    if isinstance(obj, dict):
        p, a, b = obj.get("path"), obj.get("start_line"), obj.get("end_line")
        if isinstance(p, str) and isinstance(a, int) and isinstance(b, int):
            out[p].append((a, b))
        for v in obj.values():
            _walk_ranges(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_ranges(v, out)


def _resolve(cited: str, files) -> str | None:
    cited = cited.lstrip("./")
    for f in files:
        if f == cited or f.endswith("/" + cited):
            return f
    return None


def _score(case: dict, used: list[dict], evidence: dict, answer: str, file_lines: dict[str, int]) -> dict:
    ran = [u["name"] for u in used]
    expect_tools = case.get("expect_tools") or []
    tool_hit = bool(set(ran) & set(expect_tools)) if expect_tools else not ran

    ev_paths: set[str] = set()
    _walk_paths(evidence, ev_paths)
    expect_paths = case.get("expect_paths") or []
    evidence_recall = (sum(p in ev_paths for p in expect_paths) / len(expect_paths)) if expect_paths else None

    low = answer.lower()
    targets = [p.rsplit("/", 1)[-1] for p in expect_paths] + list(case.get("expect_terms") or [])
    answer_recall = (sum(t.lower() in low for t in targets) / len(targets)) if targets else None

    cited = set(_PATH_RE.findall(answer))
    halluc = sorted(c for c in cited if _resolve(c, file_lines) is None)

    ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    _walk_ranges(evidence, ranges)
    cited_lines = unsupported = 0
    for m in _LINE_RE.finditer(answer):
        cited_lines += 1
        f = _resolve(m["path"], file_lines)
        line = int(m["a"])
        if f is None or line > file_lines.get(f, 0) or not any(a <= line <= b for a, b in ranges.get(f, [])):
            unsupported += 1

    forbidden = [p for p in case.get("must_not_contain") or [] if p in answer]
    return {
        "tools": ran, "tool_hit": tool_hit,
        "evidence_recall": evidence_recall, "answer_recall": answer_recall,
        "halluc_paths": halluc, "cited_lines": cited_lines, "unsupported_lines": unsupported,
        "forbidden_hits": forbidden,
    }


def run_case(case: dict, ctx: dict, project_id: str, model: str, file_lines: dict[str, int]) -> dict:
    calls: list[dict] = []
    orig_log, orig_gen = ai_providers._log_call, ai_providers.generate

    def capture_log(_pid, purpose, mdl, _sid, *, latency_ms, success, error, usage=None):
        calls.append({"purpose": purpose, "model": mdl, "latency_ms": latency_ms, "success": success,
                      "error": (error or "")[:200] or None,
                      "prompt_tokens": (usage or {}).get("prompt_tokens"),
                      "completion_tokens": (usage or {}).get("completion_tokens")})

    def gen(model_, prompt, **kw):
        out = orig_gen(model_, prompt, **kw)
        if calls:
            calls[-1]["prompt_chars"] = len(prompt)
        return out

    ai_providers._log_call, ai_providers.generate = capture_log, gen
    started = time.monotonic()
    events: list[dict] = []
    try:
        pipeline = chat._run_pipeline_sync(case["question"], dict(ctx), model=model, project_id=project_id,
                                           on_step=events.append)
        answer = orchestrator.synthesize_answer(
            case["question"], pipeline["evidence"], model=model, project_id=project_id,
            scan_id=ctx["scan_id"], executed_tools=[u["name"] for u in pipeline["used"]],
            analysis_available=True,
        )
        crash = None
    except Exception as exc:  # 사용자가 "답변 생성 중 오류"를 보게 되는 경우
        pipeline, answer, crash = {"used": [], "evidence": {}}, "", f"{type(exc).__name__}: {exc}"[:300]
    finally:
        ai_providers._log_call, ai_providers.generate = orig_log, orig_gen
    elapsed = time.monotonic() - started

    scored = _score(case, pipeline["used"], pipeline["evidence"], answer, file_lines)
    turn_error = crash is not None or any(m in answer for m in _ERROR_MARKERS)
    plan = next((e.get("plan_steps") for e in events if e.get("type") == "plan" and e.get("plan_steps")), None)
    return {
        "id": case["id"], "type": case["type"], "model": model, "question": case["question"],
        "crash": crash, "turn_error": turn_error, "latency_s": round(elapsed, 1),
        "plan": plan, "params": [u.get("params") for u in pipeline["used"]],
        "llm_calls": calls, "answer": answer, **scored,
    }


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def summarize(results: list[dict]) -> dict:
    def agg(rs):
        calls = [c for r in rs for c in r["llm_calls"]]
        by_purpose = defaultdict(list)
        for c in calls:
            by_purpose[c["purpose"]].append(c)
        return {
            "n": len(rs),
            "turn_error_rate": _mean([float(r["turn_error"]) for r in rs]),
            "tool_hit_rate": _mean([float(r["tool_hit"]) for r in rs]),
            "evidence_recall": _mean([r["evidence_recall"] for r in rs]),
            "answer_recall": _mean([r["answer_recall"] for r in rs]),
            "halluc_paths_per_turn": _mean([len(r["halluc_paths"]) for r in rs]),
            "turns_with_halluc_path": _mean([float(bool(r["halluc_paths"])) for r in rs]),
            "false_claim_turns": sum(bool(r.get("forbidden_hits")) for r in rs),
            "cited_lines": sum(r["cited_lines"] for r in rs),
            "unsupported_lines": sum(r["unsupported_lines"] for r in rs),
            "latency_s_mean": _mean([r["latency_s"] for r in rs]),
            "llm_calls_per_turn": _mean([len(r["llm_calls"]) for r in rs]),
            "llm_error_rate": _mean([float(not c["success"]) for c in calls]),
            "prompt_tokens_max": {p: max((c["prompt_tokens"] or 0) for c in cs) for p, cs in by_purpose.items()},
        }

    out = {}
    for model in sorted({r["model"] for r in results}):
        rs = [r for r in results if r["model"] == model]
        out[model] = {"overall": agg(rs),
                      "by_type": {t: agg([r for r in rs if r["type"] == t])
                                  for t in sorted({r["type"] for r in rs})}}
    return out


def _print_table(summary: dict) -> None:
    cols = ["n", "turn_error_rate", "tool_hit_rate", "evidence_recall", "answer_recall",
            "turns_with_halluc_path", "false_claim_turns", "unsupported_lines", "latency_s_mean"]
    for model, s in summary.items():
        print(f"\n== {model} ==")
        print("type".ljust(10) + "".join(c[:14].rjust(15) for c in cols))
        for name, row in [("ALL", s["overall"]), *s["by_type"].items()]:
            print(name.ljust(10) + "".join(str(row[c]).rjust(15) for c in cols))
        print("prompt_tokens_max:", s["overall"]["prompt_tokens_max"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(ROOT / "tests/eval/golden_chat.yaml"))
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--project", default=None, help="project_id(생략 시 스캔 레포의 최근 프로젝트)")
    ap.add_argument("--only", default=None, help="쉼표로 구분한 case id")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    only = set(args.only.split(",")) if args.only else None
    scan_id, cases = _load_cases(Path(args.golden), only)
    ctx, project_id, file_lines = _scan_ctx(scan_id, args.project)

    results = []
    for model in args.model:
        for case in cases:
            r = run_case(case, ctx, project_id, model, file_lines)
            results.append(r)
            flag = "ERR" if r["turn_error"] else ("ok " if r["tool_hit"] else "miss")
            print(f"[{model}] {r['id']:6} {flag} tools={r['tools']} ev={r['evidence_recall']} "
                  f"ans={r['answer_recall']} halluc={r['halluc_paths']} {r['latency_s']}s", flush=True)

    summary = summarize(results)
    _print_table(summary)
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "results": results},
                                             ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n저장: {args.out}")


if __name__ == "__main__":
    main()
