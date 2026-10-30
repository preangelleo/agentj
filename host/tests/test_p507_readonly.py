"""P507 audit L1: closed, masked read commands retain real device signatures."""
import _hermetic  # noqa: F401
from unittest import mock
from agentj import cmdrun, controls, passkey, wire
from test_p361_cmdrun import Base, sign
from test_l1 import Phone
from test_p57_passkey import Authenticator, pk_record

READS = cmdrun.READ_CMDS
PROTECTED = ('status', 'service_status', 'rescue_status', 'update_check', 'reconnect')


class ReadCommands(Base):
    def test_each_closed_read_runs_without_a_passkey_or_uv(self):
        self.assertIsNone(self.st.passkey_of_device(self.phone.did))
        for name in READS:
            with self.subTest(name=name), mock.patch.object(passkey, 'verify_assertion', wraps=passkey.verify_assertion) as verify:
                before = len(self.calls)
                result = self.send(sign(self.phone, name), verified=False)
                self.assertTrue(result[-1]['ok'], result[-1])
                self.assertEqual(len(self.calls), before + 1)
                verify.assert_not_called()
                self.assertEqual(self.calls[-1][0][-len(cmdrun.RUN_CMDS[name][0]):], list(cmdrun.RUN_CMDS[name][0]))

    def test_read_still_requires_exact_signature_freshness_and_single_use(self):
        other = Phone(self.st)
        obj = sign(self.phone, 'doctor')
        self.assertTrue(self.send(obj, verified=False)[-1]['ok'])
        count = len(self.calls)
        cases = [(obj, 'replay'), ({**sign(self.phone, 'doctor'), 'sig': ''}, 'bad_signature'),
                 (sign(self.phone, 'doctor', key=other.sk), 'bad_signature'),
                 (sign(self.phone, 'doctor', ts=1), 'stale'),
                 ({**sign(self.phone, 'doctor'), 'n': 'a'*32}, 'bad_signature'),
                 (sign(self.phone, 'doctor --reconnect'), 'shape'),
                 (sign(self.phone, 'unknown'), 'shape')]
        for request, why in cases:
            with self.subTest(why=why):
                result = self.send(request, verified=False)
                self.assertFalse(result[-1]['ok'])
                self.assertEqual(result[-1]['why'], why)
                self.assertEqual(len(self.calls), count)

    def test_reconnect_is_a_signed_button(self):
        # P573 (#386 ⑤): reconnect / status / an AI switch from Settings are recoverable — the signed control is the
        # owner's button; no Face ID (was P507: UV for anything beyond the zero-write doctor). Replay is still refused.
        self.assertIsNone(self.st.passkey_of_device(self.phone.did))
        for name in PROTECTED:
            with self.subTest(name=name):
                self.calls.clear()
                request = sign(self.phone, name)
                self.assertTrue(self.send(request, verified=False)[-1]['ok'])
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.send(request, verified=False)[-1]['why'], 'replay')
                self.assertEqual(len(self.calls), 1)



class ActualDoctor(__import__('unittest').TestCase):
    def assert_cli_readonly(self, name, layout='explicit'):
        import hashlib, json, os, pathlib, subprocess, sys, tempfile, time
        from agentj import shared_hook, update
        from agentj.state import State
        with tempfile.TemporaryDirectory() as d:
            home = pathlib.Path(d)
            env = {**os.environ, 'HOME':d, 'XDG_CONFIG_HOME':str(home/'config'),
                   'AGENTJ_STATE_DIR':str(home/'state'), 'PYTHONDONTWRITEBYTECODE':'1',
                   'PYTHONPATH':str(pathlib.Path(__file__).resolve().parents[1])}
            with mock.patch.dict(os.environ, env):
                st = State(); st.init()
                work = home/'work'; work.mkdir()
                st.set_agent_config('claude', str(work))
                from agentj import preferences
                preferences.path().write_text(json.dumps({'agent':{'session_mode':'shared','working_root':str(work)}}))
                shared_hook.channel_path(st.root,'claude').touch()
                (st.root/update.UPGRADED).write_text(json.dumps({'to':__import__('agentj').__version__, 'at':time.time()}))
            # Seed exactly the old defaults that migrate.auto rewrites on CLI startup.
            cfg=json.loads(st.config_path.read_text());cfg.update(relay='wss://alpha-relay.agentjarvis.net', web='https://alpha-web.agentjarvis.net', api='https://api.agentjarvis.net', app='https://alpha-app.agentjarvis.net')
            st.config_path.write_text(json.dumps(cfg))
            st.cloud_path.write_text(json.dumps({'api':'https://api.agentjarvis.net'}))
            st.migrated_path.write_text(json.dumps({'v':1,'fixture':'unchanged'}))
            units=home/'.config/systemd/user';units.mkdir(parents=True)
            (units/'agentj.service').write_text('[Service]\nExecStart=/fixture/agentj serve\n')
            if layout != 'explicit':
                dest=home/'.local/state'/('agentjarvis-alpha' if layout == 'legacy' else 'agentj')
                dest.parent.mkdir(parents=True);st.root.rename(dest)
                for key in ('AGENTJ_STATE_DIR','AGENTJARVIS_STATE_DIR'):env.pop(key,None)
            def snapshot():
                return {str(p.relative_to(home)):(hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode, p.stat().st_mtime_ns)
                        for p in home.rglob('*') if p.is_file()}
            before = snapshot()
            # The real CLI runs under a write/process audit observer, not a subprocess stub.
            program = '''import sys, os
violations=[]
def audit(event,args):
    if event == 'open':
        mode, flags = args[1:3]
        if (isinstance(mode,str) and any(x in mode for x in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
            violations.append(event)
    elif event in ('os.mkdir','os.remove','os.rename','os.rmdir','os.chmod','os.symlink','os.link','os.truncate','os.system','socket.connect','socket.bind'):
        violations.append(event)
    elif event == 'subprocess.Popen':
        assert args[1][:1] == ['git'] and 'rev-parse' in args[1], args[1]
sys.addaudithook(audit)
from agentj import cli
try: cli.main(__import__('json').loads(sys.argv[1]))
except SystemExit as e:
    if e.code not in (0,1): raise
assert not violations, violations
'''
            result = subprocess.run([sys.executable,'-B','-c',program,__import__('json').dumps(list(cmdrun.RUN_CMDS[name][0]))],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(snapshot(),before)

    def test_fixed_doctor_argv_uses_only_read_only_mode(self):
        self.assertEqual(cmdrun.RUN_CMDS['doctor'][0], ('doctor','--read-only'))

    def check_exempt_commands(self):
        self.assertTrue(cmdrun.READ_CMDS)
        for name in sorted(cmdrun.READ_CMDS):
            self.assertIn(name, cmdrun.RUN_CMDS)
            for layout in ('explicit', 'current', 'legacy'):
                self.assert_cli_readonly(name, layout)

    def test_every_exempt_command_runs_real_cli_without_writes(self):
        self.check_exempt_commands()

    def test_new_exemption_with_startup_writes_is_caught(self):
        # Run the same dynamic check with a newly exempted CLI that migrates URLs.
        # Neither subprocess nor cli.main/migrate.auto is mocked.
        with mock.patch.object(cmdrun, 'READ_CMDS', cmdrun.READ_CMDS | {'status'}):
            # Exactly the same dynamic test must reject the expanded set.
            with self.assertRaisesRegex(AssertionError, "open|os.rename"):
                self.check_exempt_commands()
