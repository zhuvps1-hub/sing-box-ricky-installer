#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "panel"))

from gateway.atomic import read_json, write_json
from gateway.audit import AuditLog
from gateway.autosave import AutosaveQueue
from gateway.helper import HelperOperations, OperationError
from gateway.locks import FileLock, LockTimeout
from gateway.protocol import receive_message, send_message
from gateway.redaction import redact, redact_text
from gateway.singbox import render


class Result:
    def __init__(self, code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr


class GatewayV71Tests(unittest.TestCase):
    def test_redaction(self) -> None:
        value = redact({"password": "secret", "nested": [{"access_token": "token"}], "server": "example.com"})
        self.assertEqual(value["password"], "***")
        self.assertEqual(value["nested"][0]["access_token"], "***")
        self.assertNotIn("secret", redact_text("password=secret authorization: Bearer abc"))
        self.assertNotIn("abc", redact_text("authorization: Bearer abc"))

    def test_canonical_render_preserves_password_and_unknown_fields(self) -> None:
        current = {
            "inbounds": [{
                "type": "iwan", "tag": "iwan-in", "address": "10.10.10.9/24",
                "users": [{"username": "old", "password": "secret"}], "custom_iwan": True,
            }],
            "outbounds": [
                {"type": "direct", "tag": "direct"},
                {"type": "shadowsocks", "tag": "old", "server": "old.example", "server_port": 443, "method": "aes-128-gcm", "password": "old-pass", "custom": 1},
            ],
            "route": {"rules": [{"protocol": "dns", "outbound": "direct"}], "final": "direct"},
            "experimental": {"cache_file": {"enabled": True}},
        }
        candidate, managed = render(current, {
            "nodes": [{"tag": "old", "server": "new.example", "server_port": 8443, "method": "aes-128-gcm", "password": ""}],
            "deleted_tags": [], "mappings": {"ai": "old"}, "default": "old",
            "iwan": {"address": "10.20.1.2/16", "username": "new", "password": ""},
        }, [])
        inbound = candidate["inbounds"][0]
        self.assertNotIn("address", inbound)
        self.assertEqual(inbound["address_pool"], "10.20.0.0/16")
        self.assertEqual(inbound["users"][0]["password"], "secret")
        self.assertTrue(inbound["custom_iwan"])
        node = next(item for item in candidate["outbounds"] if item.get("tag") == "old")
        self.assertEqual(node["password"], "old-pass")
        self.assertEqual(node["custom"], 1)
        self.assertEqual(candidate["experimental"]["cache_file"]["enabled"], True)
        self.assertEqual(candidate["route"]["final"], "old")
        self.assertEqual(managed[0]["outbound"], "old")

    def test_rejects_route_to_deleted_node(self) -> None:
        current = {
            "inbounds": [],
            "outbounds": [{"type": "direct", "tag": "direct"}, {"type": "shadowsocks", "tag": "sg", "server": "x", "server_port": 1, "method": "m", "password": "p"}],
            "route": {"rules": [], "final": "direct"},
        }
        with self.assertRaises(ValueError):
            render(current, {"nodes": [], "deleted_tags": ["sg"], "mappings": {"ai": "sg"}, "default": "direct", "iwan": {}}, [])

    def test_atomic_json_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "state.json"
            write_json(target, {"value": 1})
            self.assertEqual(read_json(target, {})["value"], 1)
            audit = AuditLog(root / "audit.jsonl", max_bytes=100, keep=2)
            audit.append("save", detail={"password": "secret"})
            events = audit.recent()
            self.assertEqual(events[-1]["detail"]["password"], "***")

    def test_lock_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock"
            first = FileLock(path).acquire()
            try:
                error: list[BaseException] = []
                def contender() -> None:
                    try:
                        FileLock(path, timeout=0.1).acquire()
                    except BaseException as exc:
                        error.append(exc)
                thread = threading.Thread(target=contender)
                thread.start(); thread.join()
                self.assertIsInstance(error[0], LockTimeout)
            finally:
                first.release()

    def test_protocol_roundtrip(self) -> None:
        left, right = socket.socketpair()
        try:
            send_message(left, {"operation": "ping", "payload": {}})
            self.assertEqual(receive_message(right)["operation"], "ping")
        finally:
            left.close(); right.close()

    def _operations(self, root: Path, runner) -> HelperOperations:
        config = root / "etc/sing-box/config.json"
        config.parent.mkdir(parents=True)
        config.write_text('{"inbounds":[],"outbounds":[{"type":"direct","tag":"direct"}],"route":{"rules":[],"final":"direct"}}\n')
        binary = root / "bin/sing-box"; binary.parent.mkdir(); binary.write_text("")
        return HelperOperations(
            singbox_config=config, singbox_binary=binary, singbox_backups=root / "backups/singbox",
            mosdns_config=root / "etc/mosdns/config.yaml", mosdns_backups=root / "backups/mosdns",
            lock_dir=root / "run/locks", audit=AuditLog(root / "audit.jsonl"), runner=runner,
        )

    def test_helper_transaction_success(self) -> None:
        def runner(command, **kwargs):
            return Result()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operations = self._operations(root, runner)
            candidate = {"inbounds": [], "outbounds": [{"type": "direct", "tag": "direct"}], "route": {"rules": [], "final": "direct"}, "log": {"level": "warn"}}
            result = operations.dispatch("singbox.apply", {"config": candidate}, actor="admin", remote="test")
            self.assertTrue(result["transaction_id"])
            self.assertEqual(json.loads(operations.singbox_config.read_text())["log"]["level"], "warn")
            self.assertTrue(list(operations.singbox_backups.glob("*.json")))

    def test_helper_check_failure_keeps_original(self) -> None:
        def runner(command, **kwargs):
            if len(command) > 1 and command[1] == "check":
                return Result(1, stderr="bad password=secret")
            return Result()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operations = self._operations(root, runner)
            original = operations.singbox_config.read_bytes()
            with self.assertRaises(OperationError) as raised:
                operations.dispatch("singbox.apply", {"config": {"invalid": True}}, actor="admin", remote="test")
            self.assertEqual(operations.singbox_config.read_bytes(), original)
            self.assertNotIn("secret", str(raised.exception))

    def test_autosave_deduplicates_and_persists(self) -> None:
        calls: list[dict] = []
        def runner(payload, actor, remote):
            calls.append(payload)
            return True, "ok"
        with tempfile.TemporaryDirectory() as directory:
            pending = Path(directory) / "pending.json"
            queue = AutosaveQueue(runner, pending, settle_seconds=0.01)
            first = queue.submit({"nodes": []}, "request_123456", "admin", "test")
            second = queue.submit({"nodes": [{"tag": "new"}]}, "request_123456", "admin", "test")
            self.assertEqual(first, second)
            deadline = time.time() + 2
            while queue.snapshot()["state"] not in {"succeeded", "failed"} and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(calls), 1)
            self.assertFalse(pending.exists())


if __name__ == "__main__":
    unittest.main()
