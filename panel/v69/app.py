#!/usr/bin/env python3
"""iWAN Gateway v6.7.0: public first-run bootstrap without private defaults."""
from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

import reliablecore

core = reliablecore.core
moscore = reliablecore.moscore
authcore = reliablecore.authcore
interactioncore = reliablecore.interactioncore
statuscore = reliablecore.statuscore
routingcore = reliablecore.routingcore
autosavecore = reliablecore.autosavecore
VERSION = "6.7.0"
for module in (core, moscore, authcore, interactioncore, statuscore, routingcore, autosavecore, reliablecore):
    module.VERSION = VERSION

ORIGINAL_APPLY = core.apply_config
AUTOSAVE_JS = core.WEB_DIR / "autosave.js"
AUTOSAVE_CSS = core.WEB_DIR / "autosave.css"


def _has_iwan(config: dict[str, Any]) -> bool:
    inbounds = config.get("inbounds", [])
    return isinstance(inbounds, list) and any(core.is_iwan_inbound(item) for item in inbounds)


def _new_iwan(patch: dict[str, Any]) -> dict[str, Any]:
    username = str(patch.get("username", "")).strip()
    password = str(patch.get("password", ""))
    if not username or not password:
        raise ValueError("首次创建 iWAN 入口时，用户名和密码都必须填写")
    try:
        port = int(patch.get("listen_port") or 8000)
        mtu = int(patch.get("mtu") or 1400)
    except (TypeError, ValueError) as exc:
        raise ValueError("iWAN 端口或 MTU 无效") from exc
    if not 1 <= port <= 65535:
        raise ValueError("iWAN 监听端口无效")
    if not 576 <= mtu <= 9000:
        raise ValueError("iWAN MTU 无效")
    pool = str(patch.get("address_pool") or patch.get("address") or "10.10.10.0/24").strip()
    if not pool:
        raise ValueError("iWAN 地址池不能为空")
    return {
        "type": "iwan",
        "tag": "iwan-in",
        "listen": str(patch.get("listen") or "::"),
        "listen_port": port,
        "address_pool": pool,
        "mtu": mtu,
        "users": [{"username": username, "password": password}],
    }


def public_apply_config(payload: dict[str, Any]) -> tuple[bool, str]:
    """Create the first iWAN inbound on demand, then use the proven apply path."""
    current = core.read_json(core.SINGBOX_CONFIG, {})
    patch = payload.get("iwan")
    if not isinstance(current, dict) or not current:
        return False, "未找到有效 sing-box 配置"
    if not isinstance(patch, dict) or not patch or _has_iwan(current):
        return ORIGINAL_APPLY(payload)

    seeded = copy.deepcopy(current)
    inbounds = seeded.setdefault("inbounds", [])
    if not isinstance(inbounds, list):
        return False, "inbounds 格式无效"
    inbounds.append(_new_iwan(patch))

    original_bytes = core.SINGBOX_CONFIG.read_bytes() if core.SINGBOX_CONFIG.exists() else b""
    fd, tmp_name = tempfile.mkstemp(prefix="bootstrap.", suffix=".json", dir=str(core.SINGBOX_CONFIG.parent))
    os.close(fd)
    temporary = Path(tmp_name)
    try:
        temporary.write_text(json.dumps(seeded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, core.SINGBOX_CONFIG)
        ok, message = ORIGINAL_APPLY(payload)
        if ok:
            return True, "iWAN 入口已创建，" + message
        if original_bytes:
            core.SINGBOX_CONFIG.write_bytes(original_bytes)
            os.chmod(core.SINGBOX_CONFIG, 0o600)
            core.service_restart("sing-box")
        return False, message
    except Exception:
        if original_bytes:
            core.SINGBOX_CONFIG.write_bytes(original_bytes)
            os.chmod(core.SINGBOX_CONFIG, 0o600)
            core.service_restart("sing-box")
        raise
    finally:
        temporary.unlink(missing_ok=True)


core.apply_config = public_apply_config
QUEUE = reliablecore.ReliableAutosaveQueue(runner=public_apply_config)


def page_html() -> bytes:
    return reliablecore.page_html()


class Handler(reliablecore.Handler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/autosave-status":
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
            seq = QUEUE.submit(reliablecore.autosave_payload(data), request_id)
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
    reliablecore.self_test()
    generated = _new_iwan({
        "listen": "::", "listen_port": 8000, "address_pool": "10.10.10.0/24",
        "mtu": 1400, "username": "public-user", "password": "public-password",
    })
    assert generated["users"][0]["username"] == "public-user"
    assert generated["listen_port"] == 8000
    assert not _has_iwan({"inbounds": []})
    assert _has_iwan({"inbounds": [generated]})
    assert b"autosave.js" in page_html()
    print(json.dumps({"ok": True, "version": VERSION, "bootstrap": "public-first-run"}))


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
