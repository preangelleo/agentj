"""P76 isolated settings, service auth transitions and held/accepted UDS delivery."""
import _hermetic
import asyncio
import hashlib
import json
import os
from pathlib import Path
import plistlib
import socket
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentj import claude_inbound as inbound, claude_auth, doctor, preferences, service, shared
from agentj.state import State
from agentj.serve import Host, Session
from agentj.agent import ClaudeAgent

class Settings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"HOME":str(self.home), "CLAUDE_CONFIG_DIR":str(self.home/"cc"),
                                         "XDG_CONFIG_HOME":str(self.home/"config"), "AGENTJ_STATE_DIR":str(self.home/"st")})
        self.env.start(); self.addCleanup(self.env.stop)
    def test_preserve_backup_private_idempotent(self):
        p=inbound.path(); p.parent.mkdir(); original=b'{"permissions":{"defaultMode":"bypassPermissions"},"other":[1,2]}\n'
        p.write_bytes(original)
        self.assertTrue(inbound.set_enabled(True)); self.assertEqual(inbound.value(),"accept")
        self.assertEqual(inbound.read()["other"],[1,2]); self.assertFalse(inbound.set_enabled(True))
        backups=list(p.parent.glob("settings.json.agentj-backup-*")); self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].read_bytes(),original); self.assertEqual(backups[0].stat().st_mode & 0o777,0o600)
        self.assertEqual(p.stat().st_mode & 0o777,0o600)
        self.assertTrue(inbound.set_enabled(False)); self.assertEqual(inbound.value(),"hold")
        self.assertFalse(inbound.set_enabled(False))
    def test_missing_invalid_symlink(self):
        self.assertEqual(inbound.value(),"unset"); inbound.set_enabled(True)
        p=inbound.path(); p.write_text("[]")
        with self.assertRaises(inbound.SettingsError): inbound.set_enabled(True)
        self.assertEqual(p.read_text(),"[]")
        p.unlink(); target=p.parent/"target";target.write_text("{}") ;p.symlink_to(target)
        with self.assertRaises(inbound.SettingsError): inbound.set_enabled(True)
        self.assertEqual(target.read_text(),"{}")
    def test_doctor_three_states(self):
        st=Mock(); st.root=self.home/"st"; st.exists.return_value=True; st.agent_config.return_value={"kind":"claude","session_mode":"independent","dir":str(self.home)}
        self.assertIsNone(doctor.check_shared_inbound(st))
        st.agent_config.return_value["session_mode"]="shared"
        self.assertEqual(doctor.check_shared_inbound(st)["status"],"ok")
        inbound.set_enabled(True);self.assertEqual(doctor.check_shared_inbound(st)["status"],"ok")
        inbound.set_enabled(False);self.assertEqual(doctor.check_shared_inbound(st)["status"],"ok")
    def test_cli_shared_auth_ok_independent_warn(self):
        st=Mock();st.exists.return_value=True;st.agent_config.return_value={"kind":"claude","session_mode":"shared"}
        with patch.object(doctor,"_agent_bin",return_value="/bin/claude"),patch.object(doctor,"_version_of",return_value="2.1"),patch.object(doctor,"_login",return_value=("warn","no login found")),patch.object(claude_auth,"available",return_value=False):
            self.assertEqual(doctor.check_agent_cli(st,{"installed":False})["status"],"ok")
            st.agent_config.return_value["session_mode"]="independent"
            self.assertEqual(doctor.check_agent_cli(st,{"installed":False})["status"],"warn")
    def test_shell_token_not_service_login(self):
        with patch.dict(os.environ,{"CLAUDE_CODE_OAUTH_TOKEN":"TEST_ONLY_TOKEN"}),patch.object(service,"status",return_value={"installed":False}),patch("agentj.harness.sys.platform","linux"):
            self.assertFalse(claude_auth.available());self.assertTrue(claude_auth.available(running=True))
    def test_launch_env_load_private_and_plist_has_path_only(self):
        f=self.home/"service.env";f.write_text('CLAUDE_CODE_OAUTH_TOKEN="TEST_ONLY_TOKEN"\nIGNORED=anything\n');f.chmod(0o600)
        env={"PATH":"/usr/bin","AGENTJ_SERVICE_ENV_FILE":str(f),"CLAUDE_CODE_OAUTH_TOKEN":"DO_NOT_EMBED"}
        doc=plistlib.loads(service.plist_bytes("test",["agentj"],env,"/tmp/test.log"))
        self.assertEqual(doc["EnvironmentVariables"]["AGENTJ_SERVICE_ENV_FILE"],str(f))
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN",doc["EnvironmentVariables"])
        with patch.dict(os.environ,{"AGENTJ_SERVICE_ENV_FILE":str(f)}):
            service.load_launch_binary_env();self.assertEqual(os.environ["CLAUDE_CODE_OAUTH_TOKEN"],"TEST_ONLY_TOKEN")
            self.assertNotIn("IGNORED",os.environ)
        f.chmod(0o644)
        with self.assertRaises(service.ServiceError):service.claude_env(str(f))
    def test_offline_transition_no_commit_on_failure(self):
        st=State(); st.init();st.set_agent_config("claude",str(self.home));preferences.ensure()
        before=preferences.path().read_bytes();raw=preferences.edit(before.decode(),"agent.session_mode","independent")
        with patch("agentj.names.ctl_call",return_value=None),patch.object(claude_auth,"available",return_value=False):
            with self.assertRaises(preferences.ConfigError):preferences.transact(st,raw)
        self.assertEqual(preferences.path().read_bytes(),before)
        with patch("agentj.names.ctl_call",return_value=None),patch.object(claude_auth,"available",return_value=True):
            self.assertTrue(preferences.transact(st,raw)["ok"])
        self.assertEqual(st.agent_config()["session_mode"],"independent")
    def test_failure_single_language(self):
        for lang in ("zh","en"):
            host=Mock();a=ClaudeAgent(host,{"kind":"claude","dir":str(self.home),"_workflow_ceo":True,"language":lang})
            a.on_event({"type":"result","is_error":True,"result":"Not logged in · Please run /login"})
            notice=host.agent_notice.call_args.args[0];self.assertIn("setup-token",notice);self.assertNotIn("Not logged in ·",notice)
            self.assertEqual("电脑" in notice,lang=="zh")

class Runtime(unittest.IsolatedAsyncioTestCase):
    async def test_phone_transition_failed_stays_shared_success_commits(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{"HOME":td,"CLAUDE_CONFIG_DIR":td+"/cc","XDG_CONFIG_HOME":td+"/config","AGENTJ_STATE_DIR":td+"/st"}):
            st=State();st.init();st.set_agent_config("claude",td);preferences.ensure();h=Host(st)
            h._send_ready=AsyncMock();h.host_info=Mock(return_value={})
            raw=preferences.edit(preferences.read()[0],"agent.session_mode","independent")
            before=preferences.path().read_bytes()
            with patch.object(claude_auth,"available",return_value=False):res=await h.apply_preferences(raw)
            self.assertFalse(res["ok"]);self.assertIn("setup-token",res["error"])
            self.assertEqual(preferences.path().read_bytes(),before);self.assertEqual(st.agent_config()["session_mode"],"shared")
            with patch.object(claude_auth,"available",return_value=True):res=await h.apply_preferences(raw)
            self.assertTrue(res["ok"]);self.assertEqual(st.agent_config()["session_mode"],"independent")
    async def test_phone_rejection_emits_actionable_notice(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{"HOME":td,"XDG_CONFIG_HOME":td+"/config","AGENTJ_STATE_DIR":td+"/st"}):
            st=State();st.init();st.set_agent_config("claude",td);preferences.ensure();h=Host(st)
            h._send_ready=AsyncMock();h.send_app=AsyncMock();h.agent_notice=Mock();h.host_info=Mock(return_value={})
            session=Session(1,state="ready",device="fixture",pub=b"fixture");h.sessions[1]=session
            with patch.object(st,"is_allowed",return_value=True),patch.object(claude_auth,"available",return_value=False):
                await h.on_pref_set(session,{"r":"12345678","key":"agent.session_mode","value":"independent"})
            self.assertFalse(h.send_app.call_args.args[1]["ok"])
            self.assertIn("setup-token",h.agent_notice.call_args.args[0])
            self.assertEqual(st.agent_config()["session_mode"],"shared")

    async def test_socket_held_and_accepted(self):
        for accepted in (False,True):
            with tempfile.TemporaryDirectory(prefix="p76-") as td,patch.dict(os.environ,{"CLAUDE_CONFIG_DIR":td+"/cc"}):
                root=Path(td);cc=root/"cc/sessions";cc.mkdir(parents=True)
                sock=socket.socket(socket.AF_UNIX);sock.bind(td+"/p.sock");sock.listen();sock.settimeout(3)
                session={"pid":os.getpid(),"sessionId":"fixture-session","messagingSocketPath":td+"/p.sock"}
                (cc/(str(os.getpid())+"."+hashlib.sha256((td+"/p.sock").encode()).hexdigest()+".key")).write_text(json.dumps({"peerToken":"TEST_ONLY_PEER"}))
                host=Mock();a=shared.SharedClaudeAgent(host,{"kind":"claude","dir":td,"_workflow_ceo":True,"language":"zh"})
                a.session=session;a.attach=AsyncMock(return_value=True)
                loop=asyncio.get_running_loop();frames=[]
                def peer():
                    with sock.accept()[0] as c,c.makefile("r") as f:
                        frames.extend(json.loads(f.readline()) for _ in range(2))
                    if accepted:
                        async def observed():
                            await a.hook_event({"session_id":"fixture-session","hook_event_name":"UserPromptSubmit","prompt":frames[1]["message"]["content"]})
                            a.done.set()
                        loop.call_soon_threadsafe(lambda: asyncio.create_task(observed()))
                thread=threading.Thread(target=peer);thread.start()
                try:
                    with patch.object(shared,"CLAUDE_INPUT_WAIT",0.05):await a.turn("fixture request")
                    await asyncio.to_thread(thread.join,2)
                    self.assertEqual(len(frames),2);self.assertNotIn("senderMode",frames[1])
                    if accepted:host.turn_failed.assert_not_called()
                    else:
                        host.turn_failed.assert_called_once();self.assertIn("agentj doctor",host.agent_notice.call_args.args[0]);self.assertIn("user",host.agent_notice.call_args.args[0])
                finally:
                    sock.close();thread.join(3)

class Statusline(unittest.TestCase):
    setUp = Settings.setUp
    def blob(self, sid='session-fixture'):
        return {'session_id':sid,'model':{'id':'claude-opus-4-6','display_name':'Opus 4.6'},'effort':{'level':'high'},
                'context_window':{'used_percentage':34.5,'context_window_size':200000},
                'rate_limits':{'five_hour':{'used_percentage':25,'resets_at':2000000000},'seven_day':{'used_percentage':60}},
                'token':'DO_NOT_CAPTURE','cwd':'DO_NOT_CAPTURE'}
    def invoke(self, raw):
        import subprocess
        return subprocess.run(inbound.read()['statusLine']['command'],shell=True,input=raw,capture_output=True)
    def test_chain_preserves_stdin_stdout_exit_restore_and_keys(self):
        from agentj import claude_statusline as tap
        import shlex
        old={'type':'command','command':shlex.quote(sys.executable)+' -c '+shlex.quote('import sys;sys.stdout.buffer.write(sys.stdin.buffer.read());sys.exit(7)'), 'padding':2}
        p=inbound.path();p.parent.mkdir();p.write_text(json.dumps({'statusLine':old,'unrelated':{'x':True}}))
        self.assertTrue(tap.set_enabled(True,self.home/'st'));self.assertFalse(tap.set_enabled(True,self.home/'st'))
        raw=json.dumps(self.blob()).encode();r=self.invoke(raw);self.assertEqual(r.stdout,raw);self.assertEqual(r.returncode,7)
        stored=self.home/'st/claude-statusline/session-fixture.json'
        self.assertEqual(stored.stat().st_mode & 0o777,0o600);self.assertNotIn('DO_NOT_CAPTURE',stored.read_text())
        r=self.invoke(b'not JSON');self.assertEqual(r.stdout,b'not JSON');self.assertEqual(r.returncode,7)
        self.assertTrue(tap.set_enabled(False,self.home/'st'));self.assertFalse(tap.set_enabled(False,self.home/'st'))
        self.assertEqual(inbound.read(),{'statusLine':old,'unrelated':{'x':True}})
        self.assertEqual(len(list(p.parent.glob('settings.json.agentj-backup-*'))),2)
    def test_absent_restore_and_later_owner_edit_never_overwritten(self):
        from agentj import claude_statusline as tap
        tap.set_enabled(True,self.home/'st')
        p=inbound.path();doc=inbound.read();doc['statusLine']['command']='owner-new-command';p.write_text(json.dumps(doc))
        with self.assertRaises(inbound.SettingsError):tap.set_enabled(False,self.home/'st')
        self.assertEqual(inbound.read()['statusLine']['command'],'owner-new-command')
        meta=tap.load_meta();doc['statusLine']=meta['installed'];p.write_text(json.dumps(doc))
        tap.set_enabled(False,self.home/'st');self.assertNotIn('statusLine',inbound.read())
    def test_session_bound_meter_age_compaction_and_adapter(self):
        from agentj import claude_statusline as tap
        tap.set_enabled(True,self.home/'st');self.assertEqual(self.invoke(json.dumps(self.blob()).encode()).returncode,0)
        file=self.home/'st/claude-statusline/session-fixture.json';os.utime(file,(1000,1000))
        m=tap.read(self.home/'st','session-fixture');self.assertEqual(m['source_at'],1000)
        self.assertEqual(m['model_name'],'Opus 4.6');self.assertEqual(m['week']['pct'],60);self.assertEqual(m['h5']['pct'],25)
        self.assertEqual(m['ctx'],{'pct':34.5,'max':200000})
        self.assertIsNone(tap.read(self.home/'st','other-session')['model'])
        self.assertIsNone(tap.read(self.home/'st',None)['model'])
        self.assertIsNone(tap.read(self.home/'st','session-fixture',1000)['ctx'])
        self.assertIsNotNone(tap.read(self.home/'st','session-fixture',999)['ctx'])
        host=Mock();host.st.root=self.home/'st';a=shared.SharedClaudeAgent(host,{'kind':'claude','dir':str(self.home),'_workflow_ceo':True})
        a.session={'sessionId':'session-fixture'};a.read_statusline();self.assertEqual(host.meter_update.call_args.kwargs['model_name'],'Opus 4.6')
        file.write_text(json.dumps({'session_id':'wrong','model':'wrong'}));self.assertIsNone(tap.read(self.home/'st','session-fixture')['model'])
        tap.set_enabled(False,self.home/'st');self.assertIsNone(tap.read(self.home/'st','session-fixture')['source_at'])
    def test_bad_numbers_and_tap_write_failure_do_not_break_owner_command(self):
        from agentj import claude_statusline as tap
        import shlex
        blob=self.blob();blob['rate_limits']['five_hour']['used_percentage']=float('nan')
        blob['context_window']['used_percentage']=True
        m=tap.measured(blob);self.assertIsNone(m['h5']);self.assertNotIn('pct',m['ctx'])
        root=self.home/'blocked';root.write_text('file')
        p=inbound.path();p.parent.mkdir();p.write_text(json.dumps({'statusLine':{'type':'command','command':'printf preserved'}}))
        tap.set_enabled(True,root);r=self.invoke(json.dumps(self.blob()).encode())
        self.assertEqual(r.stdout,b'preserved');self.assertEqual(r.returncode,0)
