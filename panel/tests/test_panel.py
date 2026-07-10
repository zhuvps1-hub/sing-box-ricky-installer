import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        os.environ['IWAN_PANEL_CONFIG_DIR'] = str(root / 'config')
        os.environ['IWAN_PANEL_DATA_DIR'] = str(root / 'data')
        os.environ['SINGBOX_BACKUP_DIR'] = str(root / 'backups')
        os.environ['SINGBOX_CONFIG'] = str(root / 'config.json')
        spec = importlib.util.spec_from_file_location('iwan_panel', Path(__file__).resolve().parents[1] / 'app.py')
        cls.panel = importlib.util.module_from_spec(spec)
        assert spec.loader
        sys.modules[spec.name] = cls.panel
        spec.loader.exec_module(cls.panel)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_parse_ss_link(self):
        node = self.panel.parse_ss_link('ss://YWVzLTEyOC1nY206cGFzcw@example.com:8388#sg')
        self.assertEqual(node['tag'], 'sg')
        self.assertEqual(node['server'], 'example.com')
        self.assertEqual(node['server_port'], 8388)

    def test_import_json_outbounds(self):
        text = '{"outbounds":[{"type":"direct","tag":"direct"},{"type":"shadowsocks","tag":"jp","server":"jp.example.com","server_port":443,"method":"aes-256-gcm","password":"secret"}]}'
        nodes, errors = self.panel.parse_import_text(text)
        self.assertFalse(errors)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]['tag'], 'jp')

    def test_public_iwan_redacts_password(self):
        result = self.panel.public_iwan({'type':'iwan','users':[{'username':'alice','password':'secret'}], 'listen_port':8000})
        self.assertEqual(result['username'], 'alice')
        self.assertTrue(result['has_password'])
        self.assertNotIn('password', result)
        self.assertNotIn('users', result)

    def test_patch_iwan_preserves_blank_password(self):
        inbound = {'users':[{'username':'old','password':'secret'}]}
        self.panel.patch_iwan_credentials(inbound, 'new', '')
        self.assertEqual(inbound['users'][0]['username'], 'new')
        self.assertEqual(inbound['users'][0]['password'], 'secret')

    def test_patch_flat_credentials(self):
        inbound = {'username':'old','password':'secret'}
        self.panel.patch_iwan_credentials(inbound, 'new', 'updated')
        self.assertEqual(inbound['username'], 'new')
        self.assertEqual(inbound['password'], 'updated')


if __name__ == '__main__':
    unittest.main()
