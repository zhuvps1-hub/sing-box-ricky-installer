"""Non-privileged application service coordinating domain renderers and helper."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import iwan_compat

from .atomic import read_json, write_json
from .client import HelperClient
from .locks import FileLock
from .redaction import redact
from .singbox import ROUTE_CATEGORIES, is_iwan, public_iwan, render


class GatewayApplication:
    def __init__(self, helper: HelperClient, data_dir: Path) -> None:
        self.helper = helper
        self.data_dir = data_dir
        self.managed_file = data_dir / "managed-rules.json"
        self.apply_lock = data_dir / "application.lock"

    @staticmethod
    def _context(actor: str, remote: str) -> dict[str, str]:
        return {"_actor": str(actor)[:128], "_remote": str(remote)[:128]}

    def load_config(self) -> dict[str, Any]:
        result = self.helper.call("singbox.read")
        config = result.get("config", {})
        if not isinstance(config, dict):
            raise ValueError("helper 返回的 sing-box 配置无效")
        return iwan_compat.migrate_config(config)

    def sample_config(self) -> dict[str, Any]:
        config = self.load_config()
        outbounds = config.get("outbounds", [])
        nodes: list[dict[str, Any]] = []
        if isinstance(outbounds, list):
            for item in outbounds:
                if not isinstance(item, dict) or item.get("type") != "shadowsocks":
                    continue
                public = {key: copy.deepcopy(item[key]) for key in ("type", "tag", "server", "server_port", "method", "plugin", "plugin_opts") if key in item}
                public["has_password"] = bool(item.get("password"))
                nodes.append(public)
        inbound = next((item for item in config.get("inbounds", []) if is_iwan(item)), {}) if isinstance(config.get("inbounds"), list) else {}
        route = config.get("route", {}) if isinstance(config.get("route"), dict) else {}
        managed = read_json(self.managed_file, [])
        mappings = {key: "" for key in ROUTE_CATEGORIES}
        if isinstance(managed, list):
            for rule in managed:
                if not isinstance(rule, dict):
                    continue
                domains = set(rule.get("domain_suffix", [])) if isinstance(rule.get("domain_suffix"), list) else set()
                for category, expected in {
                    "netflix": "netflix.com", "ai": "openai.com", "youtube": "youtube.com", "telegram": "telegram.org"
                }.items():
                    if expected in domains:
                        mappings[category] = str(rule.get("outbound", ""))
        return {
            "nodes": nodes,
            "iwan": public_iwan(inbound) if isinstance(inbound, dict) and inbound else {},
            "mappings": mappings,
            "default": str(route.get("final", "direct")),
            "raw_summary": {
                "inbounds": len(config.get("inbounds", [])) if isinstance(config.get("inbounds"), list) else 0,
                "outbounds": len(outbounds) if isinstance(outbounds, list) else 0,
                "rules": len(route.get("rules", [])) if isinstance(route.get("rules"), list) else 0,
            },
        }

    def apply(self, payload: dict[str, Any], *, actor: str, remote: str) -> tuple[bool, str]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with FileLock(self.apply_lock, timeout=45):
            current = self.load_config()
            previous = read_json(self.managed_file, [])
            if not isinstance(previous, list):
                previous = []
            candidate, generated = render(current, payload, previous)
            context = self._context(actor, remote)
            result = self.helper.call("singbox.apply", {"config": candidate, **context})
            write_json(self.managed_file, generated, 0o600)
            transaction = str(result.get("transaction_id", ""))[:12]
            backup = str(result.get("backup", ""))
            return True, f"配置已应用（事务 {transaction}，备份 {backup}）"

    def service_status(self) -> dict[str, bool]:
        return {key: bool(value) for key, value in self.helper.call("service.status").items()}

    def restart(self, service: str, *, actor: str, remote: str) -> str:
        result = self.helper.call("service.restart", {"service": service, **self._context(actor, remote)})
        return str(result.get("message", "已重启"))

    def backup_singbox(self, *, actor: str, remote: str) -> str:
        result = self.helper.call("singbox.backup", self._context(actor, remote))
        return str(result.get("backup", ""))

    def logs(self, service: str, lines: int = 200) -> str:
        return str(self.helper.call("logs.read", {"service": service, "lines": lines}).get("logs", ""))

    def network(self) -> dict[str, str]:
        result = self.helper.call("network.read")
        return {"routes": str(result.get("routes", "")), "ports": str(result.get("ports", ""))}

    def mosdns_state(self) -> dict[str, Any]:
        return redact(self.helper.call("mosdns.read"))

    def mosdns_apply(self, config: str, *, actor: str, remote: str) -> str:
        result = self.helper.call("mosdns.apply", {"config": config, **self._context(actor, remote)})
        return f"mosdns 已应用（事务 {str(result.get('transaction_id', ''))[:12]}）"

    def mosdns_action(self, action: str, name: str, *, actor: str, remote: str) -> str:
        context = self._context(actor, remote)
        if action == "restart":
            return self.restart("mosdns", actor=actor, remote=remote)
        if action == "backup":
            result = self.helper.call("mosdns.backup", context)
            return "已备份：" + str(result.get("backup", ""))
        if action == "restore":
            result = self.helper.call("mosdns.restore", {"name": name, **context})
            return f"已恢复（事务 {str(result.get('transaction_id', ''))[:12]}）"
        raise ValueError("mosdns 操作无效")

    def mosdns_file_read(self, name: str) -> dict[str, Any]:
        return self.helper.call("mosdns.file.read", {"name": name})

    def mosdns_file_save(self, name: str, content: str, *, actor: str, remote: str) -> str:
        result = self.helper.call("mosdns.file.save", {"name": name, "content": content, **self._context(actor, remote)})
        return "已保存 " + str(result.get("name", name))

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        events = self.helper.call("audit.recent", {"limit": limit}).get("events", [])
        return events if isinstance(events, list) else []


def default_application() -> GatewayApplication:
    socket_path = Path(os.environ.get("IWAN_HELPER_SOCKET", "/run/iwan-gateway/helper.sock"))
    data_dir = Path(os.environ.get("IWAN_PANEL_DATA_DIR", "/var/lib/iwan-gateway"))
    return GatewayApplication(HelperClient(socket_path), data_dir)
