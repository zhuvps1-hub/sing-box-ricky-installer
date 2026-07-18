"""Flat v7 HTTP runtime backed by the non-privileged application service."""
from __future__ import annotations

import http.server
import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

import core

from . import VERSION
from .application import GatewayApplication, default_application
from .autosave import AutosaveQueue
from .client import HelperError

core.VERSION = VERSION
core.Handler.server_version = "iWANGateway/7"

APP: GatewayApplication = default_application()
QUEUE = AutosaveQueue(
    lambda payload, actor, remote: APP.apply(payload, actor=actor, remote=remote),
    Path(os.environ.get("IWAN_PANEL_DATA_DIR", "/var/lib/iwan-gateway")) / "autosave-pending.json",
)

ASSETS = {
    "/assets/core.css": ("core.css", "text/css; charset=utf-8"),
    "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
    "/assets/interaction.css": ("interaction.css", "text/css; charset=utf-8"),
    "/assets/autosave.css": ("autosave.css", "text/css; charset=utf-8"),
    "/assets/core.js": ("core.js", "application/javascript; charset=utf-8"),
    "/assets/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/assets/remember.js": ("remember.js", "application/javascript; charset=utf-8"),
    "/assets/interaction.js": ("interaction.js", "application/javascript; charset=utf-8"),
    "/assets/refreshfix.js": ("refreshfix.js", "application/javascript; charset=utf-8"),
    "/assets/autosave.js": ("autosave.js", "application/javascript; charset=utf-8"),
}


def _page_html() -> bytes:
    text = (core.WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = "".join(
        f'<link rel="stylesheet" href="/assets/{name}">'
        for name in ("interaction.css", "autosave.css") if (core.WEB_DIR / name).exists()
    )
    js = "".join(
        f'<script src="/assets/{name}" defer></script>'
        for name in ("remember.js", "interaction.js", "refreshfix.js", "autosave.js") if (core.WEB_DIR / name).exists()
    )
    text = text.replace("</head>", css + "</head>").replace("</body>", js + "</body>")
    return text.encode("utf-8")


def _mosdns_summary(text: str) -> dict[str, Any]:
    tags = re.findall(r"(?m)^\s*-?\s*tag:\s*['\"]?([^'\"#\s]+)", text)
    types = re.findall(r"(?m)^\s*type:\s*['\"]?([^'\"#\s]+)", text)
    addresses = re.findall(r"(?m)^\s*-?\s*(?:addr|listen):\s*['\"]?([^'\"#\s]+)", text)
    return {
        "plugins": len(tags), "tags": tags[:40], "types": sorted(set(types))[:40],
        "addresses": addresses[:40], "upstreams": list(dict.fromkeys(addresses))[:40],
        "lines": text.count("\n") + (1 if text else 0),
    }


def _secure_cookie(handler: http.server.BaseHTTPRequestHandler) -> bool:
    configured = os.environ.get("IWAN_SECURE_COOKIES", "auto").lower()
    if configured in {"1", "true", "yes"}:
        return True
    if configured in {"0", "false", "no"}:
        return False
    return handler.headers.get("X-Forwarded-Proto", "").lower() == "https"


class Handler(core.Handler):
    server_version = "iWANGateway/7"

    def _remote(self) -> str:
        remote = str(self.client_address[0])
        trust_proxy = os.environ.get("IWAN_TRUST_LOCAL_PROXY", "1") not in {"0", "false", "no"}
        if trust_proxy and remote in {"127.0.0.1", "::1"}:
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            if forwarded:
                remote = forwarded
        return remote[:128]

    @staticmethod
    def _actor(session: dict[str, Any]) -> str:
        return str(session.get("username") or session.get("user") or "admin")[:128]

    def send_bytes(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8", headers: dict[str, str] | None = None) -> None:
        merged = {
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
        }
        if headers:
            merged.update(headers)
        super().send_bytes(status, body, content_type, merged)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/healthz":
            ready = APP.helper.ping()
            self.json(200 if ready else 503, {"ok": ready, "version": VERSION, "helper": ready})
            return
        if path == "/":
            try:
                self.send_bytes(200, _page_html(), "text/html; charset=utf-8")
            except OSError:
                self.json(404, {"ok": False, "error": "页面资源不存在"})
            return
        if path in ASSETS:
            name, content_type = ASSETS[path]
            self.serve_file(core.WEB_DIR / name, content_type)
            return
        if path == "/api/session":
            _, session = self.session()
            self.json(200, {"authenticated": bool(session), "csrf": session.get("csrf") if session else "", "version": VERSION})
            return
        auth = self.require()
        if not auth:
            return
        _, session = auth
        try:
            if path == "/api/dashboard":
                sample = core.SAMPLER.get()
                sample["services"] = APP.service_status()
                self.json(200, {"ok": True, **sample})
                return
            if path == "/api/config":
                self.json(200, {"ok": True, **APP.sample_config()})
                return
            if path == "/api/diagnostics":
                config = APP.sample_config()
                services = APP.service_status()
                checks = [
                    {"name": "特权 helper", "ok": APP.helper.ping(), "detail": "Unix socket"},
                    {"name": "sing-box 服务", "ok": services.get("sing-box", False), "detail": "systemd active"},
                    {"name": "mosdns 服务", "ok": services.get("mosdns", False), "detail": "systemd active"},
                    {"name": "iWAN 配置", "ok": bool(config.get("iwan")), "detail": "canonical address_pool"},
                ]
                score = round(100 * sum(1 for item in checks if item["ok"]) / len(checks))
                self.json(200, {"ok": True, "score": score, "checks": checks, "next_steps": []})
                return
            if path == "/api/logs":
                service = urllib.parse.parse_qs(parsed.query).get("service", ["sing-box"])[0]
                self.json(200, {"ok": True, "logs": APP.logs(service)})
                return
            if path == "/api/network":
                self.json(200, {"ok": True, **APP.network()})
                return
            if path == "/api/autosave-status":
                request_id = urllib.parse.parse_qs(parsed.query).get("request_id", [""])[0]
                self.json(200, {"ok": True, "autosave": QUEUE.snapshot(request_id)})
                return
            if path == "/api/mosdns":
                state = APP.mosdns_state()
                text = str(state.get("config", ""))
                services = APP.service_status()
                self.json(200, {
                    "ok": True, **state, "service_active": services.get("mosdns", False),
                    "path": os.environ.get("MOSDNS_CONFIG", "/etc/mosdns/config.yaml"),
                    "size": len(text.encode("utf-8")), "summary": _mosdns_summary(text),
                })
                return
            if path == "/api/mosdns/file":
                name = urllib.parse.parse_qs(parsed.query).get("name", [""])[0]
                self.json(200, {"ok": True, **APP.mosdns_file_read(name)})
                return
            if path == "/api/audit":
                limit = int(urllib.parse.parse_qs(parsed.query).get("limit", ["100"])[0])
                self.json(200, {"ok": True, "events": APP.audit_events(limit)})
                return
        except (HelperError, ValueError, OSError, UnicodeError) as exc:
            self.json(503 if isinstance(exc, HelperError) else 400, {"ok": False, "error": str(exc)})
            return
        self.json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        try:
            data = self.body_json(limit=1_100_000)
        except (ValueError, json.JSONDecodeError) as exc:
            self.json(400, {"ok": False, "error": str(exc)})
            return
        if path == "/api/login":
            ok, first, second = core.AUTH.login(self._remote(), str(data.get("username", "")), str(data.get("password", "")))
            if not ok:
                self.json(401, {"ok": False, "error": first})
                return
            secure = "; Secure" if _secure_cookie(self) else ""
            self.json(200, {"ok": True, "csrf": second}, {"Set-Cookie": f"iwan_session={first}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200{secure}"})
            return
        auth = self.require(mutate=True)
        if not auth:
            return
        token, session = auth
        actor, remote = self._actor(session), self._remote()
        try:
            if path == "/api/logout":
                core.AUTH.logout(token)
                secure = "; Secure" if _secure_cookie(self) else ""
                self.json(200, {"ok": True}, {"Set-Cookie": f"iwan_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict{secure}"})
                return
            if path == "/api/action":
                action, service = str(data.get("action", "")), str(data.get("service", ""))
                if action == "restart":
                    message = APP.restart(service, actor=actor, remote=remote)
                elif action == "backup":
                    message = APP.backup_singbox(actor=actor, remote=remote)
                else:
                    raise ValueError("操作无效")
                self.json(200, {"ok": True, "message": message})
                return
            if path in ("/api/import-ss", "/api/import-nodes"):
                nodes, errors = core.parse_import_text(str(data.get("text", "")))
                self.json(200, {"ok": True, "nodes": nodes, "errors": errors})
                return
            if path == "/api/latency":
                nodes = data.get("nodes", [])
                if not isinstance(nodes, list) or len(nodes) > 100:
                    raise ValueError("节点数量无效")
                self.json(200, {"ok": True, "results": core.node_latency(nodes)})
                return
            if path == "/api/save":
                ok, message = APP.apply(data, actor=actor, remote=remote)
                self.json(200 if ok else 500, {"ok": ok, "message": message, "error": "" if ok else message})
                return
            if path == "/api/autosave":
                request_id = str(data.pop("request_id", ""))
                seq = QUEUE.submit(data, request_id, actor, remote)
                self.close_connection = True
                self.json(202, {"ok": True, "seq": seq, "request_id": request_id}, {"Connection": "close"})
                return
            if path == "/api/mosdns/save":
                message = APP.mosdns_apply(str(data.get("config", "")), actor=actor, remote=remote)
                self.json(200, {"ok": True, "message": message})
                return
            if path == "/api/mosdns/action":
                message = APP.mosdns_action(str(data.get("action", "")), str(data.get("name", "")), actor=actor, remote=remote)
                self.json(200, {"ok": True, "message": message})
                return
            if path == "/api/mosdns/file/save":
                message = APP.mosdns_file_save(str(data.get("name", "")), str(data.get("content", "")), actor=actor, remote=remote)
                self.json(200, {"ok": True, "message": message})
                return
        except (HelperError, ValueError, OSError, UnicodeError) as exc:
            self.json(503 if isinstance(exc, HelperError) else 400, {"ok": False, "error": str(exc)})
            return
        self.json(404, {"ok": False, "error": "not found"})


def initialize() -> None:
    core.ensure_dirs()
    core.init_db()
    QUEUE.recover()


def serve(host: str, port: int) -> None:
    public_hosts = {"0.0.0.0", "::"}
    if host in public_hosts and os.environ.get("IWAN_ALLOW_PUBLIC_BIND", "0").lower() not in {"1", "true", "yes"}:
        raise SystemExit("拒绝公开监听；请使用反向代理，或显式设置 IWAN_ALLOW_PUBLIC_BIND=1")
    initialize()
    if not core.AUTH_FILE.exists():
        raise SystemExit("auth.json missing; run --init-auth first")
    if not APP.helper.ping():
        raise SystemExit("特权 helper 未就绪")
    core.SAMPLER.start()
    server = http.server.ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        core.SAMPLER.stop()
        server.server_close()
