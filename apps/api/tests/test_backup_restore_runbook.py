from __future__ import annotations

import os
import pathlib
import subprocess
import tarfile
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RESTORE_SCRIPT = REPO_ROOT / "infra" / "scripts" / "backup" / "restore_backup.sh"
RUNBOOK = REPO_ROOT / "docs" / "operations" / "backup-restore-runbook.md"


class BackupRestoreRunbookTests(unittest.TestCase):
    def test_restore_script_dry_run_validates_backup_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_dir = pathlib.Path(tmpdir) / "20260302T000000Z"
            archive_dir.mkdir(parents=True, exist_ok=True)

            (archive_dir / "db.dump").write_bytes(b"placeholder dump")

            blob_file = pathlib.Path(tmpdir) / "blob.txt"
            blob_file.write_text("blob-content", encoding="utf-8")
            with tarfile.open(archive_dir / "blobs.tar.gz", "w:gz") as tar:
                tar.add(blob_file, arcname="entries/example/audio/blob.txt")

            env = os.environ.copy()
            env.update(
                {
                    "DATABASE_URL": "postgresql+psycopg://voicevault:voicevault@db:5432/voicevault",
                    "BACKUP_ARCHIVE_DIR": str(archive_dir),
                    "BLOB_TARGET_DIR": str(pathlib.Path(tmpdir) / "restored-blobs"),
                    "DRY_RUN": "1",
                }
            )

            result = subprocess.run(
                ["sh", str(RESTORE_SCRIPT)],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("[dry-run] pg_restore", result.stdout)
            self.assertIn("[dry-run] tar -xzf", result.stdout)

    def test_runbook_exists_and_references_restore_target(self) -> None:
        self.assertTrue(RUNBOOK.exists())
        content = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("make backup-restore-dry-run", content)
        self.assertIn("make backup-restore", content)


if __name__ == "__main__":
    unittest.main()
