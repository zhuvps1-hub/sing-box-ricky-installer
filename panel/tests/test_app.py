import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("IWAN_PANEL_CONFIG_DIR", tempfile.mkdtemp(prefix="iwan-etc-"))
os.environ.setdefault("IWAN_PANEL_DATA_DIR", tempfile.mkdtemp(prefix="iwan-var-"))
os.environ.setdefault("SINGBOX_CONFIG", str(Path(tempfile.mkdtemp(prefix="iwan-sing-")) / "config.json"))
import sys
sys.path.insert(0, str(ROOT))
import app


class PanelTests(unittest.TestCase):
    def setUp(self):
        app.ensure_dirs()
        Path(os.environ["SINGBOX_CONFIG"]).write_text(json.dumps({
            "inbounds": [{"type": "iwan", "tag": "iwan-in", "listen": "0.0.0.0", "listen_port": 8000, "address_pool": "10.10.10.0/24"}],
            "outbounds": [{"type": "direct", "tag": "direct"}, {"type": "shadowsocks", "tag": "sg", "server": "sg.example.com", "server_port": 8388, "method": "aes-128-gcm", "password": "secret"}],
            "route": {"rules": [{"domain_suffix": ["netflix.com"], "outbound": "sg"}], "final": "sg"}
        }), encoding="utf-8")

    def test_sample_config(self):
        data = app.sample_config()
        self.assertEqual(data["iwan"]["listen_port"], 8000)
        self.assertEqual(data["nodes"][0]["tag"], "sg")
        self.assertEqual(data["mappings"]["netflix"], "sg")

    def test_auth(self):
        app.AUTH.initialize("admin", "password123")
        ok, token, csrf = app.AUTH.login("127.0.0.1", "admin", "password123")
        self.assertTrue(ok)
        self.assertTrue(token and csrf)
        self.assertIsNotNone(app.AUTH.session(token))

    def test_ss_parse(self):
        node = app.parse_ss_link("ss://YWVzLTEyOC1nY206cGFzcw@example.com:8388#test")
        self.assertEqual(node["tag"], "test")
        self.assertEqual(node["server_port"], 8388)

    @mock.patch.object(app, "service_restart", return_value=(True, ""))
    @mock.patch.object(app, "service_active", return_value=True)
    @mock.patch.object(app, "run", return_value=(0, ""))
    def test_apply_config(self, *_):
        ok, message = app.apply_config({
            "nodes": [{"tag": "sg", "server": "sg2.example.com", "server_port": 443, "method": "aes-128-gcm", "password": ""}],
            "mappings": {"netflix": "sg", "ai": "sg", "youtube": "sg", "telegram": "sg"},
            "default": "sg", "iwan": {"listen_port": 9000}, "deleted_tags": []
        })
        self.assertTrue(ok, message)
        config = json.loads(Path(os.environ["SINGBOX_CONFIG"]).read_text())
        self.assertEqual(config["inbounds"][0]["listen_port"], 9000)
        self.assertEqual(config["outbounds"][1]["server"], "sg2.example.com")
        self.assertEqual(config["outbounds"][1]["password"], "secret")

    @mock.patch.object(app, "service_restart", return_value=(True, ""))
    @mock.patch.object(app, "service_active", return_value=True)
    @mock.patch.object(app, "run", return_value=(0, ""))
    def test_apply_config_creates_iwan_for_public_first_run(self, *_):
        Path(os.environ["SINGBOX_CONFIG"]).write_text(json.dumps({
            "inbounds": [],
            "outbounds": [{"type": "direct", "tag": "direct"}],
            "route": {"rules": [], "final": "direct"}
        }), encoding="utf-8")
        ok, message = app.apply_config({
            "nodes": [],
            "mappings": {},
            "default": "direct",
            "iwan": {"listen_port": 8100, "username": "alice", "password": "password123"},
            "deleted_tags": []
        })
        self.assertTrue(ok, message)
        config = json.loads(Path(os.environ["SINGBOX_CONFIG"]).read_text())
        self.assertEqual(config["inbounds"][0]["type"], "iwan")
        self.assertEqual(config["inbounds"][0]["listen_port"], 8100)
        self.assertEqual(config["inbounds"][0]["users"][0]["username"], "alice")


if __name__ == "__main__":
    unittest.main()
