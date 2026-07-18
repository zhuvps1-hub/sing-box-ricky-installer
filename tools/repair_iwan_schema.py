#!/usr/bin/env python3
"""Repair legacy iWAN ``address`` fields transactionally.

Dry-run is the default. Use ``--apply`` to write the migrated configuration.
The script validates the candidate with sing-box before replacing the live file,
creates a timestamped backup, and restores it if restart/health checks fail.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def run(command: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        return completed.returncode, (completed.stdout + completed.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def is_iwan(value: Any) -> bool:
    return isinstance(value, dict) and (
        "iwan" in str(value.get("type", "")).lower()
        or "iwan" in str(value.get("tag", "")).lower()
    )


def migrate(config: dict[str, Any]) -> tuple[dict[str, Any], int]:
    candidate = copy.deepcopy(config)
    inbounds = candidate.get("inbounds", [])
    if not isinstance(inbounds, list):
        raise ValueError("inbounds 必须是数组")
    changed = 0
    for inbound in inbounds:
        if not is_iwan(inbound) or "address" not in inbound:
            continue
        if inbound.get("address_pool") in (None, ""):
            value = inbound.get("address")
            if isinstance(value, list):
                if len(value) != 1:
                    raise ValueError("旧 address 包含多个地址池，无法自动迁移")
                value = value[0]
            inbound["address_pool"] = value
        inbound.pop("address", None)
        changed += 1
    return candidate, changed


def write_candidate(path: Path, payload: dict[str, Any]) -> Path:
    descriptor, name = tempfile.mkstemp(prefix="iwan-repair.", suffix=".json", dir=str(path.parent))
    os.close(descriptor)
    candidate = Path(name)
    candidate.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(candidate, 0o600)
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair unsupported iWAN address fields")
    parser.add_argument("--config", default="/etc/sing-box/config.json")
    parser.add_argument("--sing-box", default="/usr/local/bin/sing-box")
    parser.add_argument("--service", default="sing-box")
    parser.add_argument("--apply", action="store_true", help="write and restart after validation")
    parser.add_argument("--no-restart", action="store_true", help="write the file but do not restart systemd")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_file():
        raise SystemExit(f"配置不存在：{config_path}")
    try:
        original = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"配置读取失败：{exc}") from exc
    if not isinstance(original, dict):
        raise SystemExit("配置顶层必须是对象")

    candidate_payload, changed = migrate(original)
    if changed == 0:
        print("未发现 iWAN legacy address 字段，无需修改。")
        return 0
    print(f"发现 {changed} 个 iWAN inbound 使用不兼容的 address 字段。")
    if not args.apply:
        print("这是预览模式；确认后重新执行并添加 --apply。")
        return 2
    if os.geteuid() != 0:
        raise SystemExit("写入系统配置需要 root 权限")

    candidate = write_candidate(config_path, candidate_payload)
    backup = config_path.with_name(config_path.name + ".bak.iwan-" + time.strftime("%Y%m%d-%H%M%S"))
    try:
        code, output = run([args.sing_box, "check", "-c", str(candidate)])
        if code != 0:
            raise SystemExit("候选配置校验失败：\n" + output)
        shutil.copy2(config_path, backup)
        os.replace(candidate, config_path)
        os.chmod(config_path, 0o600)
        if args.no_restart:
            print(f"修复完成，未重启服务。备份：{backup}")
            return 0
        code, output = run(["systemctl", "restart", args.service], timeout=45)
        active, active_output = run(["systemctl", "is-active", "--quiet", args.service], timeout=10)
        if code != 0 or active != 0:
            shutil.copy2(backup, config_path)
            run(["systemctl", "restart", args.service], timeout=45)
            raise SystemExit("服务启动失败，已恢复备份：\n" + (output or active_output))
        print(f"修复完成并已重启 {args.service}。备份：{backup}")
        return 0
    finally:
        candidate.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
