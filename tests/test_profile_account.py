"""프로필·아바타·마이페이지(sql/028, app/avatars.py, app/me.py) 통합 테스트.

실제 Postgres(028 적용)가 필요하다 — 없으면 skip. 아바타 파일은 tmp_path 의 LocalStorage 에 쓴다.
"""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from app import avatars, email_verification, me
from app.storage import LocalStorage
from tests.conftest import disable_last_admin_triggers

PW = "pw-12345678"


def _schema_ready(db) -> bool:
    with db() as cur:
        cur.execute("SELECT count(*) AS n FROM information_schema.columns "
                    "WHERE table_name='member' AND column_name IN ('avatar_key','token_version','name')")
        return cur.fetchone()["n"] == 3


@pytest.fixture
def env(db, monkeypatch, tmp_path):
    if not _schema_ready(db):
        pytest.skip("sql/028_member_profile.sql 미적용")
    from fastapi.testclient import TestClient

    from app.main import app

    store = LocalStorage(tmp_path)
    monkeypatch.setattr(avatars, "storage", lambda: store)
    monkeypatch.setattr(me, "storage", lambda: store)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(email_verification, "_send_code_email",
                        lambda email, nickname, code, purpose="signup": sent.append((email, code)) or True)
    created: list[str] = []

    def new_client():
        return TestClient(app)

    def signup(client, email=None):
        email = email or f"prof-{uuid.uuid4().hex[:10]}@example.com"
        r = client.post("/api/auth/signup",
                        json={"email": email, "password": PW, "name": "홍길동", "nickname": "길동",
                              "privacy_agreed": True})
        assert r.status_code == 201, r.text
        code = sent[-1][1]
        assert client.post("/api/auth/email-verification/verify", json={"code": code}).status_code == 204
        created.append(email)
        return email

    class Env:
        pass

    e = Env()
    e.client, e.new_client, e.signup, e.sent, e.store, e.root = new_client(), new_client, signup, sent, store, tmp_path
    yield e
    with db() as cur:
        disable_last_admin_triggers(cur)   # 마지막 admin 보호 트리거만 이 트랜잭션 동안 끈다
        for email in created:
            cur.execute("SELECT id FROM member WHERE lower(email)=%s", (email,))
            row = cur.fetchone()
            if row:
                cur.execute("DELETE FROM workspace_member WHERE member_id=%s", (row["id"],))
                cur.execute("DELETE FROM member WHERE id=%s", (row["id"],))


def _image_bytes(fmt: str, size=(800, 600), exif: bool = False) -> bytes:
    img = Image.new("RGB", size, (200, 30, 30))
    buf = io.BytesIO()
    kwargs = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "SecretCam"          # Make
        ex[0x8825] = {2: (37.0, 30.0, 0.0)}  # GPSInfo
        kwargs["exif"] = ex.tobytes()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def _upload(client, data: bytes, filename="a.png", ctype="image/png"):
    return client.post("/api/me/avatar", files={"file": (filename, data, ctype)})


def _stored_files(e) -> list:
    d = e.root / "avatars"
    return sorted(p.name for p in d.glob("*.webp")) if d.exists() else []


# --------------------------------------------------------------------- 가입 / 프로필

@pytest.mark.integration
def test_signup_requires_name_and_me_returns_profile(env):
    r = env.client.post("/api/auth/signup", json={"email": "x@example.com", "password": PW, "nickname": "n"})
    assert r.status_code == 422       # name 누락
    env.signup(env.client)
    me_ = env.client.get("/api/me").json()
    assert me_["name"] == "홍길동" and me_["nickname"] == "길동" and me_["avatar_url"] is None

    r = env.client.patch("/api/me", json={"name": "김철수", "nickname": "철수"})
    assert r.status_code == 200 and r.json()["name"] == "김철수"
    assert env.client.get("/api/me").json()["nickname"] == "철수"


# --------------------------------------------------------------------- 아바타 (사용자 요구 테스트 목록)

@pytest.mark.integration
@pytest.mark.parametrize("fmt,ctype", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
def test_valid_images_are_reencoded_to_webp_without_exif(env, fmt, ctype):
    env.signup(env.client)
    r = _upload(env.client, _image_bytes(fmt, exif=(fmt == "JPEG")), ctype=ctype)
    assert r.status_code == 200, r.text
    url = r.json()["avatar_url"]
    assert url.startswith("/api/files/avatars/") and ".webp?v=" in url

    files = _stored_files(env)
    assert len(files) == 1
    with Image.open(env.root / "avatars" / files[0]) as out:
        assert out.format == "WEBP"
        assert max(out.size) <= 512
        assert "exif" not in out.info and len(out.getexif()) == 0

    got = env.client.get(url.split("?")[0])
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/webp"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["cache-control"] == "private, max-age=86400"
    assert got.headers["content-disposition"] == "inline"


@pytest.mark.integration
def test_text_file_with_png_extension_is_rejected(env):
    env.signup(env.client)
    r = _upload(env.client, b"definitely not an image", filename="evil.png")
    assert r.status_code == 400 and _stored_files(env) == []


@pytest.mark.integration
def test_svg_and_gif_are_rejected(env):
    env.signup(env.client)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert _upload(env.client, svg, filename="a.svg", ctype="image/svg+xml").status_code == 400
    assert _upload(env.client, _image_bytes("GIF"), filename="a.gif", ctype="image/gif").status_code == 400
    assert _stored_files(env) == []


@pytest.mark.integration
def test_over_2mb_is_413(env):
    env.signup(env.client)
    r = _upload(env.client, b"\x89PNG" + b"0" * (2 * 1024 * 1024))
    assert r.status_code == 413


@pytest.mark.integration
def test_decompression_bomb_is_rejected(env):
    env.signup(env.client)
    buf = io.BytesIO()
    Image.new("1", (9000, 9000)).save(buf, format="PNG")    # 수 KB 지만 8100만 픽셀
    assert len(buf.getvalue()) < 2 * 1024 * 1024
    r = _upload(env.client, buf.getvalue())
    assert r.status_code == 400 and _stored_files(env) == []


@pytest.mark.integration
@pytest.mark.parametrize("name", ["..%2F..%2Fetc%2Fpasswd", "..%2Fx.webp", "%2Fetc%2Fpasswd", "x.webp", "abc"])
def test_path_traversal_keys_are_404(env, name):
    env.signup(env.client)
    assert env.client.get(f"/api/files/avatars/{name}").status_code == 404


@pytest.mark.integration
def test_unauthenticated_get_is_401(env):
    env.signup(env.client)
    url = _upload(env.client, _image_bytes("PNG")).json()["avatar_url"].split("?")[0]
    anon = env.new_client()
    assert anon.get(url).status_code == 401


@pytest.mark.integration
def test_replacing_avatar_deletes_previous_file(env):
    env.signup(env.client)
    _upload(env.client, _image_bytes("PNG"))
    first = _stored_files(env)
    _upload(env.client, _image_bytes("JPEG"), ctype="image/jpeg")
    second = _stored_files(env)
    assert len(first) == 1 and len(second) == 1 and first != second

    assert env.client.delete("/api/me/avatar").json()["avatar_url"] is None
    assert _stored_files(env) == []


# --------------------------------------------------------------------- 세션 무효화 / 비밀번호 / 이메일

@pytest.mark.integration
def test_password_change_logs_out_other_sessions(env):
    email = env.signup(env.client)
    other = env.new_client()
    assert other.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 204
    assert other.get("/api/me").status_code == 200

    r = env.client.post("/api/me/password", json={"current_password": "wrong", "new_password": "new-pw-1234"})
    assert r.status_code == 403
    r = env.client.post("/api/me/password", json={"current_password": PW, "new_password": "new-pw-1234"})
    assert r.status_code == 204
    assert env.client.get("/api/me").status_code == 200      # 지금 기기는 새 쿠키로 유지
    assert other.get("/api/me").status_code == 401           # 다른 기기는 끊김


@pytest.mark.integration
def test_email_change_requires_code_and_rejects_duplicates(env):
    email = env.signup(env.client)
    taken = env.signup(env.new_client())
    assert env.client.post("/api/me/email", json={"new_email": taken, "password": PW}).status_code == 409

    new = f"changed-{uuid.uuid4().hex[:8]}@example.com"
    r = env.client.post("/api/me/email", json={"new_email": new, "password": PW})
    assert r.status_code == 200, r.text
    assert env.client.get("/api/me").json()["email"] == email      # 확인 전엔 그대로
    code = env.sent[-1][1]
    r = env.client.post("/api/me/email/verify", json={"code": code})
    assert r.status_code == 200 and r.json()["email"] == new

    from app.db import tx
    with tx() as cur:   # teardown 이 원래 주소로 찾아 지울 수 있게 되돌린다
        cur.execute("UPDATE member SET email=%s WHERE lower(email)=%s", (email, new))


# --------------------------------------------------------------------- 회원 탈퇴

@pytest.mark.integration
def test_withdraw_blocks_sole_admin_then_anonymizes(env, db):
    email = env.signup(env.client)
    ws = env.client.post("/api/workspaces", json={"name": "탈퇴테스트", "type": "company"})
    assert ws.status_code == 201, ws.text
    ws_id = ws.json()["id"]

    other_email = env.signup(env.new_client())
    with db() as cur:
        cur.execute("SELECT id FROM member WHERE lower(email)=%s", (other_email,))
        other_id = cur.fetchone()["id"]
        cur.execute("INSERT INTO workspace_member (workspace_id, member_id, role) VALUES (%s, %s, 'member')",
                    (ws_id, other_id))

    body = {"password": PW, "confirm": "탈퇴합니다"}
    r = env.client.request("DELETE", "/api/me", json=body)
    assert r.status_code == 409 and r.json()["detail"]["items"][0]["kind"] == "workspace"

    with db() as cur:   # 다른 멤버를 admin 으로 올리면 탈퇴 가능
        cur.execute("UPDATE workspace_member SET role='admin' WHERE workspace_id=%s AND member_id=%s",
                    (ws_id, other_id))
    assert env.client.request("DELETE", "/api/me", json={**body, "confirm": "틀림"}).status_code == 400
    assert env.client.request("DELETE", "/api/me", json=body).status_code == 204
    assert env.client.get("/api/me").status_code == 401

    with db() as cur:
        cur.execute("SELECT email, name, deleted_at FROM member WHERE id = (SELECT created_by FROM workspace "
                    "WHERE id=%s)", (ws_id,))
        row = cur.fetchone()
        assert row["email"] is None and row["name"] == me.WITHDRAWN_NAME and row["deleted_at"] is not None
        # 정리: 테스트가 만든 워크스페이스와 탈퇴 처리된 행을 지운다(이력 FK 때문에 워크스페이스 먼저).
        cur.execute("SELECT created_by FROM workspace WHERE id=%s", (ws_id,))
        withdrawn_id = cur.fetchone()["created_by"]
        disable_last_admin_triggers(cur)
        cur.execute("DELETE FROM workspace WHERE id=%s", (ws_id,))
        cur.execute("DELETE FROM member WHERE id=%s", (withdrawn_id,))

    # 같은 이메일로 다시 가입할 수 있다.
    env.signup(env.new_client(), email=email)
