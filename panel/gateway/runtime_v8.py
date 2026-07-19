"""v8 runtime: persistent auth, real iWAN status and sing-box 1.13 routing."""
from __future__ import annotations

import copy
import ipaddress
import os
import re
import time
from email.utils import formatdate
from pathlib import Path
from typing import Any

import core

from . import runtime as base
from . import runtime_v712 as legacy

VERSION = "8.0.0"
DATA_DIR = Path(os.environ.get("IWAN_PANEL_DATA_DIR", "/var/lib/iwan-gateway"))
legacy.REMEMBER_KEY = DATA_DIR / "remember.key"
core.VERSION = VERSION


def _cookie_line(name: str, value: str, max_age: int, secure: bool) -> str:
    suffix = "; Secure" if secure else ""
    expires_at = time.time() + max(0, max_age)
    expires = formatdate(expires_at, usegmt=True)
    return (
        f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; "
        f"Max-Age={max_age}; Expires={expires}{suffix}"
    )


legacy._cookie_line = _cookie_line


def _route_rule(category: str, outbound: str) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "domain_suffix": list(core.CATEGORY_DOMAINS[category]),
        "action": "route",
        "outbound": outbound,
    }
    if category == "telegram":
        rule["ip_cidr"] = list(core.TELEGRAM_CIDRS)
    return rule


def managed_rules(mappings: dict[str, str]) -> list[dict[str, Any]]:
    """Generate modern sing-box rules.

    sing-box 1.11+ moved outbound selection into a final route action.  A sniff
    action is installed first so domain rules work for iWAN forwarded traffic.
    """
    rules: list[dict[str, Any]] = [{"action": "sniff"}]
    for category in core.ROUTE_CATEGORIES:
        outbound = str(mappings.get(category, "")).strip()
        if outbound:
            rules.append(_route_rule(category, outbound))
    return rules


core.managed_rules = managed_rules
_original_sample_config = core.sample_config


def _socket_rows() -> tuple[list[str], list[str]]:
    listen_rows: list[str] = []
    connection_rows: list[str] = []
    for args, target in ((["ss", "-H", "-lntu"], listen_rows), (["ss", "-H", "-ntu"], connection_rows)):
        code, output = core.run(args, timeout=4)
        if code == 0:
            target.extend(line.strip() for line in output.splitlines() if line.strip())
    return listen_rows, connection_rows


def _peer_from_row(row: str, port: int) -> str:
    columns = row.split()
    if len(columns) < 5:
        return ""
    candidates = columns[-2:]
    for value in reversed(candidates):
        text = value.strip("[]")
        if f":{port}" in text:
            continue
        host = text.rsplit(":", 1)[0].strip("[]")
        try:
            address = ipaddress.ip_address(host)
            if not address.is_loopback and not address.is_unspecified:
                return str(address)
        except ValueError:
            continue
    return ""


def sample_config() -> dict[str, Any]:
    sampled = _original_sample_config()
    iwan = sampled.get("iwan") if isinstance(sampled.get("iwan"), dict) else {}
    try:
        port = int(iwan.get("listen_port") or 0)
    except (TypeError, ValueError):
        port = 0
    listening = False
    peers: list[str] = []
    if port:
        listen_rows, connection_rows = _socket_rows()
        marker = f":{port}"
        listening = any(marker in row for row in listen_rows)
        for row in connection_rows:
            if marker not in row:
                continue
            peer = _peer_from_row(row, port)
            if peer and peer not in peers:
                peers.append(peer)
    raw = core.read_json(core.SINGBOX_CONFIG, {})
    route = raw.get("route", {}) if isinstance(raw, dict) else {}
    rules = route.get("rules", []) if isinstance(route, dict) else []
    modern_routes = [
        rule for rule in rules
        if isinstance(rule, dict) and rule.get("action") == "route" and rule.get("outbound")
    ] if isinstance(rules, list) else []
    sniff_enabled = any(
        isinstance(rule, dict) and rule.get("action") == "sniff"
        for rule in rules
    ) if isinstance(rules, list) else False
    sampled["iwan_runtime"] = {
        "configured": bool(iwan),
        "listening": listening,
        "port": port,
        "client_count": len(peers),
        "peers": peers[:12],
    }
    sampled["routing_runtime"] = {
        "sniff": sniff_enabled,
        "modern_rule_count": len(modern_routes),
        "effective": sniff_enabled and bool(modern_routes or sampled.get("default")),
    }
    return sampled


core.sample_config = sample_config


base.ASSETS.update({
    "/assets/v8.css": ("v8.css", "text/css; charset=utf-8"),
    "/assets/v8.js": ("v8.js", "application/javascript; charset=utf-8"),
})


def _page_html() -> bytes:
    text = (core.WEB_DIR / "index.html").read_text(encoding="utf-8")
    if "/assets/remember.css" not in text:
        text = text.replace("</head>", '<link rel="stylesheet" href="/assets/remember.css">\n</head>')
    text = text.replace("</head>", '<link rel="stylesheet" href="/assets/v8.css">\n</head>')
    if "/assets/remember.js" not in text:
        text = text.replace("</body>", '<script src="/assets/remember.js" defer></script>\n</body>')
    text = text.replace("</body>", '<script src="/assets/v8.js" defer></script>\n</body>')
    return text.encode("utf-8")


base._page_html = _page_html
legacy.base._page_html = _page_html
legacy.base.ASSETS = base.ASSETS


def serve(host: str, port: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass
    legacy.serve(host, port)
