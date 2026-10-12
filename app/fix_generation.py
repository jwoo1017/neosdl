"""사내 LLM 기반 취약점 수정 코드 생성 — `worker/handlers/fix_generate.py`("fix.generate"
job)가 부른다.

`app/security_history.py::explain_risk_identity`와의 핵심 차이: 그건 DB에 이미
있는 생애주기/관측 데이터만으로 자연어 설명을 만들고 실제 소스 코드를 읽지
않는다. 여기는 실제로 수정 코드를 "작성"해야 하므로 sink 함수(및 cross-function
이면 source 변수가 발생한 함수)의 원문을 읽어 프롬프트에 넣는다.

스캔 파이프라인의 마지막 단계(risk.build)가 끝나면 스캔 worktree가 지워지므로
(worker/handlers/clone.py docstring 참고), 수정 생성 요청이 스캔 종료 한참
뒤에 올 수 있다는 전제 하에 `scan.commit_sha` 기준으로 worktree를 짧게 다시
만들었다가 끝나면 지운다 — 브랜치가 바뀌었거나 레포 캐시가 그 사이 다른
브랜치로 갱신됐어도 항상 그 스캔이 본 정확한 커밋을 읽는다.

자유 텍스트로 unified diff를 직접 생성시키지 않는다 — [start_line,end_line]
범위를 새 코드로 통째로 교체하는 스키마 제약(`llm_schemas.FindingFixSuggestion`)이
훨씬 안정적이다. diff 자체는 `difflib`로 서버가 계산한다.
"""

from __future__ import annotations

import difflib
import logging

from app import ai_providers, llm_schemas, repo_activity_bus
from app.config import settings
from app.db import tx
from worker.handlers.clone import create_scan_worktree, remove_scan_worktree

log = logging.getLogger("app.fix_generation")

_FIX_PROMPT = """다음은 정적 분석기(자체 SAST 엔진)가 발견한 보안 취약점 1건이다.
아래 소스 코드만 근거로, 이 취약점을 고치는 최소한의 수정 코드를 작성하라.

취약점 분류: {category} (심각도 {severity})
파일: {path}
Taint 경로(오염된 데이터가 sink까지 도달한 경유지): {path_hops}

{source_function_block}수정 대상 함수({sink_qualified_name}, {start_line}~{end_line}행):
```
{sink_code}
```

규칙:
- 함수의 동작(정상 입력에 대한 결과)은 그대로 유지하면서, 이 취약점 종류에
  맞는 표준적인 방어(예: 파라미터화 쿼리, 입력 검증/이스케이프, 화이트리스트
  검사 등)만 추가/수정하라.
- replacement_code는 위 코드 블록의 {start_line}~{end_line}행 전체를 대체할
  코드다 — 그 범위 밖의 줄은 건드리지 않는다는 전제로, 들여쓰기와 스타일을
  원본과 맞춰라.
- summary는 무엇을 왜 고쳤는지 한국어 1~2문장.
- 근거에 없는 API나 라이브러리를 지어내지 마라.
"""


def _project_ctx(cur, repository_id: str) -> tuple[str | None, str, str]:
    cur.execute(
        """
        SELECT p.id AS project_id, p.ci_model_provider, p.ci_model_name
          FROM project_repository pr JOIN project p ON p.id = pr.project_id
         WHERE pr.repository_id = %s LIMIT 1
        """,
        (repository_id,),
    )
    row = cur.fetchone() or {}
    project_id = str(row["project_id"]) if row.get("project_id") else None
    provider = row.get("ci_model_provider") or "gateway"
    model = row.get("ci_model_name") or settings().default_chat_model
    return project_id, provider, model


def _resolve_symbol(cur, scan_id: str, symbol_key: str) -> dict | None:
    cur.execute(
        "SELECT path, start_line, end_line, qualified_name FROM symbol WHERE scan_id=%s AND symbol_key=%s",
        (scan_id, symbol_key),
    )
    return cur.fetchone()


def _read_lines(root, path: str) -> list[str] | None:
    try:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return text.splitlines()


def generate_fix(finding_fix_id: int) -> None:
    with tx() as cur:
        cur.execute("SELECT * FROM finding_fix WHERE id=%s", (finding_fix_id,))
        fix_row = cur.fetchone()
        if fix_row is None:
            log.warning("finding_fix %s 없음 — 건너뜀", finding_fix_id)
            return
        cur.execute("SELECT * FROM security_finding WHERE id=%s", (fix_row["finding_id"],))
        finding = cur.fetchone()
        if finding is None:
            _mark_failed(cur, finding_fix_id, "security_finding not found")
            return
        cur.execute("SELECT commit_sha FROM scan WHERE id=%s", (str(finding["scan_id"]),))
        scan_row = cur.fetchone()
        cur.execute(
            "SELECT clone_url, default_branch, provider, namespace, name FROM repository WHERE id=%s",
            (str(finding["repository_id"]),),
        )
        repo_row = cur.fetchone()
        sink_symbol = _resolve_symbol(cur, str(finding["scan_id"]), finding["sink_symbol_key"])
        source_symbol = None
        if finding["source_symbol_key"] and finding["source_symbol_key"] != finding["sink_symbol_key"]:
            source_symbol = _resolve_symbol(cur, str(finding["scan_id"]), finding["source_symbol_key"])
        project_id, provider, model = _project_ctx(cur, str(finding["repository_id"]))

    if sink_symbol is None or scan_row is None or repo_row is None:
        with tx() as cur:
            _mark_failed(cur, finding_fix_id, "sink symbol/scan/repository 정보를 찾을 수 없음")
        return

    repository_id = str(finding["repository_id"])
    scan_id = str(finding["scan_id"])
    root = create_scan_worktree(repository_id, repo_row, scan_id, scan_row["commit_sha"])
    if root is None:
        with tx() as cur:
            _mark_failed(cur, finding_fix_id, "scan worktree 생성 실패 — 소스를 읽을 수 없음")
        return

    try:
        sink_lines = _read_lines(root, sink_symbol["path"])
        if sink_lines is None:
            with tx() as cur:
                _mark_failed(cur, finding_fix_id, f"파일을 읽을 수 없음: {sink_symbol['path']}")
            return

        sink_code = "\n".join(sink_lines[sink_symbol["start_line"] - 1:sink_symbol["end_line"]])
        source_block = ""
        if source_symbol is not None and source_symbol["path"] != sink_symbol["path"]:
            source_lines = _read_lines(root, source_symbol["path"])
            if source_lines:
                source_code = "\n".join(
                    source_lines[source_symbol["start_line"] - 1:source_symbol["end_line"]]
                )
                source_block = (
                    f"오염된 데이터가 발생한 함수({source_symbol['qualified_name']}, "
                    f"{source_symbol['path']}):\n```\n{source_code}\n```\n\n"
                )

        prompt = _FIX_PROMPT.format(
            category=finding["category"], severity=finding["severity"], path=sink_symbol["path"],
            path_hops=", ".join(str(h) for h in (finding["path"] or [])) or "(직접 도달)",
            source_function_block=source_block, sink_qualified_name=sink_symbol["qualified_name"],
            start_line=sink_symbol["start_line"], end_line=sink_symbol["end_line"], sink_code=sink_code,
        )

        raw = ai_providers.generate(provider, model, prompt,
                                     response_schema=llm_schemas.FindingFixSuggestion,
                                     project_id=project_id, purpose="fix_generation", scan_id=scan_id)
        suggestion = llm_schemas.FindingFixSuggestion.model_validate_json(raw)

        start = max(1, suggestion.start_line)
        end = min(len(sink_lines), suggestion.end_line)
        if start > end:
            with tx() as cur:
                _mark_failed(cur, finding_fix_id, f"모델이 반환한 줄 범위가 유효하지 않음: {start}~{end}")
            return
        original_snippet = "\n".join(sink_lines[start - 1:end])
        diff = "\n".join(difflib.unified_diff(
            original_snippet.splitlines(), suggestion.replacement_code.splitlines(),
            fromfile=f"a/{sink_symbol['path']}", tofile=f"b/{sink_symbol['path']}",
            lineterm="",
        ))

        with tx() as cur:
            cur.execute(
                """
                UPDATE finding_fix SET
                    status='ready', file_path=%s, start_line=%s, end_line=%s,
                    original_snippet=%s, replacement_code=%s, diff=%s, summary=%s,
                    provider=%s, model=%s, error=NULL, updated_at=now()
                WHERE id=%s
                """,
                (sink_symbol["path"], start, end, original_snippet, suggestion.replacement_code,
                 diff, suggestion.summary, provider, model, finding_fix_id),
            )
            repo_activity_bus.notify_repo_activity(
                cur, repository_id,
                {"type": "fix_ready", "finding_id": fix_row["finding_id"], "finding_fix_id": finding_fix_id},
            )
        log.info("finding_fix %s: 수정 생성 완료", finding_fix_id)
    except Exception as exc:
        log.warning("finding_fix %s: 수정 생성 실패", finding_fix_id, exc_info=True)
        with tx() as cur:
            _mark_failed(cur, finding_fix_id, f"{type(exc).__name__}: {exc}")
    finally:
        remove_scan_worktree(repository_id, scan_id)


def _mark_failed(cur, finding_fix_id: int, error: str) -> None:
    cur.execute(
        "UPDATE finding_fix SET status='failed', error=%s, updated_at=now() WHERE id=%s",
        (error[:2000], finding_fix_id),
    )
