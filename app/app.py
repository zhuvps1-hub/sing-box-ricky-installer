#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from importers import SUPPORTED, parse_import

DATA = Path(os.environ.get("IWAN_DATA", "/etc/iwan-gateway"))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "gateway.db"
SB = Path("/etc/sing-box/config.json")
STATIC = Path(__file__).parent / "static"
PORT = int(os.environ.get("IWAN_PANEL_PORT", "8088"))
MAX_BODY = 6 * 1024 * 1024
CATS = ["cn", "ai", "google", "youtube", "netflix", "tiktok", "telegram"]
LABELS = {
    "cn": "国内",
    "ai": "AI",
    "google": "Google",
    "youtube": "YouTube",
    "netflix": "Netflix",
    "tiktok": "TikTok",
    "telegram": "Telegram",
}
DOMAINS = {
    "ai": [
        "openai.com",
        "chatgpt.com",
        "oaistatic.com",
        "oaiusercontent.com",
        "anthropic.com",
        "claude.ai",
        "gemini.google.com",
        "perplexity.ai",
        "copilot.microsoft.com",
    ],
    "google": [
        "google.com",
        "googleapis.com",
        "gstatic.com",
        "googleusercontent.com",
        "ggpht.com",
        "googleadservices.com",
    ],
    "youtube": [
        "youtube.com",
        "youtu.be",
        "googlevideo.com",
        "ytimg.com",
        "youtube-nocookie.com",
        "youtubei.googleapis.com",
    ],
    "netflix": [
        "netflix.com",
        "netflix.net",
        "nflxext.com",
        "nflximg.com",
        "nflxso.net",
        "nflxvideo.net",
    ],
    "tiktok": [
        "tiktok.com",
        "tiktokcdn.com",
        "tiktokv.com",
        "byteoversea.com",
        "ibytedtos.com",
        "muscdn.com",
        "musical.ly",
    ],
    "telegram": ["telegram.org", "telegram.me", "t.me", "telegra.ph", "telesco.pe"],
}
TG_CIDR = [
    "91.108.4.0/22",
    "91.108.8.0/22",
    "91.108.12.0/22",
    "91.108.16.0/22",
    "91.108.20.0/22",
    "91.108.56.0/22",
    "149.154.160.0/20",
    "2001:b28:f23d::/48",
    "2001:b28:f23f::/48",
    "2001:67c:4e8::/48",
]
CN_RULES = [
    {
        "type": "remote",
        "tag": "geosite-cn",
        "format": "binary",
        "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/cn.srs",
        "download_detour": "direct",
    },
    {
        "type": "remote",
        "tag": "geoip-cn",
        "format": "binary",
        "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geoip/cn.srs",
        "download_detour": "direct",
    },
]
LOCK = threading.Lock()


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    return connection


def pbkdf(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    raw = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260000)
    return salt.hex() + ":" + raw.hex()


def verify(password: str, value: str) -> bool:
    try:
        salt, digest = value.split(":", 1)
        candidate = pbkdf(password, bytes.fromhex(salt)).split(":", 1)[1]
        return hmac.compare_digest(candidate, digest)
    except Exception:
        return False


def init() -> None:
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,expires INTEGER NOT NULL);
            """
        )
        defaults = {
            "username": "admin",
            "password": pbkdf("admin"),
            "nodes": "[]",
            "routing": json.dumps({**{key: "direct" for key in CATS}, "default": "direct"}),
            "iwan": json.dumps(
                {
                    "enabled": True,
                    "listen": "::",
                    "port": 8000,
                    "pool": "10.10.10.0/24",
                    "mtu": 1400,
                    "username": "",
                    "password": "",
                }
            ),
        }
        for key, value in defaults.items():
            connection.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (key, value))


def get(key: str) -> str:
    with db() as connection:
        row = connection.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
        return row["v"] if row else ""


def setv(key: str, value: str) -> None:
    with db() as connection:
        connection.execute(
            "INSERT INTO settings VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )


def clean_tag(value: object, fallback: str = "node") -> str:
    tag = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    tag = re.sub(r"\s+", " ", tag)[:80] or fallback
    if tag == "direct":
        tag = "direct-node"
    return tag


def normalize_node(node: dict) -> dict:
    if isinstance(node.get("outbound"), dict):
        outbound = copy.deepcopy(node["outbound"])
        outbound_type = str(outbound.get("type") or node.get("type") or "").lower()
        outbound["type"] = outbound_type
        outbound["server"] = str(outbound.get("server") or node.get("server") or "").strip()
        outbound["server_port"] = int(outbound.get("server_port") or node.get("port") or 0)
        outbound.pop("tag", None)
        return {
            "tag": clean_tag(node.get("tag"), outbound_type or "node"),
            "type": outbound_type,
            "server": outbound["server"],
            "port": outbound["server_port"],
            "outbound": outbound,
            "source": str(node.get("source") or "已保存")[:120],
        }

    # Automatic migration from the original Shadowsocks-only database format.
    outbound = {
        "type": "shadowsocks",
        "server": str(node.get("server") or "").strip(),
        "server_port": int(node.get("port") or 0),
        "method": str(node.get("method") or ""),
        "password": str(node.get("password") or ""),
    }
    if node.get("plugin"):
        outbound["plugin"] = str(node["plugin"])
    if node.get("plugin_opts"):
        outbound["plugin_opts"] = str(node["plugin_opts"])
    return {
        "tag": clean_tag(node.get("tag"), "Shadowsocks"),
        "type": "shadowsocks",
        "server": outbound["server"],
        "port": outbound["server_port"],
        "outbound": outbound,
        "source": "旧版节点",
    }


def load_nodes() -> list[dict]:
    try:
        raw = json.loads(get("nodes") or "[]")
    except Exception:
        raw = []
    result: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            result.append(normalize_node(item))
        except Exception:
            continue
    return result


def internal_state() -> dict:
    try:
        routing = json.loads(get("routing") or "{}")
    except Exception:
        routing = {}
    try:
        iwan = json.loads(get("iwan") or "{}")
    except Exception:
        iwan = {}
    return {"nodes": load_nodes(), "routing": routing, "iwan": iwan}


def public_node(node: dict) -> dict:
    return {
        "tag": node["tag"],
        "type": node["type"],
        "server": node["server"],
        "port": node["port"],
        "source": node.get("source", "导入"),
    }


def validate_node(node: dict) -> None:
    if node.get("type") not in SUPPORTED:
        raise ValueError(f"节点 {node.get('tag', '')} 的协议不支持")
    if not node.get("server"):
        raise ValueError(f"节点 {node.get('tag', '')} 缺少服务器地址")
    port = int(node.get("port") or 0)
    if not 1 <= port <= 65535:
        raise ValueError(f"节点 {node.get('tag', '')} 端口无效")
    outbound = node.get("outbound")
    if not isinstance(outbound, dict):
        raise ValueError(f"节点 {node.get('tag', '')} 配置无效")


def normalize_routing(routing: dict, tags: set[str]) -> dict:
    result = {key: str(routing.get(key, "direct")) for key in CATS}
    result["default"] = str(routing.get("default", "direct"))
    allowed = tags | {"direct"}
    for key, value in list(result.items()):
        if value not in allowed:
            result[key] = "direct"
    return result


def build(state: dict) -> dict:
    nodes = state["nodes"]
    routing = state["routing"]
    iwan = state["iwan"]
    tags = {node["tag"] for node in nodes} | {"direct"}
    for key, value in routing.items():
        if value not in tags:
            raise ValueError(f"{LABELS.get(key, '默认出口')}选择的节点不存在")

    outbounds: list[dict] = [{"type": "direct", "tag": "direct"}]
    for node in nodes:
        validate_node(node)
        item = copy.deepcopy(node["outbound"])
        item["type"] = node["type"]
        item["tag"] = node["tag"]
        item["server"] = node["server"]
        item["server_port"] = int(node["port"])
        outbounds.append(item)

    inbounds: list[dict] = []
    if iwan.get("enabled"):
        inbound = {
            "type": "iwan",
            "tag": "iwan-in",
            "listen": iwan.get("listen", "::"),
            "listen_port": int(iwan.get("port", 8000)),
            "address_pool": iwan.get("pool", "10.10.10.0/24"),
            "mtu": int(iwan.get("mtu", 1400)),
        }
        if iwan.get("username") or iwan.get("password"):
            inbound["users"] = [
                {
                    "username": iwan.get("username", ""),
                    "password": iwan.get("password", ""),
                }
            ]
        inbounds.append(inbound)

    rules: list[dict] = [{"ip_is_private": True, "outbound": "direct"}]
    rules.append({"rule_set": ["geosite-cn", "geoip-cn"], "outbound": routing["cn"]})
    for key in ["ai", "youtube", "netflix", "tiktok", "telegram", "google"]:
        rule: dict = {"domain_suffix": DOMAINS[key], "outbound": routing[key]}
        if key == "telegram":
            rule["ip_cidr"] = TG_CIDR
        rules.append(rule)

    return {
        "log": {"level": "info", "timestamp": True},
        "inbounds": inbounds,
        "outbounds": outbounds,
        "route": {
            "rule_set": CN_RULES,
            "rules": rules,
            "final": routing["default"],
            "auto_detect_interface": True,
        },
    }


def apply_config(state: dict) -> None:
    with LOCK:
        config = build(state)
        SB.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="iwan-", suffix=".json", dir=str(SB.parent))
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
            backup = DATA / "backups" / f"config-{int(time.time())}.json"
            backup.parent.mkdir(exist_ok=True)
            if SB.exists():
                backup.write_bytes(SB.read_bytes())
            os.replace(temporary, SB)
            os.chmod(SB, 0o600)
            reloaded = subprocess.run(["systemctl", "reload", "sing-box"], capture_output=True, timeout=4)
            if reloaded.returncode:
                subprocess.run(["systemctl", "restart", "sing-box"], check=True, timeout=8)
            time.sleep(0.2)
            if subprocess.run(["systemctl", "is-active", "--quiet", "sing-box"]).returncode:
                raise ValueError("sing-box 未正常运行")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def service_active() -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", "sing-box"]).returncode == 0


def latency(node: dict) -> dict:
    started = time.perf_counter()
    socktype = socket.SOCK_DGRAM if node["type"] in {"tuic", "hysteria2"} else socket.SOCK_STREAM
    mode = "UDP 地址可达" if socktype == socket.SOCK_DGRAM else "TCP 握手"
    last_error = "连接失败"
    try:
        addresses = socket.getaddrinfo(node["server"], int(node["port"]), type=socktype)
        for family, socket_type, proto, _, address in addresses:
            sock = socket.socket(family, socket_type, proto)
            sock.settimeout(2.5)
            try:
                sock.connect(address)
                return {
                    "ok": True,
                    "ms": round((time.perf_counter() - started) * 1000),
                    "mode": mode,
                }
            except Exception as exc:
                last_error = str(exc)
            finally:
                sock.close()
    except Exception as exc:
        last_error = str(exc)
    return {"ok": False, "error": last_error[:100], "mode": mode}


def node_fingerprint(node: dict) -> str:
    return json.dumps(node["outbound"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def unique_tag(preferred: str, used: set[str]) -> str:
    base = clean_tag(preferred)
    candidate = base
    index = 2
    while candidate in used or candidate == "direct":
        suffix = f"-{index}"
        candidate = base[: 80 - len(suffix)] + suffix
        index += 1
    used.add(candidate)
    return candidate


def merge_nodes(existing: list[dict], imported: list[dict], replace: bool = False) -> tuple[list[dict], int, int]:
    result = [] if replace else [normalize_node(item) for item in existing]
    fingerprints = {node_fingerprint(item) for item in result}
    used = {item["tag"] for item in result}
    added = 0
    skipped = 0
    for raw in imported:
        node = normalize_node(raw)
        validate_node(node)
        fingerprint = node_fingerprint(node)
        if fingerprint in fingerprints:
            skipped += 1
            continue
        node["tag"] = unique_tag(node["tag"], used)
        result.append(node)
        fingerprints.add(fingerprint)
        added += 1
    if len(result) > 300:
        raise ValueError("节点数量不能超过 300 个")
    return result, added, skipped


def save_state(state: dict) -> None:
    setv("nodes", json.dumps(state["nodes"], ensure_ascii=False))
    setv("routing", json.dumps(state["routing"], ensure_ascii=False))
    setv("iwan", json.dumps(state["iwan"], ensure_ascii=False))


class Handler(BaseHTTPRequestHandler):
    server_version = "iWAN/1.1"

    def log_message(self, *_: object) -> None:
        pass

    def sendj(self, obj: object, code: int = 200, headers: dict | None = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > MAX_BODY:
            raise ValueError("请求内容超过 6 MB")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
            return value if isinstance(value, dict) else {}
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("请求格式错误") from exc

    def token(self) -> str:
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return jar.get("sid").value if jar.get("sid") else ""

    def auth(self) -> bool:
        token = self.token()
        if not token:
            return False
        with db() as connection:
            row = connection.execute("SELECT expires FROM sessions WHERE token=?", (token,)).fetchone()
            return bool(row and row["expires"] > time.time())

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/me":
            authenticated = self.auth()
            return self.sendj({"ok": authenticated, "username": get("username") if authenticated else ""})
        if path.startswith("/api/") and not self.auth():
            return self.sendj({"error": "未登录"}, 401)
        if path == "/api/state":
            state = internal_state()
            return self.sendj(
                {
                    "nodes": [public_node(node) for node in state["nodes"]],
                    "routing": normalize_routing(state["routing"], {node["tag"] for node in state["nodes"]}),
                    "iwan": {**state["iwan"], "password": "", "has_password": bool(state["iwan"].get("password"))},
                    "service": {"singbox": service_active()},
                    "supported": sorted(SUPPORTED),
                }
            )
        if path == "/api/status":
            return self.sendj({"singbox": service_active(), "time": int(time.time())})

        file_path = STATIC / ("index.html" if path in ("/", "/index.html") else path.lstrip("/"))
        if not file_path.exists() or STATIC not in file_path.resolve().parents:
            return self.send_error(404)
        body = file_path.read_bytes()
        content_type = (
            "text/html"
            if file_path.suffix == ".html"
            else "text/css"
            if file_path.suffix == ".css"
            else "application/javascript"
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.body()
        except Exception as exc:
            return self.sendj({"error": str(exc)}, 413)

        if path == "/api/login":
            if payload.get("username") != get("username") or not verify(str(payload.get("password", "")), get("password")):
                return self.sendj({"error": "账号或密码错误"}, 403)
            token = secrets.token_urlsafe(32)
            with db() as connection:
                connection.execute("INSERT INTO sessions VALUES(?,?)", (token, int(time.time() + 2592000)))
            return self.sendj(
                {"ok": True},
                headers={"Set-Cookie": f"sid={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"},
            )

        if path == "/api/logout":
            with db() as connection:
                connection.execute("DELETE FROM sessions WHERE token=?", (self.token(),))
            return self.sendj({"ok": True}, headers={"Set-Cookie": "sid=; Path=/; Max-Age=0"})

        if not self.auth():
            return self.sendj({"error": "未登录"}, 401)

        try:
            if path == "/api/import":
                source = str(payload.get("source") or "").strip()
                imported, warnings = parse_import(source)
                current = internal_state()
                nodes, added, skipped = merge_nodes(current["nodes"], imported, bool(payload.get("replace")))
                routing = normalize_routing(current["routing"], {node["tag"] for node in nodes})
                state = {"nodes": nodes, "routing": routing, "iwan": current["iwan"]}
                apply_config(state)
                save_state(state)
                return self.sendj(
                    {
                        "ok": True,
                        "added": added,
                        "skipped": skipped,
                        "total": len(nodes),
                        "warnings": warnings[:20],
                        "message": f"成功导入 {added} 个节点",
                    }
                )

            if path == "/api/delete-node":
                tag = str(payload.get("tag") or "")
                current = internal_state()
                nodes = [node for node in current["nodes"] if node["tag"] != tag]
                if len(nodes) == len(current["nodes"]):
                    raise ValueError("节点不存在")
                routing = normalize_routing(current["routing"], {node["tag"] for node in nodes})
                state = {"nodes": nodes, "routing": routing, "iwan": current["iwan"]}
                apply_config(state)
                save_state(state)
                return self.sendj({"ok": True})

            if path == "/api/rename-node":
                old = str(payload.get("tag") or "")
                new = clean_tag(payload.get("new"), "node")
                current = internal_state()
                used = {node["tag"] for node in current["nodes"] if node["tag"] != old}
                if new in used or new == "direct":
                    raise ValueError("节点名称已存在")
                found = False
                for node in current["nodes"]:
                    if node["tag"] == old:
                        node["tag"] = new
                        found = True
                        break
                if not found:
                    raise ValueError("节点不存在")
                routing = {key: (new if value == old else value) for key, value in current["routing"].items()}
                routing = normalize_routing(routing, {node["tag"] for node in current["nodes"]})
                state = {"nodes": current["nodes"], "routing": routing, "iwan": current["iwan"]}
                apply_config(state)
                save_state(state)
                return self.sendj({"ok": True})

            if path == "/api/apply":
                current = internal_state()
                routing_input = payload.get("routing") if isinstance(payload.get("routing"), dict) else {}
                routing = normalize_routing(routing_input, {node["tag"] for node in current["nodes"]})
                iwan_input = payload.get("iwan") if isinstance(payload.get("iwan"), dict) else {}
                previous_iwan = current["iwan"]
                iwan = {
                    "enabled": bool(iwan_input.get("enabled", True)),
                    "listen": str(iwan_input.get("listen", "::")),
                    "port": int(iwan_input.get("port", 8000)),
                    "pool": str(iwan_input.get("pool", "10.10.10.0/24")),
                    "mtu": int(iwan_input.get("mtu", 1400)),
                    "username": str(iwan_input.get("username", "")),
                    "password": str(iwan_input.get("password", "")) or previous_iwan.get("password", ""),
                }
                if not 1 <= iwan["port"] <= 65535:
                    raise ValueError("iWAN 端口无效")
                if not 576 <= iwan["mtu"] <= 9000:
                    raise ValueError("MTU 必须在 576–9000 之间")
                state = {"nodes": current["nodes"], "routing": routing, "iwan": iwan}
                apply_config(state)
                save_state(state)
                return self.sendj({"ok": True, "message": "已生效"})

            if path == "/api/latency":
                tag = str(payload.get("tag") or "")
                node = next((item for item in internal_state()["nodes"] if item["tag"] == tag), None)
                if not node:
                    raise ValueError("节点不存在")
                return self.sendj(latency(node))

            if path == "/api/password":
                if not verify(str(payload.get("old", "")), get("password")):
                    raise ValueError("原密码错误")
                new_password = str(payload.get("new", ""))
                if len(new_password) < 8:
                    raise ValueError("新密码至少 8 位")
                setv("password", pbkdf(new_password))
                if payload.get("username"):
                    setv("username", str(payload["username"]).strip())
                with db() as connection:
                    connection.execute("DELETE FROM sessions WHERE token<>?", (self.token(),))
                return self.sendj({"ok": True})

            return self.sendj({"error": "接口不存在"}, 404)
        except Exception as exc:
            return self.sendj({"error": str(exc)}, 400)


if __name__ == "__main__":
    init()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
