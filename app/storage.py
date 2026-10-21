"""파일 저장소 — DB 엔 키(예: "avatars/<uuid4>.webp")만 두고 실제 바이트는 여기에 둔다.

지금은 로컬 파일시스템 구현(LocalStorage) 하나뿐이다. 나중에 MinIO(S3 호환)로 옮길 때
같은 save/open/delete(키 기반) 인터페이스로 구현만 바꿔 끼우면 호출부는 그대로다.

키는 신뢰하지 않는다 — 실제 경로로 바꿀 때 resolve() 결과가 루트 하위인지 매번 검사해
"..", 절대경로, 심볼릭 링크로 루트 밖을 가리키는 키를 막는다(KeyError → 호출부가 404).
"""

from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import settings


class Storage(Protocol):
    def save(self, key: str, data: bytes) -> None: ...
    def open(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        if not key or key.startswith(("/", "\\")) or "\x00" in key:
            raise KeyError(key)
        path = (self.root / key).resolve()
        # 루트 자신이거나 루트 밖이면 거부 — "../", 절대경로, 루트 밖 심볼릭 링크 전부 여기서 걸린다.
        if path == self.root or self.root not in path.parents:
            raise KeyError(key)
        return path

    def save(self, key: str, data: bytes) -> None:
        """같은 폴더에 임시 파일로 끝까지 쓴 뒤 rename — 중간에 죽어도 반쪽 파일이 키로 보이지 않는다."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def open(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise KeyError(key)
        return path.read_bytes()

    def delete(self, key: str) -> None:
        """없는 파일이면 조용히 넘어간다(이미 지워졌어도 결과는 같다)."""
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass


@lru_cache
def storage() -> Storage:
    return LocalStorage(settings().upload_root)
