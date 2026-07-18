"""Stable sing-box application model and Ricky-Hao renderer.

Browser dictionaries are never copied directly into the service configuration.
Every mutation is validated into this module's canonical model first.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import re
from typing import Any

import iwan_compat

ROUTE_CATEGORIES = ("netflix", "ai", "youtube", "telegram")
CATEGORY_DOMAINS = {
    "netflix": ["netflix.com", "netflix.net", "nflxext.com", "nflximg.com", "nflxvideo.net"],
    "ai": ["openai.com", "chatgpt.com", "oaistatic.com", "oaiusercontent.com", "anthropic.com", "claude.ai", "gemini.google.com"],
    "youtube": ["youtube.com", "youtu.be", "googlevideo.com", "ytimg.com", "youtube-nocookie.com"],
    "telegram": ["telegram.org", "telegram.me", "t.me", "telesco.pe"],
}
TELEGRAM_CIDRS = [
    "91.108.4.0/22", "91.108.8.0/22", "91.108.12.0/22", "91.108.16.0/22",
    "91.108.20.0/22", "91.108.56.0/22", "149.154.160.0/20",
    "2001:b28:f23d::/48", "2001:b28:f23f::/48", "2001:67c:4e8::/48",
]
_TAG_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def is_iwan(value: Any) -> bool:
    return iwan_compat.is_iwan_inbound(value)


def _port(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是整数") from exc
    if not 1 <= number <= 65535:
        raise ValueError(f"{label}必须在 1 到 65535 之间")
    return number


def normalize_node(value: Any, old: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("节点必须是对象")
    tag = str(value.get("tag", "")).strip()
    server = str(value.get("server", "")).strip()
    method = str(value.get("method", "")).strip()
    if not _TAG_RE.fullmatch(tag):
        raise ValueError("节点标签仅允许字母、数字、点、下划线和短横线")
    if not server or len(server) > 255 or any(character.isspace() for character in server):
        raise ValueError(f"节点 {tag} 服务器地址无效")
    if not method or len(method) > 96 or any(character.isspace() for character in method):
        raise ValueError(f"节点 {tag} 加密方式无效")
    result: dict[str, Any] = {
        "type": "shadowsocks",
        "tag": tag,
        "server": server,
        "server_port": _port(value.get("server_port"), f"节点 {tag} 端口"),
        "method": method,
    }
    password = str(value.get("password", ""))
    if not password and old:
        password = str(old.get("password", ""))
    if not password:
        raise ValueError(f"节点 {tag} 需要密码")
    if len(password.encode("utf-8")) > 4096:
        raise ValueError(f"节点 {tag} 密码过长")
    result["password"] = password
    for key in ("plugin", "plugin_opts"):
        if value.get(key) not in (None, ""):
            text = str(value[key])
            if len(text) > 2048 or "\0" in text:
                raise ValueError(f"节点 {tag} {key} 无效")
            result[key] = text
    return result


def _credential_keys(item: dict[str, Any]) -> tuple[str, str]:
    username_key = "username" if "username" in item or "user" not in item else "user"
    password_key = "password" if "password" in item or "pass" not in item else "pass"
    return username_key, password_key


def patch_iwan_credentials(item: dict[str, Any], username: str, password: str) -> None:
    username = username.strip()
    users = item.get("users")
    if isinstance(users, list):
        if not users:
            if not username or not password:
                raise ValueError("首次设置 iWAN 登录信息时，用户名和密码都必须填写")
            item["users"] = [{"username": username, "password": password}]
            return
        if not isinstance(users[0], dict):
            raise ValueError("当前 iWAN users 格式无法安全修改")
        updated = copy.deepcopy(users)
        account = updated[0]
        username_key, password_key = _credential_keys(account)
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
        username_key, password_key = _credential_keys(item)
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


def normalize_iwan(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("iWAN 配置必须是对象")
    return iwan_compat.normalize_iwan_patch(value)


def managed_rules(mappings: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for category in ROUTE_CATEGORIES:
        outbound = str(mappings.get(category, "")).strip()
        if not outbound:
            continue
        rule: dict[str, Any] = {"domain_suffix": CATEGORY_DOMAINS[category], "outbound": outbound}
        if category == "telegram":
            rule["ip_cidr"] = TELEGRAM_CIDRS
        result.append(rule)
    return result


def normalize_plan(payload: Any, current: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("配置请求必须是对象")
    outbounds = current.get("outbounds", [])
    existing = {
        str(item.get("tag")): item
        for item in outbounds if isinstance(item, dict) and item.get("type") == "shadowsocks"
    } if isinstance(outbounds, list) else {}
    raw_nodes = payload.get("nodes", [])
    if not isinstance(raw_nodes, list) or len(raw_nodes) > 200:
        raise ValueError("节点列表无效")
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_nodes:
        old = existing.get(str(raw.get("tag", ""))) if isinstance(raw, dict) else None
        node = normalize_node(raw, old)
        if node["tag"] in seen:
            raise ValueError(f"节点标签重复：{node['tag']}")
        seen.add(node["tag"])
        nodes.append(node)
    deleted_raw = payload.get("deleted_tags", [])
    if not isinstance(deleted_raw, list) or len(deleted_raw) > 200:
        raise ValueError("删除列表无效")
    deleted = {str(value) for value in deleted_raw if _TAG_RE.fullmatch(str(value))}
    mappings_raw = payload.get("mappings", {})
    if not isinstance(mappings_raw, dict):
        raise ValueError("分流配置无效")
    mappings = {key: str(mappings_raw.get(key, "")).strip() for key in ROUTE_CATEGORIES}
    default = str(payload.get("default", "")).strip()
    available = ({str(key) for key in existing} - deleted) | seen | {"direct"}
    requested = {value for value in mappings.values() if value}
    if default:
        requested.add(default)
    missing = sorted(requested - available)
    if missing:
        raise ValueError("分流出口不存在：" + ", ".join(missing))
    conflict = sorted(deleted & requested)
    if conflict:
        raise ValueError("以下节点仍被分流使用：" + ", ".join(conflict))
    iwan = normalize_iwan(payload["iwan"]) if isinstance(payload.get("iwan"), dict) and payload["iwan"] else {}
    return {"nodes": nodes, "deleted": deleted, "mappings": mappings, "default": default, "iwan": iwan}


def render(current: dict[str, Any], payload: Any, previous_managed: list[Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(current, dict) or not current:
        raise ValueError("未找到有效 sing-box 配置")
    config = iwan_compat.migrate_config(current)
    plan = normalize_plan(payload, config)
    outbounds = config.setdefault("outbounds", [])
    if not isinstance(outbounds, list):
        raise ValueError("outbounds 格式无效")
    by_tag = {
        str(item.get("tag")): item
        for item in outbounds if isinstance(item, dict) and item.get("type") == "shadowsocks"
    }
    for node in plan["nodes"]:
        old = by_tag.get(node["tag"])
        if old is None:
            outbounds.append(node)
        else:
            merged = copy.deepcopy(old)
            merged.update(node)
            outbounds[outbounds.index(old)] = merged
    if plan["deleted"]:
        config["outbounds"] = [
            item for item in outbounds
            if not (isinstance(item, dict) and item.get("type") == "shadowsocks" and str(item.get("tag")) in plan["deleted"])
        ]
    if plan["iwan"]:
        inbounds = config.setdefault("inbounds", [])
        if not isinstance(inbounds, list):
            raise ValueError("inbounds 格式无效")
        target = next((item for item in inbounds if is_iwan(item)), None)
        if target is None:
            target = {
                "type": "iwan", "tag": "iwan-in", "listen": "::", "listen_port": 8000,
                "address_pool": "10.10.10.0/24", "mtu": 1400,
            }
            inbounds.insert(0, target)
        target.pop("address", None)
        for key in ("listen", "listen_port", "address_pool", "mtu"):
            if key in plan["iwan"]:
                target[key] = plan["iwan"][key]
        patch_iwan_credentials(target, str(plan["iwan"].get("username", "")), str(plan["iwan"].get("password", "")))
    route = config.setdefault("route", {})
    if not isinstance(route, dict):
        raise ValueError("route 格式无效")
    rules = route.setdefault("rules", [])
    if not isinstance(rules, list):
        raise ValueError("route.rules 格式无效")
    old = previous_managed or []
    generated = managed_rules(plan["mappings"])
    route["rules"] = generated + [rule for rule in rules if rule not in old]
    if plan["default"]:
        route["final"] = plan["default"]
    encoded = json.dumps(config, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 16 * 1024 * 1024:
        raise ValueError("生成的 sing-box 配置过大")
    return config, generated


def public_iwan(item: dict[str, Any]) -> dict[str, Any]:
    result = {key: copy.deepcopy(item[key]) for key in ("type", "tag", "listen", "listen_port", "address_pool", "mtu") if key in item}
    users = item.get("users")
    if isinstance(users, list) and users and isinstance(users[0], dict):
        account = users[0]
        result["username"] = str(account.get("username") or account.get("name") or account.get("user") or "")
        result["has_password"] = bool(account.get("password") or account.get("pass"))
        result["users_count"] = len(users)
        result["credential_mode"] = "users"
    else:
        result["username"] = str(item.get("username") or item.get("user") or "")
        result["has_password"] = bool(item.get("password") or item.get("pass"))
        result["credential_mode"] = "flat"
    return result


def validate_network(value: str) -> str:
    return str(ipaddress.ip_network(value, strict=False))
