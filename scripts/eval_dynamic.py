"""동적 검증 평가 — tests/eval/golden_dynamic.yaml 기준.

두 모드:
  (1) 기본(부팅 없음): 각 골든 finding 의 **엔트리포인트 도달률**을 잰다 — resolved/needs_auth/
      not_attempted + 신뢰도. "동적 검증이 애초에 시도 가능한가"의 게이팅 지표.
  (2) --base-url 제공: 실제 부팅된 앱에 무해 프로브를 보내 verdict 를 매기고, truth 와 대조해
      FP 제거율·false-negative 율을 낸다(서버 e2e). --iast 로 IAST 로그 파일 경로를 줄 수 있다.

사용:
    python scripts/eval_dynamic.py                       # 도달률만(오프라인)
    python scripts/eval_dynamic.py --base-url http://127.0.0.1:28xxx --iast /path/iast.log
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from app.db import tx  # noqa: E402
from app.dynamic import entrypoint, runner  # noqa: E402


def _load(path: Path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return doc["scan_id"], doc["cases"]


def _match_finding(cur, scan_id: str, ref: dict) -> dict | None:
    cur.execute(
        "SELECT id, category, severity, sink_symbol_key, sink_line, source_symbol_key, source_var "
        "FROM security_finding WHERE scan_id=%s AND category=%s AND sink_symbol_key LIKE %s LIMIT 1",
        (scan_id, ref["category"], f"%#{ref['sink']}(%"),
    )
    return cur.fetchone()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(ROOT / "tests/eval/golden_dynamic.yaml"))
    ap.add_argument("--base-url", default=None, help="부팅된 대상 앱(제공 시 실제 검증)")
    ap.add_argument("--rps", type=int, default=2)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    scan_id, cases = _load(Path(args.golden))
    app_client = runner.rate_limited_client(args.base_url, args.rps) if args.base_url else None

    rows, reach, verdicts, correct = [], Counter(), Counter(), 0
    with tx() as cur:
        for c in cases:
            f = _match_finding(cur, scan_id, c["finding_ref"])
            if f is None:
                rows.append({"id": c["id"], "status": "finding_not_found"}); continue
            ep = entrypoint.resolve(cur, scan_id, dict(f))
            reach_key = "resolved" if ep.ok and not ep.auth_required else (
                "needs_auth" if ep.ok and ep.auth_required else (ep.reason or "not_resolved"))
            reach[reach_key] += 1
            row = {"id": c["id"], "truth": c["truth"], "entrypoint": reach_key,
                   "route": f"{ep.method} {ep.path_template}" if ep.ok else None,
                   "param": ep.param_name, "confidence": ep.confidence}
            if app_client is not None:
                out = runner.verify_finding(cur, scan_id, dict(f), app_client=app_client)
                v = out.verdict.verdict
                verdicts[v] += 1
                row["verdict"] = v
                if v in ("confirmed", "false_positive"):
                    correct += int(v == c["truth"])
            rows.append(row)
            print(f"[{c['id']:26}] entry={reach_key:16} route={row.get('route')} "
                  f"verdict={row.get('verdict','-')} truth={c['truth']}", flush=True)

    labeled_fp = sum(1 for c in cases if c["truth"] == "false_positive")
    summary = {
        "cases": len(cases),
        "entrypoint": dict(reach),
        "attempt_rate": round(reach["resolved"] / len(cases), 3) if cases else 0,
        "verdicts": dict(verdicts),
    }
    if app_client is not None:
        fp_elim = verdicts.get("false_positive", 0)
        false_neg = sum(1 for r in rows if r.get("truth") == "false_positive" and r.get("verdict") == "confirmed")
        summary.update(fp_elimination=fp_elim, fp_elimination_rate=round(fp_elim / labeled_fp, 3) if labeled_fp else None,
                       false_negatives=false_neg)
    print("\n== summary ==")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
                                  encoding="utf-8")


if __name__ == "__main__":
    main()
