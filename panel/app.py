#!/usr/bin/env python3
"""iWAN Gateway Web Panel v6.

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

VERSION = "6.0.0"
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


def ensure_dirs() -> None:
    for p in (CONFIG_DIR, DATA_DIR, BACKUP_DIR):
        p.mkdir(parents=True, exist_ok=True)
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
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str]:
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        out = (cp.stdout + cp.stderr).strip()
        return cp.returncode, out[-20000:]
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def service_active(name: str) -> bool:
    if name not in SERVICE_ALLOWLIST:
        return False
    return run(["systemctl", "is-active", "--quiet", name], timeout=3)[0] == 0


def service_restart(name: str) -> tuple[bool, str]:
    if name not in SERVICE_ALLOWLIST:
        return False, "不允许的服务"
    code, out = run(["systemctl", "restart", name], timeout=20)
    return code == 0, out


def pbkdf2_hash(password: str, salt: bytes | None = None, iterations: int = 240_000) -> dict[str, Any]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return {"algorithm": "pbkdf2-sha256", "iterations": iterations, "salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest).decode()}


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
            attempts = [t for t in self.failures.get(ip, []) if now - t < 300]
            self.failures[ip] = attempts
            if len(attempts) >= 8:
                return False, "尝试过多，请稍后再试", ""
            rec = self.record()
            ok = hmac.compare_digest(str(rec.get("username", "")), username) and verify_password(password, rec.get("password", {}))
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
            sess = self.sessions.get(token)
            if sess:
                sess["expires"] = now + 12 * 3600
            return sess

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
            values = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            total = sum(values)
            prev = self.last_cpu
            self.last_cpu = (total, idle)
            if not prev or total <= prev[0]:
                return 0.0
            return round(100 * (1 - (idle - prev[1]) / (total - prev[0])), 1)
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
                cols = data.split()
                rx += int(cols[0]); tx += int(cols[8])
        except Exception:
            pass
        now = time.monotonic()
        prev = self.last_net
        self.last_net = (now, rx, tx)
        if not prev or now <= prev[0]:
            return 0.0, 0.0, rx + tx
        dt = now - prev[0]
        return max(0.0, (tx - prev[2]) / dt), max(0.0, (rx - prev[1]) / dt), rx + tx

    @staticmethod
    def _uptime() -> int:
        try:
            return int(float(Path("/proc/uptime").read_text().split()[0]))
        except Exception:
            return 0

    def _collect(self) -> dict[str, Any]:
        up, down, total = self._network()
        try:
            load = [round(x, 2) for x in os.getloadavg()]
        except OSError:
            load = [0, 0, 0]
        payload = {
            "version": VERSION,
            "services": {name: service_active(name) for name in SERVICE_ALLOWLIST},
            "system": {"cpu": self._cpu(), "memory": self._memory(), "load": load, "uptime": self._uptime(), "upload_bps": up, "download_bps": down, "total_bytes": total},
            "config": sample_config(),
            "timestamp": int(time.time()),
        }
        self.history.append({"t": payload["timestamp"], "up": up, "down": down})
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
    if any(x in blob for x in ("openai", "chatgpt", "claude", "anthropic", "gemini", "copilot")):
        return "ai"
    if any(x in blob for x in ("youtube", "googlevideo", "ytimg", "youtu.be")):
        return "youtube"
    if any(x in blob for x in ("telegram", "t.me", "91.108.", "149.154.")):
        return "telegram"
    return None


def sample_config() -> dict[str, Any]:
    raw = read_json(SINGBOX_CONFIG, {})
    inbounds = raw.get("inbounds", []) if isinstance(raw, dict) else []
    outbounds = raw.get("outbounds", []) if isinstance(raw, dict) else []
    route = raw.get("route", {}) if isinstance(raw, dict) else {}
    iwan: dict[str, Any] = {}
    for item in inbounds if isinstance(inbounds, list) else []:
        if isinstance(item, dict) and ("iwan" in str(item.get("type", "")).lower() or "iwan" in str(item.get("tag", "")).lower()):
            iwan = copy.deepcopy(item)
            break
    nodes = []
    for item in outbounds if isinstance(outbounds, list) else []:
        if isinstance(item, dict) and item.get("type") == "shadowsocks":
            nodes.append({"tag": item.get("tag", ""), "server": item.get("server", ""), "server_port": item.get("server_port", 0), "method": item.get("method", ""), "password": "", "has_password": bool(item.get("password")), "plugin": item.get("plugin", ""), "plugin_opts": item.get("plugin_opts", "")})
    mappings = {"netflix": "", "ai": "", "youtube": "", "telegram": ""}
    rules = route.get("rules", []) if isinstance(route, dict) else []
    for rule in rules if isinstance(rules, list) else []:
        if isinstance(rule, dict):
            cat = classify_rule(rule)
            if cat and not mappings[cat]:
                mappings[cat] = _outbound_tag(rule)
    default = str(route.get("final", "") if isinstance(route, dict) else "")
    return {"available": bool(raw), "path": str(SINGBOX_CONFIG), "mtime": int(SINGBOX_CONFIG.stat().st_mtime) if SINGBOX_CONFIG.exists() else 0, "iwan": iwan, "nodes": nodes, "mappings": mappings, "default": default, "mosdns_available": MOSDNS_CONFIG.exists()}


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
    if not server or len(server) > 255 or any(c.isspace() for c in server):
        raise ValueError(f"节点 {tag} 服务器地址无效")
    if not 1 <= port <= 65535:
        raise ValueError(f"节点 {tag} 端口无效")
    if not method:
        raise ValueError(f"节点 {tag} 缺少加密方式")
    result: dict[str, Any] = {"type": "shadowsocks", "tag": tag, "server": server, "server_port": port, "method": method}
    if password:
        result["password"] = password
    if node.get("plugin"):
        result["plugin"] = str(node["plugin"])
    if node.get("plugin_opts"):
        result["plugin_opts"] = str(node["plugin_opts"])
    return result


def managed_rules(mappings: dict[str, str]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for cat in ("netflix", "ai", "youtube", "telegram"):
        outbound = str(mappings.get(cat, "")).strip()
        if not outbound:
            continue
        rule: dict[str, Any] = {"domain_suffix": CATEGORY_DOMAINS[cat], "outbound": outbound}
        if cat == "telegram":
            rule["ip_cidr"] = TELEGRAM_CIDRS
        rules.append(rule)
    return rules


def apply_config(payload: dict[str, Any]) -> tuple[bool, str]:
    current = read_json(SINGBOX_CONFIG, {})
    if not isinstance(current, dict) or not current:
        return False, "未找到有效 sing-box 配置"
    new_config = copy.deepcopy(current)
    outbounds = new_config.setdefault("outbounds", [])
    if not isinstance(outbounds, list):
        return False, "outbounds 格式无效"
    existing_by_tag = {str(x.get("tag")): x for x in outbounds if isinstance(x, dict) and x.get("type") == "shadowsocks"}
    for raw_node in payload.get("nodes", []):
        node = validate_node(raw_node)
        old = existing_by_tag.get(node["tag"], {})
        if "password" not in node:
            if old.get("password"):
                node["password"] = old["password"]
            else:
                raise ValueError(f"节点 {node['tag']} 需要密码")
        if old:
            idx = outbounds.index(old)
            merged = copy.deepcopy(old); merged.update(node); outbounds[idx] = merged
        else:
            outbounds.append(node)
    deleted = {str(x) for x in payload.get("deleted_tags", [])}
    if deleted:
        in_use = {str(v) for v in payload.get("mappings", {}).values()} | {str(payload.get("default", ""))}
        conflict = sorted(deleted & in_use)
        if conflict:
            raise ValueError("以下节点仍被分流使用：" + ", ".join(conflict))
        new_config["outbounds"] = [x for x in outbounds if not (isinstance(x, dict) and x.get("type") == "shadowsocks" and str(x.get("tag")) in deleted)]
    iwan_patch = payload.get("iwan")
    if isinstance(iwan_patch, dict) and iwan_patch:
        for item in new_config.get("inbounds", []):
            if isinstance(item, dict) and ("iwan" in str(item.get("type", "")).lower() or "iwan" in str(item.get("tag", "")).lower()):
                for key in {"listen", "listen_port", "address", "address_pool", "mtu", "users"}:
                    if key in iwan_patch:
                        item[key] = iwan_patch[key]
                break
    old_managed = read_json(MANAGED_FILE, {"rules": []}).get("rules", [])
    route = new_config.setdefault("route", {})
    rules = route.setdefault("rules", [])
    if not isinstance(rules, list):
        return False, "route.rules 格式无效"
    rules = [r for r in rules if r not in old_managed]
    new_rules = managed_rules(payload.get("mappings", {}))
    route["rules"] = new_rules + rules
    if payload.get("default"):
        route["final"] = str(payload["default"])
    ensure_dirs()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_DIR / f"config-{stamp}.json"
    if SINGBOX_CONFIG.exists():
        shutil.copy2(SINGBOX_CONFIG, backup)
    fd, tmp_name = tempfile.mkstemp(prefix="config.", suffix=".json", dir=str(SINGBOX_CONFIG.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(json.dumps(new_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        code, output = run(["/usr/local/bin/sing-box", "check", "-c", str(tmp)], timeout=20)
        if code != 0:
            return False, "配置校验失败：\n" + output
        os.chmod(tmp, 0o600)
        os.replace(tmp, SINGBOX_CONFIG)
        ok, output = service_restart("sing-box")
        if not ok or not service_active("sing-box"):
            shutil.copy2(backup, SINGBOX_CONFIG)
            service_restart("sing-box")
            return False, "sing-box 启动失败，已恢复旧配置：\n" + output
        atomic_json(MANAGED_FILE, {"rules": new_rules, "updated_at": int(time.time())})
        return True, f"已应用，备份：{backup.name}"
    finally:
        tmp.unlink(missing_ok=True)


def parse_ss_link(link: str) -> dict[str, Any]:
    raw = link.strip()
    if not raw.startswith("ss://"):
        raise ValueError("不是 ss:// 链接")
    parsed = urllib.parse.urlsplit(raw)
    tag = urllib.parse.unquote(parsed.fragment) or "ss-node"
    plugin = ""; plugin_opts = ""
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("plugin"):
        parts = urllib.parse.unquote(query["plugin"][0]).split(";", 1)
        plugin = parts[0]; plugin_opts = parts[1] if len(parts) > 1 else ""
    if parsed.username and parsed.hostname:
        method = urllib.parse.unquote(parsed.username)
        password = urllib.parse.unquote(parsed.password or "")
        server = parsed.hostname; port = parsed.port
    else:
        encoded = raw[5:].split("#", 1)[0].split("?", 1)[0]
        encoded += "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(encoded).decode()
        creds, endpoint = decoded.rsplit("@", 1)
        method, password = creds.split(":", 1)
        server, port_s = endpoint.rsplit(":", 1)
        port = int(port_s)
    return validate_node({"tag": re.sub(r"[^A-Za-z0-9_.-]+", "-", tag)[:64], "server": server, "server_port": port, "method": method, "password": password, "plugin": plugin, "plugin_opts": plugin_opts})


def node_latency(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    lock = threading.Lock()
    sem = threading.Semaphore(6)
    def worker(node: dict[str, Any]) -> None:
        with sem:
            start = time.perf_counter(); ok = False; error = ""
            try:
                with socket.create_connection((str(node.get("server")), int(node.get("server_port"))), timeout=2.5):
                    ok = True
            except Exception as exc:
                error = str(exc)
            ms = round((time.perf_counter() - start) * 1000) if ok else None
            with lock:
                results.append({"tag": node.get("tag"), "ok": ok, "latency_ms": ms, "error": error})
    threads = [threading.Thread(target=worker, args=(n,), daemon=True) for n in nodes]
    for t in threads: t.start()
    for t in threads: t.join(4)
    return results


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
        self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY"); self.send_header("Referrer-Policy", "no-referrer"); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'")
        if headers:
            for k, v in headers.items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)
    def json(self, status: int, data: Any, headers: dict[str, str] | None = None) -> None:
        self.send_bytes(status, json.dumps(data, ensure_ascii=False).encode(), headers=headers)
    def body_json(self, limit: int = 1_000_000) -> dict[str, Any]:
        try: length = int(self.headers.get("Content-Length", "0"))
        except ValueError: raise ValueError("Content-Length 无效")
        if length < 0 or length > limit: raise ValueError("请求过大")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict): raise ValueError("JSON 必须是对象")
        return data
    def session(self) -> tuple[str, dict[str, Any] | None]:
        token = cookie_token(self.headers); return token, AUTH.session(token)
    def require(self, mutate: bool = False) -> tuple[str, dict[str, Any]] | None:
        token, sess = self.session()
        if not sess:
            self.json(401, {"ok": False, "error": "未登录"}); return None
        if mutate and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), str(sess["csrf"])):
            self.json(403, {"ok": False, "error": "CSRF 校验失败"}); return None
        return token, sess
    def serve_file(self, path: Path, content_type: str) -> None:
        try: body = path.read_bytes()
        except OSError:
            self.json(404, {"ok": False, "error": "not found"}); return
        self.send_bytes(200, body, content_type)
    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/healthz": self.json(200, {"ok": True, "version": VERSION}); return
        if path == "/": self.serve_file(WEB_DIR / "index.html", "text/html; charset=utf-8"); return
        if path == "/assets/app.css": self.serve_file(WEB_DIR / "app.css", "text/css; charset=utf-8"); return
        if path == "/assets/app.js": self.serve_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8"); return
        if path == "/api/session":
            _, sess = self.session(); self.json(200, {"authenticated": bool(sess), "csrf": sess.get("csrf") if sess else "", "version": VERSION}); return
        auth = self.require()
        if not auth: return
        if path == "/api/dashboard": self.json(200, {"ok": True, **SAMPLER.get()}); return
        if path == "/api/config": self.json(200, {"ok": True, **sample_config()}); return
        if path == "/api/logs":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query); service = query.get("service", ["sing-box"])[0]
            if service not in SERVICE_ALLOWLIST: self.json(400, {"ok": False, "error": "服务无效"}); return
            code, out = run(["journalctl", "-u", service, "-n", "200", "--no-pager", "--output=short-iso"], timeout=8)
            self.json(200, {"ok": code == 0, "logs": out}); return
        if path == "/api/network":
            _, routes = run(["ip", "route", "show"], 5); _, ports = run(["ss", "-lntup"], 5)
            self.json(200, {"ok": True, "routes": routes, "ports": ports}); return
        self.json(404, {"ok": False, "error": "not found"})
    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        try: data = self.body_json()
        except (ValueError, json.JSONDecodeError) as exc: self.json(400, {"ok": False, "error": str(exc)}); return
        if path == "/api/login":
            ok, a, b = AUTH.login(self.client_address[0], str(data.get("username", "")), str(data.get("password", "")))
            if not ok: self.json(401, {"ok": False, "error": a}); return
            self.json(200, {"ok": True, "csrf": b}, {"Set-Cookie": f"iwan_session={a}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"}); return
        auth = self.require(mutate=True)
        if not auth: return
        token, _ = auth
        if path == "/api/logout": AUTH.logout(token); self.json(200, {"ok": True}, {"Set-Cookie": "iwan_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"}); return
        if path == "/api/action":
            action = str(data.get("action", "")); service = str(data.get("service", ""))
            if action == "restart" and service in SERVICE_ALLOWLIST:
                ok, out = service_restart(service); self.json(200 if ok else 500, {"ok": ok, "message": out or ("已重启" if ok else "失败")}); return
            if action == "backup":
                ensure_dirs(); name = f"manual-{time.strftime('%Y%m%d-%H%M%S')}.json"; target = BACKUP_DIR / name
                if not SINGBOX_CONFIG.exists(): self.json(400, {"ok": False, "error": "配置不存在"}); return
                shutil.copy2(SINGBOX_CONFIG, target); self.json(200, {"ok": True, "message": name}); return
            self.json(400, {"ok": False, "error": "操作无效"}); return
        if path == "/api/import-ss":
            nodes = []; errors = []
            for line in str(data.get("text", "")).splitlines():
                if not line.strip(): continue
                try: nodes.append(parse_ss_link(line))
                except Exception as exc: errors.append(f"{line[:40]}: {exc}")
            self.json(200, {"ok": not errors, "nodes": nodes, "errors": errors}); return
        if path == "/api/latency":
            nodes = data.get("nodes", [])
            if not isinstance(nodes, list) or len(nodes) > 100: self.json(400, {"ok": False, "error": "节点数量无效"}); return
            self.json(200, {"ok": True, "results": node_latency(nodes)}); return
        if path == "/api/save":
            try: ok, message = apply_config(data)
            except (ValueError, OSError) as exc: self.json(400, {"ok": False, "error": str(exc)}); return
            self.json(200 if ok else 500, {"ok": ok, "message": message, "error": "" if ok else message}); return
        self.json(404, {"ok": False, "error": "not found"})


def init_db() -> None:
    ensure_dirs(); conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("PRAGMA journal_mode=WAL"); conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"); conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('version',?)", (VERSION,)); conn.commit()
    finally: conn.close()


def self_test() -> None:
    ensure_dirs(); init_db()
    assert parse_ss_link("ss://YWVzLTEyOC1nY206cGFzcw@example.com:8388#test")["tag"] == "test"
    assert classify_rule({"domain_suffix": ["netflix.com"], "outbound": "sg"}) == "netflix"
    print(json.dumps({"ok": True, "version": VERSION}))


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8088); parser.add_argument("--init-auth", action="store_true"); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    ensure_dirs(); init_db()
    if args.init_auth:
        AUTH.initialize(os.environ.get("PANEL_ADMIN_USER", "admin"), os.environ.get("PANEL_ADMIN_PASSWORD", "")); print("auth initialized"); return
    if args.self_test: self_test(); return
    if not AUTH_FILE.exists(): raise SystemExit("auth.json missing; run --init-auth first")
    SAMPLER.start(); server = http.server.ThreadingHTTPServer((args.host, args.port), Handler); server.daemon_threads = True
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: SAMPLER.stop(); server.server_close()


if __name__ == "__main__":
    main()
