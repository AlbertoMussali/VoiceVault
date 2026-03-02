from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.storage import LocalDiskStorage


class LocalDiskStorageTests(unittest.TestCase):
    def test_write_read_and_delete_blob(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = LocalDiskStorage(tmp_dir)
            key = "audio/entry-1.webm"
            payload = b"voice-bytes"

            storage.write_bytes(key, payload)

            self.assertTrue(storage.exists(key))
            self.assertEqual(storage.read_bytes(key), payload)

            storage.delete(key)
            self.assertFalse(storage.exists(key))

    def test_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = LocalDiskStorage(tmp_dir)
            with self.assertRaises(ValueError):
                storage.write_bytes("../escape.webm", b"bad")


if __name__ == "__main__":
    unittest.main()
