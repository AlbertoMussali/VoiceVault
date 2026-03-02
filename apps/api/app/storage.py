from __future__ import annotations

from pathlib import Path
import uuid


class LocalBlobStorage:
    """Local disk storage backend for entry audio blobs."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def store_entry_audio(self, entry_id: int, content: bytes, filename: str) -> str:
        suffix = Path(filename).suffix
        storage_key = f"entries/{entry_id}/{uuid.uuid4().hex}{suffix}"
        output_path = self._root / storage_key
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)
        return storage_key
