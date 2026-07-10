#!/usr/bin/env python3
"""iWAN Gateway v6.2.1: persistent, password-free remembered login."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import moscore

core = moscore.core
VERSION = "6.2.1"
core.VERSION = VERSION
moscore.VERSION = VERSION
REMEMBER_TTL = 30 * 24 * 60 * 60
REMEMBER_PREFIX = "r1"
REMEMBER_JS = core.WEB_DIR / "remember.js"


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def password_fingerprint(record: dict[str, Any]) -> str:
    payload = json.dumps(record.get("password", {}), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:24]


def ensure_remember_secret() -> tuple[dict[str, Any], bytes]:
    with core.AUTH.lock:
        record = core.AUTH.record()
        encoded = str(record.get("session_secret", ""))
        try:
            secret = b64url_decode(encoded)
            if len(secret) < 32:
                raise ValueError("secret too short")
        except (ValueError, TypeError):
            secret = secrets.token_bytes(32)
            record["session_secret"] = b64url_encode(secret)
            core.atomic_json(core.AUTH_FILE, record)
        return record, secret


def encode_remember_token(username: str, csrf: str, record: dict[str, Any], secret: bytes, expires: int) -> str:
    payload = {
        "u": username,
        "e": int(expires),
        "c": csrf,
        "p": password_fingerprint(record),
    }
    body = b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = b64url_encode(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    return f"{REMEMBER_PREFIX}.{body}.{signature}"


def decode_remember_token(token: str, record: dict[str, Any], secret: bytes, now: int | None = None) -> dict[str, Any] | None:
    try:
        prefix, body, signature = token.split(".", 2)
        if prefix != REMEMBER_PREFIX:
            return None
        expected = b64url_encode(hmac.new(secret, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(b64url_decode(body))
        current = int(time.time()) if now is None else int(now)
        if int(payload.get("e", 0)) <= current:
            return None
        if not hmac.compare_digest(str(payload.get("u", "")), str(record.get("username", ""))):
            return None
        if not hmac.compare_digest(str(payload.get("p", "")), password_fingerprint(record)):
            return None
        csrf = str(payload.get("c", ""))
        if len(csrf) < 16:
            return None
        return {
            "csrf": csrf,
            "expires": int(payload["e"]),
            "username": str(payload["u"]),
            "remembered": True,
        }
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def make_remember_token(username: str, csrf: str) -> str:
    record, secret = ensure_remember_secret()
    return encode_remember_token(username, csrf, record, secret, int(time.time()) + REMEMBER_TTL)


def verify_remember_token(token: str) -> dict[str, Any] | None:
    record, secret = ensure_remember_secret()
    return decode_remember_token(token, record, secret)


def session_cookie(token: str, max_age: int) -> str:
    return f"iwan_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}; Priority=High"


def clear_session_cookie() -> str:
    return "iwan_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"


def login_page() -> bytes:
    html = (core.WEB_DIR / "index.html").read_text(encoding="utf-8")
    if 'id="rememberLogin"' not in html:
        button = '<button class="btn primary wide" type="submit">登录</button>'
        remember = (
            '<label class="remember-login">'
            '<input id="rememberLogin" type="checkbox" checked>'
            '<span><b>记住登录</b><small>30 天内无需再次输入，不保存明文密码</small></span>'
            '</label>'
        )
        html = html.replace(button, remember + button, 1)
    if '/assets/remember.js' not in html:
        marker = '<script src="/assets/core.js" defer></script>'
        html = html.replace(marker, '<script src="/assets/remember.js" defer></script>\n  ' + marker, 1)
    return html.encode()


class Handler(moscore.Handler):
    def session(self) -> tuple[str, dict[str, Any] | None]:
        token = core.cookie_token(self.headers)
        if token.startswith(REMEMBER_PREFIX + "."):
            return token, verify_remember_token(token)
        return token, core.AUTH.session(token)

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            try:
                self.send_bytes(200, login_page(), "text/html; charset=utf-8")
            except OSError as exc:
                self.json(500, {"ok": False, "error": str(exc)})
            return
        if path == "/assets/remember.js":
            self.serve_file(REMEMBER_JS, "application/javascript; charset=utf-8")
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/login":
            try:
                data = self.body_json()
            except (ValueError, json.JSONDecodeError) as exc:
                self.json(400, {"ok": False, "error": str(exc)})
                return
            username = str(data.get("username", ""))
            password = str(data.get("password", ""))
            remember = data.get("remember") is True
            ok, token, csrf = core.AUTH.login(self.client_address[0], username, password)
            if not ok:
                self.json(401, {"ok": False, "error": token})
                return
            max_age = 12 * 60 * 60
            if remember:
                token = make_remember_token(username, csrf)
                max_age = REMEMBER_TTL
            self.json(
                200,
                {"ok": True, "csrf": csrf, "remembered": remember, "expires_in": max_age},
                {"Set-Cookie": session_cookie(token, max_age)},
            )
            return
        if path == "/api/logout":
            auth = self.require(mutate=True)
            if not auth:
                return
            token, _ = auth
            if not token.startswith(REMEMBER_PREFIX + "."):
                core.AUTH.logout(token)
            self.json(200, {"ok": True}, {"Set-Cookie": clear_session_cookie()})
            return
        super().do_POST()


def self_test() -> None:
    moscore.self_test()
    record = {"username": "admin", "password": {"hash": "example", "salt": "salt"}}
    secret = b"x" * 32
    token = encode_remember_token("admin", "csrf-token-1234567890", record, secret, 2000)
    assert decode_remember_token(token, record, secret, now=1000)["username"] == "admin"
    assert decode_remember_token(token + "x", record, secret, now=1000) is None
    assert decode_remember_token(token, record, secret, now=2001) is None
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
        ensure_remember_secret()
        print("auth initialized")
        return
    if args.self_test:
        self_test()
        return
    if not core.AUTH_FILE.exists():
        raise SystemExit("auth.json missing; run --init-auth first")
    ensure_remember_secret()
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
