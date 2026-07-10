#!/usr/bin/env python3
"""iWAN Gateway v6.3.0: asynchronous apply jobs and final interaction layer."""
from __future__ import annotations

import argparse
import copy
import json
import os
import secrets
import threading
import time
import urllib.parse
from collections import deque
from typing import Any, Callable

import authcore

core = authcore.core
moscore = authcore.moscore
VERSION = "6.3.0"
core.VERSION = VERSION
moscore.VERSION = VERSION
authcore.VERSION = VERSION
INTERACTION_JS = core.WEB_DIR / "interaction.js"
INTERACTION_CSS = core.WEB_DIR / "interaction.css"


class ApplyJobs:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.order: deque[str] = deque(maxlen=30)
        self.latest = ""

    def submit(self, payload: dict[str, Any], runner: Callable[[dict[str, Any]], tuple[bool, str]] | None = None) -> dict[str, Any]:
        with self.lock:
            for job in self.jobs.values():
                if job["status"] in {"queued", "running"}:
                    raise ValueError("已有配置任务正在后台执行，请稍后查看结果")
            job_id = secrets.token_urlsafe(12)
            job = {
                "id": job_id,
                "status": "queued",
                "message": "已保存，等待后台应用",
                "created_at": int(time.time()),
                "started_at": 0,
                "finished_at": 0,
            }
            self.jobs[job_id] = job
            self.order.append(job_id)
            self.latest = job_id
        thread = threading.Thread(
            target=self._run,
            args=(job_id, copy.deepcopy(payload), runner or core.apply_config),
            name=f"apply-{job_id[:6]}",
            daemon=True,
        )
        thread.start()
        return copy.deepcopy(job)

    def _run(self, job_id: str, payload: dict[str, Any], runner: Callable[[dict[str, Any]], tuple[bool, str]]) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job["status"] = "running"
            job["message"] = "后台正在校验、备份并重启 sing-box"
            job["started_at"] = int(time.time())
        try:
            ok, message = runner(payload)
            status = "succeeded" if ok else "failed"
        except Exception as exc:  # defensive: never leave a job permanently running
            status = "failed"
            message = str(exc)
        with self.lock:
            job = self.jobs[job_id]
            job["status"] = status
            job["message"] = message or ("已生效" if status == "succeeded" else "应用失败")
            job["finished_at"] = int(time.time())

    def get(self, job_id: str = "") -> dict[str, Any] | None:
        with self.lock:
            target = job_id or self.latest
            job = self.jobs.get(target)
            return copy.deepcopy(job) if job else None


JOBS = ApplyJobs()


def page_html() -> bytes:
    html = authcore.login_page().decode("utf-8")
    if "/assets/interaction.css" not in html:
        html = html.replace("</head>", '  <link rel="stylesheet" href="/assets/interaction.css">\n</head>', 1)
    if "/assets/interaction.js" not in html:
        html = html.replace("</body>", '  <script src="/assets/interaction.js" defer></script>\n</body>', 1)
    return html.encode("utf-8")


class Handler(authcore.Handler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/":
            try:
                self.send_bytes(200, page_html(), "text/html; charset=utf-8")
            except OSError as exc:
                self.json(500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/assets/interaction.js":
            self.serve_file(INTERACTION_JS, "application/javascript; charset=utf-8")
            return
        if parsed.path == "/assets/interaction.css":
            self.serve_file(INTERACTION_CSS, "text/css; charset=utf-8")
            return
        if parsed.path == "/api/apply-status":
            if not self.require():
                return
            job_id = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
            job = JOBS.get(job_id)
            self.json(200, {"ok": True, "job": job})
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path != "/api/save":
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
            job = JOBS.submit(data)
        except ValueError as exc:
            self.json(409, {"ok": False, "error": str(exc)})
            return
        self.json(202, {
            "ok": True,
            "job_id": job["id"],
            "status": job["status"],
            "message": "已保存，后台自动校验并应用",
        })


def self_test() -> None:
    authcore.self_test()
    manager = ApplyJobs()
    done = threading.Event()

    def runner(payload: dict[str, Any]) -> tuple[bool, str]:
        assert payload["value"] == 7
        done.set()
        return True, "ok"

    job = manager.submit({"value": 7}, runner)
    assert done.wait(2)
    deadline = time.time() + 2
    status = manager.get(job["id"])
    while status and status["status"] not in {"succeeded", "failed"} and time.time() < deadline:
        time.sleep(0.02)
        status = manager.get(job["id"])
    assert status and status["status"] == "succeeded"
    assert b"interaction.js" in page_html()
    assert b"interaction.css" in page_html()
    print(json.dumps({"ok": True, "version": VERSION}))


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
