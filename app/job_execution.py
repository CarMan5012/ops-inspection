"""One lease per inspection job, shared by manual and scheduled execution."""
from __future__ import annotations

import os
import time
from typing import BinaryIO

from app.settings import settings


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
