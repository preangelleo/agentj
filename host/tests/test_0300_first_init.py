"""Real first-init CLI plus one-use offline setup receipt negative controls."""
import _hermetic
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agentj import preferences, working_root
from agentj.state import State


class FirstInit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root/'home'; self.home.mkdir()
        self.env = {**os.environ, 'HOME':str(self.home), 'XDG_CONFIG_HOME':str(self.root/'config'),
                    'XDG_STATE_HOME':str(self.root/'xdg-state'), 'AGENTJ_STATE_DIR':str(self.root/'state'),
                    'PATH':str(self.root/'empty-bin'),
                    'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
        self.patch = patch.dict(os.environ, self.env); self.patch.start(); self.addCleanup(self.patch.stop)
        self.work = self.root/'work'

    def cli(self, root, force=False):
        return subprocess.run([sys.executable, '-c', 'from agentj.cli import main; main()',
                               'init', '--relay','ws://127.0.0.1:1','--working-root',str(root),
                               *(['--force'] if force else [])], env=self.env, capture_output=True, text=True, timeout=12)

    def fresh(self):
        st = State(); st.init(_setup_root=self.work)
        preferences.ensure()
        return st

    def test_actual_cli_fresh_success_then_second_root_requires_uv(self):
        result = self.cli(self.work)
        self.assertEqual(result.returncode, 0, result.stderr)
        st = State()
        self.assertEqual(preferences.get(preferences.effective(st), 'agent.working_root'),str(self.work))
        self.assertNotIn('agent', st.config())
        original = preferences.path().read_bytes()
        second = self.cli(self.root/'other')
        self.assertNotEqual(second.returncode, 0)
        self.assertIn('fresh paired-device UV',second.stderr)
        self.assertEqual(preferences.path().read_bytes(), original)

    def test_bound_root_state_identity_and_added_history_fail_once(self):
        for mutation in ('root','state','identity','removed','revoke','estop','history','device','directory','symlink'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(dir=self.root) as d:
                p = Path(d)
                with patch.dict(os.environ, {'AGENTJ_STATE_DIR':str(p/'state'),'XDG_CONFIG_HOME':str(p/'config')}):
                    st = State(); selected = p/'work'; st.init(_setup_root=selected); preferences.ensure()
                    setup = st._initial_root_setup
                    target = p/'wrong' if mutation == 'root' else selected
                    if mutation == 'state':
                        wrong = State(st.root)
                        wrong._initial_root_setup = setup
                        st = wrong
                    elif mutation == 'identity':
                        cfg = st.config(); cfg['channel'] = 'changed'; st.write_private(st.config_path,json.dumps(cfg).encode())
                    elif mutation in ('removed','revoke','estop','history'):
                        name = {'removed':'removed.json','revoke':'credential_revocations.json',
                                'estop':'estop.json','history':'history.json'}[mutation]
                        st.write_private(st.root/name,b'{}')
                    elif mutation == 'device': st.add_device(os.urandom(32),'fixture')
                    elif mutation == 'directory': (st.root/'added').mkdir()
                    elif mutation == 'symlink': (st.root/'added').symlink_to(p/'outside')
                    original = preferences.path().read_bytes()
                    with self.assertRaises(preferences.ConfigError): working_root.record(st,target)
                    self.assertTrue(setup.used)
                    self.assertIsNone(st._initial_root_setup)
                    self.assertEqual(preferences.path().read_bytes(),original)
                    with self.assertRaises(preferences.ConfigError): working_root.record(st,selected)

    def test_success_receipt_cannot_replay_or_change_other_preferences(self):
        st = self.fresh(); setup = st._initial_root_setup
        working_root.record(st,self.work)
        self.assertTrue(setup.used)
        st._initial_root_setup = setup
        with self.assertRaises(preferences.ConfigError): working_root.record(st,self.root/'other')
        # A fresh identity is not permission to co-write unrelated preferences.
        with tempfile.TemporaryDirectory(dir=self.root) as d, patch.dict(os.environ, {'AGENTJ_STATE_DIR':d+'/s','XDG_CONFIG_HOME':d+'/c'}):
            st = State(); st.init(_setup_root=self.work); preferences.ensure()
            raw,_ = preferences.read()
            raw = preferences.edit(preferences.edit(raw,'agent.working_root',str(self.work)),'appearance.language','en')
            with self.assertRaises(preferences.ConfigError): preferences.transact(st,raw,_working_root_record=True)
            self.assertIsNone(st._initial_root_setup)

    def test_partial_history_and_force_never_mint_setup(self):
        for kind in ('partial','force','paired_force'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.root) as d, patch.dict(os.environ, {'AGENTJ_STATE_DIR':d+'/s','XDG_CONFIG_HOME':d+'/c'}):
                st = State()
                if kind == 'partial':
                    st.root.mkdir(); st.write_private(st.root/'removed.json',b'{}')
                elif kind == 'paired_force':
                    st.init(); st.add_device(os.urandom(32),'fixture')
                st.init(force=kind != 'partial', _setup_root=self.work)
                self.assertIsNone(st._initial_root_setup)
                preferences.ensure()
                original = preferences.path().read_bytes()
                with self.assertRaises(preferences.ConfigError): working_root.record(st,self.work)
                self.assertEqual(preferences.path().read_bytes(),original)

    def test_receipt_revalidated_at_final_commit_after_ctl_and_runtime(self):
        for boundary in ('ctl_removed','ctl_device','runtime_history','runtime_identity','runtime_stop'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory(dir=self.root) as d, patch.dict(os.environ, {'AGENTJ_STATE_DIR':d+'/s','XDG_CONFIG_HOME':d+'/c'}):
                st = State(); st.init(_setup_root=self.work); preferences.ensure()
                original = preferences.path().read_bytes()
                def mutate(*args, **kwargs):
                    if boundary == 'ctl_device': st.add_device(os.urandom(32),'fixture')
                    elif boundary == 'runtime_identity':
                        cfg=st.config(); cfg['channel']='changed'; st.write_private(st.config_path,json.dumps(cfg).encode())
                    else:
                        name = {'ctl_removed':'removed.json','runtime_history':'history.json','runtime_stop':'estop.json'}[boundary]
                        st.write_private(st.root/name,b'{}')
                    return None
                target = 'agentj.names.ctl_call' if boundary.startswith('ctl_') else 'agentj.voice.validate_runtime'
                with patch(target,side_effect=mutate):
                    with self.assertRaises(preferences.ConfigError): working_root.record(st,self.work)
                self.assertEqual(preferences.path().read_bytes(),original)
                self.assertIsNone(st._initial_root_setup)

    def test_wire_like_bool_or_dictionary_is_not_setup_capability(self):
        st = self.fresh()
        for forged in (True, {'root':str(self.work),'fresh':True}):
            st._initial_root_setup = forged
            with self.assertRaises(preferences.ConfigError): working_root.record(st,self.work)
            self.assertIsNone(st._initial_root_setup)
