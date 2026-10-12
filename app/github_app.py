"""GitHub App 서버 신원 — App JWT 서명 및 installation 토큰 발급.

원래 worker/handlers/clone.py 안에 private 함수로만 있었으나, 로그인/대시보드
API(app/installations.py)도 같은 신원으로 GitHub API를 호출해야 해서 여기로
옮기고 공개 함수로 만들었다. worker 는 이미 app.config/app.db를 import하고
있으니 worker -> app 의존 방향은 새로운 패턴이 아니다.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import jwt

from app.config import settings

_GITHUB_API = "https://api.github.com"


def app_jwt() -> str:
    app_id = settings().github_app_id
    key_path = settings().github_app_private_key_path
    if not app_id or not key_path:
        raise RuntimeError("GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY_PATH not set")
    private_key = Path(key_path).read_text()
    now = int(time.time())
    # GitHub 권장대로 clock skew 방지를 위해 iat 을 60초 과거로, exp 는 최대 10분 중 9분으로.
    payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": app_id}
    return jwt.encode(payload, private_key, algorithm="RS256")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {app_jwt()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def installation_token_for_repo(namespace: str, name: str) -> str:
    """레포별 installation_id 를 저장하지 않는다 — 매번 API 로 조회한다.

    토큰은 1시간 만료이므로 어차피 클론/fetch마다 재발급해야 하고,
    조회 한 번을 더 타는 대신 스키마에 GitHub 전용 컬럼을 추가하지 않는다.
    """
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{_GITHUB_API}/repos/{namespace}/{name}/installation",
                           headers=_headers())
        resp.raise_for_status()
        installation_id = resp.json()["id"]
        return installation_token(installation_id)


def get_installation(installation_id: int) -> dict:
    """Setup URL 콜백에서 account_login/account_type 을 채우기 위해 조회."""
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{_GITHUB_API}/app/installations/{installation_id}",
                           headers=_headers())
        resp.raise_for_status()
        return resp.json()


def installation_token(installation_id: int) -> str:
    """installation_id 를 이미 알고 있을 때 직접 토큰 발급 (대시보드용)."""
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            f"{_GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()["token"]


def list_installation_repositories(installation_id: int) -> list[dict]:
    """GET /installation/repositories, 100개씩 페이지네이션."""
    token = installation_token(installation_id)
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    repos: list[dict] = []
    page = 1
    with httpx.Client(timeout=10) as client:
        while True:
            resp = client.get(f"{_GITHUB_API}/installation/repositories",
                               headers=headers, params={"per_page": 100, "page": page})
            resp.raise_for_status()
            batch = resp.json()["repositories"]
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return repos


def _token_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def list_recent_commits(installation_id: int, owner: str, repo: str,
                         limit: int = 100, sha: str | None = None) -> tuple[str, list[dict]]:
    """GET /repos/{owner}/{repo}/commits — 최근 커밋 목록(파일 목록은 안 옴, 별도 조회 필요).

    발급받은 토큰을 같이 돌려준다 — 호출자가 get_commit_files() 를 커밋 수만큼
    반복 호출할 때 매번 새 토큰을 발급받지 않고 이 토큰을 재사용하기 위함
    (안 그러면 100개 커밋에 토큰 발급까지 총 200번 API 콜이 나간다).

    `sha`에 브랜치명을 넘기면 그 브랜치 기준으로 조회한다(GitHub API가 원래
    지원하는 파라미터) — 생략하면 저장소 기본 브랜치.
    """
    token = installation_token(installation_id)
    headers = _token_headers(token)
    commits: list[dict] = []
    page = 1
    params: dict = {}
    if sha:
        params["sha"] = sha
    with httpx.Client(timeout=10) as client:
        while len(commits) < limit:
            per_page = min(100, limit - len(commits))
            resp = client.get(f"{_GITHUB_API}/repos/{owner}/{repo}/commits",
                               headers=headers, params={**params, "per_page": per_page, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            commits.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
    return token, commits


def list_branches(installation_id: int, owner: str, repo: str) -> list[dict]:
    """GET /repos/{owner}/{repo}/branches — 브랜치 목록(이름, 최신 커밋 sha, 보호 여부)."""
    token = installation_token(installation_id)
    headers = _token_headers(token)
    branches: list[dict] = []
    page = 1
    with httpx.Client(timeout=10) as client:
        while True:
            resp = client.get(f"{_GITHUB_API}/repos/{owner}/{repo}/branches",
                               headers=headers, params={"per_page": 100, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            branches.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return branches


def get_repo_contents(installation_id: int, owner: str, repo: str,
                       path: str = "", ref: str | None = None) -> dict | list[dict]:
    """GET /repos/{owner}/{repo}/contents/{path} — 디렉터리면 목록, 파일이면 단일 객체(base64 content 포함)."""
    token = installation_token(installation_id)
    headers = _token_headers(token)
    params = {"ref": ref} if ref else {}
    resp = httpx.get(f"{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
                      headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def list_pull_requests(installation_id: int, owner: str, repo: str,
                        state: str = "all", limit: int = 100) -> list[dict]:
    """GET /repos/{owner}/{repo}/pulls — 웹훅이 못 잡은 과거 PR을 백필할 때 사용."""
    token = installation_token(installation_id)
    headers = _token_headers(token)
    prs: list[dict] = []
    page = 1
    with httpx.Client(timeout=10) as client:
        while len(prs) < limit:
            per_page = min(100, limit - len(prs))
            resp = client.get(f"{_GITHUB_API}/repos/{owner}/{repo}/pulls",
                               headers=headers,
                               params={"state": state, "per_page": per_page, "page": page,
                                       "sort": "created", "direction": "desc"})
            resp.raise_for_status()
            batch = resp.json()
            prs.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
    return prs


def list_releases(installation_id: int, owner: str, repo: str) -> list[dict]:
    """GET /repos/{owner}/{repo}/releases — 태그가 아니라 실제 GitHub Release 객체 기준."""
    token = installation_token(installation_id)
    headers = _token_headers(token)
    releases: list[dict] = []
    page = 1
    with httpx.Client(timeout=10) as client:
        while True:
            resp = client.get(f"{_GITHUB_API}/repos/{owner}/{repo}/releases",
                               headers=headers, params={"per_page": 100, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            releases.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return releases


def resolve_commit_sha(installation_id: int, owner: str, repo: str, ref: str) -> str:
    """태그/브랜치명 등 임의의 ref가 가리키는 정확한 커밋 sha. get_latest_commit_sha와 같은 패턴."""
    token = installation_token(installation_id)
    resp = httpx.get(f"{_GITHUB_API}/repos/{owner}/{repo}/commits/{ref}",
                      headers=_token_headers(token), timeout=10)
    resp.raise_for_status()
    return resp.json()["sha"]


def compare_commits(installation_id: int, owner: str, repo: str, base: str, head: str) -> dict:
    """GET /repos/{owner}/{repo}/compare/{base}...{head} — commits/files 배열 + ahead_by/behind_by."""
    token = installation_token(installation_id)
    resp = httpx.get(f"{_GITHUB_API}/repos/{owner}/{repo}/compare/{base}...{head}",
                      headers=_token_headers(token), timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_commit_files(token: str, owner: str, repo: str, sha: str) -> list[dict]:
    """GET /repos/{owner}/{repo}/commits/{sha} 의 files[] — 그 커밋이 건드린 파일 목록.

    "커밋 목록" 조회 1콜로는 안 오고, 커밋마다 상세 조회를 한 번씩 더 해야 한다
    (100개 커밋이면 list_recent_commits 1콜 + 이 함수 100콜 = 총 101콜 —
    installation 토큰 시간당 한도 5,000에 비하면 여유롭지만 레포가 많아지면
    누적되니 참고). `token`은 list_recent_commits()가 돌려준 걸 그대로 쓴다.
    """
    resp = httpx.get(f"{_GITHUB_API}/repos/{owner}/{repo}/commits/{sha}",
                      headers=_token_headers(token), timeout=10)
    resp.raise_for_status()
    return resp.json().get("files", [])


def get_repository_languages(installation_id: int, owner: str, repo: str) -> dict[str, int]:
    """GET /repos/{owner}/{repo}/languages — 언어별 바이트 수(내림차순 아님, GitHub가 주는 그대로)."""
    token = installation_token(installation_id)
    resp = httpx.get(f"{_GITHUB_API}/repos/{owner}/{repo}/languages",
                      headers=_token_headers(token), timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_branch_sha(installation_id: int, owner: str, repo: str, branch: str) -> str:
    """GET /repos/{owner}/{repo}/git/ref/heads/{branch} — 그 브랜치 tip 커밋 sha (create-pr의 base_sha)."""
    token = installation_token(installation_id)
    resp = httpx.get(f"{_GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/{branch}",
                      headers=_token_headers(token), timeout=10)
    resp.raise_for_status()
    return resp.json()["object"]["sha"]


def create_branch(installation_id: int, owner: str, repo: str, new_branch: str, base_sha: str) -> None:
    """POST /repos/{owner}/{repo}/git/refs — base_sha 에서 새 브랜치를 만든다.

    이미 같은 이름의 브랜치가 있으면(재시도 등으로 우리가 이미 만든 것일
    가능성이 높다) 422를 조용히 삼킨다 — 호출부(create-pr)가 그 브랜치에
    그대로 커밋한다.
    """
    token = installation_token(installation_id)
    resp = httpx.post(f"{_GITHUB_API}/repos/{owner}/{repo}/git/refs",
                       headers=_token_headers(token),
                       json={"ref": f"refs/heads/{new_branch}", "sha": base_sha}, timeout=10)
    if resp.status_code == 422 and "already exists" in resp.text:
        return
    resp.raise_for_status()


def get_file_with_sha(installation_id: int, owner: str, repo: str, path: str, ref: str) -> dict:
    """get_repo_contents의 결과에서 create-pr에 필요한 최소 필드만 뽑는다(content는 base64 유지)."""
    entry = get_repo_contents(installation_id, owner, repo, path, ref=ref)
    return {"sha": entry["sha"], "content_b64": entry["content"]}


def update_file_contents(installation_id: int, owner: str, repo: str, path: str, new_content_b64: str,
                          message: str, branch: str, sha: str) -> dict:
    """PUT /repos/{owner}/{repo}/contents/{path} — 지정 브랜치에 파일 1개를 갱신하는 커밋을 만든다.

    `sha`는 갱신 대상 파일의 현재 blob sha(낙관적 동시성 제어) — GitHub가 그
    사이 파일이 바뀌었으면 409를 돌려준다.
    """
    token = installation_token(installation_id)
    resp = httpx.put(f"{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
                      headers=_token_headers(token),
                      json={"message": message, "content": new_content_b64, "branch": branch, "sha": sha},
                      timeout=15)
    resp.raise_for_status()
    return resp.json()


def create_pull_request(installation_id: int, owner: str, repo: str, title: str, body: str,
                         head: str, base: str) -> dict:
    """POST /repos/{owner}/{repo}/pulls."""
    token = installation_token(installation_id)
    resp = httpx.post(f"{_GITHUB_API}/repos/{owner}/{repo}/pulls",
                       headers=_token_headers(token),
                       json={"title": title, "body": body, "head": head, "base": base}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_latest_commit_sha(installation_id: int, owner: str, repo: str, branch: str) -> str:
    """스캔 실행 시 사용자가 커밋 SHA를 직접 넣지 않아도 되도록 자동 조회."""
    token = installation_token(installation_id)
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{_GITHUB_API}/repos/{owner}/{repo}/commits/{branch}",
                           headers=headers)
        resp.raise_for_status()
        return resp.json()["sha"]
