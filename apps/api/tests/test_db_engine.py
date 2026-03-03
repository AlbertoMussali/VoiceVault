from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import _resolve_engine_connect_args


class DbEngineConfigTests(unittest.TestCase):
    def test_psycopg_postgres_defaults_to_gss_disabled(self) -> None:
        self.assertEqual(
            _resolve_engine_connect_args("postgresql+psycopg://user:pass@localhost:5432/dbname"),
            {"gssencmode": "disable"},
        )

    def test_psycopg_respects_explicit_gssencmode(self) -> None:
        self.assertEqual(
            _resolve_engine_connect_args("postgresql+psycopg://user:pass@localhost:5432/dbname?gssencmode=require"),
            {},
        )

    def test_non_psycopg_urls_do_not_set_connect_args(self) -> None:
        self.assertEqual(
            _resolve_engine_connect_args("sqlite+pysqlite:///tmp/test.db"),
            {},
        )


if __name__ == "__main__":
    unittest.main()
