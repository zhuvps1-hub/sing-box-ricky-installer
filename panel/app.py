#!/usr/bin/env python3
"""iWAN Gateway Web Panel.

A dependency-free management panel that samples an existing sing-box/iWAN and
mosdns installation. Panel upgrades never modify the core services. Core
configuration is changed only through an explicit authenticated save action.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import hmac
import http.cookies
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VERSION = "7.0.0"
APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
CONFIG_DIR = Path(os.environ.get("IWAN_PANEL_CONFIG_DIR", "/etc/iwan-gateway"))
DATA_DIR = Path(os.environ.get("IWAN_PANEL_DATA_DIR", "/var/lib/iwan-gateway"))
SINGBOX_CONFIG = Path(os.environ.get("SINGBOX_CONFIG", "/etc/sing-box/config.json"))
MOSDNS_CONFIG = Path(os.environ.get("MOSDNS_CONFIG", "/etc/mosdns/config.yaml"))
AUTH_FILE = CONFIG_DIR / "auth.json"
MANAGED_FILE = CONFIG_DIR / "managed.json"
BACKUP_DIR = Path(os.environ.get("SINGBOX_BACKUP_DIR", "/etc/sing-box/backups"))
DB_FILE = DATA_DIR / "panel.db"

SERVICE_ALLOWLIST = {"sing-box", "mosdns", "iwan-gateway"}
CATEGORY_DOMAINS = {
    "netflix": ["netflix.com", "netflix.net", "nflxext.com", "nflximg.com", "nflximg.net", "nflxso.net", "nflxvideo.net"],
    "ai": ["openai.com", "chatgpt.com", "oaistatic.com", "oaiusercontent.com", "anthropic.com", "claude.ai", "gemini.google.com", "githubcopilot.com", "copilot.microsoft.com"],
    "youtube": ["youtube.com", "youtu.be", "googlevideo.com", "ytimg.com", "youtube-nocookie.com"],
    "telegram": ["telegram.org", "telegram.me", "t.me", "telesco.pe"],
}
TELEGRAM_CIDRS = [
    "91.108.4.0/22", "91.108.8.0/22", "91.108.12.0/22", "91.108.16.0/22", "91.108.20.0/22", "91.108.56.0/22", "149.154.160.0/20",
    "2001:b28:f23d::/48", "2001:b28:f23f::/48", "2001:67c:4e8::/48", "2001:b28:f23c::/48",
]

DEFAULT_IWAN_INBOUND = {
    "type": "iwan",
    "tag": "iwan-in",
    "listen": "::",
    "listen_port": 8000,
    "address_pool": "10.10.10.0/24",
    "mtu": 1400,
}
ROUTE_CATEGORIES = ("netflix", "ai", "youtube", "telegram")


def ensure_dirs() -> None:
    for path in (CONFIG_DIR, DATA_DIR, BACKUP_DIR):
        path.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG_DIR.chmod(0o700)
        DATA_DIR.chmod(0o700)
    except PermissionError:
        pass


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return copy.deepcopy(default)


def atomic_json(path: Path, data: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def run(command: list[str], timeout: float = 8.0) -> tuple[int, str]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        output = (completed.stdout + completed.stderr).strip()
        return completed.returncode, output[-20000:]
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def service_active(name: str) -> bool:
    if name not in SERVICE_ALLOWLIST:
        return False
    return run(["systemctl", "is-active", "--quiet", name], timeout=3)[0] == 0


def service_restart(name: str) -> tuple[bool, str]:
    if name not in SERVICE_ALLOWLIST:
        return False, "不允许的服务"
    code, output = run(["systemctl", "restart", name], timeout=20)
    return code == 0, output


def pbkdf2_hash(password: str, salt: bytes | None = None, iterations: int = 240_000) -> dict[str, Any]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return {
        "algorithm": "pbkdf2-sha256",
        "iterations": iterations,
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(digest).decode(),
    }


def verify_password(password: str, record: dict[str, Any]) -> bool:
    try:
        salt = base64.b64decode(record["salt"])
        expected = base64.b64decode(record["hash"])
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(record.get("iterations", 240_000)))
        return hmac.compare_digest(actual, expected)
    except (KeyError, ValueError, TypeError):
        return False


class AuthManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.failures: dict[str, list[float]] = {}

    def initialize(self, username: str, password: str) -> None:
        ensure_dirs()
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", username):
            raise ValueError("用户名格式无效")
        if len(password) < 8:
            raise ValueError("密码至少 8 位")
        atomic_json(AUTH_FILE, {"username": username, "password": pbkdf2_hash(password)})

    def record(self) -> dict[str, Any]:
        return read_json(AUTH_FILE, {})

    def login(self, ip: str, username: str, password: str) -> tuple[bool, str, str]:
        now = time.time()
        with self.lock:
            attempts = [stamp for stamp in self.failures.get(ip, []) if now - stamp < 300]
            self.failures[ip] = attempts
            if len(attempts) >= 8:
                return False, "尝试过多，请稍后再试", ""
            record = self.record()
            ok = hmac.compare_digest(str(record.get("username", "")), username) and verify_password(password, record.get("password", {}))
            if not ok:
                attempts.append(now)
                self.failures[ip] = attempts
                return False, "用户名或密码错误", ""
            self.failures.pop(ip, None)
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(24)
            self.sessions[token] = {"csrf": csrf, "expires": now + 12 * 3600, "username": username}
            return True, token, csrf

    def session(self, token: str) -> dict[str, Any] | None:
        now = time.time()
        with self.lock:
            for key in list(self.sessions):
                if self.sessions[key]["expires"] < now:
                    self.sessions.pop(key, None)
            session = self.sessions.get(token)
            if session:
                session["expires"] = now + 12 * 3600
            return session

    def logout(self, token: str) -> None:
        with self.lock:
            self.sessions.pop(token, None)


AUTH = AuthManager()


@dataclass
class Snapshot:
    timestamp: float
    payload: dict[str, Any]


class Sampler:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.snapshot = Snapshot(0, {})
        self.last_cpu: tuple[int, int] | None = None
        self.last_net: tuple[float, int, int] | None = None
        self.history: list[dict[str, Any]] = []
        self.thread = threading.Thread(target=self._loop, name="sampler", daemon=True)

    def start(self) -> None:
        if not self.thread.is_alive():
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _cpu(self) -> float:
        try:
            values = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            total = sum(values)
            previous = self.last_cpu
            self.last_cpu = (total, idle)
            if not previous or total <= previous[0]:
                return 0.0
            return round(100 * (1 - (idle - previous[1]) / (total - previous[0])), 1)
        except Exception:
            return 0.0

    @staticmethod
    def _memory() -> float:
        try:
            fields: dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, value = line.split(":", 1)
                fields[key] = int(value.strip().split()[0])
            total = fields["MemTotal"]
            available = fields.get("MemAvailable", fields.get("MemFree", 0))
            return round(100 * (total - available) / total, 1)
        except Exception:
            return 0.0

    def _network(self) -> tuple[float, float, int]:
        rx = tx = 0
        try:
            for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
                name, data = line.split(":", 1)
                if name.strip() == "lo":
                    continue
                columns = data.split()
                rx += int(columns[0])
                tx += int(columns[8])
        except Exception:
            pass
        now = time.monotonic()
        previous = self.last_net
        self.last_net = (now, rx, tx)
        if not previous or now <= previous[0]:
            return 0.0, 0.0, rx + tx
        elapsed = now - previous[0]
        return max(0.0, (tx - previous[2]) / elapsed), max(0.0, (rx - previous[1]) / elapsed), rx + tx

    @staticmethod
    def _uptime() -> int:
        try:
            return int(float(Path("/proc/uptime").read_text().split()[0]))
        except Exception:
            return 0

    def _collect(self) -> dict[str, Any]:
        upload, download, total = self._network()
        try:
            load = [round(value, 2) for value in os.getloadavg()]
        except OSError:
            load = [0, 0, 0]
        payload = {
            "version": VERSION,
            "services": {name: service_active(name) for name in SERVICE_ALLOWLIST},
            "system": {
                "cpu": self._cpu(),
                "memory": self._memory(),
                "load": load,
                "uptime": self._uptime(),
                "upload_bps": upload,
                "download_bps": download,
                "total_bytes": total,
            },
            "config": sample_config(),
            "timestamp": int(time.time()),
        }
        self.history.append({"t": payload["timestamp"], "up": upload, "down": download})
        self.history = self.history[-240:]
        payload["history"] = list(self.history)
        return payload

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                payload = self._collect()
                with self.lock:
                    self.snapshot = Snapshot(time.time(), payload)
            except Exception:
                pass
            self.stop_event.wait(3.0)

    def get(self) -> dict[str, Any]:
        with self.lock:
            if not self.snapshot.payload:
                return self._collect()
            return copy.deepcopy(self.snapshot.payload)


SAMPLER = Sampler()


def _outbound_tag(rule: dict[str, Any]) -> str:
    action = rule.get("action")
    return str(rule.get("outbound") or (action.get("outbound") if isinstance(action, dict) else "") or "")


def classify_rule(rule: dict[str, Any]) -> str | None:
    blob = json.dumps(rule, ensure_ascii=False).lower()
    if "netflix" in blob or "nflx" in blob:
        return "netflix"
    if any(value in blob for value in ("openai", "chatgpt", "claude", "anthropic", "gemini", "copilot")):
        return "ai"
    if any(value in blob for value in ("youtube", "googlevideo", "ytimg", "youtu.be")):
        return "youtube"
    if any(value in blob for value in ("telegram", "t.me", "91.108.", "149.154.")):
        return "telegram"
    return None


def is_iwan_inbound(item: Any) -> bool:
    return isinstance(item, dict) and ("iwan" in str(item.get("type", "")).lower() or "iwan" in str(item.get("tag", "")).lower())


def public_iwan(item: dict[str, Any]) -> dict[str, Any]:
    """Return editable iWAN fields without exposing any password."""
    result: dict[str, Any] = {}
    for key in ("type", "tag", "listen", "listen_port", "address", "address_pool", "mtu"):
        if key in item:
            result[key] = copy.deepcopy(item[key])

    username = ""
    has_password = False
    users = item.get("users")
    if isinstance(users, list) and users:
        first = users[0]
        if isinstance(first, dict):
            username = str(first.get("username") or first.get("name") or first.get("user") or "")
            has_password = bool(first.get("password") or first.get("pass"))
        result["users_count"] = len(users)
        result["credential_mode"] = "users"
    else:
        username = str(item.get("username") or item.get("user") or "")
        has_password = bool(item.get("password") or item.get("pass"))
        result["credential_mode"] = "flat"
    result["username"] = username
    result["has_password"] = has_password
    return result


def sample_config() -> dict[str, Any]:
    raw = read_json(SINGBOX_CONFIG, {})
    inbounds = raw.get("inbounds", []) if isinstance(raw, dict) else []
    outbounds = raw.get("outbounds", []) if isinstance(raw, dict) else []
    route = raw.get("route", {}) if isinstance(raw, dict) else {}

    iwan: dict[str, Any] = {}
    for item in inbounds if isinstance(inbounds, list) else []:
        if is_iwan_inbound(item):
            iwan = public_iwan(item)
            break

    nodes = []
    for item in outbounds if isinstance(outbounds, list) else []:
        if isinstance(item, dict) and item.get("type") == "shadowsocks":
            nodes.append({
                "tag": item.get("tag", ""),
                "server": item.get("server", ""),
                "server_port": item.get("server_port", 0),
                "method": item.get("method", ""),
                "password": "",
                "has_password": bool(item.get("password")),
                "plugin": item.get("plugin", ""),
                "plugin_opts": item.get("plugin_opts", ""),
            })

    mappings = {"netflix": "", "ai": "", "youtube": "", "telegram": ""}
    rules = route.get("rules", []) if isinstance(route, dict) else []
    for rule in rules if isinstance(rules, list) else []:
        if isinstance(rule, dict):
            category = classify_rule(rule)
            if category and not mappings[category]:
                mappings[category] = _outbound_tag(rule)
    default = str(route.get("final", "") if isinstance(route, dict) else "")
    return {
        "available": bool(raw),
        "path": str(SINGBOX_CONFIG),
        "mtime": int(SINGBOX_CONFIG.stat().st_mtime) if SINGBOX_CONFIG.exists() else 0,
        "iwan": iwan,
        "nodes": nodes,
        "mappings": mappings,
        "default": default,
        "mosdns_available": MOSDNS_CONFIG.exists(),
    }


def validate_node(node: dict[str, Any]) -> dict[str, Any]:
    tag = str(node.get("tag", "")).strip()
    server = str(node.get("server", "")).strip()
    method = str(node.get("method", "")).strip()
    password = str(node.get("password", ""))
    try:
        port = int(node.get("server_port"))
    except (TypeError, ValueError):
        raise ValueError(f"节点 {tag or '?'} 端口无效")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", tag):
        raise ValueError("节点标签仅允许字母、数字、点、下划线和短横线")
    if not server or len(server) > 255 or any(char.isspace() for char in server):
        raise ValueError(f"节点 {tag} 服务器地址无效")
    if not 1 <= port <= 65535:
        raise ValueError(f"节点 {tag} 端口无效")
    if not method:
        raise ValueError(f"节点 {tag} 缺少加密方式")
    result: dict[str, Any] = {
        "type": "shadowsocks",
        "tag": tag,
        "server": server,
        "server_port": port,
        "method": method,
    }
    if password:
        result["password"] = password
    if node.get("plugin"):
        result["plugin"] = str(node["plugin"])
    if node.get("plugin_opts"):
        result["plugin_opts"] = str(node["plugin_opts"])
    return result


def managed_rules(mappings: dict[str, str]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for category in ROUTE_CATEGORIES:
        outbound = str(mappings.get(category, "")).strip()
        if not outbound:
            continue
        rule: dict[str, Any] = {"domain_suffix": CATEGORY_DOMAINS[category], "outbound": outbound}
        if category == "telegram":
            rule["ip_cidr"] = TELEGRAM_CIDRS
        rules.append(rule)
    return rules


def normalize_payload(payload: dict[str, Any], current_nodes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Validate a browser payload and return normalized nodes/routes/iWAN data."""
    nodes = []
    seen: set[str] = set()
    for raw_node in payload.get("nodes", []):
        node = validate_node(raw_node)
        if node["tag"] in seen:
            raise ValueError(f"节点标签重复：{node['tag']}")
        seen.add(node["tag"])
        old = current_nodes.get(node["tag"], {})
        if "password" not in node:
            if old.get("password"):
                node["password"] = old["password"]
            else:
                raise ValueError(f"节点 {node['tag']} 需要密码")
        nodes.append(node)

    deleted = {str(value) for value in payload.get("deleted_tags", [])}
    available_tags = {tag for tag in current_nodes if tag not in deleted} | seen | {"direct"}
    mappings = {category: str(payload.get("mappings", {}).get(category, "")).strip() for category in ROUTE_CATEGORIES}
    default = str(payload.get("default", "")).strip()
    requested = {value for value in mappings.values() if value} | ({default} if default else set())
    missing = sorted(requested - available_tags)
    if missing:
        raise ValueError("分流出口不存在：" + ", ".join(missing))
    conflict = sorted(deleted & requested)
    if conflict:
        raise ValueError("以下节点仍被分流使用：" + ", ".join(conflict))
    return {"nodes": nodes, "deleted": deleted, "mappings": mappings, "default": default, "iwan": payload.get("iwan") if isinstance(payload.get("iwan"), dict) else {}}


def upsert_iwan_inbound(config: dict[str, Any], iwan_patch: dict[str, Any]) -> None:
    """Patch existing iWAN inbound or create a safe public-version default."""
    inbounds = config.setdefault("inbounds", [])
    if not isinstance(inbounds, list):
        raise ValueError("inbounds 格式无效")
    target = next((item for item in inbounds if is_iwan_inbound(item)), None)
    if target is None:
        target = copy.deepcopy(DEFAULT_IWAN_INBOUND)
        inbounds.insert(0, target)
    for key in ("listen", "listen_port", "address", "address_pool", "mtu"):
        if key in iwan_patch and iwan_patch[key] not in (None, ""):
            target[key] = iwan_patch[key]
    patch_iwan_credentials(target, str(iwan_patch.get("username", "")), str(iwan_patch.get("password", "")))


def patch_iwan_credentials(item: dict[str, Any], username: str, password: str) -> None:
    username = username.strip()
    if not username and not password:
        return

    users = item.get("users")
    if isinstance(users, list):
        updated = copy.deepcopy(users)
        if not updated:
            updated.append({})
        if not isinstance(updated[0], dict):
            raise ValueError("当前 iWAN users 格式无法由面板安全修改")
        account = updated[0]
        username_key = "username" if "username" in account or not any(key in account for key in ("name", "user")) else ("name" if "name" in account else "user")
        password_key = "password" if "password" in account or "pass" not in account else "pass"
        if username:
            account[username_key] = username
        if password:
            account[password_key] = password
        elif not account.get(password_key):
            raise ValueError("当前 iWAN 用户没有密码，请输入新密码")
        updated[0] = account
        item["users"] = updated
        return

    flat_mode = any(key in item for key in ("username", "user", "password", "pass"))
    if flat_mode:
        username_key = "username" if "username" in item or "user" not in item else "user"
        password_key = "password" if "password" in item or "pass" not in item else "pass"
        if username:
            item[username_key] = username
        if password:
            item[password_key] = password
        elif not item.get(password_key):
            raise ValueError("当前 iWAN 用户没有密码，请输入新密码")
        return

    if not username or not password:
        raise ValueError("首次设置 iWAN 登录信息时，用户名和密码都必须填写")
    item["users"] = [{"username": username, "password": password}]


def apply_config(payload: dict[str, Any]) -> tuple[bool, str]:
    current = read_json(SINGBOX_CONFIG, {})
    if not isinstance(current, dict) or not current:
        return False, "未找到有效 sing-box 配置"
    new_config = copy.deepcopy(current)
    outbounds = new_config.setdefault("outbounds", [])
    if not isinstance(outbounds, list):
        return False, "outbounds 格式无效"

    existing_by_tag = {
        str(item.get("tag")): item
        for item in outbounds
        if isinstance(item, dict) and item.get("type") == "shadowsocks"
    }
    plan = normalize_payload(payload, existing_by_tag)
    for node in plan["nodes"]:
        old = existing_by_tag.get(node["tag"], {})
        if old:
            index = outbounds.index(old)
            merged = copy.deepcopy(old)
            merged.update(node)
            outbounds[index] = merged
        else:
            outbounds.append(node)

    deleted = plan["deleted"]
    if deleted:
        new_config["outbounds"] = [
            item for item in outbounds
            if not (isinstance(item, dict) and item.get("type") == "shadowsocks" and str(item.get("tag")) in deleted)
        ]

    if plan["iwan"]:
        upsert_iwan_inbound(new_config, plan["iwan"])

    old_managed = read_json(MANAGED_FILE, {"rules": []}).get("rules", [])
    route = new_config.setdefault("route", {})
    rules = route.setdefault("rules", [])
    if not isinstance(rules, list):
        return False, "route.rules 格式无效"
    rules = [rule for rule in rules if rule not in old_managed]
    new_rules = managed_rules(plan["mappings"])
    route["rules"] = new_rules + rules
    if plan["default"]:
        route["final"] = plan["default"]

    ensure_dirs()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_DIR / f"config-{stamp}.json"
    if SINGBOX_CONFIG.exists():
        shutil.copy2(SINGBOX_CONFIG, backup)
    fd, tmp_name = tempfile.mkstemp(prefix="config.", suffix=".json", dir=str(SINGBOX_CONFIG.parent))
    os.close(fd)
    temporary = Path(tmp_name)
    try:
        temporary.write_text(json.dumps(new_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        code, output = run(["/usr/local/bin/sing-box", "check", "-c", str(temporary)], timeout=20)
        if code != 0:
            return False, "配置校验失败：\n" + output
        os.chmod(temporary, 0o600)
        os.replace(temporary, SINGBOX_CONFIG)
        ok, output = service_restart("sing-box")
        if not ok or not service_active("sing-box"):
            shutil.copy2(backup, SINGBOX_CONFIG)
            service_restart("sing-box")
            return False, "sing-box 启动失败，已恢复旧配置：\n" + output
        atomic_json(MANAGED_FILE, {"rules": new_rules, "updated_at": int(time.time())})
        return True, f"已保存并自动重连，备份：{backup.name}"
    finally:
        temporary.unlink(missing_ok=True)


def parse_ss_link(link: str) -> dict[str, Any]:
    raw = link.strip()
    if not raw.startswith("ss://"):
        raise ValueError("不是 ss:// 链接")
    parsed = urllib.parse.urlsplit(raw)
    tag = urllib.parse.unquote(parsed.fragment) or "ss-node"
    plugin = ""
    plugin_opts = ""
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("plugin"):
        parts = urllib.parse.unquote(query["plugin"][0]).split(";", 1)
        plugin = parts[0]
        plugin_opts = parts[1] if len(parts) > 1 else ""
    if parsed.username and parsed.hostname:
        server = parsed.hostname
        port = parsed.port
        if parsed.password is not None:
            method = urllib.parse.unquote(parsed.username)
            password = urllib.parse.unquote(parsed.password)
        else:
            userinfo = urllib.parse.unquote(parsed.username)
            padded = userinfo + "=" * (-len(userinfo) % 4)
            decoded_userinfo = base64.urlsafe_b64decode(padded).decode()
            method, password = decoded_userinfo.split(":", 1)
    else:
        encoded = raw[5:].split("#", 1)[0].split("?", 1)[0]
        encoded += "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(encoded).decode()
        credentials, endpoint = decoded.rsplit("@", 1)
        method, password = credentials.split(":", 1)
        server, port_text = endpoint.rsplit(":", 1)
        port = int(port_text)
    return validate_node({
        "tag": re.sub(r"[^A-Za-z0-9_.-]+", "-", tag)[:64],
        "server": server,
        "server_port": port,
        "method": method,
        "password": password,
        "plugin": plugin,
        "plugin_opts": plugin_opts,
    })


def import_json_nodes(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("outbounds"), list):
            items = value["outbounds"]
        elif isinstance(value.get("nodes"), list):
            items = value["nodes"]
        else:
            items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError("JSON 必须是节点对象、节点数组或含 outbounds 的配置")

    result = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 项不是对象")
        if item.get("type") not in (None, "", "shadowsocks"):
            continue
        normalized = {
            "tag": item.get("tag") or item.get("name") or f"ss-node-{index}",
            "server": item.get("server") or item.get("address"),
            "server_port": item.get("server_port") or item.get("port"),
            "method": item.get("method") or item.get("cipher"),
            "password": item.get("password") or "",
            "plugin": item.get("plugin") or "",
            "plugin_opts": item.get("plugin_opts") or item.get("plugin_options") or "",
        }
        result.append(validate_node(normalized))
    if not result:
        raise ValueError("没有找到 Shadowsocks 节点")
    return result


def parse_import_text(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    raw = text.strip()
    if not raw:
        raise ValueError("请粘贴节点内容")
    if raw[0] in "[{":
        try:
            return import_json_nodes(json.loads(raw)), []
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 格式错误：{exc.msg}") from exc

    nodes: list[dict[str, Any]] = []
    errors: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            nodes.append(parse_ss_link(line))
        except Exception as exc:
            errors.append(f"{line[:48]}：{exc}")
    if not nodes and errors:
        raise ValueError(errors[0])
    return nodes, errors


def node_latency(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    lock = threading.Lock()
    semaphore = threading.Semaphore(6)

    def worker(node: dict[str, Any]) -> None:
        with semaphore:
            start = time.perf_counter()
            ok = False
            error = ""
            try:
                with socket.create_connection((str(node.get("server")), int(node.get("server_port"))), timeout=2.5):
                    ok = True
            except Exception as exc:
                error = str(exc)
            latency = round((time.perf_counter() - start) * 1000) if ok else None
            with lock:
                results.append({"tag": node.get("tag"), "ok": ok, "latency_ms": latency, "error": error})

    threads = [threading.Thread(target=worker, args=(node,), daemon=True) for node in nodes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(4)
    return results


def diagnostics() -> dict[str, Any]:
    config = sample_config()
    checks = [
        {"name": "sing-box 配置文件", "ok": SINGBOX_CONFIG.exists(), "detail": str(SINGBOX_CONFIG)},
        {"name": "iWAN inbound", "ok": bool(config.get("iwan")), "detail": "未配置时可在 iWAN 页面首次保存自动创建"},
        {"name": "落地节点", "ok": bool(config.get("nodes")), "detail": f"{len(config.get('nodes', []))} 个 Shadowsocks 节点"},
        {"name": "默认出口", "ok": bool(config.get("default")), "detail": config.get("default") or "未设置"},
        {"name": "mosdns 配置", "ok": MOSDNS_CONFIG.exists(), "detail": str(MOSDNS_CONFIG)},
    ]
    for service in ("sing-box", "mosdns", "iwan-gateway"):
        checks.append({"name": f"{service} 服务", "ok": service_active(service), "detail": "systemd active"})
    score = round(100 * sum(1 for item in checks if item["ok"]) / max(1, len(checks)))
    next_steps = []
    if not config.get("iwan"):
        next_steps.append("打开 iWAN 页面填写账号和密码，然后保存并应用。")
    if not config.get("nodes"):
        next_steps.append("打开节点页面导入自己的 ss:// 或 sing-box outbounds。")
    if not config.get("default"):
        next_steps.append("在分流页面选择“其他流量”的默认出口。")
    if not next_steps:
        next_steps.append("配置完整，可按需测速节点或查看日志排障。")
    return {"score": score, "checks": checks, "next_steps": next_steps}


def cookie_token(headers: Any) -> str:
    cookie = http.cookies.SimpleCookie()
    try:
        cookie.load(headers.get("Cookie", ""))
        return cookie.get("iwan_session").value if cookie.get("iwan_session") else ""
    except Exception:
        return ""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "iWANGateway/6"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_bytes(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8", headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def json(self, status: int, data: Any, headers: dict[str, str] | None = None) -> None:
        self.send_bytes(status, json.dumps(data, ensure_ascii=False).encode(), headers=headers)

    def body_json(self, limit: int = 1_000_000) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Content-Length 无效")
        if length < 0 or length > limit:
            raise ValueError("请求过大")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON 必须是对象")
        return data

    def session(self) -> tuple[str, dict[str, Any] | None]:
        token = cookie_token(self.headers)
        return token, AUTH.session(token)

    def require(self, mutate: bool = False) -> tuple[str, dict[str, Any]] | None:
        token, session = self.session()
        if not session:
            self.json(401, {"ok": False, "error": "未登录"})
            return None
        if mutate and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), str(session["csrf"])):
            self.json(403, {"ok": False, "error": "CSRF 校验失败"})
            return None
        return token, session

    def serve_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self.json(404, {"ok": False, "error": "not found"})
            return
        self.send_bytes(200, body, content_type)

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/healthz":
            self.json(200, {"ok": True, "version": VERSION})
            return
        if path == "/":
            self.serve_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return
        if path == "/assets/app.css":
            self.serve_file(WEB_DIR / "app.css", "text/css; charset=utf-8")
            return
        if path == "/assets/app.js":
            self.serve_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
            return
        if path == "/api/session":
            _, session = self.session()
            self.json(200, {"authenticated": bool(session), "csrf": session.get("csrf") if session else "", "version": VERSION})
            return

        auth = self.require()
        if not auth:
            return
        if path == "/api/dashboard":
            self.json(200, {"ok": True, **SAMPLER.get()})
            return
        if path == "/api/config":
            self.json(200, {"ok": True, **sample_config()})
            return
        if path == "/api/diagnostics":
            self.json(200, {"ok": True, **diagnostics()})
            return
        if path == "/api/logs":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            service = query.get("service", ["sing-box"])[0]
            if service not in SERVICE_ALLOWLIST:
                self.json(400, {"ok": False, "error": "服务无效"})
                return
            code, output = run(["journalctl", "-u", service, "-n", "200", "--no-pager", "--output=short-iso"], timeout=8)
            self.json(200, {"ok": code == 0, "logs": output})
            return
        if path == "/api/network":
            _, routes = run(["ip", "route", "show"], 5)
            _, ports = run(["ss", "-lntup"], 5)
            self.json(200, {"ok": True, "routes": routes, "ports": ports})
            return
        self.json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        try:
            data = self.body_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self.json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/login":
            ok, first, second = AUTH.login(self.client_address[0], str(data.get("username", "")), str(data.get("password", "")))
            if not ok:
                self.json(401, {"ok": False, "error": first})
                return
            self.json(200, {"ok": True, "csrf": second}, {"Set-Cookie": f"iwan_session={first}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"})
            return

        auth = self.require(mutate=True)
        if not auth:
            return
        token, _ = auth

        if path == "/api/logout":
            AUTH.logout(token)
            self.json(200, {"ok": True}, {"Set-Cookie": "iwan_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"})
            return
        if path == "/api/action":
            action = str(data.get("action", ""))
            service = str(data.get("service", ""))
            if action == "restart" and service in SERVICE_ALLOWLIST:
                ok, output = service_restart(service)
                self.json(200 if ok else 500, {"ok": ok, "message": output or ("已重启" if ok else "失败")})
                return
            if action == "backup":
                ensure_dirs()
                name = f"manual-{time.strftime('%Y%m%d-%H%M%S')}.json"
                target = BACKUP_DIR / name
                if not SINGBOX_CONFIG.exists():
                    self.json(400, {"ok": False, "error": "配置不存在"})
                    return
                shutil.copy2(SINGBOX_CONFIG, target)
                self.json(200, {"ok": True, "message": name})
                return
            self.json(400, {"ok": False, "error": "操作无效"})
            return
        if path in ("/api/import-ss", "/api/import-nodes"):
            try:
                nodes, errors = parse_import_text(str(data.get("text", "")))
            except ValueError as exc:
                self.json(400, {"ok": False, "error": str(exc)})
                return
            self.json(200, {"ok": True, "nodes": nodes, "errors": errors})
            return
        if path == "/api/latency":
            nodes = data.get("nodes", [])
            if not isinstance(nodes, list) or len(nodes) > 100:
                self.json(400, {"ok": False, "error": "节点数量无效"})
                return
            self.json(200, {"ok": True, "results": node_latency(nodes)})
            return
        if path == "/api/save":
            try:
                ok, message = apply_config(data)
            except (ValueError, OSError) as exc:
                self.json(400, {"ok": False, "error": str(exc)})
                return
            self.json(200 if ok else 500, {"ok": ok, "message": message, "error": "" if ok else message})
            return
        self.json(404, {"ok": False, "error": "not found"})


def init_db() -> None:
    ensure_dirs()
    connection = sqlite3.connect(DB_FILE)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('version',?)", (VERSION,))
        connection.commit()
    finally:
        connection.close()


def self_test() -> None:
    ensure_dirs()
    init_db()
    assert parse_ss_link("ss://YWVzLTEyOC1nY206cGFzcw@example.com:8388#test")["tag"] == "test"
    assert classify_rule({"domain_suffix": ["netflix.com"], "outbound": "sg"}) == "netflix"
    nodes, errors = parse_import_text('[{"type":"shadowsocks","tag":"sg","server":"example.com","server_port":8388,"method":"aes-128-gcm","password":"p"}]')
    assert len(nodes) == 1 and not errors
    sample = {"users": [{"username": "old", "password": "secret"}]}
    patch_iwan_credentials(sample, "new", "")
    assert sample["users"][0]["username"] == "new" and sample["users"][0]["password"] == "secret"
    print(json.dumps({"ok": True, "version": VERSION}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--init-auth", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    ensure_dirs()
    init_db()
    if args.init_auth:
        AUTH.initialize(os.environ.get("PANEL_ADMIN_USER", "admin"), os.environ.get("PANEL_ADMIN_PASSWORD", ""))
        print("auth initialized")
        return
    if args.self_test:
        self_test()
        return
    if not AUTH_FILE.exists():
        raise SystemExit("auth.json missing; run --init-auth first")
    SAMPLER.start()
    server = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SAMPLER.stop()
        server.server_close()


if __name__ == "__main__":
    main()
