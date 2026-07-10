#!/usr/bin/env python3
"""iWAN Gateway v6.4.0: make domain routing effective for IP-only iWAN traffic."""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

import statuscore

core = statuscore.core
moscore = statuscore.moscore
authcore = statuscore.authcore
interactioncore = statuscore.interactioncore
VERSION = "6.4.0"
for module in (core, moscore, authcore, interactioncore, statuscore):
    module.VERSION = VERSION


def managed_rules(mappings: dict[str, str]) -> list[dict[str, Any]]:
    """Generate effective transparent-proxy rules.

    iWAN usually forwards connections with an IP destination. Domain suffix
    rules cannot match those connections until sing-box sniffs HTTP Host, TLS
    SNI or QUIC Server Name. The sniff action is non-final, so routing
    continues into the independent category rules below.
    """
    rules: list[dict[str, Any]] = []
    active = any(str(mappings.get(category, "")).strip() for category in ("netflix", "ai", "youtube", "telegram"))
    if active:
        rules.append({
            "action": "sniff",
            "sniffer": ["http", "tls", "quic"],
        })

    for category in ("netflix", "ai", "youtube"):
        outbound = str(mappings.get(category, "")).strip()
        if not outbound:
            continue
        rules.append({
            "domain_suffix": core.CATEGORY_DOMAINS[category],
            "action": "route",
            "outbound": outbound,
        })

    telegram_outbound = str(mappings.get("telegram", "")).strip()
    if telegram_outbound:
        # Different fields in one sing-box rule are AND conditions. Keep domain
        # and IP matching separate so either form can route Telegram traffic.
        rules.append({
            "domain_suffix": core.CATEGORY_DOMAINS["telegram"],
            "action": "route",
            "outbound": telegram_outbound,
        })
        rules.append({
            "ip_cidr": core.TELEGRAM_CIDRS,
            "action": "route",
            "outbound": telegram_outbound,
        })
    return rules


# core.apply_config resolves managed_rules from its module globals at runtime.
core.managed_rules = managed_rules


def page_html() -> bytes:
    """Expose the inherited page renderer to extension layers."""
    return statuscore.page_html()


class Handler(statuscore.Handler):
    pass


def self_test() -> None:
    statuscore.self_test()
    rules = managed_rules({
        "netflix": "sg",
        "ai": "ai-node",
        "youtube": "jp",
        "telegram": "hk",
    })
    assert rules[0]["action"] == "sniff"
    assert rules[0]["sniffer"] == ["http", "tls", "quic"]
    assert rules[1]["action"] == "route" and rules[1]["outbound"] == "sg"
    assert rules[2]["outbound"] == "ai-node"
    assert rules[3]["outbound"] == "jp"
    assert rules[4]["outbound"] == "hk" and "domain_suffix" in rules[4] and "ip_cidr" not in rules[4]
    assert rules[5]["outbound"] == "hk" and "ip_cidr" in rules[5] and "domain_suffix" not in rules[5]
    assert managed_rules({}) == []
    assert b"refreshfix.js" in page_html()
    print(json.dumps({"ok": True, "version": VERSION, "routing": "sniff-first"}))


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
