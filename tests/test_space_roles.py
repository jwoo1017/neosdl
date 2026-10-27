"""워크스페이스·팀스페이스 역할 정책 owner > admin > member (sql/038·039, app/auth.assert_can_manage_member).

실제 Postgres 가 필요하다(없거나 039 미적용이면 skip).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def owner_role_ready(db):
    with db() as cur:
        cur.execute("SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'workspace_role' AND e.enumlabel = 'owner'")
        if cur.fetchone() is None:
            pytest.skip("sql/038·039 미적용")


def _space(alice, kind):
    """(멤버 API prefix, 공간 id). kind = workspace | teamspace — 생성자 alice 가 owner."""
    ws = alice.client.post("/api/workspaces", json={"name": "역할테스트", "type": "company"})
    assert ws.status_code == 201 and ws.json()["role"] == "owner", ws.text
    if kind == "workspace":
        return "/api/workspaces", ws.json()["id"], ws.json()["id"]
    ts = alice.client.post(f"/api/workspaces/{ws.json()['id']}/teamspaces", json={"name": "역할팀"})
    assert ts.status_code == 201 and ts.json()["role"] == "owner", ts.text
    return "/api/teamspaces", ts.json()["id"], ws.json()["id"]


def _add(db, kind, space_id, workspace_id, user, role):
    with db() as cur:
        cur.execute("INSERT INTO workspace_member (workspace_id, member_id, role) VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING", (workspace_id, user.id, role if kind == "workspace" else "member"))
        if kind == "teamspace":
            cur.execute("INSERT INTO teamspace_member (teamspace_id, member_id, role) VALUES (%s, %s, %s)",
                        (space_id, user.id, role))


def _roles(owner, base, space_id):
    return {m["member_id"]: m["role"] for m in owner.client.get(f"{base}/{space_id}").json()["members"]}


@pytest.mark.parametrize("kind", ["workspace", "teamspace"])
def test_sole_owner_cannot_leave_or_remove_self(db, api_users, owner_role_ready, kind):
    alice = api_users.new("alice")
    base, sid, _ = _space(alice, kind)

    # 이번 버그: 유일 관리자가 본인 '제거' → 409. 이제 본인은 제거 대상이 아니고(400), 나가기는 last_owner 409.
    r = alice.client.delete(f"{base}/{sid}/members/{alice.id}")
    assert r.status_code == 400 and r.json()["detail"] == "use_leave_endpoint"
    r = alice.client.post(f"{base}/{sid}/leave")
    assert r.status_code == 409 and r.json()["detail"] == "last_owner"
    r = alice.client.patch(f"{base}/{sid}/members/{alice.id}", json={"role": "admin"})
    assert r.status_code == 409 and r.json()["detail"] == "last_owner"


@pytest.mark.parametrize("kind", ["workspace", "teamspace"])
def test_admin_manages_members_only(db, api_users, owner_role_ready, kind):
    alice, bob, carol, dave = (api_users.new(n) for n in ("alice", "bob", "carol", "dave"))
    base, sid, ws_id = _space(alice, kind)
    _add(db, kind, sid, ws_id, bob, "admin")
    _add(db, kind, sid, ws_id, carol, "admin")
    _add(db, kind, sid, ws_id, dave, "member")

    # admin 은 다른 admin·owner 를 건드릴 수 없다.
    assert bob.client.delete(f"{base}/{sid}/members/{carol.id}").json()["detail"] == "owner_required"
    assert bob.client.delete(f"{base}/{sid}/members/{alice.id}").status_code == 403
    assert bob.client.patch(f"{base}/{sid}/members/{carol.id}", json={"role": "member"}).status_code == 403
    # owner 지정도 owner 만.
    assert bob.client.patch(f"{base}/{sid}/members/{dave.id}", json={"role": "owner"}).status_code == 403
    # 공간 삭제도 owner 만.
    assert bob.client.delete(f"{base}/{sid}").status_code == 403

    # member 는 승격(admin 까지)·제거할 수 있다.
    assert bob.client.patch(f"{base}/{sid}/members/{dave.id}", json={"role": "admin"}).status_code == 200
    assert alice.client.patch(f"{base}/{sid}/members/{dave.id}", json={"role": "member"}).status_code == 200
    assert bob.client.delete(f"{base}/{sid}/members/{dave.id}").status_code == 204

    # owner 는 admin 도 제거한다. admin 은 스스로 나갈 수 있다.
    assert alice.client.delete(f"{base}/{sid}/members/{carol.id}").status_code == 204
    assert bob.client.post(f"{base}/{sid}/leave").status_code == 204
    assert _roles(alice, base, sid) == {alice.id: "owner"}


@pytest.mark.parametrize("kind", ["workspace", "teamspace"])
def test_transfer_ownership_then_leave(db, api_users, owner_role_ready, kind):
    alice, bob = api_users.new("alice"), api_users.new("bob")
    base, sid, ws_id = _space(alice, kind)
    _add(db, kind, sid, ws_id, bob, "member")

    assert alice.client.patch(f"{base}/{sid}/members/{bob.id}", json={"role": "owner"}).status_code == 200
    assert alice.client.post(f"{base}/{sid}/leave").status_code == 204
    assert _roles(bob, base, sid) == {bob.id: "owner"}
    # 목록은 owner → admin → member 순.
    _add(db, kind, sid, ws_id, alice, "member")
    assert [m["role"] for m in bob.client.get(f"{base}/{sid}").json()["members"]] == ["owner", "member"]


def test_invite_accept_never_downgrades_owner(db, api_users, owner_role_ready):
    alice, bob = api_users.new("alice"), api_users.new("bob")
    base, sid, ws_id = _space(alice, "workspace")
    _add(db, "workspace", sid, ws_id, bob, "member")
    assert alice.client.patch(f"{base}/{sid}/members/{bob.id}", json={"role": "owner"}).status_code == 200

    inv = alice.client.post(f"{base}/{sid}/invites", json={"role": "member", "target_email": bob.email}).json()
    assert bob.client.post(f"/api/workspace-invites/{inv['token']}/accept").status_code == 201
    assert _roles(alice, base, sid)[bob.id] == "owner"
    # 초대로 owner 를 줄 수는 없다.
    assert alice.client.post(f"{base}/{sid}/invites", json={"role": "owner"}).status_code == 422
