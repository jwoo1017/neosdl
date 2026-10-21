"""접속 상태(하트비트) — 상단바의 "지금 활동 중인 팀스페이스 멤버" (sql/030).

- POST /api/presence: 프론트가 30초마다 {teamspace_id, state} 를 보낸다.
  state: active(탭이 보이고 최근 5분 내 입력) | idle(자리 비움) | offline(탭 닫힘 — 행 삭제).
- GET /api/teamspaces/{id}/presence: 그 팀스페이스를 보고 있고 90초 안에 신호가 온 멤버.

팀 규모가 작아 WebSocket 대신 짧은 주기 polling 으로 단순하게 간다. 쓰기는 사용자당
1행 upsert 라 30초 주기여도 부하가 거의 없다.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app import profiles
from app.auth import assert_teamspace_role, get_current_member
from app.db import tx

router = APIRouter(tags=["presence"])

ONLINE_WINDOW_SECONDS = 90


class HeartbeatIn(BaseModel):
    teamspace_id: UUID | None = None
    state: Literal["active", "idle", "offline"] = "active"


@router.post("/presence", status_code=204)
def heartbeat(body: HeartbeatIn, member: dict = Depends(get_current_member)):
    with tx() as cur:
        if body.state == "offline":
            cur.execute("DELETE FROM member_presence WHERE member_id=%s", (member["id"],))
            return Response(status_code=204)
        teamspace_id = None
        if body.teamspace_id is not None:
            # 멤버가 아닌 팀스페이스를 "보고 있다" 고 기록하지 않는다(남의 접속 목록에 끼어들기 방지).
            assert_teamspace_role(cur, body.teamspace_id, member["id"])
            teamspace_id = str(body.teamspace_id)
        cur.execute(
            """
            INSERT INTO member_presence (member_id, teamspace_id, state, last_seen_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (member_id) DO UPDATE
                SET teamspace_id = EXCLUDED.teamspace_id, state = EXCLUDED.state, last_seen_at = now()
            """,
            (member["id"], teamspace_id, body.state),
        )
    return Response(status_code=204)


@router.get("/teamspaces/{teamspace_id}/presence")
def teamspace_presence(teamspace_id: UUID, member: dict = Depends(get_current_member)):
    """팀스페이스 멤버만 조회 가능(아니면 404). 활동 중 → 자리 비움 순, 그 안에선 닉네임 순."""
    with tx() as cur:
        assert_teamspace_role(cur, teamspace_id, member["id"])
        cur.execute(
            f"""
            SELECT {profiles.PROFILE_COLUMNS}, tm.role, p.state, p.last_seen_at
              FROM member_presence p
              JOIN teamspace_member tm ON tm.member_id = p.member_id AND tm.teamspace_id = p.teamspace_id
              JOIN member m ON m.id = p.member_id AND m.deleted_at IS NULL
             WHERE p.teamspace_id = %s
               AND p.last_seen_at > now() - make_interval(secs => %s)
             ORDER BY (p.state = 'active') DESC, m.nickname
            """,
            (str(teamspace_id), ONLINE_WINDOW_SECONDS),
        )
        return [{**profiles.public_profile(r), "role": r["role"], "state": r["state"]} for r in cur.fetchall()]
