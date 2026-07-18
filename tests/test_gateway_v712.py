from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GatewayV712Tests(unittest.TestCase):
    def test_release_uses_one_frontend_generation(self) -> None:
        source = (ROOT / "tools/build_release_manifest.py").read_text(encoding="utf-8")
        self.assertIn('("panel/web/index.html", "web/index.html"', source)
        self.assertIn('("panel/web/app.css", "web/app.css"', source)
        self.assertIn('("panel/web/app.js", "web/app.js"', source)
        self.assertIn('("panel/web/remember.js", "web/remember.js"', source)
        self.assertNotIn("panel/v62/web", source)
        self.assertNotIn("panel/v63/web", source)

    def test_public_installer_repairs_data_permissions(self) -> None:
        source = (ROOT / "install-panel-v712.sh").read_text(encoding="utf-8")
        self.assertIn('PANEL_BIND:-0.0.0.0', source)
        self.assertIn('IWAN_ALLOW_PUBLIC_BIND:-1', source)
        self.assertIn('chown -R "$PANEL_USER:$PANEL_GROUP" "$DATA_DIR"', source)
        self.assertIn('ufw allow "${PANEL_PORT}/tcp"', source)

    def test_remember_token_survives_process_memory_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth_file = root / "auth.json"
            auth_file.write_text(json.dumps({"username": "admin", "password": {"hash": "one"}}), encoding="utf-8")

            core = types.ModuleType("core")
            core.AUTH_FILE = auth_file
            core.WEB_DIR = root
            core.ensure_dirs = lambda: root.mkdir(parents=True, exist_ok=True)
            core.read_json = lambda path, default: json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else default
            core.AUTH = types.SimpleNamespace(login=lambda *args: (True, "session", "csrf"), logout=lambda token: None)

            runtime = types.ModuleType("gateway.runtime")
            runtime.Handler = type("Handler", (), {})
            runtime.ASSETS = {}
            runtime._secure_cookie = lambda handler: False
            runtime.serve = lambda host, port: None

            package = types.ModuleType("gateway")
            package.__path__ = [str(ROOT / "panel/gateway")]

            old_modules = {name: sys.modules.get(name) for name in ("core", "gateway", "gateway.runtime", "gateway.runtime_v712")}
            sys.modules["core"] = core
            sys.modules["gateway"] = package
            sys.modules["gateway.runtime"] = runtime
            try:
                spec = importlib.util.spec_from_file_location("gateway.runtime_v712", ROOT / "panel/gateway/runtime_v712.py")
                module = importlib.util.module_from_spec(spec)
                sys.modules["gateway.runtime_v712"] = module
                assert spec.loader is not None
                spec.loader.exec_module(module)
                module.CONFIG_DIR = root
                module.REMEMBER_KEY = root / "remember.key"

                token, csrf = module._create_remember_token("admin")
                session = module._verify_remember_token(token)
                self.assertEqual(session["username"], "admin")
                self.assertEqual(session["csrf"], csrf)
                self.assertTrue(session["remembered"])
                self.assertIsNone(module._verify_remember_token(token + "x"))

                auth_file.write_text(json.dumps({"username": "admin", "password": {"hash": "two"}}), encoding="utf-8")
                self.assertIsNone(module._verify_remember_token(token))
            finally:
                for name, value in old_modules.items():
                    if value is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = value

    def test_frontend_never_stores_plain_password(self) -> None:
        source = (ROOT / "panel/web/remember.js").read_text(encoding="utf-8")
        self.assertIn("remember: remember.checked", source)
        self.assertNotIn("setItem('password'", source)
        self.assertNotIn('setItem("password"', source)


if __name__ == "__main__":
    unittest.main()
