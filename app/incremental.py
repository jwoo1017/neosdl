"""증분 스캔 — parent 스캔 해석 + 변경 파일 계산 + 영향 심볼 집합(phase2 plan §12).

이번 범위는 비싼 LLM 단계(annotate / embed / risk.build)만 증분화한다. 구조
추출(parse/pdg/taint)은 매번 전체 실행하므로, 여기서 하는 일은 "이 스캔이
parent 대비 무엇을 바꿨나"를 계산해서 그 세 핸들러가 안 바뀐 부분을 parent
에서 copy-forward 하도록 돕는 것뿐이다.

전부 fail-safe: parent를 못 찾거나 GitHub compare가 실패하면 `changed_paths=None`
을 돌려주고, 호출부는 전체 분석으로 폴백한다(= 오늘 동작). 증분은 순수 최적화라
실패해도 결과는 항상 전체 스캔과 동일하다.
"""

from __future__ import annotations

import logging

log = logging.getLogger("app.incremental")

# parse.py LANG_BY_SUFFIX 와 동일해야 한다 — 이 확장자만 분석에 영향을 준다.
_CODE_SUFFIXES = (".java", ".py", ".ts", ".tsx", ".js", ".jsx")

# GitHub compare API 의 files[] 상한. 이 이상이면 diff가 잘리므로 전체 폴백.
_COMPARE_FILE_CAP = 300


def resolve_parent_scan(cur, repository_id: str, parser_version: str,
                         exclude_scan_id: str | None = None) -> dict | None:
    """copy-forward 대상 parent 스캔.

    같은 repo에서 완전히 처리된(risk_analyzed) 가장 최근 성공 스캔.
    parser_version이 다르면(분석 로직이 바뀜) 재사용 불가 → None.
    git 조상 관계는 확인하지 않는다 — GitHub compare가 status로 걸러준다.
    """
    cur.execute(
        """
        SELECT id, commit_sha, parser_version
          FROM scan
         WHERE repository_id = %s
           AND status = 'succeeded'
           AND parser_version = %s
           AND stats->>'risk_analyzed' = 'true'
           AND (%s::uuid IS NULL OR id <> %s::uuid)
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (repository_id, parser_version, exclude_scan_id, exclude_scan_id),
    )
    return cur.fetchone()


def compute_changed_paths(cur, repository_id: str, base_sha: str, head_sha: str) -> dict | None:
    """base_sha..head_sha 사이 변경 파일. GitHub compare API 사용.

    반환: {"changed": [path...], "removed": [path...]} — 코드 확장자만.
    실패/부적합(diverged, 파일 300개 초과, GitHub 아님, 예외)이면 None → 전체 폴백.
    """
    if base_sha == head_sha:
        return {"changed": [], "removed": []}

    try:
        from app.github_app import compare_commits
        from app.repo_queries import repo_github_info

        repo = repo_github_info(cur, repository_id)
        cmp = compare_commits(repo["github_installation_id"], repo["namespace"], repo["name"],
                              base_sha, head_sha)
    except Exception:
        log.warning("compare_commits 실패 — 전체 스캔으로 폴백 (repo=%s %s..%s)",
                    repository_id, base_sha[:8], head_sha[:8], exc_info=True)
        return None

    status = cmp.get("status")
    if status == "identical":
        return {"changed": [], "removed": []}
    if status in ("diverged", "behind"):
        # diverged: 공통 조상에서 갈라짐 / behind: 새 커밋이 parent 의 조상
        # (되감기) — 둘 다 "안 바뀐 파일"이 실제로는 다를 수 있어 전체 재분석.
        log.info("compare status=%s — 전체 스캔 (repo=%s)", status, repository_id)
        return None

    files = cmp.get("files") or []
    if len(files) >= _COMPARE_FILE_CAP:
        log.info("compare files=%d (>= %d) — 전체 스캔 (repo=%s)", len(files), _COMPARE_FILE_CAP, repository_id)
        return None

    changed: set[str] = set()
    removed: set[str] = set()
    for f in files:
        name = f.get("filename")
        prev = f.get("previous_filename")
        status = f.get("status")
        if not name or not _is_code(name):
            # rename 의 이전 경로가 코드면 removed 로는 잡아야 한다
            if prev and _is_code(prev):
                removed.add(prev)
            continue
        if status == "removed":
            removed.add(name)
        elif status == "renamed":
            # symbol_key 에 path 가 들어가므로 rename = (옛 키 제거) + (새 키 추가)
            if prev and _is_code(prev):
                removed.add(prev)
            changed.add(name)
        else:  # added | modified | changed | copied
            changed.add(name)

    return {"changed": sorted(changed), "removed": sorted(removed)}


def _is_code(path: str) -> bool:
    return path.endswith(_CODE_SUFFIXES)


def affected_symbols(cur, scan_id: str, changed_paths: list[str]) -> set[str]:
    """증분 재분석이 필요한 심볼 집합.

      변경 파일의 심볼
        ∪ 그 심볼들의 1-hop caller  (centrality/called_by 근거가 바뀔 수 있음)
        ∪ 변경 파일에 걸린 security_finding 의 양 끝 심볼

    나머지 심볼은 parent 의 classification 을 그대로 재사용하고 LLM 호출을 건너뛴다.
    """
    if not changed_paths:
        return set()

    cur.execute(
        "SELECT symbol_key FROM symbol WHERE scan_id = %s AND path = ANY(%s) AND kind IN ('function','method')",
        (scan_id, list(changed_paths)),
    )
    changed_syms = {r["symbol_key"] for r in cur.fetchall()}
    affected = set(changed_syms)

    if changed_syms:
        # 1-hop caller: symbol_edge 를 직접 조회(callers_within 은 재귀 — 여기선 depth 1 이면 충분).
        cur.execute(
            """
            SELECT DISTINCT src_symbol_key
              FROM symbol_edge
             WHERE scan_id = %s AND resolved AND edge_type = 'calls'
               AND dst_symbol_key = ANY(%s)
            """,
            (scan_id, list(changed_syms)),
        )
        affected |= {r["src_symbol_key"] for r in cur.fetchall()}

    # 변경 파일에 걸린 taint finding 의 양 끝 (findings 는 risk_build 가 ensure 로 채운 뒤 호출).
    try:
        cur.execute(
            """
            SELECT sf.sink_symbol_key, sf.source_symbol_key, sf.path
              FROM security_finding sf
              JOIN symbol ss ON ss.scan_id = sf.scan_id AND ss.symbol_key = sf.sink_symbol_key
             WHERE sf.scan_id = %s
               AND (ss.path = ANY(%s)
                    OR EXISTS (SELECT 1 FROM symbol s2
                                WHERE s2.scan_id = sf.scan_id
                                  AND s2.symbol_key = sf.source_symbol_key
                                  AND s2.path = ANY(%s)))
            """,
            (scan_id, list(changed_paths), list(changed_paths)),
        )
        for r in cur.fetchall():
            for k in (r["sink_symbol_key"], r["source_symbol_key"]):
                if k:
                    affected.add(k)
            for hop in (r["path"] or []):
                if isinstance(hop, dict) and hop.get("symbol_key"):
                    affected.add(hop["symbol_key"])
    except Exception:
        log.debug("security_finding 기반 affected 계산 실패(무시)", exc_info=True)

    return affected


def read_incremental_context(cur, scan_id: str) -> tuple[str | None, list[str] | None]:
    """핸들러 진입부에서: (parent_scan_id, changed_paths) 또는 (None, None) = 전체 분석."""
    cur.execute("SELECT parent_scan_id, stats FROM scan WHERE id = %s", (scan_id,))
    row = cur.fetchone()
    if row is None or not row["parent_scan_id"]:
        return None, None
    stats = row["stats"] or {}
    if not stats.get("incremental"):
        return None, None
    changed = stats.get("changed_paths")
    if changed is None:
        return None, None
    return str(row["parent_scan_id"]), list(changed)
