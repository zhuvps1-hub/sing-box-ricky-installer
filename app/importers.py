#!/usr/bin/env python3
from __future__ import annotations

import base64
import copy
import gzip
import json
import re
import urllib.request
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional outside installer
    yaml = None

SUPPORTED = {"shadowsocks", "vmess", "vless", "trojan", "tuic", "hysteria2"}
MAX_SUBSCRIPTION_BYTES = 4 * 1024 * 1024


def _first(mapping: dict[str, list[str]], *names: str, default: str = "") -> str:
    for name in names:
        values = mapping.get(name)
        if values:
            return str(values[0])
    return default


def _bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _clean_name(value: Any, fallback: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    name = re.sub(r"\s+", " ", name)
    return (name or fallback)[:80]


def _decode_b64(value: str) -> str:
    compact = re.sub(r"\s+", "", value.strip())
    if not compact:
        raise ValueError("Base64 内容为空")
    compact += "=" * (-len(compact) % 4)
    try:
        raw = base64.urlsafe_b64decode(compact.encode())
        return raw.decode("utf-8-sig")
    except Exception as exc:
        raise ValueError("Base64 解码失败") from exc


def _split_host_port(value: str) -> tuple[str, int]:
    parsed = urlsplit("//" + value)
    if not parsed.hostname or not parsed.port:
        raise ValueError("服务器地址或端口无效")
    return parsed.hostname, parsed.port


def _alpn(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if not value:
        return []
    return [x.strip() for x in str(value).split(",") if x.strip()]


def _tls(
    enabled: bool,
    server_name: str = "",
    insecure: bool = False,
    alpn: Any = None,
    fingerprint: str = "",
    public_key: str = "",
    short_id: str = "",
) -> dict[str, Any] | None:
    if not enabled and not public_key:
        return None
    result: dict[str, Any] = {"enabled": True}
    if server_name:
        result["server_name"] = server_name
    if insecure:
        result["insecure"] = True
    alpn_list = _alpn(alpn)
    if alpn_list:
        result["alpn"] = alpn_list
    if fingerprint and fingerprint.lower() not in {"none", "random"}:
        result["utls"] = {"enabled": True, "fingerprint": fingerprint}
    if public_key:
        reality: dict[str, Any] = {"enabled": True, "public_key": public_key}
        if short_id:
            reality["short_id"] = short_id
        result["reality"] = reality
    return result


def _transport(network: str, path: str = "", host: str = "", service_name: str = "") -> dict[str, Any] | None:
    network = str(network or "").strip().lower()
    if network in {"", "tcp", "none"}:
        return None
    if network in {"ws", "websocket"}:
        result: dict[str, Any] = {"type": "ws", "path": path or "/"}
        if host:
            result["headers"] = {"Host": host}
        return result
    if network == "grpc":
        return {"type": "grpc", "service_name": service_name or path.lstrip("/")}
    if network in {"h2", "http"}:
        result = {"type": "http", "path": path or "/"}
        if host:
            result["host"] = [x.strip() for x in host.split(",") if x.strip()]
        return result
    if network in {"httpupgrade", "http-upgrade"}:
        result = {"type": "httpupgrade", "path": path or "/"}
        if host:
            result["host"] = host
        return result
    return None


def _node(tag: str, outbound: dict[str, Any], source: str) -> dict[str, Any]:
    outbound = copy.deepcopy(outbound)
    outbound_type = str(outbound.get("type", "")).lower()
    if outbound_type not in SUPPORTED:
        raise ValueError(f"不支持的协议：{outbound_type or '未知'}")
    server = str(outbound.get("server", "")).strip()
    port = _int(outbound.get("server_port"))
    if not server or not 1 <= port <= 65535:
        raise ValueError("服务器地址或端口无效")
    outbound["type"] = outbound_type
    outbound["server"] = server
    outbound["server_port"] = port
    outbound.pop("tag", None)
    return {
        "tag": _clean_name(tag, f"{outbound_type}-{server}"),
        "type": outbound_type,
        "server": server,
        "port": port,
        "outbound": outbound,
        "source": _clean_name(source, "导入"),
    }


def parse_ss(uri: str, source: str) -> dict[str, Any]:
    raw = uri[5:]
    raw, _, fragment = raw.partition("#")
    raw, _, query = raw.partition("?")
    name = unquote(fragment) if fragment else "Shadowsocks"
    if "@" in raw:
        left, server_part = raw.rsplit("@", 1)
        left = unquote(left)
        credentials = left if ":" in left else _decode_b64(left)
    else:
        decoded = _decode_b64(raw)
        if "@" not in decoded:
            raise ValueError("SS 链接缺少服务器信息")
        credentials, server_part = decoded.rsplit("@", 1)
    if ":" not in credentials:
        raise ValueError("SS 链接缺少加密方式或密码")
    method, password = credentials.split(":", 1)
    server, port = _split_host_port(server_part)
    params = parse_qs(query, keep_blank_values=True)
    plugin_value = unquote(_first(params, "plugin"))
    plugin = ""
    plugin_opts = ""
    if plugin_value:
        parts = plugin_value.split(";", 1)
        plugin = parts[0]
        plugin_opts = parts[1] if len(parts) > 1 else ""
    outbound: dict[str, Any] = {
        "type": "shadowsocks",
        "server": server,
        "server_port": port,
        "method": unquote(method),
        "password": unquote(password),
    }
    if plugin:
        outbound["plugin"] = plugin
    if plugin_opts:
        outbound["plugin_opts"] = plugin_opts
    return _node(name, outbound, source)


def parse_vmess(uri: str, source: str) -> dict[str, Any]:
    payload = json.loads(_decode_b64(uri.split("://", 1)[1]))
    server = str(payload.get("add") or payload.get("server") or "").strip()
    port = _int(payload.get("port") or payload.get("server_port"))
    uuid = str(payload.get("id") or payload.get("uuid") or "").strip()
    if not uuid:
        raise ValueError("VMess 缺少 UUID")
    outbound: dict[str, Any] = {
        "type": "vmess",
        "server": server,
        "server_port": port,
        "uuid": uuid,
        "security": str(payload.get("scy") or payload.get("security") or "auto"),
        "alter_id": _int(payload.get("aid") or payload.get("alter_id"), 0),
    }
    transport = _transport(
        str(payload.get("net") or payload.get("network") or ""),
        str(payload.get("path") or ""),
        str(payload.get("host") or ""),
        str(payload.get("path") or payload.get("serviceName") or ""),
    )
    if transport:
        outbound["transport"] = transport
    tls_enabled = str(payload.get("tls") or "").lower() not in {"", "none", "false"}
    tls = _tls(
        tls_enabled,
        str(payload.get("sni") or (payload.get("host") if tls_enabled else "") or ""),
        _bool(payload.get("allowInsecure")),
        payload.get("alpn"),
        str(payload.get("fp") or ""),
    )
    if tls:
        outbound["tls"] = tls
    return _node(str(payload.get("ps") or "VMess"), outbound, source)


def _uri_common(uri: str) -> tuple[Any, dict[str, list[str]], str, int, str]:
    parsed = urlsplit(uri)
    server = parsed.hostname or ""
    port = parsed.port or 0
    params = parse_qs(parsed.query, keep_blank_values=True)
    name = unquote(parsed.fragment) if parsed.fragment else parsed.scheme.upper()
    return parsed, params, server, port, name


def _uri_tls(params: dict[str, list[str]], server: str, default: bool = False) -> dict[str, Any] | None:
    security = _first(params, "security").lower()
    public_key = _first(params, "pbk", "publicKey", "public-key", "reality-public-key")
    enabled = default or security in {"tls", "reality"} or bool(public_key)
    if security in {"none", "false"} and not public_key:
        enabled = False
    return _tls(
        enabled,
        _first(params, "sni", "servername", "peer", default=server if enabled else ""),
        _bool(_first(params, "insecure", "allowInsecure", "allow_insecure")),
        _first(params, "alpn"),
        _first(params, "fp", "fingerprint"),
        public_key,
        _first(params, "sid", "shortId", "short_id"),
    )


def _uri_transport(params: dict[str, list[str]]) -> dict[str, Any] | None:
    return _transport(
        _first(params, "type", "network"),
        unquote(_first(params, "path")),
        _first(params, "host"),
        _first(params, "serviceName", "service_name", "service-name"),
    )


def parse_vless(uri: str, source: str) -> dict[str, Any]:
    parsed, params, server, port, name = _uri_common(uri)
    uuid = unquote(parsed.username or "")
    if not uuid:
        raise ValueError("VLESS 缺少 UUID")
    outbound: dict[str, Any] = {
        "type": "vless",
        "server": server,
        "server_port": port,
        "uuid": uuid,
    }
    flow = _first(params, "flow")
    if flow:
        outbound["flow"] = flow
    packet_encoding = _first(params, "packetEncoding", "packet_encoding", "packet-encoding")
    if packet_encoding and packet_encoding.lower() != "none":
        outbound["packet_encoding"] = packet_encoding
    transport = _uri_transport(params)
    if transport:
        outbound["transport"] = transport
    tls = _uri_tls(params, server)
    if tls:
        outbound["tls"] = tls
    return _node(name or "VLESS", outbound, source)


def parse_trojan(uri: str, source: str) -> dict[str, Any]:
    parsed, params, server, port, name = _uri_common(uri)
    password = unquote(parsed.username or "")
    if parsed.password is not None:
        password += ":" + unquote(parsed.password)
    if not password:
        raise ValueError("Trojan 缺少密码")
    outbound: dict[str, Any] = {
        "type": "trojan",
        "server": server,
        "server_port": port,
        "password": password,
    }
    transport = _uri_transport(params)
    if transport:
        outbound["transport"] = transport
    tls = _uri_tls(params, server, default=True)
    if tls:
        outbound["tls"] = tls
    return _node(name or "Trojan", outbound, source)


def parse_tuic(uri: str, source: str) -> dict[str, Any]:
    parsed, params, server, port, name = _uri_common(uri)
    uuid = unquote(parsed.username or _first(params, "uuid"))
    password = unquote(parsed.password or _first(params, "password"))
    if not uuid or not password:
        raise ValueError("TUIC 缺少 UUID 或密码")
    outbound: dict[str, Any] = {
        "type": "tuic",
        "server": server,
        "server_port": port,
        "uuid": uuid,
        "password": password,
    }
    congestion = _first(params, "congestion_control", "congestion-controller", "congestion_control_algorithm")
    if congestion:
        outbound["congestion_control"] = congestion
    relay = _first(params, "udp_relay_mode", "udp-relay-mode")
    if relay:
        outbound["udp_relay_mode"] = relay
    if _bool(_first(params, "reduce_rtt", "reduce-rtt", "zero_rtt_handshake")):
        outbound["zero_rtt_handshake"] = True
    heartbeat = _first(params, "heartbeat", "heartbeat_interval", "heartbeat-interval")
    if heartbeat:
        outbound["heartbeat"] = heartbeat
    tls = _uri_tls(params, server, default=True)
    if tls:
        outbound["tls"] = tls
    return _node(name or "TUIC", outbound, source)


def parse_hysteria2(uri: str, source: str) -> dict[str, Any]:
    parsed, params, server, port, name = _uri_common(uri)
    password = unquote(parsed.username or "")
    if parsed.password is not None:
        password += ":" + unquote(parsed.password)
    password = password or _first(params, "auth", "password")
    if not password:
        raise ValueError("Hysteria2 缺少密码")
    outbound: dict[str, Any] = {
        "type": "hysteria2",
        "server": server,
        "server_port": port,
        "password": password,
    }
    ports = _first(params, "mport", "ports", "server_ports")
    if ports:
        outbound["server_ports"] = [x.strip() for x in ports.split(",") if x.strip()]
    hop = _first(params, "hop_interval", "hop-interval")
    if hop:
        outbound["hop_interval"] = hop
    up = _int(_first(params, "upmbps", "up_mbps", "up"))
    down = _int(_first(params, "downmbps", "down_mbps", "down"))
    if up:
        outbound["up_mbps"] = up
    if down:
        outbound["down_mbps"] = down
    obfs_type = _first(params, "obfs")
    obfs_password = _first(params, "obfs-password", "obfs_password")
    if obfs_type or obfs_password:
        outbound["obfs"] = {"type": obfs_type or "salamander", "password": obfs_password}
    tls = _uri_tls(params, server, default=True)
    if tls:
        outbound["tls"] = tls
    return _node(name or "Hysteria2", outbound, source)


def parse_uri(uri: str, source: str) -> dict[str, Any]:
    scheme = urlsplit(uri).scheme.lower()
    if scheme == "ss":
        return parse_ss(uri, source)
    if scheme == "vmess":
        return parse_vmess(uri, source)
    if scheme == "vless":
        return parse_vless(uri, source)
    if scheme == "trojan":
        return parse_trojan(uri, source)
    if scheme == "tuic":
        return parse_tuic(uri, source)
    if scheme in {"hy2", "hysteria2"}:
        return parse_hysteria2(uri, source)
    raise ValueError(f"不支持的分享链接：{scheme or '未知'}")


def _clash_transport(proxy: dict[str, Any]) -> dict[str, Any] | None:
    network = str(proxy.get("network") or "")
    if network in {"ws", "websocket"}:
        opts = proxy.get("ws-opts") or proxy.get("ws_opts") or {}
        headers = opts.get("headers") or {}
        return _transport(network, str(opts.get("path") or "/"), str(headers.get("Host") or headers.get("host") or ""))
    if network == "grpc":
        opts = proxy.get("grpc-opts") or proxy.get("grpc_opts") or {}
        return _transport(network, service_name=str(opts.get("grpc-service-name") or opts.get("service-name") or opts.get("service_name") or ""))
    if network in {"h2", "http"}:
        opts = proxy.get("h2-opts") or proxy.get("http-opts") or {}
        path = opts.get("path") or "/"
        if isinstance(path, list):
            path = path[0] if path else "/"
        host = opts.get("host") or ""
        if isinstance(host, list):
            host = ",".join(str(x) for x in host)
        return _transport(network, str(path), str(host))
    if network in {"httpupgrade", "http-upgrade"}:
        opts = proxy.get("http-upgrade-opts") or {}
        return _transport(network, str(opts.get("path") or "/"), str(opts.get("host") or ""))
    return None


def _clash_tls(proxy: dict[str, Any], server: str, default: bool = False) -> dict[str, Any] | None:
    reality = proxy.get("reality-opts") or proxy.get("reality_opts") or {}
    public_key = str(reality.get("public-key") or reality.get("public_key") or "")
    return _tls(
        _bool(proxy.get("tls"), default) or default or bool(public_key),
        str(proxy.get("servername") or proxy.get("sni") or (server if default else "")),
        _bool(proxy.get("skip-cert-verify")),
        proxy.get("alpn"),
        str(proxy.get("client-fingerprint") or proxy.get("fingerprint") or ""),
        public_key,
        str(reality.get("short-id") or reality.get("short_id") or ""),
    )


def parse_clash_proxy(proxy: dict[str, Any], source: str) -> dict[str, Any]:
    typ = str(proxy.get("type") or "").lower()
    if typ == "ss":
        typ = "shadowsocks"
    if typ in {"hy2", "hysteria-2"}:
        typ = "hysteria2"
    server = str(proxy.get("server") or "").strip()
    port = _int(proxy.get("port"))
    name = str(proxy.get("name") or typ.upper())
    if typ == "shadowsocks":
        outbound: dict[str, Any] = {
            "type": typ,
            "server": server,
            "server_port": port,
            "method": str(proxy.get("cipher") or proxy.get("method") or ""),
            "password": str(proxy.get("password") or ""),
        }
        plugin = str(proxy.get("plugin") or "")
        if plugin:
            outbound["plugin"] = plugin
            opts = proxy.get("plugin-opts") or proxy.get("plugin_opts") or {}
            if isinstance(opts, dict):
                outbound["plugin_opts"] = ";".join(f"{k}={v}" for k, v in opts.items())
            elif opts:
                outbound["plugin_opts"] = str(opts)
        return _node(name, outbound, source)
    if typ == "vmess":
        outbound = {
            "type": typ,
            "server": server,
            "server_port": port,
            "uuid": str(proxy.get("uuid") or ""),
            "security": str(proxy.get("cipher") or "auto"),
            "alter_id": _int(proxy.get("alterId") or proxy.get("alter_id"), 0),
        }
    elif typ == "vless":
        outbound = {
            "type": typ,
            "server": server,
            "server_port": port,
            "uuid": str(proxy.get("uuid") or ""),
        }
        if proxy.get("flow"):
            outbound["flow"] = str(proxy["flow"])
        if proxy.get("packet-encoding"):
            outbound["packet_encoding"] = str(proxy["packet-encoding"])
    elif typ == "trojan":
        outbound = {
            "type": typ,
            "server": server,
            "server_port": port,
            "password": str(proxy.get("password") or ""),
        }
    elif typ == "tuic":
        outbound = {
            "type": typ,
            "server": server,
            "server_port": port,
            "uuid": str(proxy.get("uuid") or ""),
            "password": str(proxy.get("password") or ""),
        }
        congestion = proxy.get("congestion-controller") or proxy.get("congestion_control")
        if congestion:
            outbound["congestion_control"] = str(congestion)
        relay = proxy.get("udp-relay-mode") or proxy.get("udp_relay_mode")
        if relay:
            outbound["udp_relay_mode"] = str(relay)
        if _bool(proxy.get("reduce-rtt")):
            outbound["zero_rtt_handshake"] = True
    elif typ == "hysteria2":
        outbound = {
            "type": typ,
            "server": server,
            "server_port": port,
            "password": str(proxy.get("password") or proxy.get("auth") or ""),
        }
        up = _int(proxy.get("up") or proxy.get("up-mbps") or proxy.get("up_mbps"))
        down = _int(proxy.get("down") or proxy.get("down-mbps") or proxy.get("down_mbps"))
        if up:
            outbound["up_mbps"] = up
        if down:
            outbound["down_mbps"] = down
        obfs_type = str(proxy.get("obfs") or "")
        obfs_password = str(proxy.get("obfs-password") or proxy.get("obfs_password") or "")
        if obfs_type or obfs_password:
            outbound["obfs"] = {"type": obfs_type or "salamander", "password": obfs_password}
        ports = proxy.get("ports")
        if ports:
            outbound["server_ports"] = ports if isinstance(ports, list) else [str(ports)]
    else:
        raise ValueError(f"Clash 节点协议不支持：{typ or '未知'}")

    if typ in {"vmess", "vless", "trojan"}:
        transport = _clash_transport(proxy)
        if transport:
            outbound["transport"] = transport
    tls = _clash_tls(proxy, server, default=typ in {"trojan", "tuic", "hysteria2"})
    if tls:
        outbound["tls"] = tls
    return _node(name, outbound, source)


def parse_singbox_outbound(outbound: dict[str, Any], source: str) -> dict[str, Any]:
    typ = str(outbound.get("type") or "").lower()
    if typ not in SUPPORTED:
        raise ValueError(f"sing-box 出站协议不支持：{typ or '未知'}")
    name = str(outbound.get("tag") or typ.upper())
    return _node(name, outbound, source)


def _parse_structured(value: Any, source: str) -> tuple[list[dict[str, Any]], list[str]]:
    nodes: list[dict[str, Any]] = []
    errors: list[str] = []
    items: list[Any]
    mode = "auto"
    if isinstance(value, dict) and isinstance(value.get("proxies"), list):
        items = value["proxies"]
        mode = "clash"
    elif isinstance(value, dict) and isinstance(value.get("outbounds"), list):
        items = value["outbounds"]
        mode = "singbox"
    elif isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        items = [value]
    else:
        return nodes, errors
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        try:
            if mode == "clash":
                nodes.append(parse_clash_proxy(item, source))
            elif mode == "singbox":
                if str(item.get("type") or "").lower() in SUPPORTED:
                    nodes.append(parse_singbox_outbound(item, source))
            elif "server_port" in item or str(item.get("type") or "").lower() in SUPPORTED:
                nodes.append(parse_singbox_outbound(item, source))
            elif "server" in item and "port" in item:
                nodes.append(parse_clash_proxy(item, source))
        except Exception as exc:
            errors.append(f"第 {index} 个节点：{exc}")
    return nodes, errors


def fetch_subscription(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("订阅地址只支持 HTTP/HTTPS")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "iWAN-Gateway/1.1 sing-box",
            "Accept": "application/json, application/yaml, text/yaml, text/plain, */*",
            "Accept-Encoding": "gzip, identity",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(MAX_SUBSCRIPTION_BYTES + 1)
        if len(data) > MAX_SUBSCRIPTION_BYTES:
            raise ValueError("订阅内容超过 4 MB")
        if str(response.headers.get("Content-Encoding", "")).lower() == "gzip":
            data = gzip.decompress(data)
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            text = data.decode(charset)
        except Exception:
            text = data.decode("utf-8", errors="replace")
        return text, response.geturl()


def _looks_like_url(value: str) -> bool:
    parsed = urlsplit(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _maybe_decode_subscription(value: str) -> str | None:
    compact = re.sub(r"\s+", "", value)
    if len(compact) < 16 or not re.fullmatch(r"[A-Za-z0-9_+/=-]+", compact):
        return None
    try:
        decoded = _decode_b64(compact)
    except Exception:
        return None
    sample = decoded.lstrip()
    if "://" in decoded or sample.startswith(("{", "[", "proxies:", "outbounds:")):
        return decoded
    return None


def parse_import(value: str, source: str = "粘贴内容", depth: int = 0) -> tuple[list[dict[str, Any]], list[str]]:
    if depth > 3:
        raise ValueError("订阅嵌套层级过深")
    text = str(value or "").strip().lstrip("\ufeff")
    if not text:
        raise ValueError("请输入分享链接、订阅地址或配置内容")

    if "\n" not in text and "\r" not in text and _looks_like_url(text):
        body, final_url = fetch_subscription(text)
        return parse_import(body, final_url, depth + 1)

    errors: list[str] = []
    try:
        structured = json.loads(text)
        nodes, structured_errors = _parse_structured(structured, source)
        errors.extend(structured_errors)
        if nodes:
            return nodes, errors
    except Exception:
        pass

    if yaml is not None and ("proxies:" in text or "outbounds:" in text):
        try:
            structured = yaml.safe_load(text)
            nodes, structured_errors = _parse_structured(structured, source)
            errors.extend(structured_errors)
            if nodes:
                return nodes, errors
        except Exception as exc:
            errors.append(f"YAML 解析失败：{exc}")

    nodes: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if _looks_like_url(line):
                body, final_url = fetch_subscription(line)
                imported, nested_errors = parse_import(body, final_url, depth + 1)
                nodes.extend(imported)
                errors.extend(nested_errors)
            elif urlsplit(line).scheme.lower() in {"ss", "vmess", "vless", "trojan", "tuic", "hy2", "hysteria2"}:
                nodes.append(parse_uri(line, source))
        except Exception as exc:
            errors.append(f"第 {index} 行：{exc}")
    if nodes:
        return nodes, errors

    decoded = _maybe_decode_subscription(text)
    if decoded and decoded.strip() != text:
        return parse_import(decoded, source, depth + 1)

    message = "未识别到支持的节点"
    if errors:
        message += "；" + "；".join(errors[:5])
    raise ValueError(message)
