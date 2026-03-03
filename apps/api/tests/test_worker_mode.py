from __future__ import annotations

import os
import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.worker import _should_use_simple_worker, run_worker


class WorkerModeTests(unittest.TestCase):
    def test_should_use_simple_worker_on_macos_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("app.worker.platform.system", return_value="Darwin"):
            self.assertTrue(_should_use_simple_worker())

    def test_should_not_use_simple_worker_on_linux_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("app.worker.platform.system", return_value="Linux"):
            self.assertFalse(_should_use_simple_worker())

    def test_env_override_enables_simple_worker(self) -> None:
        with patch.dict(os.environ, {"VOICEVAULT_SIMPLE_WORKER": "true"}, clear=True):
            self.assertTrue(_should_use_simple_worker())

    def test_env_override_disables_simple_worker(self) -> None:
        with patch.dict(os.environ, {"VOICEVAULT_SIMPLE_WORKER": "0"}, clear=True):
            self.assertFalse(_should_use_simple_worker())

    def test_run_worker_uses_simple_worker_when_enabled(self) -> None:
        simple_worker_instance = MagicMock()
        with patch("app.worker._should_use_simple_worker", return_value=True), patch(
            "app.worker.get_redis_connection",
            return_value=object(),
        ), patch("app.worker.SimpleWorker", return_value=simple_worker_instance) as simple_worker_cls, patch(
            "app.worker.Worker"
        ) as normal_worker_cls:
            run_worker()

        simple_worker_cls.assert_called_once()
        normal_worker_cls.assert_not_called()
        simple_worker_instance.work.assert_called_once_with(with_scheduler=False)

    def test_run_worker_uses_standard_worker_when_disabled(self) -> None:
        worker_instance = MagicMock()
        with patch("app.worker._should_use_simple_worker", return_value=False), patch(
            "app.worker.get_redis_connection",
            return_value=object(),
        ), patch("app.worker.Worker", return_value=worker_instance) as normal_worker_cls, patch(
            "app.worker.SimpleWorker"
        ) as simple_worker_cls:
            run_worker()

        normal_worker_cls.assert_called_once()
        simple_worker_cls.assert_not_called()
        worker_instance.work.assert_called_once_with(with_scheduler=False)


if __name__ == "__main__":
    unittest.main()
