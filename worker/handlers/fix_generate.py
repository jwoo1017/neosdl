"""fix.generate — `app/fix_generation.py::generate_fix`의 얇은 job 래퍼.

git worktree 체크아웃 + LLM 호출이 블로킹이라 `POST /findings/{id}/generate-fix`
(app/finding_fix_api.py)는 job만 큐에 넣고 202를 돌려준다 — 실제 작업은
risk.build 등 다른 파이프라인 단계와 동일하게 워커 프로세스에서 돈다.
"""

from __future__ import annotations

from app.fix_generation import generate_fix


def handle(payload: dict) -> None:
    generate_fix(payload["finding_fix_id"])
