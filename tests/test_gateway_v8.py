#!/usr/bin/env python3
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "panel"
if str(PANEL) not in sys.path:
    sys.path.insert(0, str(PANEL))


class GatewayV8Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        os.environ["IWAN_PANEL_CONFIG_DIR"] = cls.temp.name
        os.environ["IWAN_PANEL_DATA_DIR"] = cls.temp.name
        global core, runtime
        core = importlib.import_module("app")
        sys.modules["core"] = core
        runtime = importlib.import_module("gateway.runtime_v8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_modern_rules_include_sniff_and_route_action(self) -> None:
        rules = runtime.managed_rules({"ai": "hk", "netflix": "jp", "youtube": "", "telegram": "", "default": ""})
        self.assertEqual(rules[0], {"action": "sniff"})
        route_rules = [item for item in rules if item.get("action") == "route"]
        self.assertEqual({item["outbound"] for item in route_rules}, {"hk", "jp"})
        self.assertTrue(all("domain_suffix" in item for item in route_rules))

    def test_cookie_has_max_age_and_expires(self) -> None:
        line = runtime._cookie_line("iwan_remember", "token", 3600, False)
        self.assertIn("Max-Age=3600", line)
        self.assertIn("Expires=", line)
        self.assertIn("HttpOnly", line)
        self.assertIn("SameSite=Lax", line)

    def test_remember_key_uses_writable_data_directory(self) -> None:
        self.assertEqual(runtime.legacy.REMEMBER_KEY.parent, Path(self.temp.name))

    def test_v8_assets_are_registered(self) -> None:
        self.assertIn("/assets/v8.css", runtime.base.ASSETS)
        self.assertIn("/assets/v8.js", runtime.base.ASSETS)


if __name__ == "__main__":
    unittest.main()
