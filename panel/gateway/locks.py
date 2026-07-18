"""Inter-process locks for configuration and audit transactions."""
from __future__ import annotations

import errno
import fcntl
import os
import time
from pathlib import Path
from types import TracebackType
from typing import Self


class LockTimeout(TimeoutError):
    pass


class FileLock:
    def __init__(self, path: Path, timeout: float = 15.0, poll: float = 0.05) -> None:
        self.path = path
        self.timeout = timeout
        self.poll = poll
        self._descriptor: int | None = None

    def acquire(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.ftruncate(descriptor, 0)
                os.write(descriptor, f"{os.getpid()}\n".encode())
                self._descriptor = descriptor
                return self
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    os.close(descriptor)
                    raise
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise LockTimeout(f"等待事务锁超时：{self.path}") from exc
                time.sleep(self.poll)

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
