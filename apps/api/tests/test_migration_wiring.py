from __future__ import annotations

import configparser
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class MigrationWiringTests(unittest.TestCase):
    def test_alembic_ini_defaults_to_compose_postgres(self) -> None:
        parser = configparser.ConfigParser()
        parser.read(ROOT / "alembic.ini")
        self.assertEqual(
            parser["alembic"]["sqlalchemy.url"],
            "postgresql+psycopg://voicevault:voicevault@db:5432/voicevault",
        )

    def test_env_uses_shared_database_url(self) -> None:
        env_py = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
        self.assertIn("from app.settings import get_database_url", env_py)
        self.assertIn('config.set_main_option("sqlalchemy.url", get_database_url())', env_py)

    def test_initial_migration_exists(self) -> None:
        versions = ROOT / "alembic" / "versions"
        migration_files = [path.name for path in versions.glob("*.py")]
        self.assertIn("20260301_0001_create_migration_probe.py", migration_files)


if __name__ == "__main__":
    unittest.main()
