#!/usr/bin/env python3
"""iWAN Gateway v6.2 extension: complete mosdns management."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

import core

VERSION = "6.2.0"
core.VERSION = VERSION
MOSDNS_CONFIG = Path(os.environ.get("MOSDNS_CONFIG", "/etc/mosdns/config.yaml"))
MOSDNS_BACKUP_DIR = Path(os.environ.get("MOSDNS_BACKUP_DIR", "/etc/mosdns/backups"))
MAX_TEXT_FILE = 1_048_576


def read_text_limited(path: Path, limit: int = MAX_TEXT_FILE) -> str:
    data = path.read_bytes()
    if len(data) > limit:
        raise ValueError(f"文件超过 {limit // 1024} KB，拒绝在网页中编辑")
    if b"\0" in data:
        raise ValueError("文件包含二进制数据，无法在网页中编辑")
    return data.decode("utf-8")


def mosdns_summary(text: str) -> dict[str, Any]:
    tags = re.findall(r"(?m)^\s*-?\s*tag:\s*['\"]?([^'\"#\s]+)", text)
    types = re.findall(r"(?m)^\s*type:\s*['\"]?([^'\"#\s]+)", text)
    addresses = re.findall(r"(?m)^\s*-?\s*(?:addr|listen):\s*['\"]?([^'\"#\s]+)", text)
    upstreams: list[str] = []
    for value in re.findall(r"(?m)^\s*-?\s*addr:\s*['\"]?([^'\"#\s]+)", text):
        if value not in upstreams:
            upstreams.append(value)
    return {
        "plugins": len(tags), "tags": tags[:40], "types": sorted(set(types))[:40],
        "addresses": addresses[:40], "upstreams": upstreams[:40],
        "lines": text.count("\n") + (1 if text else 0),
    }


def list_backups(limit: int = 40) -> list[dict[str, Any]]:
    if not MOSDNS_BACKUP_DIR.exists():
        return []
    items = []
    for path in MOSDNS_BACKUP_DIR.iterdir():
        if not path.is_file() or path.suffix.lower() not in (".yaml", ".yml"):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append({"name": path.name, "size": stat.st_size, "mtime": int(stat.st_mtime)})
    return sorted(items, key=lambda item: item["mtime"], reverse=True)[:limit]


def safe_mosdns_path(name: str) -> Path:
    name = name.strip().replace("\\", "/")
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError("文件路径无效")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", name):
        raise ValueError("文件路径包含不允许的字符")
    root = MOSDNS_CONFIG.parent.resolve()
    target = (root / name).resolve()
    if root not in target.parents and target != root:
        raise ValueError("文件路径越界")
    if MOSDNS_BACKUP_DIR.resolve() in target.parents:
        raise ValueError("不能直接编辑备份目录")
    return target


def list_mosdns_files() -> list[dict[str, Any]]:
    root = MOSDNS_CONFIG.parent
    if not root.exists():
        return []
    allowed = {".yaml", ".yml", ".txt", ".list", ".conf", ".rules", ".hosts"}
    items = []
    for path in root.rglob("*"):
        try:
            resolved = path.resolve()
            if not path.is_file() or MOSDNS_BACKUP_DIR.resolve() in resolved.parents:
                continue
            stat = path.stat()
            if path.suffix.lower() not in allowed or stat.st_size > MAX_TEXT_FILE:
                continue
            items.append({
                "name": path.relative_to(root).as_posix(), "size": stat.st_size,
                "mtime": int(stat.st_mtime), "config": resolved == MOSDNS_CONFIG.resolve(),
            })
        except OSError:
            continue
    return sorted(items, key=lambda item: (not item["config"], item["name"]))[:200]


def mosdns_state() -> dict[str, Any]:
    text = ""; error = ""
    if MOSDNS_CONFIG.exists():
        try:
            text = read_text_limited(MOSDNS_CONFIG)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            error = str(exc)
    stat = MOSDNS_CONFIG.stat() if MOSDNS_CONFIG.exists() else None
    return {
        "available": MOSDNS_CONFIG.exists(), "service_active": core.service_active("mosdns"),
        "path": str(MOSDNS_CONFIG), "config": text, "error": error,
        "size": stat.st_size if stat else 0, "mtime": int(stat.st_mtime) if stat else 0,
        "summary": mosdns_summary(text), "backups": list_backups(), "files": list_mosdns_files(),
    }


def validate_mosdns_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("mosdns 配置不能为空")
    if len(text.encode("utf-8")) > MAX_TEXT_FILE:
        raise ValueError("mosdns 配置超过 1 MB")
    if "\0" in text:
        raise ValueError("配置包含 NUL 字符")
    if any(line.startswith("\t") for line in text.splitlines()):
        raise ValueError("YAML 缩进不能使用 Tab，请改为空格")
    return text if text.endswith("\n") else text + "\n"


def atomic_text(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text); handle.flush(); os.fsync(handle.fileno())
        os.chmod(tmp_name, mode); os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def wait_mosdns(seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if core.service_active("mosdns"):
            return True
        time.sleep(0.5)
    return False


def apply_mosdns_config(text: str) -> tuple[bool, str]:
    text = validate_mosdns_text(text)
    MOSDNS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    MOSDNS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    existed = MOSDNS_CONFIG.exists()
    backup = MOSDNS_BACKUP_DIR / f"config-{time.strftime('%Y%m%d-%H%M%S')}.yaml"
    if existed:
        shutil.copy2(MOSDNS_CONFIG, backup)
    mode = (MOSDNS_CONFIG.stat().st_mode & 0o777) if existed else 0o600
    atomic_text(MOSDNS_CONFIG, text, mode)
    ok, output = core.service_restart("mosdns")
    if not ok or not wait_mosdns():
        if existed and backup.exists():
            shutil.copy2(backup, MOSDNS_CONFIG)
        elif not existed:
            MOSDNS_CONFIG.unlink(missing_ok=True)
        core.service_restart("mosdns")
        return False, "mosdns 启动失败，已恢复旧配置：\n" + output
    return True, f"mosdns 配置已应用，备份：{backup.name if existed else '首次创建'}"


def backup_mosdns() -> str:
    if not MOSDNS_CONFIG.exists():
        raise ValueError("mosdns 配置不存在")
    MOSDNS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"manual-{time.strftime('%Y%m%d-%H%M%S')}.yaml"
    shutil.copy2(MOSDNS_CONFIG, MOSDNS_BACKUP_DIR / name)
    return name


def restore_mosdns(name: str) -> tuple[bool, str]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:yaml|yml)", name):
        raise ValueError("备份文件名无效")
    source = MOSDNS_BACKUP_DIR / name
    if not source.is_file():
        raise ValueError("备份不存在")
    return apply_mosdns_config(read_text_limited(source))


def read_mosdns_file(name: str) -> dict[str, Any]:
    target = safe_mosdns_path(name)
    if not target.is_file():
        raise ValueError("文件不存在")
    stat = target.stat()
    return {"name": name, "content": read_text_limited(target), "size": stat.st_size, "mtime": int(stat.st_mtime)}


def save_mosdns_file(name: str, content: str) -> str:
    target = safe_mosdns_path(name)
    if len(content.encode("utf-8")) > MAX_TEXT_FILE or "\0" in content:
        raise ValueError("文件内容无效或超过 1 MB")
    target.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = MOSDNS_BACKUP_DIR / "files"; backup_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        shutil.copy2(target, backup_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_name}")
    mode = (target.stat().st_mode & 0o777) if target.exists() else 0o644
    atomic_text(target, content if content.endswith("\n") else content + "\n", mode)
    return name


class Handler(core.Handler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/assets/core.css":
            self.serve_file(core.WEB_DIR / "core.css", "text/css; charset=utf-8"); return
        if parsed.path == "/assets/core.js":
            self.serve_file(core.WEB_DIR / "core.js", "application/javascript; charset=utf-8"); return
        if parsed.path not in ("/api/mosdns", "/api/mosdns/file"):
            super().do_GET(); return
        if not self.require():
            return
        try:
            if parsed.path == "/api/mosdns":
                self.json(200, {"ok": True, **mosdns_state()})
            else:
                name = urllib.parse.parse_qs(parsed.query).get("name", [""])[0]
                self.json(200, {"ok": True, **read_mosdns_file(name)})
        except (ValueError, OSError, UnicodeError) as exc:
            self.json(400, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path not in ("/api/mosdns/save", "/api/mosdns/action", "/api/mosdns/file/save"):
            super().do_POST(); return
        try:
            data = self.body_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self.json(400, {"ok": False, "error": str(exc)}); return
        if not self.require(mutate=True):
            return
        try:
            if path == "/api/mosdns/save":
                ok, message = apply_mosdns_config(str(data.get("config", "")))
                self.json(200 if ok else 500, {"ok": ok, "message": message, "error": "" if ok else message}); return
            if path == "/api/mosdns/file/save":
                name = save_mosdns_file(str(data.get("name", "")), str(data.get("content", "")))
                self.json(200, {"ok": True, "message": f"已保存 {name}"}); return
            action = str(data.get("action", ""))
            if action == "restart":
                ok, output = core.service_restart("mosdns")
                self.json(200 if ok else 500, {"ok": ok, "message": output or ("mosdns 已重启" if ok else "重启失败")}); return
            if action == "backup":
                self.json(200, {"ok": True, "message": "已备份：" + backup_mosdns()}); return
            if action == "restore":
                ok, message = restore_mosdns(str(data.get("name", "")))
                self.json(200 if ok else 500, {"ok": ok, "message": message, "error": "" if ok else message}); return
            self.json(400, {"ok": False, "error": "操作无效"})
        except (ValueError, OSError, UnicodeError) as exc:
            self.json(400, {"ok": False, "error": str(exc)})


def self_test() -> None:
    core.self_test()
    assert mosdns_summary("plugins:\n  - tag: cache\n    type: cache\n  - tag: forward\n    type: forward\n    args:\n      upstreams:\n        - addr: 1.1.1.1\n")["plugins"] == 2
    assert "1.1.1.1" in mosdns_summary("- addr: 1.1.1.1\n")["upstreams"]
    try:
        safe_mosdns_path("../passwd")
        raise AssertionError("path traversal accepted")
    except ValueError:
        pass
    print(json.dumps({"ok": True, "version": VERSION}))


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8088); parser.add_argument("--init-auth", action="store_true"); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    core.ensure_dirs(); core.init_db()
    if args.init_auth:
        core.AUTH.initialize(os.environ.get("PANEL_ADMIN_USER", "admin"), os.environ.get("PANEL_ADMIN_PASSWORD", "")); print("auth initialized"); return
    if args.self_test:
        self_test(); return
    if not core.AUTH_FILE.exists():
        raise SystemExit("auth.json missing; run --init-auth first")
    core.SAMPLER.start(); server = core.http.server.ThreadingHTTPServer((args.host, args.port), Handler); server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        core.SAMPLER.stop(); server.server_close()


if __name__ == "__main__":
    main()
