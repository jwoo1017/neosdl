"""세션 JWT 발급/검증 + workspace/project 역할 기반 접근 체크.

서버 세션 테이블 없이 httpOnly JWT 쿠키만 쓴다. get_current_member 는
쿠키의 클레임을 그대로 신뢰하고 매 요청마다 DB를 왕복하지 않는다 —
계정 삭제/차단 기능이 아직 없어서 지금은 그래도 안전하고, 그런 기능이
생기면 그때 조회를 추가하면 된다(지금 미리 넣어봐야 죽은 코드).

역할은 고정 계층이다 — workspace: member < admin, project: viewer <
developer < manager < owner. 매 체크는 SELECT 한 번으로 caller의 role을
가져온 뒤 Python에서 랭크를 비교한다. 다른 테넌트의 존재 여부가 응답
코드로 새지 않도록, 관계 자체가 없으면 404, 관계는 있는데 역할이
모자라면 403이다(이미 소속이 확인된 뒤라 자원 존재를 더 숨길 이유가 없다).
"""

from __future__ import annotations

import time

import jwt
from fastapi import Cookie, HTTPException, WebSocket

from app.config import settings

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
    }


def get_current_member(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    member = _decode_session(session)
    if member is None:
        raise HTTPException(401, "not authenticated")
    return member


def get_current_member_optional(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict | None:
    """setup URL 콜백처럼 "로그인 안 됐으면 리다이렉트" 를 직접 하고 싶을 때 씀."""
    return _decode_session(session)


def get_current_member_ws(websocket: WebSocket) -> dict | None:
    """WebSocket 라우트는 `Depends(Cookie(...))`가 안 통해서(HTTP 요청 전용) 따로 둔다 —
    Starlette가 WS 핸드셰이크의 쿠키도 `websocket.cookies`로 똑같이 파싱해준다."""
    return _decode_session(websocket.cookies.get(SESSION_COOKIE))


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
