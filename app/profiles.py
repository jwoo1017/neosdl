"""멤버 공개 프로필(닉네임·이름·아바타 URL) — 모든 API 가 같은 모양으로 내려주게 한 곳에 둔다.

DB 엔 아바타 스토리지 키만 있다(sql/028). URL 은 여기서 조립한다:
- upload   → /api/files/avatars/<파일명>?v=<갱신시각> (인증 프록시, app/avatars.py)
- github   → 연결된 GitHub 계정의 avatar_url
- initials → None (프론트 components/Avatar.jsx 가 이름 첫 글자 SVG 로 그린다)

쿼리 쪽은 PROFILE_COLUMNS 를 SELECT 에 끼워 넣고(member 별칭 m), 결과 행을
public_profile() 에 넘기면 된다.
"""

from __future__ import annotations

AVATAR_ROUTE = "/api/files/avatars/"

# member 를 m 으로 조인한 쿼리에 그대로 끼워 넣는다.
PROFILE_COLUMNS = """
    m.id AS member_id, m.nickname, m.name, m.avatar_source, m.avatar_key, m.avatar_updated_at,
    (SELECT ga.avatar_url FROM github_account ga
      WHERE ga.member_id = m.id AND ga.avatar_url IS NOT NULL
      ORDER BY ga.linked_at LIMIT 1) AS github_avatar_url
"""


def avatar_url(row: dict) -> str | None:
    source = row.get("avatar_source") or "initials"
    if source == "upload" and row.get("avatar_key"):
        name = row["avatar_key"].rsplit("/", 1)[-1]
        updated = row.get("avatar_updated_at")
        version = int(updated.timestamp()) if updated else 0
        return f"{AVATAR_ROUTE}{name}?v={version}"   # 교체 시 브라우저 캐시를 끊는다
    if source == "github":
        return row.get("github_avatar_url")
    return None


def public_profile(row: dict) -> dict:
    """PROFILE_COLUMNS 로 조회한 행 → {id, nickname, name, avatar_url}."""
    return {
        "id": str(row["member_id"]),
        "nickname": row["nickname"],
        "name": row.get("name") or row["nickname"],
        "avatar_url": avatar_url(row),
    }


_INTERNAL_KEYS = ("avatar_source", "avatar_key", "avatar_updated_at", "github_avatar_url")


def with_profile(row: dict) -> dict:
    """멤버 목록 행(PROFILE_COLUMNS + role 등) → 내부 아바타 컬럼을 빼고 name/avatar_url 을 단다."""
    out = {k: v for k, v in row.items() if k not in _INTERNAL_KEYS}
    out["name"] = row.get("name") or row["nickname"]
    out["avatar_url"] = avatar_url(row)
    return out


def load_profile(cur, member_id: str) -> dict | None:
    cur.execute(f"SELECT {PROFILE_COLUMNS}, m.email, m.email_verified_at FROM member m WHERE m.id=%s",
                (str(member_id),))
    row = cur.fetchone()
    if row is None:
        return None
    return {**public_profile(row), "email": row["email"], "avatar_source": row["avatar_source"],
            "has_github_avatar": row["github_avatar_url"] is not None}


def load_authors(cur, member_ids, project_id: str) -> dict[str, dict]:
    """채팅 작성자 프로필 + 그 프로젝트가 속한 팀스페이스 안의 역할(admin/member) — 말풍선 옆 표시용.

    반환: {member_id: {id, nickname, name, avatar_url, role}} (role 은 팀스페이스를 나갔으면 None).
    """
    ids = sorted({str(i) for i in member_ids if i})
    if not ids:
        return {}
    cur.execute(
        f"""
        SELECT {PROFILE_COLUMNS}, tm.role
          FROM member m
          LEFT JOIN teamspace_member tm
                 ON tm.member_id = m.id
                AND tm.teamspace_id = (SELECT teamspace_id FROM project WHERE id = %s)
         WHERE m.id = ANY(%s::uuid[])
        """,
        (str(project_id), ids),
    )
    return {str(r["member_id"]): {**public_profile(r), "role": r["role"]} for r in cur.fetchall()}

