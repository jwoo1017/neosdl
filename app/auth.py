"""세션 JWT 발급/검증 + workspace/project 역할 기반 접근 체크.

서버 세션 테이블 없이 httpOnly JWT 쿠키만 쓴다. 다만 회원 탈퇴·비밀번호 변경으로
기존 세션을 끊을 수 있어야 해서(sql/028), 요청마다 member PK 조회 1번으로
`token_version`(클레임 tv)과 `deleted_at` 을 확인한다 — 다르거나 탈퇴했으면 401.

이메일 인증(app/email_verification.py): 세션 클레임 `ev`(email_verified)로 들고 다닌다.
미인증 계정은 get_current_member 가 403 `email_not_verified` 로 막는다 — 기능별로
막는 대신 기본 차단이고, /me·인증 코드 발송/확인처럼 미인증 상태에서도 필요한
라우트만 get_current_member_allow_unverified 를 쓴다. 인증 직후엔 쿠키를 재발급한다.

역할은 고정 계층이다 — workspace: member < admin, project: viewer <
developer < manager < owner. 매 체크는 SELECT 한 번으로 caller의 role을
가져온 뒤 Python에서 랭크를 비교한다. 다른 테넌트의 존재 여부가 응답
코드로 새지 않도록, 관계 자체가 없으면 404, 관계는 있는데 역할이
모자라면 403이다(이미 소속이 확인된 뒤라 자원 존재를 더 숨길 이유가 없다).
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

import jwt
from fastapi import Cookie, HTTPException, WebSocket
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db import tx

SESSION_COOKIE = "neosdl_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600

_WORKSPACE_ROLE_RANK = {"member": 0, "admin": 1}
_TEAMSPACE_ROLE_RANK = {"member": 0, "admin": 1}
_PROJECT_ROLE_RANK = {"viewer": 0, "developer": 1, "manager": 2, "owner": 3}


def issue_session_token(member: dict) -> str:
    now = int(time.time())
    claims = {
        "sub": str(member["id"]),
        "nickname": member["nickname"],
        "email": member.get("email"),
        "ev": bool(member.get("email_verified", False)),
        # 세션 무효화 버전 — member.token_version 과 다르면 401. 호출부가 반드시 실어 보낸다.
        "tv": int(member.get("token_version", 0)),
        "iat": now,
        "exp": now + SESSION_TTL_SECONDS,
    }
    return jwt.encode(claims, settings().session_secret, algorithm="HS256")


def _decode_session(session: str | None) -> dict | None:
    if session is None:
        return None
    try:
        claims = jwt.decode(session, settings().session_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    # 클레임 모양이 예전 버전(팀 모델 이전, gh_id/login/avatar_url 사용)일
    # 수 있다 — 그런 쿠키가 브라우저에 남아있으면 KeyError로 500을 내는
    # 대신 그냥 "세션 무효"로 취급해서 재로그인을 유도한다.
    if "sub" not in claims or "nickname" not in claims:
        return None
    return {
        "id": claims["sub"],
        "nickname": claims["nickname"],
        "email": claims.get("email"),
        # `ev` 가 없는 쿠키는 이메일 인증 도입 전에 발급된 것 — 그때 계정은 전부
        # 인증 완료로 백필했으므로(sql/027) 인증된 것으로 본다.
        "email_verified": bool(claims.get("ev", True)),
        "token_version": int(claims.get("tv", 0)),
    }


def _still_valid(member: dict) -> bool:
    """탈퇴·관리자 비활성화했거나 token_version 이 올라갔으면(비밀번호 변경 등) 그 세션은 무효."""
    with tx() as cur:
        cur.execute("SELECT token_version, deleted_at, suspended_at FROM member WHERE id=%s", (member["id"],))
        row = cur.fetchone()
    return (row is not None and row["deleted_at"] is None and row["suspended_at"] is None
            and row["token_version"] == member["token_version"])


def _load_session(session: str | None) -> dict | None:
    member = _decode_session(session)
    if member is None or not _still_valid(member):
        return None
    return member


def get_current_member_allow_unverified(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    """로그인만 확인 — /me, 인증 코드 발송/확인처럼 미인증 상태에서도 필요한 라우트 전용."""
    member = _load_session(session)
    if member is None:
        raise HTTPException(401, "not authenticated")
    return member


def get_current_member(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    member = get_current_member_allow_unverified(session)
    if not member["email_verified"]:
        raise HTTPException(403, "email_not_verified")
    return member


def get_current_member_optional(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict | None:
    """setup URL 콜백처럼 "로그인 안 됐으면 리다이렉트" 를 직접 하고 싶을 때 씀.

    미인증 계정은 로그인 안 한 것과 같게 취급한다(기본 차단 원칙 유지).
    """
    member = _load_session(session)
    return member if member and member["email_verified"] else None


WS_REVALIDATE_SECONDS = 60


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def ws_origin_allowed(websocket: WebSocket) -> bool:
    """Cross-Site WebSocket Hijacking 방어 — 핸드셰이크 Origin 이 우리 화면 주소일 때만 허용.

    브라우저는 WS 핸드셰이크에 항상 Origin 을 싣고, 다른 사이트의 스크립트가 우리 쿠키로
    소켓을 여는 공격(CSWSH)은 그 사이트의 Origin 으로 들어온다. 세션 쿠키가 SameSite=lax 라
    대부분 이미 막히지만 서버에서 명시적으로 한 번 더 막는다(현업 관례).
    Origin 이 아예 없으면 브라우저가 아닌 클라이언트(점검용 curl 등)라 CSWSH 대상이 아니다 —
    그 경우엔 쿠키 인증만으로 판단한다.
    """
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    allowed = {_origin_of(settings().frontend_url), _origin_of(settings().backend_base_url)}
    return origin.lower().rstrip("/") in allowed


async def watch_ws_session(websocket: WebSocket, member: dict) -> None:
    """열린 소켓의 세션을 주기적으로 재검증 — 비밀번호 변경·탈퇴(token_version/deleted_at,
    sql/028) 뒤에도 이미 열린 실시간 연결이 계속 살아 있지 않게 한다. 무효면 4401 로 닫는다.
    소켓 핸들러가 asyncio 태스크로 띄우고, 연결이 끝나면 cancel 한다."""
    while True:
        await asyncio.sleep(WS_REVALIDATE_SECONDS)
        try:
            valid = await run_in_threadpool(_still_valid, member)
        except Exception:
            continue  # DB 일시 장애로 멀쩡한 연결을 끊지 않는다(fail-open)
        if not valid:
            try:
                await websocket.close(code=4401)
            finally:
                return


def get_current_member_ws(websocket: WebSocket) -> dict | None:
    """WebSocket 라우트는 `Depends(Cookie(...))`가 안 통해서(HTTP 요청 전용) 따로 둔다 —
    Starlette가 WS 핸드셰이크의 쿠키도 `websocket.cookies`로 똑같이 파싱해준다.
    미인증 계정은 None(호출부가 연결을 거절한다)."""
    member = _load_session(websocket.cookies.get(SESSION_COOKIE))
    return member if member and member["email_verified"] else None


def assert_not_self_invite(member: dict, target_email: str | None) -> None:
    """지정 이메일 초대에서 자기 자신을 초대하는 걸 막는다.

    초대를 만드는 사람은 이미 그 workspace/teamspace/project의 멤버라(admin/
    manager 권한 체크를 통과한 뒤다) 자기 이메일로 링크를 만들어봐야 의미가
    없고, 수락하면 중복 멤버 INSERT로 터진다. target_email이 없으면(링크
    전용 초대) 검사할 대상이 없다.
    """
    if not target_email:
        return
    own = (member.get("email") or "").strip().lower()
    if own and target_email.strip().lower() == own:
        raise HTTPException(400, "본인은 초대할 수 없습니다")


def assert_workspace_role(cur, workspace_id, member_id: str, min_role: str | None = None) -> str:
    cur.execute(
        """
        SELECT wm.role FROM workspace_member wm
          JOIN workspace w ON w.id = wm.workspace_id AND w.deleted_at IS NULL
         WHERE wm.workspace_id = %s AND wm.member_id = %s
        """,
        (str(workspace_id), member_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "workspace not found")
    if min_role and _WORKSPACE_ROLE_RANK[row["role"]] < _WORKSPACE_ROLE_RANK[min_role]:
        raise HTTPException(403, "insufficient workspace role")
    return row["role"]


def assert_teamspace_role(cur, teamspace_id, member_id: str, min_role: str | None = None) -> str:
    cur.execute(
        """
        SELECT tm.role FROM teamspace_member tm
          JOIN teamspace t ON t.id = tm.teamspace_id AND t.deleted_at IS NULL
         WHERE tm.teamspace_id = %s AND tm.member_id = %s
        """,
        (str(teamspace_id), member_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "teamspace not found")
    if min_role and _TEAMSPACE_ROLE_RANK[row["role"]] < _TEAMSPACE_ROLE_RANK[min_role]:
        raise HTTPException(403, "insufficient teamspace role")
    return row["role"]


def effective_project_role(
    project_role: str | None,
    workspace_role: str | None,
    teamspace_visibility: str | None = None,
    is_teamspace_member: bool = False,
) -> str | None:
    """project_member로 명시 초대된 역할이 있으면 그걸 쓴다.

    그 다음으로 teamspace_member면(비공개 teamspace에 개별 초대된
    경우 포함 — workspace 소속 여부와 무관하게, project 단위 초대가
    workspace 합류를 의미하지 않는 것과 같은 이유로) 최소 viewer를 준다.
    그것도 아니면, workspace 소속이면서 teamspace가 'open'/'closed'(비공개가
    아님)일 때만 최소 viewer를 준다 — workspace 멤버끼리는 공개/참가제한
    teamspace 안의 project가 기본적으로 공유되어야 하지만, 'private'
    teamspace는 workspace 소속만으로 뚫리면 안 되기 때문.
    아무 것도 해당 안 되면 접근 불가(None)."""
    if project_role is not None:
        return project_role
    if is_teamspace_member:
        return "viewer"
    if workspace_role is not None and teamspace_visibility != "private":
        return "viewer"
    return None


def assert_project_role(cur, project_id, member_id: str, min_role: str | None = None) -> str:
    cur.execute(
        """
        SELECT pm.role AS project_role, wm.role AS workspace_role,
               t.visibility AS teamspace_visibility, (tm.member_id IS NOT NULL) AS is_teamspace_member
          FROM project p
          JOIN teamspace t ON t.id = p.teamspace_id AND t.deleted_at IS NULL
          LEFT JOIN project_member pm ON pm.project_id = p.id AND pm.member_id = %s
          LEFT JOIN workspace_member wm ON wm.workspace_id = p.workspace_id AND wm.member_id = %s
          LEFT JOIN teamspace_member tm ON tm.teamspace_id = t.id AND tm.member_id = %s
         WHERE p.id = %s AND p.deleted_at IS NULL
        """,
        (member_id, member_id, member_id, str(project_id)),
    )
    row = cur.fetchone()
    effective = (
        effective_project_role(row["project_role"], row["workspace_role"],
                                 row["teamspace_visibility"], row["is_teamspace_member"])
        if row else None
    )
    if effective is None:
        raise HTTPException(404, "project not found")
    if min_role and _PROJECT_ROLE_RANK[effective] < _PROJECT_ROLE_RANK[min_role]:
        raise HTTPException(403, "insufficient project role")
    return effective


def assert_installation_owned(cur, installation_id, member_id: str) -> None:
    """installation은 그것을 연동한 member 개인 소유다 — project 권한과 무관."""
    cur.execute(
        """
        SELECT 1 FROM installation i
          JOIN github_account ga ON ga.id = i.github_account_id
         WHERE i.id = %s AND i.status = 'active' AND ga.member_id = %s
        """,
        (str(installation_id), member_id),
    )
    if cur.fetchone() is None:
        raise HTTPException(404, "installation not found")


def _best_project_role(roles: list[str | None]) -> str | None:
    ranked = [r for r in roles if r is not None]
    if not ranked:
        return None
    return max(ranked, key=lambda r: _PROJECT_ROLE_RANK[r])


def assert_repository_access(cur, repository_id, member_id: str, min_role: str = "viewer") -> str:
    """repository는 여러 project에 동시에 attach될 수 있다 — caller가 속한

    모든 project 중 가장 높은 role로 판정한다(한 프로젝트에선 viewer,
    다른 프로젝트에선 manager면 manager로 취급). project_member로 명시
    초대되지 않았어도 그 project의 workspace 소속이면 viewer로 쳐준다
    (assert_project_role과 동일한 규칙 — [[effective_project_role]]).
    """
    cur.execute(
        """
        SELECT pm.role AS project_role, wm.role AS workspace_role,
               t.visibility AS teamspace_visibility, (tm.member_id IS NOT NULL) AS is_teamspace_member
          FROM project_repository pr
          JOIN project p ON p.id = pr.project_id AND p.deleted_at IS NULL
          JOIN teamspace t ON t.id = p.teamspace_id AND t.deleted_at IS NULL
          LEFT JOIN project_member pm ON pm.project_id = p.id AND pm.member_id = %s
          LEFT JOIN workspace_member wm ON wm.workspace_id = p.workspace_id AND wm.member_id = %s
          LEFT JOIN teamspace_member tm ON tm.teamspace_id = t.id AND tm.member_id = %s
         WHERE pr.repository_id = %s
        """,
        (member_id, member_id, member_id, str(repository_id)),
    )
    best = _best_project_role(
        effective_project_role(row["project_role"], row["workspace_role"],
                                 row["teamspace_visibility"], row["is_teamspace_member"])
        for row in cur.fetchall()
    )
    if best is None:
        raise HTTPException(404, "repository not found")
    if _PROJECT_ROLE_RANK[best] < _PROJECT_ROLE_RANK[min_role]:
        raise HTTPException(403, "insufficient project role")
    return best


def assert_scan_access(cur, scan_id, member_id: str, min_role: str = "viewer") -> str:
    cur.execute(
        """
        SELECT pm.role AS project_role, wm.role AS workspace_role,
               t.visibility AS teamspace_visibility, (tm.member_id IS NOT NULL) AS is_teamspace_member
          FROM scan s
          JOIN project_repository pr ON pr.repository_id = s.repository_id
          JOIN project p ON p.id = pr.project_id AND p.deleted_at IS NULL
          JOIN teamspace t ON t.id = p.teamspace_id AND t.deleted_at IS NULL
          LEFT JOIN project_member pm ON pm.project_id = p.id AND pm.member_id = %s
          LEFT JOIN workspace_member wm ON wm.workspace_id = p.workspace_id AND wm.member_id = %s
          LEFT JOIN teamspace_member tm ON tm.teamspace_id = t.id AND tm.member_id = %s
         WHERE s.id = %s
        """,
        (member_id, member_id, member_id, str(scan_id)),
    )
    best = _best_project_role(
        effective_project_role(row["project_role"], row["workspace_role"],
                                 row["teamspace_visibility"], row["is_teamspace_member"])
        for row in cur.fetchall()
    )
    if best is None:
        raise HTTPException(404, "scan not found")
    if _PROJECT_ROLE_RANK[best] < _PROJECT_ROLE_RANK[min_role]:
        raise HTTPException(403, "insufficient project role")
    return best
