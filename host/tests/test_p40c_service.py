"""B11 regression: saved owner paths, upgrade drift, credential store hints."""
import _hermetic
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch, Mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import service, harness, doctor

class ServiceOverrides(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p40c-service-", dir="/var/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home/"cfg"), "PATH": str(self.home/"bin")}
        self.patch = patch.dict(os.environ, self.env, clear=True)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.plat = patch.object(service, "platform", return_value="linux")
        self.plat.start(); self.addCleanup(self.plat.stop)
    def exe(self, name):
        p = self.home/name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/bin/sh\nexit 0\n"); p.chmod(0o755); return str(p)
    def save(self, text):
        p = Path(service.env_file("test")); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text); return p
    def test_upgrade_preserves_existing_owner_env_not_shell_or_path(self):
        owner = self.exe("mise/1.18.34/opencode"); newer = self.exe("bin/opencode")
        p = self.save('AGENTJ_OPENCODE_BIN="'+owner+'"\nUNKNOWN=preserve-this\n')
        before = p.read_bytes()
        Path(service.unit_dir(), "test.service").write_text('Environment="AGENTJ_OPENCODE_BIN='+newer+'"\n')
        os.environ["AGENTJ_OPENCODE_BIN"] = newer
        env, notes = service.prepare_binary_env("test")
        self.assertEqual(service.binary_env(str(p))["AGENTJ_OPENCODE_BIN"], owner)
        self.assertEqual(p.read_bytes(), before)
        self.assertNotIn("AGENTJ_OPENCODE_BIN", env)
        unit = service.unit_text("test", ["/bin/true"], env)
        self.assertNotIn("Environment=\"AGENTJ_OPENCODE_BIN", unit)
        self.assertTrue(any("selection changed" in n for n in notes))
        self.assertIn("AGENTJ_OPENCODE_BIN", service.binary_env_mismatches("test"))
        res=doctor.check_service({"name":"test", "installed":True, "active":"active", "kind":"systemd"})
        self.assertEqual(res["status"], "fail")
        Path(service.unit_dir(), "test.service").write_text(unit)
        self.assertEqual(service.binary_env_mismatches("test"), [])
        service.prepare_binary_env("test")
        self.assertEqual(p.read_bytes(), before)
    def test_missing_paths_appended_without_touching_other_bytes(self):
        oc=self.exe("bin/opencode"); self.exe("bin/claude")
        p=self.save("# owner\nUNRELATED=keep-exactly")
        env,notes=service.prepare_binary_env("test")
        self.assertTrue(p.read_bytes().startswith(b"# owner\nUNRELATED=keep-exactly\n"))
        self.assertEqual(service.binary_env(str(p))["AGENTJ_OPENCODE_BIN"],oc)
        self.assertEqual(p.stat().st_mode&0o777,0o600)
        self.assertTrue(any("first PATH" in n for n in notes))
    def test_launchd_path_file_loaded_without_plist_binary_assignments(self):
        oc=self.exe("bin/opencode")
        with patch.object(service,"platform",return_value="macos"):
            env,notes=service.prepare_binary_env("test")
            data=plistlib.loads(service.plist_bytes("test",["/bin/true"],env,"/tmp/log"))
            self.assertNotIn("AGENTJ_OPENCODE_BIN",data["EnvironmentVariables"])
            os.environ.update(env);service.load_launch_binary_env()
            self.assertEqual(os.environ["AGENTJ_OPENCODE_BIN"],oc)
    def test_credential_v1_v2_existence_only_never_opened(self):
        data=self.home/"data";root=data/"opencode";root.mkdir(parents=True)
        os.environ["XDG_DATA_HOME"]=str(data)
        (root/"auth.json").write_text("not-a-key")
        self.assertEqual(harness.opencode_login("2.0.22")[0],"warn")
        (root/"opencode.db").write_bytes(b"not-a-db")
        with patch.object(Path,"read_text",side_effect=AssertionError("must not read")),patch.object(Path,"read_bytes",side_effect=AssertionError("must not read")):
            self.assertIn("v2 SQLite",harness.opencode_login("2.0.22")[1])
            self.assertIn("v1 credential",harness.opencode_login("1.18.34")[1])
        self.assertIn("unverified",harness.opencode_login("2.0.22")[1])
    def test_restart_named_service_only(self):
        with patch.object(service,"name",return_value="test"),patch.object(service,"_systemctl") as ctl:
            service.restart();ctl.assert_called_once_with("restart","test.service",check=True)

    def test_doctor_selects_saved_service_binary_not_its_shell_path(self):
        owner=self.exe("mise/1.18.34/opencode");newer=self.exe("bin/opencode")
        self.save('AGENTJ_OPENCODE_BIN="'+owner+'"\n')
        e=service.effective_binary_environment({'installed':True,'name':'test'})
        from agentj.binaries import resolve
        self.assertEqual(resolve('opencode',e)['path'],owner)
        self.assertNotEqual(resolve('opencode',e)['path'],newer)
    def test_selection_changes_reported_after_saved_override_is_edited(self):
        first=self.exe("v1/opencode");second=self.exe("v2/opencode")
        self.save('AGENTJ_OPENCODE_BIN="'+second+'"\n')
        _,notes=service.prepare_binary_env('test', previous={'AGENTJ_OPENCODE_BIN':first})
        self.assertTrue(any('selection changed' in n and service.tilde(first) in n and service.tilde(second) in n for n in notes))
