from __future__ import annotations

import sqlite3
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

class BasicCrudTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            "CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL)"
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_create_read_update_delete(self) -> None:
        cursor = self.connection.cursor()

        cursor.execute("INSERT INTO entries (title) VALUES (?)", ("initial",))
        created_id = cursor.lastrowid
        self.connection.commit()

        row = cursor.execute("SELECT id, title FROM entries WHERE id = ?", (created_id,)).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row[1], "initial")

        cursor.execute("UPDATE entries SET title = ? WHERE id = ?", ("updated", created_id))
        self.connection.commit()
        updated = cursor.execute("SELECT title FROM entries WHERE id = ?", (created_id,)).fetchone()
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated[0], "updated")

        cursor.execute("DELETE FROM entries WHERE id = ?", (created_id,))
        self.connection.commit()
        deleted = cursor.execute("SELECT id FROM entries WHERE id = ?", (created_id,)).fetchone()
        self.assertIsNone(deleted)


if __name__ == "__main__":
    unittest.main()
