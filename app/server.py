#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib import error as urlerror, request as urlrequest
from urllib.parse import quote

import app as core

SELECTOR_KEYS = [*core.CATS, "default"]
SELECTOR_TAGS = {key: f"iwan-route-{key}" for key in SELECTOR_KEYS}
SELECTOR_RESERVED = set(SELECTOR_TAGS.values())
CLASH_API = os.environ.get("IWAN_CLASH_API", "http://127.0.0.1:19090").rstrip("/")

ORIGINAL_BUILD = core.build


def build_with_selectors(state: dict) -> dict:
    config = ORIGINAL_BUILD(state)
    nodes = state["nodes"]
    routing = state["routing"]

    collisions = {node["tag"] for node in nodes} & SELECTOR_RESERVED
    if collisions:
        raise ValueError(f"节点名称与系统分流名称冲突：{', '.join(sorted(collisions))}")

    members = ["direct", *[node["tag"] for node in nodes]]
    for key in SELECTOR_KEYS:
        config["outbounds"].append(
            {
                "type": "selector",
                "tag": SELECTOR_TAGS[key],
                "outbounds": members,
                "default": routing[key],
                "interrupt_exist_connections": False,
            }
        )

    for rule in config["route"]["rules"]:
        rule_set = rule.get("rule_set")
        if rule_set == ["geosite-cn", "geoip-cn"]:
            rule["outbound"] = SELECTOR_TAGS["cn"]
            continue
        if rule_set == "geosite-ai":
            rule["outbound"] = SELECTOR_TAGS["ai"]
            continue

        domains = rule.get("domain_suffix")
        if not isinstance(domains, list):
            continue
        for key in ["ai", "youtube", "netflix", "tiktok", "telegram", "google"]:
            if domains == core.DOMAINS[key]:
                rule["outbound"] = SELECTOR_TAGS[key]
                break

    config["route"]["final"] = SELECTOR_TAGS["default"]
    experimental = config.setdefault("experimental", {})
    experimental["cache_file"] = {
        "enabled": True,
        "path": str(core.DATA / "selector-cache.db"),
    }
    experimental["clash_api"] = {
        "external_controller": "127.0.0.1:19090",
    }
    return config


def clash_request(method: str, path: str, payload: dict | None = None, timeout: float = 2.0) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urlrequest.Request(
        CLASH_API + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urlerror.HTTPError as exc:
        detail = exc.read().decode(errors="replace").strip()
        raise ValueError(detail or f"Clash API 返回 HTTP {exc.code}") from exc
    except urlerror.URLError as exc:
        raise ValueError(f"Clash API 不可用：{exc.reason}") from exc

    if not body:
        return {}
    try:
        value = json.loads(body)
    except Exception as exc:
        raise ValueError("Clash API 返回内容无效") from exc
    return value if isinstance(value, dict) else {}


def selector_path(key: str) -> str:
    return f"/proxies/{quote(SELECTOR_TAGS[key], safe='')}"


def switch_routing(previous: dict, desired: dict) -> list[str]:
    changed: list[str] = []
    applied: list[tuple[str, str]] = []

    try:
        for key in SELECTOR_KEYS:
            path = selector_path(key)
            status = clash_request("GET", path)
            original = str(status.get("now") or previous[key])
            target = desired[key]
            if original == target:
                continue

            clash_request("PUT", path, {"name": target})
            applied.append((key, original))

            confirmed = clash_request("GET", path)
            if confirmed.get("now") != target:
                raise ValueError(f"{core.LABELS.get(key, '默认出口')}线路切换未确认")
            changed.append(key)
    except Exception:
        for key, original in reversed(applied):
            try:
                clash_request("PUT", selector_path(key), {"name": original})
            except Exception:
                pass
        raise

    return changed


def full_reload(state: dict) -> None:
    with core.LOCK:
        config = core.build(state)
        core.SB.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix="iwan-",
            suffix=".json",
            dir=str(core.SB.parent),
        )
        os.close(descriptor)
        Path(temporary).write_text(json.dumps(config, ensure_ascii=False, indent=2))
        try:
            checked = subprocess.run(
                ["/usr/local/bin/sing-box", "check", "-c", temporary],
                text=True,
                capture_output=True,
                timeout=10,
            )
            if checked.returncode:
                raise ValueError((checked.stderr or checked.stdout).strip() or "配置检查失败")

            os.replace(temporary, core.SB)
            os.chmod(core.SB, 0o600)

            reloaded = subprocess.run(
                ["systemctl", "reload", "sing-box"],
                capture_output=True,
                timeout=4,
            )
            if reloaded.returncode:
                subprocess.run(
                    ["systemctl", "restart", "sing-box"],
                    check=True,
                    timeout=8,
                )
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def smart_apply_config(state: dict) -> None:
    desired_tags = {node["tag"] for node in state["nodes"]}
    state["routing"] = core.normalize_routing(state["routing"], desired_tags)

    current = core.internal_state()
    current_tags = {node["tag"] for node in current["nodes"]}
    previous_routing = core.normalize_routing(current["routing"], current_tags)

    structure_unchanged = (
        current["nodes"] == state["nodes"]
        and current["iwan"] == state["iwan"]
    )
    if structure_unchanged:
        try:
            switch_routing(previous_routing, state["routing"])
            return
        except Exception:
            pass

    full_reload(state)


core.build = build_with_selectors
core.apply_config = smart_apply_config


def sync_config() -> None:
    state = core.internal_state()
    state["routing"] = core.normalize_routing(
        state["routing"],
        {node["tag"] for node in state["nodes"]},
    )
    full_reload(state)
    core.save_state(state)


if __name__ == "__main__":
    core.init()
    if "--sync-config" in sys.argv:
        sync_config()
        raise SystemExit(0)
    core.ThreadingHTTPServer(("0.0.0.0", core.PORT), core.Handler).serve_forever()
