from __future__ import annotations

from pathlib import Path


class LocalDiskStorage:
    """Store blobs on a local filesystem path using storage keys."""

    def __init__(self, base_path: Path | str) -> None:
        self._base_path = Path(base_path).resolve()
        self._base_path.mkdir(parents=True, exist_ok=True)

    def write_bytes(self, key: str, payload: bytes) -> str:
        path = self._resolve_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return key

    def read_bytes(self, key: str) -> bytes:
        return self._resolve_key(key).read_bytes()

    def delete(self, key: str) -> None:
        path = self._resolve_key(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._resolve_key(key).exists()

    def _resolve_key(self, key: str) -> Path:
        candidate = (self._base_path / key).resolve()
        if self._base_path not in (candidate, *candidate.parents):
            raise ValueError("Invalid storage key path")
        return candidate
