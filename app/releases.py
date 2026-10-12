"""GitHub Releases API 동기화 + 릴리즈 드릴다운.

Release는 "이 릴리즈 시점 이전 중 가장 최근 scan"을 간접 추정하지 않는다 —
release.commit_sha(태그가 가리키는 정확한 커밋)와 정확히 일치하는 scan만
release.scan_id로 연결한다. 그 커밋을 아직 스캔한 적 없으면 scan_id는
NULL이고, 드릴다운 응답이 "스캔하기" CTA에 필요한 정보(commit_sha)를
그대로 실어 보낸다 — 프론트가 POST /repositories/{id}/scans를 그 커밋으로
직접 호출하면, app/scans.py의 create_scan()이 역방향으로 scan_id를
채워준다(sql/013 참고).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.auth import assert_repository_access, get_current_member
from app.db import tx
from app.github_app import compare_commits, list_releases as gh_list_releases, resolve_commit_sha
from app.repo_queries import list_releases, repo_github_info
from app.scan_queries import ensure_security_findings

router = APIRouter(tags=["releases"])

_DOMAIN_LABELS = {
    "auth": "사용자 인증 흐름",
    "payment": "결제 모듈",
}


@router.post("/repositories/{repository_id}/releases/sync")
def sync_releases(repository_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "manager")
        repo = repo_github_info(cur, repository_id)

    gh_releases = gh_list_releases(repo["github_installation_id"], repo["namespace"], repo["name"])
    with tx() as cur:
        for gr in gh_releases:
            ref = gr["tag_name"]
            try:
                commit_sha = resolve_commit_sha(repo["github_installation_id"], repo["namespace"],
                                                 repo["name"], ref)
            except Exception:
                commit_sha = None  # 태그가 지워졌거나 접근 불가 — 커밋 해석 실패해도 릴리즈 자체는 기록

            cur.execute(
                """
                INSERT INTO release (repository_id, tag_name, name, is_draft, is_prerelease,
                                     target_commitish, commit_sha, html_url, published_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (repository_id, tag_name) DO UPDATE
                    SET name = EXCLUDED.name, is_draft = EXCLUDED.is_draft,
                        is_prerelease = EXCLUDED.is_prerelease,
                        target_commitish = EXCLUDED.target_commitish,
                        commit_sha = COALESCE(EXCLUDED.commit_sha, release.commit_sha),
                        html_url = EXCLUDED.html_url, published_at = EXCLUDED.published_at,
                        synced_at = now()
                RETURNING id
                """,
                (str(repository_id), gr["tag_name"], gr.get("name"), gr.get("draft", False),
                 gr.get("prerelease", False), gr.get("target_commitish"), commit_sha,
                 gr.get("html_url"), gr.get("published_at")),
            )
            release_row = cur.fetchone()

            if commit_sha:
                # 정확히 그 커밋을 이미 스캔한 적 있으면 바로 연결(추정 없음).
                cur.execute(
                    """
                    UPDATE release SET scan_id = s.id
                      FROM scan s
                     WHERE release.id = %s AND s.repository_id = %s AND s.commit_sha = %s
                       AND release.scan_id IS NULL
                    """,
                    (str(release_row["id"]), str(repository_id), commit_sha),
                )
    return {"synced": len(gh_releases)}


@router.get("/repositories/{repository_id}/releases")
def list_repository_releases(repository_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        return list_releases(cur, repository_id)


def _finding_counts(cur, scan_id) -> dict[str, dict]:
    cur.execute(
        "SELECT category, severity, count(*) AS n FROM security_finding WHERE scan_id=%s GROUP BY category, severity",
        (str(scan_id),),
    )
    return {r["category"]: {"severity": r["severity"], "count": r["n"]} for r in cur.fetchall()}


@router.get("/repositories/{repository_id}/releases/{release_id}")
def get_release_detail(repository_id: UUID, release_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        repo = repo_github_info(cur, repository_id)

        cur.execute("SELECT * FROM release WHERE id=%s AND repository_id=%s",
                     (str(release_id), str(repository_id)))
        release = cur.fetchone()
        if release is None:
            raise HTTPException(404, "release not found")

        cur.execute(
            """
            SELECT * FROM release WHERE repository_id=%s AND published_at < %s
             ORDER BY published_at DESC LIMIT 1
            """,
            (str(repository_id), release["published_at"]),
        )
        previous = cur.fetchone()

        base_ref = previous["tag_name"] if previous else None

        changes = {"commits": None, "files_changed": None}
        if base_ref:
            try:
                cmp = compare_commits(repo["github_installation_id"], repo["namespace"], repo["name"],
                                       base_ref, release["tag_name"])
                changes = {"commits": len(cmp.get("commits", [])),
                           "files_changed": len(cmp.get("files", [])),
                           "changed_paths": [f["filename"] for f in cmp.get("files", [])]}
            except Exception:
                changes = {"commits": None, "files_changed": None, "changed_paths": []}
        else:
            changes["changed_paths"] = []

        pr_count = None
        if previous and previous["published_at"] and release["published_at"]:
            cur.execute(
                """
                SELECT count(*) AS n FROM pull_request
                 WHERE repository_id=%s AND merged_at BETWEEN %s AND %s
                """,
                (str(repository_id), previous["published_at"], release["published_at"]),
            )
            pr_count = cur.fetchone()["n"]

        security = None
        business_impact: list[str] = []
        if release["scan_id"]:
            ensure_security_findings(cur, release["scan_id"])
            if previous and previous["scan_id"]:
                ensure_security_findings(cur, previous["scan_id"])
            current_counts = _finding_counts(cur, release["scan_id"])
            previous_counts = _finding_counts(cur, previous["scan_id"]) if previous and previous["scan_id"] else {}
            categories = set(current_counts) | set(previous_counts)
            security = [
                {
                    "category": cat,
                    "severity": (current_counts.get(cat) or previous_counts.get(cat))["severity"],
                    "count": current_counts.get(cat, {}).get("count", 0),
                    "delta": current_counts.get(cat, {}).get("count", 0) - previous_counts.get(cat, {}).get("count", 0),
                }
                for cat in categories
            ]

            changed_paths = changes.get("changed_paths") or []
            if changed_paths:
                cur.execute(
                    """
                    SELECT DISTINCT sem.domain
                      FROM symbol s
                      JOIN symbol_semantic sem ON sem.scan_id = s.scan_id AND sem.symbol_key = s.symbol_key
                     WHERE s.scan_id=%s AND s.kind='module' AND s.path = ANY(%s) AND sem.domain IS NOT NULL
                    """,
                    (str(release["scan_id"]), changed_paths),
                )
                business_impact = [
                    _DOMAIN_LABELS.get(r["domain"], f"{r['domain']} 모듈") + " 변경"
                    for r in cur.fetchall()
                ]

        return {
            "release": release,
            "previous_release_id": previous["id"] if previous else None,
            "changes": {"commits": changes["commits"], "files_changed": changes["files_changed"]},
            "pull_request_count": pr_count,
            "security": security,
            "business_impact": business_impact,
            "scan_id": release["scan_id"],
            "commit_sha": release["commit_sha"],
        }
