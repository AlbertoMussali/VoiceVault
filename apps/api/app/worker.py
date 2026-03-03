from __future__ import annotations

import os
import platform

from rq import SimpleWorker, Worker

from app.jobs import DEFAULT_QUEUE_NAME, get_redis_connection


def _should_use_simple_worker() -> bool:
    raw = os.getenv("VOICEVAULT_SIMPLE_WORKER")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return platform.system().lower() == "darwin"


def run_worker() -> None:
    """Start the RQ worker process for configured queues."""
    worker_cls = SimpleWorker if _should_use_simple_worker() else Worker
    worker = worker_cls([DEFAULT_QUEUE_NAME], connection=get_redis_connection())
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    run_worker()
