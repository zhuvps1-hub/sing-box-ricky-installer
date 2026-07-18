#!/usr/bin/env python3
"""Create and verify canonical Ed25519 release-manifest signatures."""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit("manifest 顶层必须是对象")
    return value


def canonical_bytes(manifest: dict[str, Any]) -> bytes:
    value = copy.deepcopy(manifest)
    value.pop("signature", None)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True)


def sign(manifest_path: Path, private_key: Path, key_id: str) -> None:
    manifest = load(manifest_path)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        canonical = root / "manifest.canonical.json"
        signature = root / "manifest.sig"
        canonical.write_bytes(canonical_bytes(manifest))
        run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(private_key), "-in", str(canonical), "-out", str(signature)])
        manifest["signature"] = {
            "algorithm": "ed25519",
            "key_id": key_id,
            "value": base64.b64encode(signature.read_bytes()).decode("ascii"),
        }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify(manifest_path: Path, public_key: Path) -> None:
    manifest = load(manifest_path)
    signature = manifest.get("signature")
    if not isinstance(signature, dict) or signature.get("algorithm") != "ed25519":
        raise SystemExit("manifest 缺少 Ed25519 签名")
    try:
        signature_bytes = base64.b64decode(str(signature.get("value", "")), validate=True)
    except ValueError as exc:
        raise SystemExit("manifest 签名不是有效 Base64") from exc
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        canonical = root / "manifest.canonical.json"
        signature_file = root / "manifest.sig"
        canonical.write_bytes(canonical_bytes(manifest))
        signature_file.write_bytes(signature_bytes)
        run(["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(public_key), "-sigfile", str(signature_file), "-in", str(canonical)])


def verify_files(manifest_path: Path, root: Path) -> None:
    manifest = load(manifest_path)
    files = manifest.get("files")
    if not isinstance(files, list):
        raise SystemExit("manifest.files 必须是数组")
    for item in files:
        if not isinstance(item, dict):
            raise SystemExit("manifest 文件项无效")
        target = root / str(item.get("target", ""))
        expected = str(item.get("sha256", ""))
        if not target.is_file() or len(expected) != 64:
            raise SystemExit(f"文件缺失或 sha256 无效：{target}")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"文件校验失败：{target}")


def public_key_fingerprint(public_key: Path) -> str:
    return hashlib.sha256(public_key.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    sign_parser = subparsers.add_parser("sign")
    sign_parser.add_argument("manifest", type=Path)
    sign_parser.add_argument("private_key", type=Path)
    sign_parser.add_argument("--key-id", default="iwan-release-2026-01")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("manifest", type=Path)
    verify_parser.add_argument("public_key", type=Path)
    files_parser = subparsers.add_parser("verify-files")
    files_parser.add_argument("manifest", type=Path)
    files_parser.add_argument("root", type=Path)
    fingerprint_parser = subparsers.add_parser("fingerprint")
    fingerprint_parser.add_argument("public_key", type=Path)
    args = parser.parse_args()
    if args.command == "sign":
        sign(args.manifest, args.private_key, args.key_id)
    elif args.command == "verify":
        verify(args.manifest, args.public_key)
        print("signature verified")
    elif args.command == "verify-files":
        verify_files(args.manifest, args.root)
        print("files verified")
    else:
        print(public_key_fingerprint(args.public_key))


if __name__ == "__main__":
    main()
