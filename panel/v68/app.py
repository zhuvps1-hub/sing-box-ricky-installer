#!/usr/bin/env python3
"""iWAN Gateway v6.6.0: durable idempotent autosave with immediate acknowledgement."""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

import autosavecore

core = autosavecore.core
moscore = autosavecore.moscore
authcore = autosavecore.authcore
interactioncore = autosavecore.interactioncore
statuscore = autosavecore.statuscore
routingcore = autosavecore.routingcore
VERSION = "6.6.0"
for module in (core, moscore, authcore, interactioncore, statuscore, routingcore, autosavecore):
    module.VERSION = VERSION

AUTOSAVE_JS = core.WEB_DIR / "autosave.js"
AUTOSAVE_CSS = core.WEB_DIR / "autosave.css"
PENDING_FILE = core.DATA_DIR / "autosave-pending.json"
REQUEST_RE = re.compile(r"[A-Za-z0-9_-]{8,96}")


class ReliableAutosaveQueue:
    """Persist before acknowledging, coalesce rapid edits, and deduplicate retries."""

    def __init__(
        self,
        runner: Callable[[dict[str, Any]], tuple[bool, str]] | None = None,
        settle_seconds: float = 5.0,
        pending_file: Path = PENDING_FILE,
    ) -> None:
        self.runner = runner or core.apply_config
        self.settle_seconds = settle_seconds
        self.pending_file = pending_file
        self.lock = threading.RLock()
        self.pending: tuple[int, str, dict[str, Any]] | None = None
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
        while len(self.requests) > 64:
            self.requests.popitem(last=False)

    def _persist(self, seq: int, request_id: str, payload: dict[str, Any]) -> None:
        core.atomic_json(self.pending_file, {
            "seq": seq,
            "request_id": request_id,
            "payload": payload,
            "created_at": int(time.time()),
        })

    def _clear_persisted(self, seq: int) -> None:
        try:
            current = core.read_json(self.pending_file, {})
            if int(current.get("seq", 0)) == seq:
                self.pending_file.unlink(missing_ok=True)
        except OSError:
            pass

    def submit(self, payload: dict[str, Any], request_id: str) -> int:
        if not REQUEST_RE.fullmatch(request_id):
            raise ValueError("自动保存请求号无效")
        with self.lock:
            existing = self.requests.get(request_id)
            if existing:
                return existing
            self.latest_seq += 1
            seq = self.latest_seq
            saved = copy.deepcopy(payload)
            self._persist(seq, request_id, saved)
            self.pending = (seq, request_id, saved)
            self._remember(request_id, seq)
            self.state = "queued"
            self.message = ""
            self.updated_at = int(time.time())
            if not self.worker_active:
                self.worker_active = True
                threading.Thread(target=self._worker, name="reliable-autosave", daemon=True).start()
            return seq

    def recover(self) -> None:
        data = core.read_json(self.pending_file, {})
        if not isinstance(data, dict):
            return
        try:
            seq = int(data.get("seq", 0))
            request_id = str(data.get("request_id", ""))
            payload = data.get("payload")
        except (TypeError, ValueError):
            return
        if seq <= 0 or not REQUEST_RE.fullmatch(request_id) or not isinstance(payload, dict):
            return
        with self.lock:
            self.latest_seq = max(self.latest_seq, seq)
            self.pending = (seq, request_id, copy.deepcopy(payload))
            self._remember(request_id, seq)
            self.state = "queued"
            self.updated_at = int(time.time())
            if not self.worker_active:
                self.worker_active = True
                threading.Thread(target=self._worker, name="recovered-autosave", daemon=True).start()

    def snapshot(self, request_id: str = "") -> dict[str, Any]:
        with self.lock:
            return {
                "state": self.state,
                "latest_seq": self.latest_seq,
                "running_seq": self.running_seq,
                "applied_seq": self.applied_seq,
                "failed_seq": self.failed_seq,
                "request_seq": self.requests.get(request_id, 0) if request_id else 0,
                "message": self.message,
                "updated_at": self.updated_at,
            }

    def _worker(self) -> None:
        while True:
            time.sleep(self.settle_seconds)
            with self.lock:
                if self.pending is None:
                    self.worker_active = False
                    if self.state not in {"failed", "succeeded"}:
                        self.state = "idle"
                    return
                seq, request_id, payload = self.pending
                self.pending = None
                self.running_seq = seq
                self.state = "running"
                self.updated_at = int(time.time())

            try:
                ok, message = self.runner(payload)
            except Exception as exc:
                ok, message = False, str(exc)

            with self.lock:
                if ok:
                    self.applied_seq = max(self.applied_seq, seq)
                else:
                    self.failed_seq = max(self.failed_seq, seq)
                self.message = message or ("配置已生效" if ok else "自动保存失败")
                self.updated_at = int(time.time())
                if self.pending is not None:
                    self.state = "queued"
                else:
                    self.state = "succeeded" if ok else "failed"
                    self.worker_active = False
                self._clear_persisted(seq)
                if not self.worker_active:
                    return


def autosave_payload(data: dict[str, Any]) -> dict[str, Any]:
    return autosavecore.autosave_payload(data)


QUEUE = ReliableAutosaveQueue()


def page_html() -> bytes:
    return autosavecore.page_html()


class Handler(autosavecore.Handler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/assets/autosave.js":
            self.serve_file(AUTOSAVE_JS, "application/javascript; charset=utf-8")
            return
        if path == "/assets/autosave.css":
            self.serve_file(AUTOSAVE_CSS, "text/css; charset=utf-8")
            return
        if path == "/api/autosave-status":
            if not self.require():
                return
            request_id = urllib.parse.parse_qs(parsed.query).get("request_id", [""])[0]
            self.json(200, {"ok": True, "autosave": QUEUE.snapshot(request_id)})
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path != "/api/autosave":
            super().do_POST()
            return
        try:
            data = self.body_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self.json(400, {"ok": False, "error": str(exc)})
            return
        if not self.require(mutate=True):
            return
        request_id = str(data.pop("request_id", ""))
        try:
            seq = QUEUE.submit(autosave_payload(data), request_id)
        except (ValueError, OSError) as exc:
            self.json(400, {"ok": False, "error": str(exc)})
            return
        self.close_connection = True
        self.json(202, {"ok": True, "seq": seq, "request_id": request_id}, {"Connection": "close"})
        try:
            self.wfile.flush()
        except OSError:
            pass


def self_test() -> None:
    autosavecore.self_test()
    calls: list[int] = []
    with tempfile.TemporaryDirectory() as directory:
        pending = Path(directory) / "pending.json"

        def runner(payload: dict[str, Any]) -> tuple[bool, str]:
            calls.append(int(payload["value"]))
            return True, "ok"

        queue = ReliableAutosaveQueue(runner=runner, settle_seconds=0.03, pending_file=pending)
        first = queue.submit({"value": 1}, "request_0001")
        duplicate = queue.submit({"value": 999}, "request_0001")
        second = queue.submit({"value": 2}, "request_0002")
        assert duplicate == first and second > first
        assert pending.exists()
        deadline = time.time() + 2
        while queue.snapshot("request_0002")["applied_seq"] < second and time.time() < deadline:
            time.sleep(0.02)
        status = queue.snapshot("request_0002")
        assert status["request_seq"] == second
        assert status["applied_seq"] == second
        assert calls == [2]
        assert not pending.exists()
    assert b"autosave.js" in page_html()
    print(json.dumps({"ok": True, "version": VERSION, "autosave": "durable-idempotent"}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--init-auth", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    core.ensure_dirs()
    core.init_db()
    if args.init_auth:
        core.AUTH.initialize(os.environ.get("PANEL_ADMIN_USER", "admin"), os.environ.get("PANEL_ADMIN_PASSWORD", ""))
        authcore.ensure_remember_secret()
        print("auth initialized")
        return
    if args.self_test:
        self_test()
        return
    if not core.AUTH_FILE.exists():
        raise SystemExit("auth.json missing; run --init-auth first")
    authcore.ensure_remember_secret()
    QUEUE.recover()
    core.SAMPLER.start()
    server = core.http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        core.SAMPLER.stop()
        server.server_close()


if __name__ == "__main__":
    main()
