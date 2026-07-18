#!/usr/bin/env python3
"""Unified entrypoint for the flat web process and privileged helper."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path


def _prepare_path() -> Path:
    script = Path(__file__).resolve()
    root = script.parent if script.parent.name != "gateway" else script.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def _load_core() -> object:
    try:
        module = importlib.import_module("core")
    except ModuleNotFoundError:
        module = importlib.import_module("app")
        sys.modules["core"] = module
    return module


def _self_test() -> None:
    from gateway import VERSION
    from gateway.redaction import redact, redact_text
    from gateway.singbox import render

    current = {
        "inbounds": [{"type": "iwan", "tag": "iwan-in", "address": "10.10.10.8/24", "users": [{"username": "old", "password": "secret"}]}],
        "outbounds": [{"type": "direct", "tag": "direct"}],
        "route": {"rules": [], "final": "direct"},
    }
    candidate, managed = render(current, {
        "nodes": [{"tag": "sg", "server": "example.com", "server_port": 8388, "method": "aes-128-gcm", "password": "node-secret"}],
        "deleted_tags": [],
        "mappings": {"ai": "sg"},
        "default": "sg",
        "iwan": {"address": "10.20.0.9/16", "username": "new", "password": ""},
    }, [])
    inbound = candidate["inbounds"][0]
    assert inbound["address_pool"] == "10.20.0.0/16" and "address" not in inbound
    assert inbound["users"][0]["password"] == "secret"
    assert candidate["route"]["final"] == "sg" and managed
    assert redact({"password": "x", "nested": {"token": "y"}}) == {"password": "***", "nested": {"token": "***"}}
    assert "secret" not in redact_text("password=secret")
    print(json.dumps({"ok": True, "version": VERSION, "architecture": "flat-package+root-helper"}))


def main() -> None:
    _prepare_path()
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("PANEL_BIND", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PANEL_PORT", "8088")))
    parser.add_argument("--helper", action="store_true")
    parser.add_argument("--init-auth", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        return
    if args.helper:
        from gateway.helper import _gid_from_env, _uid_from_env, main as helper_main
        if _uid_from_env() is None or _gid_from_env() is None:
            raise SystemExit("helper 拒绝启动：面板用户或用户组不存在")
        helper_main()
        return
    core = _load_core()
    if args.init_auth:
        core.ensure_dirs()
        core.init_db()
        core.AUTH.initialize(os.environ.get("PANEL_ADMIN_USER", "admin"), os.environ.get("PANEL_ADMIN_PASSWORD", ""))
        print("auth initialized")
        return
    from gateway.runtime_v8 import serve
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
