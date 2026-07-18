"""Append-only, redacted JSONL audit trail with bounded rotation."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .locks import FileLock
from .redaction import redact


class AuditLog:
    def __init__(self, path: Path, max_bytes: int = 5 * 1024 * 1024, keep: int = 5) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.keep = max(1, keep)
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def _rotate(self) -> None:
        try:
            if self.path.stat().st_size < self.max_bytes:
                return
        except FileNotFoundError:
            return
        for index in range(self.keep, 0, -1):
            source = self.path if index == 1 else self.path.with_name(f"{self.path.name}.{index - 1}")
            target = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                if index == self.keep:
                    target.unlink(missing_ok=True)
                os.replace(source, target)

    def append(
        self,
        event: str,
        *,
        actor: str = "system",
        remote: str = "local",
        ok: bool = True,
        transaction_id: str = "",
        detail: Any = None,
    ) -> None:
        record = {
            "timestamp": int(time.time()),
            "event": str(event)[:96],
            "actor": str(actor)[:128],
            "remote": str(remote)[:128],
            "ok": bool(ok),
            "transaction_id": str(transaction_id)[:128],
            "detail": redact(detail),
        }
        encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(self.lock_path):
            self._rotate()
            descriptor = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = min(max(int(limit), 1), 500)
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        except (FileNotFoundError, OSError, UnicodeError):
            return []
        result: list[dict[str, Any]] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                result.append(item)
        return result
