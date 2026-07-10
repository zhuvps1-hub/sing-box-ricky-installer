#!/usr/bin/env python3
"""iWAN Gateway v6.3.2: resilient post-save status confirmation."""
from __future__ import annotations

import argparse
import json
import os
import urllib.parse

import interactioncore

core = interactioncore.core
moscore = interactioncore.moscore
authcore = interactioncore.authcore
VERSION = "6.3.2"
core.VERSION = VERSION
moscore.VERSION = VERSION
authcore.VERSION = VERSION
interactioncore.VERSION = VERSION
REFRESH_FIX_JS = core.WEB_DIR / "refreshfix.js"


def page_html() -> bytes:
    html = interactioncore.page_html().decode("utf-8")
    if "/assets/refreshfix.js" not in html:
        html = html.replace("</body>", '  <script src="/assets/refreshfix.js" defer></script>\n</body>', 1)
    return html.encode("utf-8")


class Handler(interactioncore.Handler):
    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            try:
                self.send_bytes(200, page_html(), "text/html; charset=utf-8")
            except OSError as exc:
                self.json(500, {"ok": False, "error": str(exc)})
            return
        if path == "/assets/refreshfix.js":
            self.serve_file(REFRESH_FIX_JS, "application/javascript; charset=utf-8")
            return
        super().do_GET()


def self_test() -> None:
    interactioncore.self_test()
    assert b"refreshfix.js" in page_html()
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
