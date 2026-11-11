"""0225: actual Host signed decision reaches Shared; all tools and state isolated."""
import _hermetic
import asyncio
import pathlib
import subprocess
import shlex
import tempfile
import unittest
from unittest import mock
from agentj import shared, approval_jev, provenance
from test_l1 import _state, _review_owner_host, _ready, Phone


class ApprovedDelivery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Preferences are per fixture: another test's working root is not initial setup.
        from tempfile import TemporaryDirectory as _ConfigDirectory
        from unittest.mock import patch as _config_patch
        self._config_tmp = _ConfigDirectory(prefix='aj-0300-config-')
        self.addCleanup(self._config_tmp.cleanup)
        _config_env = _config_patch.dict('os.environ', {'XDG_CONFIG_HOME': self._config_tmp.name})
        _config_env.start()
        self.addCleanup(_config_env.stop)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name); work = self.root / 'work'; work.mkdir()
        st = _state(self.tmp.name); st.set_agent_config('claude', str(work))
        self.sent = []; self.h = _review_owner_host(st, self.sent); self.h.ask_ttl = 5
        self.h.emit = lambda *a, **kw: None
        self.a = shared.SharedClaudeAgent(self.h, {'kind':'claude','dir':str(work),'_workflow_ceo':True,'high_risk_warnings':True})
        self.a.session = {'sessionId':'delivery-owner'}; self.a.native_source = provenance.terminal(); self.h.agent = self.a
        self.ph = Phone(st, with_passkey=True); self.s = _ready(self.h, self.ph)
        patch = mock.patch.object(approval_jev, 'available', return_value=False); patch.start(); self.addCleanup(patch.stop)
        self.addAsyncCleanup(self.cleanup_tasks)

    async def cleanup_tasks(self):
        tasks = list(getattr(self.h, '_bg_tasks', ()))   # Host._bg creates the set lazily; zero-card runs never call it
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def phase(self, event, allow):
        """One hook phase; a card it opens (PreToolUse UV gate or PermissionRequest) is answered on the paired phone."""
        before = len(self.sent); task = asyncio.create_task(self.a.hook_event(event))
        for _ in range(500):
            cards = [m for _, m in self.sent[before:] if m.get('t') == 'ask']
            if cards or task.done(): break
            await asyncio.sleep(.005)
        if cards:
            await self.h._app(self.s, self.ph.answer(cards[-1], allow))
        return await task, len(cards)

    async def request(self, inp, allow=True, tool='Bash'):
        event = {'session_id':'delivery-owner','tool_name':tool,'tool_input':inp,'tool_use_id':'delivery-call'}
        pre, early = await self.phase(dict(event, hook_event_name='PreToolUse'), allow)
        self.pre_cards = early
        hso = pre.get('hookSpecificOutput') or {}
        if hso.get('permissionDecision') == 'deny':      # Claude Code stops here: no PermissionRequest follows
            return {'behavior':'deny','message':hso.get('permissionDecisionReason','')}, early
        out, late = await self.phase(dict(event, hook_event_name='PermissionRequest'), allow)
        return out['hookSpecificOutput']['decision'], early + late

    async def test_approved_service_and_literal_output_reaches_native_execution(self):
        # ADR-0307-offlist: an off-list command in an owner turn (owner_terminal here) is allowed at once, zero cards.
        command = 'systemctl --user start qa-never-executed.service; printf ready'
        decision, cards = await self.request({'command':command})
        self.assertEqual(cards, 0); self.assertEqual(decision['behavior'], 'allow')
        self.assertNotIn('updatedInput', decision)   # the harness runs the unchanged input
        self.assertFalse(self.a.risk_denied)
        # Native shell executes the unchanged approved input; its private CLI fixture
        # replaces only systemctl and cannot touch any actual service or owner HOME.
        bindir = self.root / 'bin'; bindir.mkdir(); ctl = bindir / 'systemctl'
        ctl.write_text('#!/bin/sh\nexit 0\n'); ctl.chmod(0o700)
        result = subprocess.run(['/bin/sh','-c',command], env={'PATH':str(bindir),'HOME':str(self.root)}, capture_output=True, text=True)
        self.assertEqual((result.returncode,result.stdout), (0,'ready'))
        follow, _ = await self.request({'file_path':str(pathlib.Path.home()/'.config/systemd/user/qa-never-written.service'),'content':'[Service]\nExecStart=/usr/bin/true\n'}, tool='Write')
        self.assertEqual(follow['behavior'],'allow')
        # A listed (UV) action still opens exactly one card, and the owner's signed allow reaches the harness unchanged.
        listed, cards = await self.request({'file_path':str(self.h.st.root / 'devices.json'),'content':'{}'}, True, tool='Write')
        self.assertEqual(self.pre_cards, 1); self.assertGreaterEqual(cards, 1); self.assertEqual(listed, {'behavior':'allow'})

    async def test_timer_list_filter_is_not_secret_output(self):
        decision, _ = await self.request({'command':"systemctl --user daemon-reload; systemctl --user list-timers --all --no-pager | grep -E 'qa-never'"})
        self.assertEqual(decision['behavior'],'allow'); self.assertFalse(self.a.risk_denied)

    async def test_secret_route_after_uv_does_not_cache_owner_refusal(self):
        # ADR-0307-offlist: a credential read is refused for every source, owner included — without a card, and the
        # refusal is the host's, never worded as the owner's, and never cached as an Agent-wide lock.
        decision, cards = await self.request({'command':'systemctl --user start qa.service; cat ~/.env'})
        self.assertEqual((cards, decision['behavior']), (0, 'deny'))
        self.assertNotIn('主人在已配对设备上拒绝',decision.get('message','')); self.assertIn('未确认是主人拒绝', decision['message'])
        self.assertFalse(self.a.risk_denied)
        after, cards = await self.request({'command':'systemctl --user start qa.service; printf ready'})
        self.assertEqual((cards, after['behavior']), (0, 'allow'))

    async def test_actual_owner_denial_still_caches(self):
        # Since ADR-0307-offlist only a listed action reaches the owner; a protected state write is one.
        target = str(self.h.st.root / 'devices.json')
        decision, cards = await self.request({'file_path':target,'content':'{}'}, False, tool='Write')
        self.assertEqual((cards, decision['behavior']), (1, 'deny')); self.assertIn('The owner denied', decision['message'])
        self.assertFalse(self.a.risk_denied)  # refusal is request-local, not an Agent-wide lock
        follow, cards = await self.request({'file_path':target,'content':'{"x":1}'}, True, tool='Write')
        self.assertEqual(self.pre_cards,1); self.assertEqual(follow['behavior'],'allow')  # new exact request needs its own real signed decision

class DisplayBoundary(unittest.TestCase):
    def test_unknown_and_secret_producers_stay_on_controlled_route(self):
        from agentj.secret_display_route import reveals_to_session
        tails = ['base64 ~/.ssh/id_ed25519; printf ready',
                 'dd if=/proc/self/environ; printf ready',
                 'qa-secret-dumper; printf ready',
                 'printf "$SERVICE_TOKEN"', 'printf "${SERVICE_TOKEN}"',
                 'printf "$(cat ~/.env)"', 'printf "`cat ~/.env`"',
                 'env; printf ready', 'printenv; printf ready',
                 'bw get password synthetic; printf ready',
                 'security find-generic-password -w; printf ready',
                 'git credential fill; printf ready',
                 'cat ~/.env | grep TOKEN; printf ready',
                 'grep TOKEN ~/.env', 'grep TOKEN < ~/.env',
                 'python3 -c "print(123)"; printf ready',
                 'python3 -c "import os; os.system(\'qa-secret-dumper\')"; printf ready',
                 'systemctl --user show-environment; printf ready',
                 'systemctl --user status qa.service | grep TOKEN',
                 'cat <<EOF\nsynthetic\nEOF\nprintf ready']
        for tail in tails:
            with self.subTest(tail=tail):
                self.assertTrue(reveals_to_session('Bash',{'command':'systemctl --user start qa.service; '+tail}))

    def test_unit_writer_is_closed_and_cannot_write_to_output_aliases(self):
        from agentj.secret_display_route import reveals_to_session
        program = "import os; d=os.path.expanduser('~/.config/systemd/user'); open(d+'/synthetic.service','w').write('[Service]\\nExecStart=/usr/bin/true\\n')"
        command = 'python3 -c ' + shlex.quote(program) + '; systemctl --user daemon-reload; systemctl --user list-timers --no-legend | grep synthetic | cut -c1-80'
        self.assertFalse(reveals_to_session('Bash', {'command':command}))
        programs = ["open('/dev/stdout','w').write(open('/proc/self/environ').read())",
                    "open('/dev/stderr','w').write('synthetic')",
                    "open('/dev/tty','w').write('synthetic')",
                    "open('/proc/self/fd/1','w').write('synthetic')",
                    "open('/dev/fd/1','w').write('synthetic')",
                    "import os; d=os.path.expanduser('~/.config/systemd/user'); open(d+'/synthetic.service','w').write(open('/proc/self/environ').read())",
                    "import os; d=os.path.expanduser('~/.config/systemd/user'); open(d+'/../synthetic.service','w').write('synthetic')"]
        for program in programs:
            with self.subTest(program=program):
                self.assertTrue(reveals_to_session('Bash',{'command':'systemctl --user start qa.service; python3 -c '+shlex.quote(program)+'; printf ready'}))
        for command in ['PATH=/tmp systemctl --user start qa.service; printf ready',
                        '/tmp/systemctl --user start qa.service; printf ready',
                        'systemctl --user start qa.service; systemctl --user list-timers | qa-secret-dumper | grep qa',
                        'systemctl --user start qa.service; systemctl --user list-timers | grep qa ~/.env']:
            with self.subTest(command=command):
                self.assertTrue(reveals_to_session('Bash',{'command':command}))
