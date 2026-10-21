"""팀스페이스 협업 표시 — 채팅 작성자/역할(sql/029), 접속 상태(030), 개인 페이지(031).

실제 Postgres 가 필요하다(없거나 해당 마이그레이션 미적용이면 skip).
"""

from __future__ import annotations

import pytest

from app import chat, chat_bus


def _column_exists(db, table: str, column: str) -> bool:
    with db() as cur:
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
                    (table, column))
        return cur.fetchone() is not None


def _team(owner, *, teamspace_name="개발팀"):
    """owner 의 워크스페이스 → 팀스페이스 → 프로젝트를 만들어 id 들을 돌려준다."""
    c = owner.client
    ws = c.post("/api/workspaces", json={"name": f"{owner.nickname}-ws", "type": "company"}).json()
    ts = c.post(f"/api/workspaces/{ws['id']}/teamspaces", json={"name": teamspace_name}).json()
    proj = c.post(f"/api/teamspaces/{ts['id']}/projects", json={"name": "채팅프로젝트"}).json()
    return ws["id"], ts["id"], proj["id"]


def _join(db, member, workspace_id, teamspace_id, role="member"):
    with db() as cur:
        cur.execute("INSERT INTO workspace_member (workspace_id, member_id, role) VALUES (%s, %s, 'member')",
                    (workspace_id, member.id))
        cur.execute("INSERT INTO teamspace_member (teamspace_id, member_id, role) VALUES (%s, %s, %s)",
                    (teamspace_id, member.id, role))


# --------------------------------------------------------------------- D. 채팅 작성자 + 역할

@pytest.mark.integration
def test_chat_messages_carry_author_profile_and_teamspace_role(db, api_users):
    if not _column_exists(db, "chat_message", "author_member_id"):
        pytest.skip("sql/029_chat_author.sql 미적용")
    alice, bob = api_users.new("alice"), api_users.new("bob")
    ws_id, ts_id, project_id = _team(alice)
    _join(db, bob, ws_id, ts_id, role="member")

    session = alice.client.post(f"/api/projects/{project_id}/chat/sessions", json={}).json()
    chat._insert_user_message(session["id"], project_id, "앨리스 질문", alice.id, [], None)
    chat._insert_user_message(session["id"], project_id, "밥 질문", bob.id, [], None)

    msgs = bob.client.get(f"/api/projects/{project_id}/chat/sessions/{session['id']}/messages").json()
    by_text = {m["content"]: m for m in msgs}
    assert by_text["앨리스 질문"]["author"]["nickname"] == "alice"
    assert by_text["앨리스 질문"]["author"]["role"] == "admin"      # 팀스페이스를 만든 사람
    assert by_text["밥 질문"]["author"]["role"] == "member"
    assert by_text["밥 질문"]["author"]["name"] == "bob 실명"
    assert "avatar_url" in by_text["밥 질문"]["author"]

    # 실시간 브로드캐스트 경로(chat_bus)도 같은 작성자 정보를 싣는다.
    pushed = chat_bus._fetch_message(by_text["밥 질문"]["id"])
    assert pushed["author"]["id"] == bob.id and pushed["author"]["role"] == "member"


# --------------------------------------------------------------------- E. 접속 상태

@pytest.mark.integration
def test_presence_heartbeat_visibility_and_expiry(db, api_users):
    with db() as cur:
        cur.execute("SELECT to_regclass('public.member_presence') AS t")
        if cur.fetchone()["t"] is None:
            pytest.skip("sql/030_member_presence.sql 미적용")
    alice, bob, carol = api_users.new("alice"), api_users.new("bob"), api_users.new("carol")
    ws_id, ts_id, _ = _team(alice)
    _join(db, bob, ws_id, ts_id)

    assert alice.client.post("/api/presence", json={"teamspace_id": ts_id, "state": "active"}).status_code == 204
    assert bob.client.post("/api/presence", json={"teamspace_id": ts_id, "state": "idle"}).status_code == 204

    seen = bob.client.get(f"/api/teamspaces/{ts_id}/presence").json()
    assert [m["nickname"] for m in seen] == ["alice", "bob"]          # active 먼저
    assert seen[0]["state"] == "active" and seen[0]["role"] == "admin"
    assert seen[1]["state"] == "idle" and seen[1]["role"] == "member"

    # 비멤버는 조회도, 그 팀스페이스로 하트비트도 못 한다.
    assert carol.client.get(f"/api/teamspaces/{ts_id}/presence").status_code == 404
    assert carol.client.post("/api/presence", json={"teamspace_id": ts_id, "state": "active"}).status_code == 404

    # 90초 넘게 신호가 없으면 빠진다.
    with db() as cur:
        cur.execute("UPDATE member_presence SET last_seen_at = now() - interval '2 minutes' WHERE member_id=%s",
                    (bob.id,))
    assert [m["nickname"] for m in alice.client.get(f"/api/teamspaces/{ts_id}/presence").json()] == ["alice"]

    # 탭 종료(offline)는 즉시 빠진다.
    assert alice.client.post("/api/presence", json={"state": "offline"}).status_code == 204
    assert alice.client.get(f"/api/teamspaces/{ts_id}/presence").json() == []


# --------------------------------------------------------------------- F. 개인 페이지

@pytest.mark.integration
def test_personal_space_is_private_and_sharing_by_move(db, api_users):
    if not _column_exists(db, "teamspace", "personal_owner_id"):
        pytest.skip("sql/031_personal_space.sql 미적용")
    alice, bob = api_users.new("alice"), api_users.new("bob")
    ws_id, team_ts, _ = _team(alice)
    _join(db, bob, ws_id, team_ts)

    personal = alice.client.get(f"/api/workspaces/{ws_id}/personal-space").json()
    assert personal["personal"] is True and personal["visibility"] == "private"
    # get-or-create: 두 번 불러도 같은 공간
    assert alice.client.get(f"/api/workspaces/{ws_id}/personal-space").json()["id"] == personal["id"]
    # 일반 팀스페이스 목록에는 안 나온다.
    assert personal["id"] not in [t["id"] for t in alice.client.get(f"/api/workspaces/{ws_id}/teamspaces").json()]

    proj = alice.client.post(f"/api/teamspaces/{personal['id']}/projects", json={"name": "나만의 실험"}).json()
    bob_projects = [p["id"] for p in bob.client.get(f"/api/workspaces/{ws_id}/projects").json()]
    assert proj["id"] not in bob_projects                                   # 팀원에겐 안 보임
    assert bob.client.get(f"/api/teamspaces/{personal['id']}").status_code == 404

    # 개인 공간엔 초대·가입·이름 변경을 할 수 없다.
    assert alice.client.post(f"/api/teamspaces/{personal['id']}/invites",
                             json={"role": "member"}).status_code == 400
    assert bob.client.post(f"/api/teamspaces/{personal['id']}/join").status_code in (400, 404)
    assert alice.client.patch(f"/api/teamspaces/{personal['id']}", json={"name": "x"}).status_code == 400

    # 드래그(이동)로 팀스페이스에 공유 → 팀원도 본다.
    r = alice.client.post(f"/api/projects/{proj['id']}/move", json={"teamspace_id": team_ts})
    assert r.status_code == 200, r.text
    assert proj["id"] in [p["id"] for p in bob.client.get(f"/api/workspaces/{ws_id}/projects").json()]

    # 다른 멤버가 명시적으로 붙은 팀 프로젝트는 개인 페이지로 되돌릴 수 없다.
    with db() as cur:
        cur.execute("INSERT INTO project_member (project_id, member_id, role) VALUES (%s, %s, 'developer')",
                    (proj["id"], bob.id))
    r = alice.client.post(f"/api/projects/{proj['id']}/move", json={"teamspace_id": personal["id"]})
    assert r.status_code == 400 and r.json()["detail"] == "cannot_make_shared_project_personal"
    # 나 혼자인 팀 프로젝트는 되돌릴 수 있다.
    with db() as cur:
        cur.execute("DELETE FROM project_member WHERE project_id=%s AND member_id=%s", (proj["id"], bob.id))
    assert alice.client.post(f"/api/projects/{proj['id']}/move",
                             json={"teamspace_id": personal["id"]}).status_code == 200
