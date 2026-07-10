#!/usr/bin/env python3
"""iWAN Gateway v6.5.0: non-blocking coalesced autosave for nodes and routing."""
from __future__ import annotations

import argparse
import copy
import json
import os
import threading
import time
import urllib.parse
from typing import Any, Callable

import routingcore

core = routingcore.core
moscore = routingcore.moscore
authcore = routingcore.authcore
interactioncore = routingcore.interactioncore
statuscore = routingcore.statuscore
VERSION = "6.5.0"
for module in (core, moscore, authcore, interactioncore, statuscore, routingcore):
    module.VERSION = VERSION

AUTOSAVE_JS = core.WEB_DIR / "autosave.js"
AUTOSAVE_CSS = core.WEB_DIR / "autosave.css"


class AutosaveQueue:
    """Keep one worker and coalesce rapid edits into the newest payload."""

    def __init__(
        self,
        runner: Callable[[dict[str, Any]], tuple[bool, str]] | None = None,
        settle_seconds: float = 1.2,
    ) -> None:
        self.runner = runner or core.apply_config
        self.settle_seconds = settle_seconds
        self.lock = threading.RLock()
        self.pending: tuple[int, dict[str, Any]] | None = None
        self.worker_active = False
        self.latest_seq = 0
        self.running_seq = 0
        self.applied_seq = 0
        self.failed_seq = 0
        self.state = "idle"
        self.message = ""
        self.updated_at = int(time.time())

    def submit(self, payload: dict[str, Any]) -> int:
        with self.lock:
            self.latest_seq += 1
            seq = self.latest_seq
            self.pending = (seq, copy.deepcopy(payload))
            self.state = "queued"
            self.message = ""
            self.updated_at = int(time.time())
            if not self.worker_active:
                self.worker_active = True
                threading.Thread(target=self._worker, name="autosave-worker", daemon=True).start()
            return seq

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "state": self.state,
                "latest_seq": self.latest_seq,
                "running_seq": self.running_seq,
                "applied_seq": self.applied_seq,
                "failed_seq": self.failed_seq,
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
                seq, payload = self.pending
                self.pending = None
                self.running_seq = seq
                self.state = "running"
                self.updated_at = int(time.time())

            try:
                ok, message = self.runner(payload)
            except Exception as exc:  # pragma: no cover - defensive boundary
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
                    return


def autosave_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Only nodes and routing are eligible for silent autosave."""
    nodes = data.get("nodes", [])
    deleted = data.get("deleted_tags", [])
    mappings = data.get("mappings", {})
    if not isinstance(nodes, list) or not isinstance(deleted, list) or not isinstance(mappings, dict):
        raise ValueError("自动保存数据格式无效")
    return {
        "nodes": nodes,
        "deleted_tags": deleted,
        "mappings": mappings,
        "default": str(data.get("default", "")),
        "iwan": {},
    }


AUTOSAVE = AutosaveQueue()


def page_html() -> bytes:
    html = routingcore.page_html().decode("utf-8")
    if "silent-autosave" not in html.partition(">")[0]:
        html = html.replace("<html", '<html class="silent-autosave"', 1)
    if "/assets/autosave.css" not in html:
        html = html.replace("</head>", '  <link rel="stylesheet" href="/assets/autosave.css">\n</head>', 1)
    if "/assets/autosave.js" not in html:
        html = html.replace("</body>", '  <script src="/assets/autosave.js" defer></script>\n</body>', 1)
    return html.encode("utf-8")


class Handler(routingcore.Handler):
    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            try:
                self.send_bytes(200, page_html(), "text/html; charset=utf-8")
            except OSError as exc:
                self.json(500, {"ok": False, "error": str(exc)})
            return
        if path == "/assets/autosave.js":
            self.serve_file(AUTOSAVE_JS, "application/javascript; charset=utf-8")
            return
        if path == "/assets/autosave.css":
            self.serve_file(AUTOSAVE_CSS, "text/css; charset=utf-8")
            return
        if path == "/api/autosave-status":
            if not self.require():
                return
            self.json(200, {"ok": True, "autosave": AUTOSAVE.snapshot()})
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
        try:
            seq = AUTOSAVE.submit(autosave_payload(data))
        except ValueError as exc:
            self.json(400, {"ok": False, "error": str(exc)})
            return
        self.json(202, {"ok": True, "seq": seq})


def self_test() -> None:
    routingcore.self_test()
    calls: list[int] = []

    def runner(payload: dict[str, Any]) -> tuple[bool, str]:
        calls.append(int(payload["value"]))
        return True, "ok"

    queue = AutosaveQueue(runner=runner, settle_seconds=0.02)
    first = queue.submit({"value": 1})
    second = queue.submit({"value": 2})
    assert second > first
    deadline = time.time() + 2
    while queue.snapshot()["applied_seq"] < second and time.time() < deadline:
        time.sleep(0.02)
    status = queue.snapshot()
    assert status["applied_seq"] == second
    assert calls == [2]
    rendered = page_html()
    assert b"autosave.js" in rendered
    assert b"autosave.css" in rendered
    assert b"silent-autosave" in rendered
    print(json.dumps({"ok": True, "version": VERSION, "autosave": "coalesced"}))


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
