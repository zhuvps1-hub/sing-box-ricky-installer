"""Durable, idempotent autosave queue independent of the HTTP layer."""
from __future__ import annotations

import copy
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from .atomic import read_json, write_json

REQUEST_RE = re.compile(r"[A-Za-z0-9_-]{8,96}")


class AutosaveQueue:
    def __init__(
        self,
        runner: Callable[[dict[str, Any], str, str], tuple[bool, str]],
        pending_file: Path,
        settle_seconds: float = 1.5,
    ) -> None:
        self.runner = runner
        self.pending_file = pending_file
        self.settle_seconds = settle_seconds
        self.lock = threading.RLock()
        self.pending: tuple[int, str, dict[str, Any], str, str] | None = None
        self.worker_active = False
        self.latest_seq = 0
        self.running_seq = 0
        self.applied_seq = 0
        self.failed_seq = 0
        self.state = "idle"
        self.message = ""
        self.updated_at = int(time.time())
        self.requests: OrderedDict[str, int] = OrderedDict()

    def _remember(self, request_id: str, seq: int) -> None:
        self.requests[request_id] = seq
        self.requests.move_to_end(request_id)
        while len(self.requests) > 128:
            self.requests.popitem(last=False)

    def _persist(self, item: tuple[int, str, dict[str, Any], str, str]) -> None:
        seq, request_id, payload, actor, remote = item
        write_json(self.pending_file, {
            "seq": seq, "request_id": request_id, "payload": payload,
            "actor": actor, "remote": remote, "created_at": int(time.time()),
        }, 0o600)

    def submit(self, payload: dict[str, Any], request_id: str, actor: str, remote: str) -> int:
        if not REQUEST_RE.fullmatch(request_id):
            raise ValueError("自动保存请求号无效")
        if not isinstance(payload, dict):
            raise ValueError("自动保存内容无效")
        with self.lock:
            existing = self.requests.get(request_id)
            if existing:
                return existing
            self.latest_seq += 1
            seq = self.latest_seq
            item = (seq, request_id, copy.deepcopy(payload), actor[:128], remote[:128])
            self._persist(item)
            self.pending = item
            self._remember(request_id, seq)
            self.state = "queued"
            self.message = ""
            self.updated_at = int(time.time())
            if not self.worker_active:
                self.worker_active = True
                threading.Thread(target=self._worker, name="gateway-autosave", daemon=True).start()
            return seq

    def recover(self) -> None:
        value = read_json(self.pending_file, {})
        if not isinstance(value, dict):
            return
        try:
            item = (
                int(value.get("seq", 0)), str(value.get("request_id", "")), value.get("payload"),
                str(value.get("actor", "recovered")), str(value.get("remote", "local")),
            )
        except (TypeError, ValueError):
            return
        seq, request_id, payload, actor, remote = item
        if seq <= 0 or not REQUEST_RE.fullmatch(request_id) or not isinstance(payload, dict):
            return
        with self.lock:
            self.latest_seq = max(self.latest_seq, seq)
            self.pending = (seq, request_id, copy.deepcopy(payload), actor, remote)
            self._remember(request_id, seq)
            self.state = "queued"
            self.updated_at = int(time.time())
            if not self.worker_active:
                self.worker_active = True
                threading.Thread(target=self._worker, name="gateway-autosave-recovery", daemon=True).start()

    def snapshot(self, request_id: str = "") -> dict[str, Any]:
        with self.lock:
            return {
                "state": self.state, "latest_seq": self.latest_seq, "running_seq": self.running_seq,
                "applied_seq": self.applied_seq, "failed_seq": self.failed_seq,
                "request_seq": self.requests.get(request_id, 0) if request_id else 0,
                "message": self.message, "updated_at": self.updated_at,
            }

    def _clear(self, seq: int) -> None:
        current = read_json(self.pending_file, {})
        if isinstance(current, dict) and int(current.get("seq", 0)) == seq:
            self.pending_file.unlink(missing_ok=True)

    def _worker(self) -> None:
        while True:
            time.sleep(self.settle_seconds)
            with self.lock:
                if self.pending is None:
                    self.worker_active = False
                    if self.state not in {"failed", "succeeded"}:
                        self.state = "idle"
                    return
                seq, request_id, payload, actor, remote = self.pending
                self.pending = None
                self.running_seq = seq
                self.state = "running"
                self.updated_at = int(time.time())
            try:
                ok, message = self.runner(payload, actor, remote)
            except Exception as exc:
                ok, message = False, str(exc)
            with self.lock:
                if ok:
                    self.applied_seq = max(self.applied_seq, seq)
                else:
                    self.failed_seq = max(self.failed_seq, seq)
                self.message = message or ("配置已生效" if ok else "自动保存失败")
                self.updated_at = int(time.time())
                self._clear(seq)
                if self.pending is not None:
                    self.state = "queued"
                    continue
                self.state = "succeeded" if ok else "failed"
                self.worker_active = False
                return
