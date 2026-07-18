#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("iwan_compat", ROOT / "panel" / "iwan_compat.py")
assert SPEC and SPEC.loader
compat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compat)


class IwanCompatibilityTests(unittest.TestCase):
    def test_migrate_config_replaces_legacy_address(self) -> None:
        source = {
            "inbounds": [
                {
                    "type": "iwan",
                    "tag": "iwan-in",
                    "listen": "::",
                    "listen_port": 8000,
                    "address": "10.10.10.1/24",
                    "mtu": 1400,
                }
            ]
        }
        migrated = compat.migrate_config(source)
        inbound = migrated["inbounds"][0]
        self.assertNotIn("address", inbound)
        self.assertEqual(inbound["address_pool"], "10.10.10.1/24")
        self.assertIn("address", source["inbounds"][0], "migration must not mutate the caller")

    def test_normalize_payload_emits_only_address_pool(self) -> None:
        payload = {
            "iwan": {
                "listen": "::",
                "listen_port": "8100",
                "address": "10.20.30.9/24",
                "mtu": "1420",
                "username": "alice",
                "password": "secret-password",
                "unexpected": "must-not-reach-sing-box",
            }
        }
        normalized = compat.normalize_payload(payload)
        patch = normalized["iwan"]
        self.assertNotIn("address", patch)
        self.assertNotIn("unexpected", patch)
        self.assertEqual(patch["address_pool"], "10.20.30.0/24")
        self.assertEqual(patch["listen_port"], 8100)
        self.assertEqual(patch["mtu"], 1420)

    def test_canonical_field_wins_over_legacy_alias(self) -> None:
        patch = compat.normalize_iwan_patch({
            "address_pool": "10.30.0.0/16",
            "address": "10.99.0.0/16",
        })
        self.assertEqual(patch["address_pool"], "10.30.0.0/16")
        self.assertNotIn("address", patch)

    def test_one_item_legacy_list_is_accepted(self) -> None:
        patch = compat.normalize_iwan_patch({"address": ["fd00:10::1/64"]})
        self.assertEqual(patch["address_pool"], "fd00:10::/64")

    def test_multiple_pools_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "只能填写一个"):
            compat.normalize_iwan_patch({"address_pool": ["10.0.0.0/24", "10.1.0.0/24"]})

    def test_invalid_limits_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "监听端口"):
            compat.normalize_iwan_patch({"listen_port": 70000})
        with self.assertRaisesRegex(ValueError, "MTU"):
            compat.normalize_iwan_patch({"mtu": 100})


if __name__ == "__main__":
    unittest.main(verbosity=2)
