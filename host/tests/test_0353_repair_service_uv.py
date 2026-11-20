"""A website repair must also repair uv discovery after the bootstrap exits."""
import _hermetic
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentj import auto_update, service, service_path, update, upgrade_uv


class RepairServiceUv(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.root = self.home / '.local/share/agentj/upgrade-bin'
        self.root.mkdir(parents=True, mode=0o700)
        self.seed = self.root / 'uv'
        self.seed.write_bytes(b'#!/bin/sh\necho verified\n')
        self.seed.chmod(0o700)
        self.bad = self.home / '.local/share/mise/shims'
        self.bad.mkdir(parents=True)
        (self.bad/'uv').write_bytes(b'#!/bin/sh\nexit 1\n')
        (self.bad/'uv').chmod(0o755)
        self.enterContext(patch.dict(os.environ, {'HOME':str(self.home), 'XDG_CONFIG_HOME':str(self.home/'cfg'),
                                                 'PATH':str(self.bad)+':/usr/bin:/bin', 'AGENTJ_SERVICE_NAME':'repair-test'}, clear=True))
        self.enterContext(patch.object(service_path, 'login_path', return_value=str(self.bad)+':'+str(self.root)))
        self.enterContext(patch.dict(upgrade_uv.PINS, {upgrade_uv.target_platform():('',hashlib.sha256(self.seed.read_bytes()).hexdigest())}))

    def test_actual_registrations_and_worker_use_same_private_executable(self):
        (self.root/'git').write_bytes(self.seed.read_bytes())
        (self.root/'git').chmod(0o700)
        result = subprocess.CompletedProcess([], 0, '', '')
        state = self.home/'state'
        state.mkdir()
        for plat in ('linux', 'macos'):
            with self.subTest(platform=plat), patch.object(service,'platform',return_value=plat), \
                 patch.object(service,'_systemctl',return_value=result), patch.object(service,'_bootstrap',return_value=result), \
                 patch.object(service,'launchd_disabled',return_value=False), patch.object(service,'remove_legacy'), \
                 patch.object(service,'_linger',return_value='yes'), patch.object(service,'_register_pair_protocol'):
                service.install(SimpleNamespace(root=state))
                path = service.installed_environment('repair-test')['PATH']
                selected = shutil.which('uv',path=path)
                self.assertNotEqual(selected,str(self.bad/'uv'))
                self.assertEqual(subprocess.check_output([selected]).strip(), b'verified')
                self.assertNotIn(str(self.root),path.split(':'))
                self.assertEqual(update.installer_binary('uv'), selected)
                self.assertEqual(auto_update.install_commands({'kind':'uv','where':str(self.home/'tool')},'signed.whl')[0][0],selected)

    def test_tainted_copy_is_not_rediscovered_through_saved_path(self):
        good = Path(update.installer_binary('uv'))
        self.assertNotEqual(good, self.bad/'uv')
        good.chmod(0o700)
        good.write_bytes(b'broken')
        with patch.dict(os.environ, {'PATH': str(good.parent)+':'+str(self.bad)}):
            self.assertEqual(update.installer_binary('uv'),str(self.bad/'uv'))
        self.assertNotIn(str(good.parent), service.service_env()[0]['PATH'].split(':'))

    def test_symlink_seed_is_rejected(self):
        renamed = self.seed.rename(self.root/'old')
        self.seed.symlink_to(renamed)
        path = service.service_env()[0]['PATH']
        self.assertEqual(shutil.which('uv',path=path),str(self.bad/'uv'))
        self.assertNotIn(str(self.root),path.split(':'))
