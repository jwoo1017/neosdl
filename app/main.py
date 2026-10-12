"""NeoSDL API — WBS 1.1 Repository 연동 및 코드 스캔 API + 계정/협업 모델.

등록은 installation(개인 소유) 범위, 조회/스캔 실행은 project(workspace
소속, project_repository로 attach된 레포) 범위로 스코프된다.
"""

import base64
import json
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import (
    ask_stream, audit, chat, chat_bus, finding_fix_api, github_oauth, installations, projects, queue,
    releases, repo_activity, repo_activity_bus, risk_api, security_history, teamspaces, workspaces,
)
from app.auth import (
    assert_installation_owned,
    assert_project_role,
    assert_repository_access,
    assert_scan_access,
    get_current_member,
)
from app.config import settings
from app.db import tx
from app.github_app import (
    get_commit_files,
    get_latest_commit_sha,
    get_repo_contents,
    get_repository_languages,
    installation_token,
    list_pull_requests as gh_list_pull_requests,
    list_recent_commits,
)
from app.graph import make_graph_store
from app.language_colors import color_for
from app.repo_queries import list_commits, list_pull_requests, list_repo_branches, repo_github_info
from app.scan_queries import domain_overview, ensure_security_findings, run_dataflow, search_symbols
from app.scans import create_scan
from worker.handlers.clone import workdir_for

app = FastAPI(title="NeoSDL API", version="0.1.0")

# frontend/ 는 별도 Vite dev 서버(5173)에서 뜨고 이 API를 직접 호출한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,  # 세션 쿠키가 크로스 포트로 오가려면 필수
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(github_oauth.router)
app.include_router(installations.router)
app.include_router(workspaces.router)
app.include_router(teamspaces.router)
app.include_router(projects.router)
app.include_router(releases.router)
app.include_router(risk_api.router)
app.include_router(security_history.router)
app.include_router(chat.router)
app.include_router(repo_activity.router)
app.include_router(finding_fix_api.router)


@app.on_event("startup")
async def _start_chat_bus() -> None:
    await chat_bus.bus.start()


@app.on_event("shutdown")
async def _stop_chat_bus() -> None:
    await chat_bus.bus.stop()


@app.on_event("startup")
async def _start_repo_activity_bus() -> None:
    await repo_activity_bus.bus.start()


@app.on_event("shutdown")
async def _stop_repo_activity_bus() -> None:
    await repo_activity_bus.bus.stop()


# ---------------------------------------------------------------- models
class RepositoryIn(BaseModel):
    installation_id: UUID
    provider: Literal["gitlab", "github"] | None = None
    remote_id: str
    namespace: str
    name: str
    clone_url: str
    default_branch: str = "main"


class ScanIn(BaseModel):
    # 생략하면 서버가 default_branch 최신 커밋을 GitHub API로 자동 조회한다.
    commit_sha: str | None = Field(default=None, min_length=40, max_length=40)
    ref: str | None = None
    trigger: Literal["manual", "webhook", "schedule", "backfill"] = "manual"


class RepositoryWebhookIn(BaseModel):
    webhook_ci_enabled: bool
    # 마스코트 사이드바의 "PR 생성" 액션 — GitHub App 자체의 Contents/Pull
    # requests write 권한 재승인과는 별개의, 저장소별 2차 동의 게이트(sql/026).
    # 생략하면 기존 값을 유지한다(웹훅 옵트인 토글만 하는 기존 호출을 깨지 않기 위해).
    pr_fix_enabled: bool | None = None


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # 게이트웨이 모델 별칭(선택). 없거나 서빙되지 않는 값이면 프로젝트/전역 기본으로 폴백.
    model: str | None = Field(default=None, max_length=100)


# ---------------------------------------------------------------- routes
@app.get("/config")
def public_config():
    """프론트엔드가 'GitHub App 설치하기' 링크를 만드는 데 필요한 공개 설정."""
    return {"github_app_slug": settings().github_app_slug}


# 게이트웨이 별칭 → 화면 표시 메타데이터. litellm/config.yaml 의 model_name 과 맞춘다.
# 여기에 없는 별칭이 게이트웨이에 있으면 provider "other" / 별칭 그대로 label 로 노출된다.
_MODEL_CATALOG: list[dict] = [
    {"id": "claude-opus-5", "label": "Claude Opus 5", "provider": "anthropic", "group": "가장 어려운 작업에 적합", "starred": True},
    {"id": "gpt-5.1", "label": "GPT-5.1", "provider": "openai", "group": "가장 어려운 작업에 적합"},
    {"id": "grok-4", "label": "Grok 4", "provider": "xai", "group": "가장 어려운 작업에 적합"},
    {"id": "deepseek-reasoner", "label": "DeepSeek Reasoner", "provider": "deepseek", "group": "가장 어려운 작업에 적합"},
    {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "provider": "anthropic", "group": "Claude"},
    {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5", "provider": "anthropic", "group": "Claude"},
    {"id": "claude-fable-5-1", "label": "Claude Fable 5.1", "provider": "anthropic", "group": "Claude"},
    {"id": "gpt-5.1-mini", "label": "GPT-5.1 mini", "provider": "openai", "group": "GPT"},
    {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "provider": "gemini", "group": "Gemini", "starred": True},
    {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "provider": "gemini", "group": "Gemini"},
    {"id": "deepseek-chat", "label": "DeepSeek Chat", "provider": "deepseek", "group": "그 외"},
    {"id": "mistral-large", "label": "Mistral Large", "provider": "mistral", "group": "그 외"},
    {"id": "kimi-k2", "label": "Kimi K2", "provider": "kimi", "group": "그 외"},
    {"id": "llama-3.3-70b", "label": "Llama 3.3 70B", "provider": "meta", "group": "그 외"},
    {"id": "local-qwen-coder", "label": "Qwen2.5 Coder (로컬)", "provider": "local", "group": "로컬"},
]


@app.get("/models")
def list_models(member: dict = Depends(get_current_member)):
    """채팅 드롭다운용 모델 목록. `available` 은 게이트웨이가 지금 서빙 중인지 여부

    (프로바이더 키가 채워졌는지) — 프론트는 available=False 를 비활성 항목으로 보여준다.
    게이트웨이 조회 실패 시 전부 available=True 로 두어 사용을 막지 않는다(fail-open).
    """
    from app import ai_providers

    live = set(ai_providers.available_models())
    catalog_ids = {m["id"] for m in _MODEL_CATALOG}
    rows = [{**m, "available": (not live) or (m["id"] in live)} for m in _MODEL_CATALOG]
    # 카탈로그에 없지만 게이트웨이가 서빙하는 별칭도 노출(직접 config.yaml 만 편집한 경우).
    for extra in sorted(live - catalog_ids):
        if extra in ("auto", "local-embed") or extra.startswith("local-embed"):
            continue
        rows.append({"id": extra, "label": extra, "provider": "other", "group": "그 외", "available": True})
    return {"default": settings().default_chat_model, "models": rows}


@app.get("/healthz")
def healthz():
    with tx() as cur:
        cur.execute("SELECT 1 AS ok")
        cur.fetchone()
    return {"ok": True, "graph_backend": settings().graph_backend}


@app.post("/repositories", status_code=201)
def register_repository(body: RepositoryIn, member: dict = Depends(get_current_member)):
    """installation은 개인 소유라 등록엔 project 권한이 필요 없다 — 소유권만 확인한다.

    프로젝트에서 보이게 하려면 별도로 POST /projects/{id}/repositories 로 붙여야 한다.
    """
    provider = body.provider or settings().scm_provider
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_installation_owned(cur, body.installation_id, member["id"])

        cur.execute(
            """
            INSERT INTO repository (provider, remote_id, namespace, name,
                                    clone_url, default_branch, installation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (provider, remote_id) DO UPDATE
                SET name = EXCLUDED.name,
                    clone_url = EXCLUDED.clone_url,
                    default_branch = EXCLUDED.default_branch,
                    installation_id = EXCLUDED.installation_id,
                    updated_at = now()
            RETURNING id, provider, namespace, name, default_branch
            """,
            (provider, body.remote_id, body.namespace, body.name,
             body.clone_url, body.default_branch, str(body.installation_id)),
        )
        repo = cur.fetchone()

        # 등록과 기록과 잡 적재가 한 트랜잭션. 셋 중 하나만 남는 상태가 없다.
        audit.record(cur, actor=actor, action="repository.register",
                     subject={"repository_id": str(repo["id"])},
                     payload={"provider": provider, "remote_id": body.remote_id,
                              "member_id": member["id"]})
        queue.enqueue(cur, "repo.clone",
                      {"repository_id": str(repo["id"])},
                      priority=50, dedupe_key=f"clone:{repo['id']}")
        # git 히스토리는 스캔(불변 스냅샷)이 아니라 레포 시간축에 속하는 정보라
        # 스캔 파이프라인과 무관하게 등록 즉시 시작한다.
        queue.enqueue(cur, "git.sync_history",
                      {"repository_id": str(repo["id"])},
                      priority=150, dedupe_key=f"history:{repo['id']}")
    return repo


@app.patch("/repositories/{repository_id}")
def update_repository_webhook(repository_id: UUID, body: RepositoryWebhookIn,
                               member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "manager")
        if body.pr_fix_enabled is None:
            cur.execute(
                "UPDATE repository SET webhook_ci_enabled=%s, updated_at=now() WHERE id=%s "
                "RETURNING id, pr_fix_enabled",
                (body.webhook_ci_enabled, str(repository_id)),
            )
        else:
            cur.execute(
                "UPDATE repository SET webhook_ci_enabled=%s, pr_fix_enabled=%s, updated_at=now() "
                "WHERE id=%s RETURNING id, pr_fix_enabled",
                (body.webhook_ci_enabled, body.pr_fix_enabled, str(repository_id)),
            )
        row = cur.fetchone()
        audit.record(cur, actor=actor, action="repository.webhook_ci_toggle",
                     subject={"repository_id": str(repository_id)},
                     payload={"webhook_ci_enabled": body.webhook_ci_enabled,
                              "pr_fix_enabled": row["pr_fix_enabled"]})
    return {"id": str(repository_id), "webhook_ci_enabled": body.webhook_ci_enabled,
            "pr_fix_enabled": row["pr_fix_enabled"]}


@app.get("/repositories/{repository_id}/commits")
def repository_commits(repository_id: UUID, branch: str | None = None, limit: int = 100,
                        member: dict = Depends(get_current_member)):
    """git.sync_history 가 채운 최근 커밋 목록(기본 브랜치만, 최대 100개).

    `branch`가 없거나 저장소 기본 브랜치와 같으면 로컬 테이블을 그대로 쓴다
    (빠름). 다른 브랜치를 요청하면 그 브랜치는 동기화 대상이 아니므로 GitHub
    API로 즉석 조회한다(영속화하지 않음 — 스코프 최소화).
    """
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        cur.execute("SELECT default_branch FROM repository WHERE id=%s", (str(repository_id),))
        default_branch = cur.fetchone()["default_branch"]

        if branch and branch != default_branch:
            repo = repo_github_info(cur, repository_id)
            _, commits = list_recent_commits(repo["github_installation_id"], repo["namespace"],
                                              repo["name"], limit=limit, sha=branch)
            return [
                {
                    "commit_sha": c["sha"],
                    "author_name": (c.get("commit") or {}).get("author", {}).get("name"),
                    "author_email": (c.get("commit") or {}).get("author", {}).get("email"),
                    "committed_at": (c.get("commit") or {}).get("author", {}).get("date"),
                    "message": (c.get("commit") or {}).get("message"),
                }
                for c in commits
            ]

        return list_commits(cur, repository_id, limit=limit)


@app.get("/repositories/{repository_id}/commits/{sha}/files")
def repository_commit_files(repository_id: UUID, sha: str, member: dict = Depends(get_current_member)):
    """commit_file에 이미 있으면 그대로, 없으면(다른 브랜치 커밋 등) GitHub에서 라이브 조회."""
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        cur.execute(
            """
            SELECT cf.path, cf.change_type, cf.additions, cf.deletions
              FROM commit_file cf
              JOIN git_commit gc ON gc.id = cf.commit_id
             WHERE gc.repository_id=%s AND gc.commit_sha=%s
            """,
            (str(repository_id), sha),
        )
        rows = cur.fetchall()
        if rows:
            return rows
        repo = repo_github_info(cur, repository_id)

    token = installation_token(repo["github_installation_id"])
    files = get_commit_files(token, repo["namespace"], repo["name"], sha)
    return [
        {"path": f["filename"], "change_type": f["status"],
         "additions": f.get("additions", 0), "deletions": f.get("deletions", 0)}
        for f in files
    ]


@app.get("/repositories/{repository_id}/branches")
def repository_branches(repository_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        repo = repo_github_info(cur, repository_id)
    return list_repo_branches(repo)


@app.get("/repositories/{repository_id}/tree")
def repository_tree(repository_id: UUID, path: str = "", ref: str | None = None,
                     member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        repo = repo_github_info(cur, repository_id)
    entries = get_repo_contents(repo["github_installation_id"], repo["namespace"], repo["name"],
                                 path=path, ref=ref)
    if isinstance(entries, dict):
        # 경로가 디렉터리가 아니라 파일을 직접 가리킨 경우 — 단일 항목 리스트로 통일.
        entries = [entries]
    return [
        {"name": e["name"], "path": e["path"], "type": "dir" if e["type"] == "dir" else "file",
         "size": e.get("size", 0)}
        for e in entries
    ]


@app.get("/repositories/{repository_id}/contents")
def repository_file_contents(repository_id: UUID, path: str, ref: str | None = None,
                              member: dict = Depends(get_current_member)):
    """GitHub API로 임의 브랜치/커밋 시점의 파일 원문을 바로 열람(스캔과 무관).

    기존 `/scans/{id}/file`은 그 스캔이 인덱싱한 파일만, 로컬 클론 디스크
    기준으로 보여준다 — 이건 그와 별개로 Repository > Files 탭에서 아무
    브랜치나 골라 지금 GitHub에 있는 내용을 보는 용도다.
    """
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        repo = repo_github_info(cur, repository_id)
    entry = get_repo_contents(repo["github_installation_id"], repo["namespace"], repo["name"],
                               path=path, ref=ref)
    if isinstance(entry, list) or entry.get("type") != "file":
        raise HTTPException(400, "path is not a file")
    content = base64.b64decode(entry["content"]).decode("utf-8", errors="replace")
    return {"path": path, "content": content, "size": entry.get("size", 0)}


@app.get("/repositories/{repository_id}/pull-requests")
def repository_pull_requests(repository_id: UUID, state: str | None = None,
                              member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        return list_pull_requests(cur, repository_id, state=state)


@app.post("/repositories/{repository_id}/pull-requests/sync")
def sync_pull_requests(repository_id: UUID, member: dict = Depends(get_current_member)):
    """웹훅이 못 잡은 과거 PR을 GitHub에서 백필 — 새 레포를 붙였을 때 한 번 눌러주는 용도."""
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "manager")
        repo = repo_github_info(cur, repository_id)

    prs = gh_list_pull_requests(repo["github_installation_id"], repo["namespace"], repo["name"])
    with tx() as cur:
        for pr in prs:
            cur.execute(
                """
                INSERT INTO pull_request (repository_id, number, title, state, author_login,
                                          base_ref, head_ref, head_sha, merged, html_url,
                                          opened_at, closed_at, merged_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (repository_id, number) DO UPDATE
                    SET title = EXCLUDED.title, state = EXCLUDED.state,
                        merged = EXCLUDED.merged, closed_at = EXCLUDED.closed_at,
                        merged_at = EXCLUDED.merged_at, updated_at = now()
                """,
                (str(repository_id), pr["number"], pr["title"], pr["state"],
                 (pr.get("user") or {}).get("login"), pr["base"]["ref"], pr["head"]["ref"],
                 pr["head"]["sha"], bool(pr.get("merged_at")), pr.get("html_url"),
                 pr.get("created_at"), pr.get("closed_at"), pr.get("merged_at")),
            )
    return {"synced": len(prs)}


@app.get("/repositories/{repository_id}/languages")
def repository_languages(repository_id: UUID, member: dict = Depends(get_current_member)):
    """GitHub Repository Languages API(바이트 단위) 를 그대로 비율로 환산해 돌려준다."""
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        cur.execute(
            """
            SELECT r.namespace, r.name, i.github_installation_id
              FROM repository r JOIN installation i ON i.id = r.installation_id
             WHERE r.id=%s
            """,
            (str(repository_id),),
        )
        repo = cur.fetchone()
        if repo is None:
            raise HTTPException(404, "repository not found")

    by_bytes = get_repository_languages(repo["github_installation_id"], repo["namespace"], repo["name"])
    total = sum(by_bytes.values())
    languages = sorted(
        (
            {"name": name, "bytes": n, "percent": round(n / total * 100, 1) if total else 0.0,
             "color": color_for(name)}
            for name, n in by_bytes.items()
        ),
        key=lambda x: x["bytes"],
        reverse=True,
    )
    return {"total_bytes": total, "languages": languages}


@app.post("/repositories/{repository_id}/scans", status_code=202)
def request_scan(repository_id: UUID, body: ScanIn, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "developer")
        cur.execute(
            """
            SELECT r.namespace, r.name, r.default_branch, i.github_installation_id
              FROM repository r JOIN installation i ON i.id = r.installation_id
             WHERE r.id=%s AND r.is_active
            """,
            (str(repository_id),),
        )
        repo = cur.fetchone()
        if repo is None:
            raise HTTPException(404, "repository not found")

    commit_sha = body.commit_sha
    ref = body.ref or f"refs/heads/{repo['default_branch']}"
    if commit_sha is None:
        commit_sha = get_latest_commit_sha(repo["github_installation_id"], repo["namespace"],
                                            repo["name"], repo["default_branch"])

    actor = f"user:{member['nickname']}"
    with tx() as cur:
        return create_scan(cur, repository_id=str(repository_id), commit_sha=commit_sha,
                            ref=ref, trigger=body.trigger, actor=actor)


@app.get("/projects/{project_id}/scans")
def list_scans(project_id: UUID, repository_id: UUID | None = None, limit: int = 20,
               member: dict = Depends(get_current_member)):
    """최근 스캔 목록. 이 프로젝트에 attach된 레포로만 스코프(목록 가시성 — 역할 무관)."""
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"])
        cur.execute(
            """
            SELECT s.id, s.repository_id, s.commit_sha, s.ref, s.status, s.trigger, s.created_at,
                   r.namespace AS repo_namespace, r.name AS repo_name
              FROM scan s
              JOIN repository r ON r.id = s.repository_id
              JOIN project_repository pr ON pr.repository_id = r.id AND pr.project_id = %s
             WHERE (%s::uuid IS NULL OR s.repository_id = %s)
             ORDER BY s.created_at DESC LIMIT %s
            """,
            (str(project_id), str(repository_id) if repository_id else None,
             str(repository_id) if repository_id else None, limit),
        )
        return cur.fetchall()


@app.get("/scans/{scan_id}")
def get_scan(scan_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        cur.execute(
            """
            SELECT s.*,
                   (SELECT count(*) FROM code_file f WHERE f.scan_id = s.id) AS file_count,
                   (SELECT count(*) FROM symbol y WHERE y.scan_id = s.id) AS symbol_count,
                   (SELECT count(*) FROM symbol_edge e
                     WHERE e.scan_id = s.id AND e.resolved) AS resolved_edges,
                   (SELECT count(*) FROM symbol_edge e
                     WHERE e.scan_id = s.id AND NOT e.resolved) AS unresolved_edges
              FROM scan s WHERE s.id = %s
            """,
            (str(scan_id),),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "scan not found")
    return row


@app.get("/scans/{scan_id}/callers")
def callers(scan_id: UUID, symbol_key: str, depth: int = 3,
            member: dict = Depends(get_current_member)):
    """위험도 전파 조회. 반환된 path 가 그대로 Risk Evidence 가 된다.

    어느 백엔드가 답했는지 응답에 싣는다. 같은 질문에 두 백엔드가 다른 답을
    내면 그건 폴백이 아니라 버그이므로, 운영 중에 대조할 수 있어야 한다.
    """
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")

    store = make_graph_store()
    try:
        if not store.health():
            raise HTTPException(503, f"graph backend {store.backend} unreachable")
        try:
            paths = store.callers_within(str(scan_id), symbol_key, depth)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        close = getattr(store, "close", None)
        if close:
            close()

    return {
        "target": symbol_key,
        "depth": depth,
        "backend": store.backend,
        "callers": [p.as_dict() for p in paths],
    }


@app.get("/scans/{scan_id}/graph")
def scan_graph(scan_id: UUID, include_unresolved: bool = True, domain: str | None = None,
               view: Literal["all", "calls", "dependency"] = "all",
               member: dict = Depends(get_current_member)):
    """Cytoscape.js elements 형태로 스캔 전체 그래프를 반환한다 (프론트엔드 시각화용).

    resolved=false 인 엣지는 대상 심볼이 없으므로, dst_hint 를 라벨로 갖는
    가상의 external:<hint> 노드를 합성해서 연결한다 — 외부 패키지 호출까지
    그래프에서 보이게 하기 위함.

    symbol_semantic(로컬 LLM 의미 태깅, WBS 2.2 프로토타입)이 채워져 있으면
    role/domain/summary 를 같이 실어준다 — 아직 안 돌았으면 전부 null 이라
    기존 프론트엔드는 그대로 동작한다. `domain` 쿼리파라미터를 주면 그
    도메인에 속한 심볼 + 그 심볼과 직접 연결된 이웃만 남긴다("결제 로직
    어디 있지?" 질문에 440개가 아니라 관련된 것만 보여주는 게 목적).

    `view`는 edge_type 을 필터링한다 — 'calls'는 런타임 호출관계(Call Graph),
    'dependency'는 그 외 정적 구조 의존(imports/extends/implements/references,
    Dependency Graph), 'all'은 필터 없음(기존 동작). 노드(심볼) 목록은 view와
    무관하게 항상 스캔 전체를 반환한다 — 그래야 domain 필터와 조합했을 때도
    일관된 이웃 계산이 된다.
    """
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        cur.execute(
            """
            SELECT s.symbol_key, s.kind, s.qualified_name, s.lang, s.path,
                   s.start_line, s.end_line,
                   sem.summary AS own_summary,
                   mod_sem.role AS file_role, mod_sem.domain AS file_domain,
                   mod_sem.summary AS file_summary
              FROM symbol s
              LEFT JOIN symbol_semantic sem
                     ON sem.scan_id = s.scan_id AND sem.symbol_key = s.symbol_key
              LEFT JOIN symbol mod_s
                     ON mod_s.scan_id = s.scan_id AND mod_s.path = s.path AND mod_s.kind = 'module'
              LEFT JOIN symbol_semantic mod_sem
                     ON mod_sem.scan_id = s.scan_id AND mod_sem.symbol_key = mod_s.symbol_key
             WHERE s.scan_id=%s
            """,
            (str(scan_id),),
        )
        symbols = cur.fetchall()

        view_clause = {
            "all": "TRUE",
            "calls": "edge_type = 'calls'",
            "dependency": "edge_type != 'calls'",
        }[view]
        cur.execute(
            f"""
            SELECT src_symbol_key, dst_symbol_key, dst_hint, edge_type, resolved, confidence
              FROM symbol_edge WHERE scan_id=%s AND (resolved OR %s) AND {view_clause}
            """,
            (str(scan_id), include_unresolved),
        )
        edges = cur.fetchall()

        # WBS 1.4 — 최근 커밋 이력(git_commit/commit_file)은 scan 이 아니라
        # repository 시간축에 속하므로, 조회 시점에 path 로 대조한다.
        cur.execute("SELECT repository_id FROM scan WHERE id=%s", (str(scan_id),))
        scan_row = cur.fetchone()
        change_counts: dict[str, int] = {}
        if scan_row:
            cur.execute(
                """
                SELECT cf.path, count(DISTINCT cf.commit_id) AS change_count
                  FROM commit_file cf
                  JOIN git_commit gc ON gc.id = cf.commit_id
                 WHERE gc.repository_id = %s
                 GROUP BY cf.path
                """,
                (str(scan_row["repository_id"]),),
            )
            change_counts = {r["path"]: r["change_count"] for r in cur.fetchall()}

    nodes = {
        s["symbol_key"]: {"data": {
            "id": s["symbol_key"], "label": s["qualified_name"], "kind": s["kind"],
            "lang": s["lang"], "path": s["path"],
            "start_line": s["start_line"], "end_line": s["end_line"],
            "role": s["file_role"], "domain": s["file_domain"],
            "summary": s["own_summary"] or (s["file_summary"] if s["kind"] == "module" else None),
            "recent_changes": change_counts.get(s["path"], 0),
        }}
        for s in symbols
    }

    cy_edges = []
    for i, e in enumerate(edges):
        if e["resolved"]:
            target = e["dst_symbol_key"]
        else:
            target = f"external:{e['dst_hint']}"
            if target not in nodes:
                nodes[target] = {"data": {"id": target, "label": e["dst_hint"], "kind": "external",
                                           "lang": None, "path": None, "start_line": None, "end_line": None,
                                           "role": None, "domain": None,
                                           "summary": None, "recent_changes": 0}}
        cy_edges.append({"data": {
            "id": f"e{i}", "source": e["src_symbol_key"], "target": target,
            "edge_type": e["edge_type"], "resolved": e["resolved"], "confidence": e["confidence"],
        }})

    if domain:
        focus_ids = {sid for sid, n in nodes.items() if n["data"].get("domain") == domain}
        kept_edges = [e for e in cy_edges
                      if e["data"]["source"] in focus_ids or e["data"]["target"] in focus_ids]
        keep_ids = set(focus_ids)
        for e in kept_edges:
            keep_ids.add(e["data"]["source"])
            keep_ids.add(e["data"]["target"])
        nodes = {sid: n for sid, n in nodes.items() if sid in keep_ids}
        cy_edges = kept_edges

    max_recent_changes = max((n["data"]["recent_changes"] for n in nodes.values()), default=0)
    return {"nodes": list(nodes.values()), "edges": cy_edges, "max_recent_changes": max_recent_changes}


@app.get("/scans/{scan_id}/graph/domains")
def scan_domain_graph(scan_id: UUID, member: dict = Depends(get_current_member)):
    """도메인을 노드로 하는 롤업 그래프 — symbol_semantic.domain 을 file(module) 단위로

    집계해서, 도메인 간 실제 호출/의존 엣지 수를 가중치로 삼는다. 같은
    도메인 내부 엣지(자기 자신)는 제외한다 — 이 뷰의 목적은 "도메인들이
    서로 얼마나 얽혀있는가"지 도메인 내부 응집도가 아니다.
    symbol.annotate 가 아직 안 돌았으면(domain 태깅 없음) 빈 그래프.
    """
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        cur.execute(
            """
            WITH file_domain AS (
                SELECT s.path, sem.domain
                  FROM symbol s
                  JOIN symbol_semantic sem
                    ON sem.scan_id = s.scan_id AND sem.symbol_key = s.symbol_key
                 WHERE s.scan_id = %s AND s.kind = 'module' AND sem.domain IS NOT NULL
            )
            SELECT domain, count(*) AS file_count FROM file_domain GROUP BY domain
            """,
            (str(scan_id),),
        )
        domain_rows = cur.fetchall()

        cur.execute(
            """
            WITH file_domain AS (
                SELECT s.path, sem.domain
                  FROM symbol s
                  JOIN symbol_semantic sem
                    ON sem.scan_id = s.scan_id AND sem.symbol_key = s.symbol_key
                 WHERE s.scan_id = %s AND s.kind = 'module' AND sem.domain IS NOT NULL
            )
            SELECT fd1.domain AS src_domain, fd2.domain AS dst_domain, count(*) AS weight
              FROM symbol_edge e
              JOIN symbol s1 ON s1.symbol_key = e.src_symbol_key AND s1.scan_id = e.scan_id
              JOIN symbol s2 ON s2.symbol_key = e.dst_symbol_key AND s2.scan_id = e.scan_id
              JOIN file_domain fd1 ON fd1.path = s1.path
              JOIN file_domain fd2 ON fd2.path = s2.path
             WHERE e.scan_id = %s AND e.resolved AND fd1.domain <> fd2.domain
             GROUP BY fd1.domain, fd2.domain
            """,
            (str(scan_id), str(scan_id)),
        )
        edge_rows = cur.fetchall()

    nodes = [{"data": {"id": r["domain"], "label": r["domain"], "file_count": r["file_count"]}}
             for r in domain_rows]
    edges = [{"data": {"id": f"{r['src_domain']}->{r['dst_domain']}",
                        "source": r["src_domain"], "target": r["dst_domain"], "weight": r["weight"]}}
              for r in edge_rows]
    max_file_count = max((n["data"]["file_count"] for n in nodes), default=0)
    return {"nodes": nodes, "edges": edges, "max_file_count": max_file_count}


@app.get("/scans/{scan_id}/domains")
def scan_domains(scan_id: UUID, member: dict = Depends(get_current_member)):
    """의미 태깅(symbol.annotate)이 만든 도메인 목록 + 파일 수. 아직 안 돌았으면 빈 리스트."""
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        return domain_overview(cur, scan_id)


@app.get("/scans/{scan_id}/search")
def scan_search(scan_id: UUID, q: str, limit: int = 20, domain: str | None = None,
                 member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        return search_symbols(cur, scan_id, q, limit, domain=domain)


@app.get("/scans/{scan_id}/dataflow")
def scan_dataflow(scan_id: UUID, mode: Literal["security", "business"] = "security", limit: int = 50,
                   member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        return run_dataflow(cur, scan_id, mode, limit)


_SEVERITY_ORDER = ["critical", "high", "medium", "low"]


@app.get("/scans/{scan_id}/security/findings")
def scan_security_findings(scan_id: UUID, include_whitelisted: bool = False,
                            member: dict = Depends(get_current_member)):
    """최초 조회 시 1회 계산+영속화, 이후는 저장된 결과를 그대로 반환(캐시처럼 동작)."""
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        cur.execute("SELECT id FROM scan WHERE id=%s", (str(scan_id),))
        if cur.fetchone() is None:
            raise HTTPException(404, "scan not found")
        ensure_security_findings(cur, scan_id)

        cur.execute(
            """
            SELECT sf.id, sf.kind, sf.category, sf.severity, sf.sink_symbol_key, sf.sink_line,
                   sf.source_symbol_key, sf.source_var, sf.path, sf.evidence_kinds, sf.created_at,
                   (fw.id IS NOT NULL) AS is_whitelisted
              FROM security_finding sf
              LEFT JOIN finding_whitelist fw
                     ON fw.repository_id = sf.repository_id AND fw.category = sf.category
                    AND (fw.sink_symbol_key IS NULL OR fw.sink_symbol_key = sf.sink_symbol_key)
             WHERE sf.scan_id = %s AND (%s OR fw.id IS NULL)
             ORDER BY array_position(%s::text[], sf.severity), sf.created_at
            """,
            (str(scan_id), include_whitelisted, _SEVERITY_ORDER),
        )
        return cur.fetchall()


@app.get("/repositories/{repository_id}/security/risk-history")
def repository_risk_history(repository_id: UUID, limit: int = 30,
                             member: dict = Depends(get_current_member)):
    """scan별 severity 개수 시계열 — 이미 security 분석이 끝난(security_finding이 있는) scan만."""
    with tx() as cur:
        assert_repository_access(cur, repository_id, member["id"], "viewer")
        cur.execute(
            """
            SELECT s.id AS scan_id, s.commit_sha, s.created_at, sf.severity, count(*) AS n
              FROM scan s
              JOIN security_finding sf ON sf.scan_id = s.id
             WHERE s.repository_id = %s
             GROUP BY s.id, s.commit_sha, s.created_at, sf.severity
             ORDER BY s.created_at
            """,
            (str(repository_id),),
        )
        rows = cur.fetchall()

    by_scan: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        sid = str(r["scan_id"])
        if sid not in by_scan:
            by_scan[sid] = {"scan_id": sid, "commit_sha": r["commit_sha"],
                             "created_at": r["created_at"], "counts": {}}
            order.append(sid)
        by_scan[sid]["counts"][r["severity"]] = r["n"]
    return [by_scan[sid] for sid in order][-limit:]


class WhitelistIn(BaseModel):
    repository_id: UUID
    category: str
    sink_symbol_key: str | None = None
    reason: str | None = None


@app.get("/projects/{project_id}/security/whitelist")
def list_whitelist(project_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"])
        cur.execute(
            """
            SELECT id, repository_id, category, sink_symbol_key, reason, created_by, created_at
              FROM finding_whitelist WHERE project_id=%s ORDER BY created_at DESC
            """,
            (str(project_id),),
        )
        return cur.fetchall()


@app.post("/projects/{project_id}/security/whitelist", status_code=201)
def create_whitelist_entry(project_id: UUID, body: WhitelistIn,
                            member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"], "manager")
        assert_repository_access(cur, body.repository_id, member["id"], "viewer")
        cur.execute(
            """
            INSERT INTO finding_whitelist (project_id, repository_id, category, sink_symbol_key,
                                           reason, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, repository_id, category, sink_symbol_key, reason, created_by, created_at
            """,
            (str(project_id), str(body.repository_id), body.category, body.sink_symbol_key,
             body.reason, member["id"]),
        )
        row = cur.fetchone()
        audit.record(cur, actor=actor, action="security.whitelist.create",
                     subject={"whitelist_id": str(row["id"])},
                     payload={"project_id": str(project_id), "category": body.category})
        return row


@app.delete("/projects/{project_id}/security/whitelist/{whitelist_id}", status_code=204)
def delete_whitelist_entry(project_id: UUID, whitelist_id: UUID,
                            member: dict = Depends(get_current_member)):
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"], "manager")
        cur.execute(
            "DELETE FROM finding_whitelist WHERE id=%s AND project_id=%s RETURNING id",
            (str(whitelist_id), str(project_id)),
        )
        if cur.fetchone() is None:
            raise HTTPException(404, "whitelist entry not found")
        audit.record(cur, actor=actor, action="security.whitelist.delete",
                     subject={"whitelist_id": str(whitelist_id)}, payload={"project_id": str(project_id)})


class ProjectAiSettingsIn(BaseModel):
    # provider 는 이제 라우팅에 쓰이지 않는다(게이트웨이가 model 별칭으로 결정) —
    # 하위 호환을 위해 컬럼은 두고 값만 'gateway'/'ollama' 로 받는다.
    ci_model_provider: Literal["gateway", "ollama"] = "gateway"
    ci_model_name: str = Field(default="", max_length=100)  # litellm/config.yaml 의 model_name
    qa_model_provider: Literal["gateway", "ollama"] = "gateway"
    qa_model_name: str = Field(default="", max_length=100)


@app.get("/projects/{project_id}/ai-settings")
def get_project_ai_settings(project_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"])
        cur.execute(
            "SELECT ci_model_provider, ci_model_name, qa_model_provider, qa_model_name FROM project WHERE id=%s",
            (str(project_id),),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "project not found")
        return row


@app.patch("/projects/{project_id}/ai-settings")
def update_project_ai_settings(project_id: UUID, body: ProjectAiSettingsIn,
                                member: dict = Depends(get_current_member)):
    """qa_model_name/ci_model_name 은 LiteLLM 게이트웨이의 model_name(공개 별칭)이다.

    비우면 요청별 선택 또는 전역 기본(settings().default_chat_model)으로 떨어진다.
    게이트웨이가 서빙하지 않는 별칭을 저장해도 막지 않는다 — 호출 시점에
    ai_providers.resolve_requested_model 이 기본으로 폴백한다.
    """
    actor = f"user:{member['nickname']}"
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"], "manager")
        cur.execute(
            """
            UPDATE project SET ci_model_provider=%s, ci_model_name=%s,
                               qa_model_provider=%s, qa_model_name=%s
             WHERE id=%s
             RETURNING ci_model_provider, ci_model_name, qa_model_provider, qa_model_name
            """,
            (body.ci_model_provider, body.ci_model_name, body.qa_model_provider, body.qa_model_name,
             str(project_id)),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "project not found")
        audit.record(cur, actor=actor, action="project.ai_settings.update",
                     subject={"project_id": str(project_id)}, payload=dict(row))
        return row


@app.get("/projects/{project_id}/ai-usage")
def get_project_ai_usage(project_id: UUID, member: dict = Depends(get_current_member)):
    """purpose/provider/model별 호출 건수·성공률·평균 latency."""
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"])
        cur.execute(
            """
            SELECT purpose, provider, model, count(*) AS calls,
                   count(*) FILTER (WHERE success) AS successes,
                   round(avg(latency_ms)) AS avg_latency_ms,
                   max(created_at) AS last_called_at
              FROM ai_call_log
             WHERE project_id=%s
             GROUP BY purpose, provider, model
             ORDER BY purpose, provider, model
            """,
            (str(project_id),),
        )
        return cur.fetchall()


@app.post("/scans/{scan_id}/ask")
async def scan_ask(scan_id: UUID, body: AskIn, member: dict = Depends(get_current_member)):
    """자연어 질의 → 라우팅(어떤 도구를 부를지) → 도구 실행 → 답변 합성.

    진행 상황(계획/생각 중/도구 호출/결과/답변 토큰)을 SSE로 실시간
    스트리밍한다 — 프론트가 전체 응답을 기다리는 대신 단계별로 렌더링할 수
    있게 한다(`app/ask_stream.py`). 권한/provider 확인(`load_ask_context`)은
    스트림을 열기 전에 끝내서, 그 실패가 SSE 에러 프레임이 아니라 평범한
    HTTP 에러 응답이 되게 한다.
    """
    ask_ctx = await run_in_threadpool(ask_stream.load_ask_context, scan_id, member["id"], body.model)
    return StreamingResponse(
        ask_stream.stream_answer(body.question, ask_ctx),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/scans/{scan_id}/file")
def scan_file(scan_id: UUID, path: str, member: dict = Depends(get_current_member)):
    """그래프 ↔ 코드 동기화용 — 노드를 클릭했을 때 실제 소스를 보여준다.

    `code_file`에 정확히 이 (scan_id, path)가 있는지부터 확인한다 — 그
    스캔이 실제로 인덱싱한 파일만 통과하므로 임의 경로 조작(`../../etc`
    같은)이 통하지 않는다. 클론 워크디렉터리는 스캔과 무관하게 저장소당
    하나뿐이라(얕은 클론을 계속 갱신) 스캔 시점 이후 파일이 바뀌었을 수는
    있다 — 그건 이 엔드포인트가 "그때 그 코드"가 아니라 "지금 그 경로"를
    보여준다는 뜻이고, 코드 뷰는 참고용이라 크게 문제되지 않는다.
    """
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        cur.execute(
            "SELECT lang FROM code_file WHERE scan_id=%s AND path=%s",
            (str(scan_id), path),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "file not indexed in this scan")

        cur.execute("SELECT repository_id FROM scan WHERE id=%s", (str(scan_id),))
        repository_id = cur.fetchone()["repository_id"]

    full_path = workdir_for(str(repository_id)) / path
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(404, f"file not found on disk: {exc}") from exc

    return {"path": path, "lang": row["lang"], "content": content}


@app.get("/scans/{scan_id}/functions")
def scan_functions(scan_id: UUID, member: dict = Depends(get_current_member)):
    """CFG/PDG 뷰의 함수 선택기용 목록 — cfg_block이 있는(=CFG 추출된) 심볼만."""
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        cur.execute(
            """
            SELECT DISTINCT s.symbol_key, s.qualified_name, s.path
              FROM cfg_block b
              JOIN symbol s ON s.scan_id = b.scan_id AND s.symbol_key = b.symbol_key
             WHERE b.scan_id = %s
             ORDER BY s.path, s.qualified_name
            """,
            (str(scan_id),),
        )
        return cur.fetchall()


@app.get("/scans/{scan_id}/function-graph")
def scan_function_graph(scan_id: UUID, symbol_key: str, member: dict = Depends(get_current_member)):
    """CFG/PDG/CPG 셋 다 이 엔드포인트 하나로 처리한다.

    엣지마다 layer(cfg|control|data)를 붙여서 하나의 그래프로 반환한다 —
    "하나의 그래프 위에 여러 종류의 edge를 얹는다"는 CPG 개념 그대로다.
    프론트는 뷰에 따라 필요한 layer만 걸러서 그린다.
    """
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        cur.execute(
            "SELECT qualified_name, path FROM symbol WHERE scan_id=%s AND symbol_key=%s",
            (str(scan_id), symbol_key),
        )
        meta = cur.fetchone()
        if meta is None:
            raise HTTPException(404, "symbol not found")

        cur.execute(
            "SELECT block_id, start_line, end_line FROM cfg_block WHERE scan_id=%s AND symbol_key=%s",
            (str(scan_id), symbol_key),
        )
        blocks = cur.fetchall()

        cur.execute(
            "SELECT src_block_id, dst_block_id, branch_label FROM cfg_edge WHERE scan_id=%s AND symbol_key=%s",
            (str(scan_id), symbol_key),
        )
        cfg_edges = cur.fetchall()

        cur.execute(
            """
            SELECT branch_block_id, dependent_block_id, branch_label
              FROM pdg_control_edge WHERE scan_id=%s AND symbol_key=%s
            """,
            (str(scan_id), symbol_key),
        )
        control_edges = cur.fetchall()

        cur.execute(
            """
            SELECT var_name, def_block_id, use_line, evidence_kind
              FROM pdg_data_edge WHERE scan_id=%s AND symbol_key=%s
            """,
            (str(scan_id), symbol_key),
        )
        data_rows = cur.fetchall()

    def block_of_line(line: int) -> int | None:
        # use_block_id 를 안 남겨서, 그 줄을 담고 있는 블록을 범위로 찾는다
        # (함수당 블록 수가 적어 매번 선형 탐색해도 비용 문제 없음).
        best = None
        for b in blocks:
            if b["start_line"] <= line <= b["end_line"]:
                if best is None or (b["end_line"] - b["start_line"]) < (best["end_line"] - best["start_line"]):
                    best = b
        return best["block_id"] if best else None

    nodes = [{"data": {"id": f"b{b['block_id']}", "label": f"L{b['start_line']}-{b['end_line']}",
                        "start_line": b["start_line"], "is_entry": b["block_id"] == 0}}
             for b in blocks]

    edges = []
    for e in cfg_edges:
        edges.append({"data": {"id": f"cfg-{e['src_block_id']}-{e['dst_block_id']}-{e['branch_label']}",
                                "source": f"b{e['src_block_id']}", "target": f"b{e['dst_block_id']}",
                                "layer": "cfg", "branch_label": e["branch_label"]}})
    for e in control_edges:
        edges.append({"data": {"id": f"control-{e['branch_block_id']}-{e['dependent_block_id']}",
                                "source": f"b{e['branch_block_id']}", "target": f"b{e['dependent_block_id']}",
                                "layer": "control", "branch_label": e["branch_label"]}})
    for i, e in enumerate(data_rows):
        use_block = block_of_line(e["use_line"])
        if use_block is None or use_block == e["def_block_id"]:
            continue  # 같은 블록 안 자기참조는 그래프에서 안 보여줘도 됨(노이즈)
        edges.append({"data": {"id": f"data-{i}", "source": f"b{e['def_block_id']}", "target": f"b{use_block}",
                                "layer": "data", "var": e["var_name"], "evidence_kind": e["evidence_kind"]}})

    return {"symbol_key": symbol_key, "qualified_name": meta["qualified_name"], "path": meta["path"],
            "nodes": nodes, "edges": edges}


@app.get("/scans/{scan_id}/projection")
def projection(scan_id: UUID, member: dict = Depends(get_current_member)):
    """파생 계층의 상태. stale=true 여도 판정은 성립한다."""
    with tx() as cur:
        assert_scan_access(cur, scan_id, member["id"], "viewer")
        cur.execute("SELECT * FROM graph_projection WHERE scan_id=%s", (str(scan_id),))
        row = cur.fetchone()
    return row or {"scan_id": str(scan_id), "backend": settings().graph_backend,
                   "stale": True, "projected_at": None,
                   "note": "아직 투영되지 않음 — postgres_cte 로 조회 가능"}
