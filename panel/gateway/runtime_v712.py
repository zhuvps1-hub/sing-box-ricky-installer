"""v7.1.2 runtime fixes for public access, persistent login and unified assets."""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.cookies
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import core

from . import runtime as base

REMEMBER_COOKIE = "iwan_remember"
REMEMBER_TTL = int(os.environ.get("IWAN_REMEMBER_TTL", str(30 * 24 * 3600)))
CONFIG_DIR = Path(os.environ.get("IWAN_PANEL_CONFIG_DIR", "/etc/iwan-gateway"))
REMEMBER_KEY = CONFIG_DIR / "remember.key"


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _remember_secret() -> bytes:
    core.ensure_dirs()
    try:
        data = REMEMBER_KEY.read_bytes()
        if len(data) >= 32:
            return data
    except OSError:
        pass
    data = secrets.token_bytes(32)
    try:
        descriptor = os.open(REMEMBER_KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return REMEMBER_KEY.read_bytes()
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return data


def _auth_fingerprint() -> str:
    record = core.read_json(core.AUTH_FILE, {})
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _create_remember_token(username: str) -> tuple[str, str]:
    csrf = secrets.token_urlsafe(24)
    payload = {
        "username": username,
        "csrf": csrf,
        "expires": int(time.time()) + REMEMBER_TTL,
        "auth": _auth_fingerprint(),
    }
    encoded = _b64encode(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    signature = _b64encode(hmac.new(_remember_secret(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}", csrf


def _verify_remember_token(token: str) -> dict[str, Any] | None:
    try:
        encoded, signature = token.split(".", 1)
        expected = _b64encode(hmac.new(_remember_secret(), encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_b64decode(encoded))
        if not isinstance(payload, dict):
            return None
        if int(payload.get("expires", 0)) <= int(time.time()):
            return None
        if not hmac.compare_digest(str(payload.get("auth", "")), _auth_fingerprint()):
            return None
        record = core.read_json(core.AUTH_FILE, {})
        if not hmac.compare_digest(str(payload.get("username", "")), str(record.get("username", ""))):
            return None
        csrf = str(payload.get("csrf", ""))
        if len(csrf) < 20:
            return None
        return {
            "username": str(payload["username"]),
            "csrf": csrf,
            "expires": int(payload["expires"]),
            "remembered": True,
        }
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, OSError):
        return None


def _cookie(headers: Any, name: str) -> str:
    value = http.cookies.SimpleCookie()
    try:
        value.load(headers.get("Cookie", ""))
        item = value.get(name)
        return item.value if item else ""
    except Exception:
        return ""


def _cookie_line(name: str, value: str, max_age: int, secure: bool) -> str:
    suffix = "; Secure" if secure else ""
    return f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}{suffix}"


def _page_html() -> bytes:
    text = (core.WEB_DIR / "index.html").read_text(encoding="utf-8")
    text = text.replace("</head>", '<link rel="stylesheet" href="/assets/remember.css"></head>')
    text = text.replace("</body>", '<script src="/assets/remember.js" defer></script></body>')
    return text.encode("utf-8")


base.ASSETS.update({
    "/assets/remember.css": ("remember.css", "text/css; charset=utf-8"),
    "/assets/remember.js": ("remember.js", "application/javascript; charset=utf-8"),
})
base._page_html = _page_html


class Handler(base.Handler):
    """Adds multi-cookie responses and a restart-safe remembered session."""

    def send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str = "application/json; charset=utf-8",
        headers: dict[str, str | list[str] | tuple[str, ...]] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if headers:
            for key, value in headers.items():
                values = value if isinstance(value, (list, tuple)) else (value,)
                for item in values:
                    self.send_header(key, str(item))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def session(self) -> tuple[str, dict[str, Any] | None]:
        token, session = super().session()
        if session:
            return token, session
        remember = _cookie(self.headers, REMEMBER_COOKIE)
        if not remember:
            return "", None
        return remember, _verify_remember_token(remember)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/login":
            try:
                data = self.body_json(limit=64 * 1024)
            except (ValueError, json.JSONDecodeError) as exc:
                self.json(400, {"ok": False, "error": str(exc)})
                return
            username = str(data.get("username", ""))
            ok, first, second = core.AUTH.login(self._remote(), username, str(data.get("password", "")))
            if not ok:
                self.json(401, {"ok": False, "error": first})
                return
            secure = base._secure_cookie(self)
            remember = data.get("remember") is True
            cookies: list[str] = [_cookie_line("iwan_session", first, 12 * 3600, secure)]
            csrf = second
            if remember:
                persistent, csrf = _create_remember_token(username)
                cookies.append(_cookie_line(REMEMBER_COOKIE, persistent, REMEMBER_TTL, secure))
            else:
                cookies.append(_cookie_line(REMEMBER_COOKIE, "", 0, secure))
            self.json(200, {"ok": True, "csrf": csrf, "remembered": remember}, {"Set-Cookie": cookies})
            return

        if path == "/api/logout":
            try:
                self.body_json(limit=64 * 1024)
            except (ValueError, json.JSONDecodeError) as exc:
                self.json(400, {"ok": False, "error": str(exc)})
                return
            auth = self.require(mutate=True)
            if not auth:
                return
            token, _ = auth
            core.AUTH.logout(token)
            secure = base._secure_cookie(self)
            cookies = [
                _cookie_line("iwan_session", "", 0, secure),
                _cookie_line(REMEMBER_COOKIE, "", 0, secure),
            ]
            self.json(200, {"ok": True}, {"Set-Cookie": cookies})
            return

        super().do_POST()


base.Handler = Handler


def serve(host: str, port: int) -> None:
    if host in {"0.0.0.0", "::"}:
        os.environ.setdefault("IWAN_ALLOW_PUBLIC_BIND", "1")
    base.serve(host, port)
