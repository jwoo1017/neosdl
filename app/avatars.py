"""프로필 아바타 업로드/삭제/조회.

보안 원칙(업로드 파일은 전부 적대적이라고 가정):
- 형식은 확장자·Content-Type 이 아니라 Pillow 로 실제 내용을 열어 판별 — PNG/JPEG/WebP 만.
  SVG(스크립트 삽입), GIF, HEIC 등은 거부.
- 디코딩 전에 픽셀 수를 먼저 보고(압축 폭탄), Pillow 의 MAX_IMAGE_PIXELS 경고도 에러로 승격.
- 원본은 저장하지 않는다 — 최대 512x512 로 줄이고 WebP 로 새로 인코딩해 EXIF(GPS 등)와
  숨은 데이터를 버린다. 회전 정보만 먼저 픽셀에 반영(exif_transpose)한다.
- 파일 이름은 서버가 uuid4 로 만든다. DB 엔 스토리지 키만 저장한다(app/storage.py).
- 교체 순서: 새 파일 저장 → DB 키 갱신(커밋) → 성공하면 이전 파일 삭제. 중간에 실패해도
  DB 가 존재하지 않는 파일을 가리키는 상태는 생기지 않는다.
- 조회는 로그인한 사용자만, 백엔드가 파일을 읽어 응답한다(키를 URL 로 노출하지 않음).
"""

from __future__ import annotations

import io
import logging
import re
import uuid
import warnings
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel

from app import audit, profiles
from app.auth import get_current_member, get_current_member_allow_unverified
from app.config import settings
from app.db import tx
from app.storage import storage

log = logging.getLogger("app.avatars")
router = APIRouter(tags=["avatars"])

ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
MAX_SIDE = 512
# 디코딩 전 허용 픽셀 수 — 2MB 안에 들어가는 정상 사진은 이보다 훨씬 작다. 넘으면 압축 폭탄 의심.
MAX_SOURCE_PIXELS = 40_000_000
_NAME_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.webp$")


class AvatarSourceIn(BaseModel):
    source: Literal["initials", "upload", "github"]


def _reencode(data: bytes) -> bytes:
    """검증 + 512px 축소 + WebP 재인코딩. 실패는 HTTPException(400) — 사유는 일부러 뭉뚱그린다."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:          # 헤더만 읽음(아직 디코딩 전)
                if img.format not in ALLOWED_FORMATS:
                    raise HTTPException(400, "unsupported_image_type")
                width, height = img.size
                if width * height > MAX_SOURCE_PIXELS:
                    raise HTTPException(400, "image_too_large")
                img = ImageOps.exif_transpose(img)            # 회전만 픽셀에 반영(이후 EXIF 는 버림)
                img.thumbnail((MAX_SIDE, MAX_SIDE))
                has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
                img = img.convert("RGBA" if has_alpha else "RGB")
                out = io.BytesIO()
                # exif/icc 를 넘기지 않으므로 새 파일엔 메타데이터가 없다.
                img.save(out, format="WEBP", quality=85, method=4)
                return out.getvalue()
    except HTTPException:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError,
            ValueError, SyntaxError):
        raise HTTPException(400, "invalid_image")


@router.post("/me/avatar")
async def upload_avatar(file: UploadFile = File(...), member: dict = Depends(get_current_member)):
    limit = settings().avatar_max_bytes
    data = await file.read(limit + 1)            # 한도 +1 바이트만 읽어 초과 여부를 판단
    if len(data) > limit:
        raise HTTPException(413, "file_too_large")
    if not data:
        raise HTTPException(400, "invalid_image")

    webp = _reencode(data)
    new_key = f"avatars/{uuid.uuid4()}.webp"
    storage().save(new_key, webp)

    try:
        with tx() as cur:
            cur.execute("SELECT avatar_key FROM member WHERE id=%s FOR UPDATE", (member["id"],))
            old_key = cur.fetchone()["avatar_key"]
            cur.execute(
                "UPDATE member SET avatar_key=%s, avatar_source='upload', avatar_updated_at=now(), "
                "updated_at=now() WHERE id=%s",
                (new_key, member["id"]),
            )
            audit.record(cur, actor=f"user:{member['nickname']}", action="member.avatar.upload",
                         subject={"member_id": member["id"]}, payload={})
            profile = profiles.load_profile(cur, member["id"])
    except BaseException:
        storage().delete(new_key)                # DB 반영 실패 — 고아 파일을 남기지 않는다
        raise

    if old_key and old_key != new_key:
        try:
            storage().delete(old_key)            # 커밋 이후에만 이전 파일 삭제
        except Exception:
            log.warning("이전 아바타 파일 삭제 실패(key=%s) — 고아 파일로 남음", old_key, exc_info=True)
    return profile


@router.delete("/me/avatar")
def delete_avatar(member: dict = Depends(get_current_member)):
    with tx() as cur:
        cur.execute("SELECT avatar_key FROM member WHERE id=%s FOR UPDATE", (member["id"],))
        old_key = cur.fetchone()["avatar_key"]
        cur.execute(
            "UPDATE member SET avatar_key=NULL, avatar_source='initials', avatar_updated_at=now(), "
            "updated_at=now() WHERE id=%s",
            (member["id"],),
        )
        audit.record(cur, actor=f"user:{member['nickname']}", action="member.avatar.delete",
                     subject={"member_id": member["id"]}, payload={})
        profile = profiles.load_profile(cur, member["id"])
    if old_key:
        storage().delete(old_key)
    return profile


@router.patch("/me/avatar-source")
def set_avatar_source(body: AvatarSourceIn, member: dict = Depends(get_current_member)):
    """이니셜/업로드 사진/GitHub 사진 중 무엇을 보일지. 업로드 파일은 바꿔도 지우지 않는다(되돌릴 수 있게)."""
    with tx() as cur:
        profile = profiles.load_profile(cur, member["id"])
        cur.execute("SELECT avatar_key FROM member WHERE id=%s", (member["id"],))
        has_upload = cur.fetchone()["avatar_key"] is not None
        if body.source == "github" and not profile["has_github_avatar"]:
            raise HTTPException(400, "no_github_avatar")
        if body.source == "upload" and not has_upload:
            raise HTTPException(400, "no_uploaded_avatar")
        cur.execute(
            "UPDATE member SET avatar_source=%s, avatar_updated_at=now(), updated_at=now() WHERE id=%s",
            (body.source, member["id"]),
        )
        return profiles.load_profile(cur, member["id"])


@router.get("/files/avatars/{name}")
def get_avatar_file(name: str, member: dict = Depends(get_current_member_allow_unverified)):
    """로그인한 사용자만. 이름은 서버가 만든 uuid4.webp 형식만 받는다 — 그 외(경로 조작 포함)는 404."""
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(404, "not found")
    try:
        data = storage().open(f"avatars/{name}")
    except KeyError:
        raise HTTPException(404, "not found")
    return Response(
        content=data,
        media_type="image/webp",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=86400",
            "Content-Disposition": "inline",
        },
    )
