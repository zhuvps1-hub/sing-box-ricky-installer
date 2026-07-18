#!/usr/bin/env python3
"""Build an unsigned deterministic v8.0.0 release manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

STATIC_FILES = [
    ("install-panel-v712.sh", "install-panel-v71.sh", "0755"),
    ("panel/gateway/entrypoint.py", "app.py", "0755"),
    ("panel/app.py", "core.py", "0644"),
    ("panel/iwan_compat.py", "iwan_compat.py", "0644"),
    ("panel/web/index.html", "web/index.html", "0644"),
    ("panel/web/app.css", "web/app.css", "0644"),
    ("panel/web/app.js", "web/app.js", "0644"),
    ("panel/web/remember.css", "web/remember.css", "0644"),
    ("panel/web/remember.js", "web/remember.js", "0644"),
    ("panel/web/v8.css", "web/v8.css", "0644"),
    ("panel/web/v8.js", "web/v8.js", "0644"),
]


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def sources(root: Path) -> Iterable[tuple[str, str, str]]:
    yield from STATIC_FILES
    for path in sorted((root / "panel/gateway").glob("*.py")):
        yield path.relative_to(root).as_posix(), f"gateway/{path.name}", "0644"


def build(root: Path, version: str, ref: str) -> dict:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9_.-]+)?", version):
        raise SystemExit("version invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise SystemExit("ref invalid")
    files = []
    targets: set[str] = set()
    for source, target, mode in sources(root):
        if target in targets:
            raise SystemExit(f"duplicate target: {target}")
        targets.add(target)
        data = (root / source).read_bytes()
        files.append({
            "source": source,
            "target": target,
            "mode": mode,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "git_blob": git_blob(data),
        })
    return {
        "schema": 2,
        "product": "iwan-gateway-panel",
        "version": version,
        "ref": ref,
        "architecture": "flat-package+root-helper",
        "minimum_python": "3.11",
        "release_key": {
            "algorithm": "ed25519",
            "key_id": "iwan-release-2026-01",
            "public_key_sha256": "ebc439b79669d73666d10989b5dfe9438976c6f5f4f9a5064cc3ab02edbdc25e",
        },
        "files": files,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--version", default="8.0.0")
    parser.add_argument("--ref", required=True)
    parser.add_argument("--output", type=Path, default=Path("panel-release-v8-unsigned.json"))
    args = parser.parse_args()
    manifest = build(args.root.resolve(), args.version, args.ref)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
