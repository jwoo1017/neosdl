"""repo.clone — Repository 를 작업 디렉터리에 확보한다.

GitLab / GitHub 두 provider 모두 구현한다. 인증 토큰은 URL 에만 실리고
저장소에는 남지 않는다 — 매 클론/fetch 마다 새로 발급받아 주입한다.

작업 디렉터리는 두 가지다.

  workdir_for(repository_id)   = repos/<repo_id>
      레포당 하나. shallow, default 브랜치 tip. 레포 등록 시(repo.clone)와
      스캔 시(scan.fetch) 계속 갱신된다. **on-demand 소스 읽기** 전용
      (CodeViewer, /scans/{id}/file, risk_api 즉석 계산) — "그때 그 커밋"이
      아니라 "지금 그 경로"를 보여준다(app/main.py scan_file docstring).

  scan_workdir_for(scan_id)    = scans/<scan_id>
      스캔당 git worktree. scan.commit_sha 로 정확히 체크아웃된다. 스캔
      **파이프라인**(parse/pdg/risk_build 소스 스캔)이 읽는 곳. 파이프라인이
      끝나면(risk_build) 또는 오래되면(worker idle 스윕) 지워진다.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.config import settings
from app.db import tx
from app.github_app import installation_token_for_repo

log = logging.getLogger("worker.clone")


def workdir_for(repository_id: str) -> Path:
    return Path(settings().neosdl_workdir) / "repos" / repository_id


def scan_workdir_for(scan_id: str) -> Path:
    return Path(settings().neosdl_workdir) / "scans" / scan_id


def pipeline_source_root(scan_id: str, repository_id: str) -> Path:
    """파이프라인 핸들러가 소스를 읽을 루트. 스캔 worktree 가 있으면 그것(정확한
    커밋), 없으면 레포 캐시로 폴백(= scan.fetch 이전 동작)."""
    sw = scan_workdir_for(scan_id)
    if (sw / ".git").exists() or sw.exists():
        return sw
    return workdir_for(repository_id)


def handle(payload: dict) -> None:
    """repo.clone — 레포 캐시(repos/<id>)를 default 브랜치 tip 으로 확보."""
    repository_id = payload["repository_id"]
    with tx() as cur:
        repo = _repo_row(cur, repository_id)
    ensure_repo_cache(repository_id, repo)


# --------------------------------------------------------------------- helpers

def _repo_row(cur, repository_id: str) -> dict:
    cur.execute(
        "SELECT clone_url, default_branch, provider, namespace, name FROM repository WHERE id=%s",
        (repository_id,),
    )
    repo = cur.fetchone()
    if repo is None:
        raise ValueError(f"repository {repository_id} not found")
    return repo


def ensure_repo_cache(repository_id: str, repo: dict) -> Path:
    """repos/<id> 를 clone 하거나 fetch 로 갱신한다. default 브랜치 tip."""
    dest = workdir_for(repository_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = _authenticated_url(repo)
    branch = repo["default_branch"]

    if (dest / ".git").exists():
        log.info("fetch %s", dest)
        _git("-C", str(dest), "remote", "set-url", "origin", url)
        _git("-C", str(dest), "fetch", "--prune", "--depth", "1", "origin", branch)
        _git("-C", str(dest), "reset", "--hard", f"origin/{branch}")
    else:
        log.info("clone %s → %s", repo["provider"], dest)
        _git("clone", "--depth", "1", "--branch", branch, url, str(dest))

    head = _git("-C", str(dest), "rev-parse", "HEAD").strip()
    log.info("repository %s HEAD=%s", repository_id, head)
    return dest


def create_scan_worktree(repository_id: str, repo: dict, scan_id: str, commit_sha: str) -> Path | None:
    """scans/<scan_id> 를 commit_sha 로 체크아웃한 worktree 로 만든다.

    실패하면 None — 호출부는 worktree 없이 진행하고 파이프라인은 레포 캐시로
    폴백한다(= scan.fetch 이전 동작). 증분은 이때 비활성(parent NULL).
    """
    cache = workdir_for(repository_id)
    try:
        ensure_repo_cache(repository_id, repo)
        url = _authenticated_url(repo)
        _git("-C", str(cache), "remote", "set-url", "origin", url)
        # 그 커밋 객체를 가져온다. PR head SHA 도 open PR 이면 fetch 된다.
        _git("-C", str(cache), "fetch", "--depth", "1", "origin", commit_sha)

        sw = scan_workdir_for(scan_id)
        remove_scan_worktree(repository_id, scan_id)  # 재시도 대비 정리
        sw.parent.mkdir(parents=True, exist_ok=True)
        _git("-C", str(cache), "worktree", "add", "--force", "--detach", str(sw), commit_sha)
        head = _git("-C", str(sw), "rev-parse", "HEAD").strip()
        log.info("scan %s worktree HEAD=%s", scan_id, head)
        return sw
    except Exception:
        log.warning("scan %s: worktree 생성 실패 — 레포 캐시로 폴백(증분 비활성)", scan_id, exc_info=True)
        return None


def remove_scan_worktree(repository_id: str, scan_id: str) -> None:
    """스캔 worktree 정리(best-effort). 파이프라인 종료 시 / idle 스윕에서 호출."""
    sw = scan_workdir_for(scan_id)
    cache = workdir_for(repository_id)
    try:
        if (cache / ".git").exists():
            _git("-C", str(cache), "worktree", "remove", "--force", str(sw))
    except Exception:
        log.debug("worktree remove 실패(무시): %s", sw, exc_info=True)
    if sw.exists():
        shutil.rmtree(sw, ignore_errors=True)
    try:
        if (cache / ".git").exists():
            _git("-C", str(cache), "worktree", "prune")
    except Exception:
        pass


def _authenticated_url(repo) -> str:
    provider = repo["provider"]
    if provider == "gitlab":
        token = settings().gitlab_token
        if not token:
            raise RuntimeError("GITLAB_TOKEN not set")
        return _inject_userinfo(repo["clone_url"], "oauth2", token)
    if provider == "github":
        token = installation_token_for_repo(repo["namespace"], repo["name"])
        return _inject_userinfo(repo["clone_url"], "x-access-token", token)
    raise ValueError(f"unknown provider {provider!r}")


def _inject_userinfo(url: str, user: str, token: str) -> str:
    parts = urlsplit(url)
    netloc = f"{user}:{token}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _git(*args: str) -> str:
    # text=True 만 쓰면 OS 로캘(한국어 Windows는 cp949)로 디코딩하는데
    # git 출력은 항상 UTF-8이라, 한글 파일명/커밋 메시지가 있으면 깨진다.
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=600, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()[:500]}")
    return proc.stdout
