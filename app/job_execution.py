"""One lease per inspection job, shared by manual and scheduled execution."""
from __future__ import annotations

import os
import time
import logging
from datetime import datetime
from typing import BinaryIO
from zoneinfo import ZoneInfo

from app.settings import settings

logger = logging.getLogger("app.job_execution")


class JobAlreadyRunning(RuntimeError):
    pass


class JobLease:
    def __init__(self, handle: BinaryIO) -> None:
        self.handle = handle

    def close(self) -> None:
        if self.handle.closed:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


def acquire_job_lease(job_id: int, *, blocking: bool = False) -> JobLease:
    folder = settings.data_dir / "job-locks"
    folder.mkdir(parents=True, exist_ok=True)
    handle = (folder / f"job-{int(job_id)}.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except (BlockingIOError, PermissionError):
                    if not blocking:
                        raise
                    time.sleep(.1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except (BlockingIOError, PermissionError) as exc:
        handle.close()
        raise JobAlreadyRunning("任务正在运行，请完成后再试") from exc
    except Exception:
        handle.close()
        raise
    return JobLease(handle)


def recover_interrupted_runs(
    *, include_deleted_jobs: bool = False, include_queued: bool = True,
    queued_grace_seconds: int = 600,
) -> int:
    """Finish abandoned runs only when no worker owns their job lease.

    A queued run has time to enter its worker before periodic recovery probes it.
    Orphaned rows left by older versions are recovered after the queue grace period.
    """
    from app.db import connect
    from app.repository import finish_active_run

    with connect() as conn:
        statuses = ("queued", "running") if include_queued else ("running",)
        placeholders = ", ".join("?" for _ in statuses)
        rows = conn.execute(
            f"SELECT id, job_id, status, started_at FROM run_records WHERE status IN ({placeholders}) ORDER BY id",
            statuses,
        ).fetchall()
    now = datetime.now(ZoneInfo(settings.default_timezone))
    grouped: dict[int | None, list[dict]] = {}
    for row in rows:
        run = dict(row)
        grouped.setdefault(run["job_id"], []).append(run)

    recovered = 0
    for job_id, job_runs in grouped.items():
        grace_seconds = (
            queued_grace_seconds if job_id is not None or include_deleted_jobs
            else max(queued_grace_seconds, 600)
        )
        lease = None
        if job_id is not None:
            try:
                lease = acquire_job_lease(int(job_id))
            except JobAlreadyRunning:
                continue
            except Exception:
                logger.exception("检查任务 #%s 的执行锁失败，保留运行中记录", job_id)
                continue
        try:
            for run in job_runs:
                if (run["status"] == "queued" or job_id is None) and grace_seconds > 0:
                    try:
                        queued_at = datetime.fromisoformat(str(run["started_at"]).replace("Z", "+00:00"))
                        if queued_at.tzinfo is None:
                            queued_at = queued_at.replace(tzinfo=now.tzinfo)
                        if (now - queued_at).total_seconds() < grace_seconds:
                            continue
                    except (TypeError, ValueError):
                        continue
                reason = (
                    "任务排队期间执行进程中断，未开始巡检。"
                    if run["status"] == "queued" else
                    "巡检执行进程中断，未能完成；已保留生成的截图和报告。"
                )
                recovered += int(finish_active_run(int(run["id"]), "interrupted", reason))
        finally:
            if lease is not None:
                lease.close()
    return recovered
