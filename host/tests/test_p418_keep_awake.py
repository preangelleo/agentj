"""P418 (#183, Leo 10-20): the install-end keep-awake question — lid closed on AC stays awake (Linux logind drop-in, macOS
disablesleep with its reminder), Linux linger by default, terminal [Y/n] / phone card, read back, `off` restores."""
import _hermetic  # noqa: F401
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import keep_awake as k, elevate, doctor, reset, service, onboarding
from agentj.state import State


def mac(**kw):
    return {'system': 'macos', 'sleep': {'ac': 20, 'battery': 5}, 'awake': False, 'disablesleep': 0, 'lid_awake': False, **kw}


def linux(**kw):
    return {'system': 'linux', 'targets': {t: 'static' for t in k.TARGETS}, 'awake': False, 'lid_awake': False,
            'lid_owned': False, **kw}


class Plan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name) / 'state')

    def test_mac_lid_sets_and_restores_disablesleep(self):
        current = mac()
        def send(st, req):
            a = req['argv']; self.assertEqual(a[:2], ['/usr/bin/pmset', '-c'])
            if a[2] == 'disablesleep':
                current['disablesleep'] = int(a[3]); current['lid_awake'] = a[3] == '1'
                if a[3] == '1':
                    self.assertIn('关机', req['effect'])
            else:
                current['sleep']['ac'] = int(a[3])
            return {'result': 'done', 'code': 0}
        with patch.object(k, 'status', side_effect=lambda: copy.deepcopy(current)), \
                patch.object(elevate, 'client_request', side_effect=send) as call:
            self.assertTrue(k.execute('on', st=self.st, lid=True)['verified'])
            self.assertEqual((current['sleep']['ac'], current['disablesleep']), (0, 1))
            rec = json.loads((self.st.root / 'keep-awake.json').read_text())
            self.assertEqual((rec['sleep'], rec['disablesleep']), ({'ac': 20}, 0))
            self.assertTrue(k.execute('on', st=self.st, lid=True)['verified']); self.assertEqual(call.call_count, 2)
            self.assertTrue(k.execute('off', st=self.st)['verified'])
            self.assertEqual((current['sleep']['ac'], current['disablesleep']), (20, 0))
            self.assertFalse((self.st.root / 'keep-awake.json').exists())

    def test_mac_without_lid_never_touches_disablesleep(self):
        cmds, rec = k.plan('on', mac(), None, 'ac')
        self.assertEqual(cmds, [['/usr/bin/pmset', '-c', 'sleep', '0']]); self.assertNotIn('disablesleep', rec)
        cmds, rec = k.plan('on', mac(sleep={'ac': 0}, awake=True), None, 'ac', lid=True)
        self.assertEqual(cmds, [['/usr/bin/pmset', '-c', 'disablesleep', '1']])
        self.assertEqual(rec, {'version': 1, 'system': 'macos', 'sleep': {}, 'disablesleep': 0})
        with tempfile.TemporaryDirectory() as d:     # a record holding only disablesleep loads back
            p = Path(d) / 'r.json'; p.write_text(json.dumps(rec)); self.assertEqual(k.load(p), rec)

    def test_linux_lid_dropin_written_once_and_removed_by_off(self):
        current, files = linux(), set()
        def send(st, req):
            a = req['argv']
            if a[0] == '/bin/sh':
                self.assertEqual(a, ['/bin/sh', '-c', k.LID_SCRIPT]); files.add(k.LID_DROPIN)
                self.assertIn('HandleLidSwitchExternalPower=ignore', a[2]); self.assertEqual(req['effect'], k.LID_EFFECT)
            elif a[0] == '/bin/rm':
                self.assertEqual(a, ['/bin/rm', '-f', k.LID_DROPIN]); files.discard(k.LID_DROPIN)
            else:
                for t in a[2:]: current['targets'][t] = 'masked' if a[1] == 'mask' else 'static'
            return {'result': 'done', 'code': 0}
        def now():
            c = copy.deepcopy(current); c['lid_owned'] = c['lid_awake'] = k.LID_DROPIN in files
            return c
        with patch.object(k, 'status', side_effect=now), patch.object(elevate, 'client_request', side_effect=send) as call, \
                patch.object(k.os.path, 'lexists', side_effect=lambda p: p in files):
            self.assertTrue(k.execute('on', st=self.st, lid=True)['verified'])
            self.assertIn(k.LID_DROPIN, files); self.assertEqual(call.call_count, 2)
            self.assertTrue(json.loads((self.st.root / 'keep-awake.json').read_text())['lid'])
            self.assertTrue(k.execute('on', st=self.st, lid=True)['verified']); self.assertEqual(call.call_count, 2)
            self.assertTrue(k.execute('off', st=self.st)['verified'])
            self.assertNotIn(k.LID_DROPIN, files)
            self.assertTrue(all(v == 'static' for v in current['targets'].values()))

    def test_linux_foreign_dropin_refused(self):
        with patch.object(k.os.path, 'lexists', side_effect=lambda p: p == k.LID_DROPIN):
            with self.assertRaises(ValueError):
                k.plan('on', linux(), None, 'ac', lid=True)
            # ours (recorded) and present → nothing to write
            cmds, rec = k.plan('on', linux(targets={t: 'masked' for t in k.TARGETS}), {'version': 1, 'system': 'linux', 'targets': [], 'lid': True}, 'ac', lid=True)
            self.assertEqual(cmds, []); self.assertTrue(rec['lid'])

    def test_status_reads_disablesleep_and_lid(self):
        outs = {('/usr/bin/pmset', '-g', 'custom'): 'AC Power:\n sleep 0\n', ('/usr/bin/pmset', '-g'): 'System-wide power settings:\n SleepDisabled\t\t1\n'}
        with patch.object(k, 'system', return_value='macos'), patch.object(k, 'read', side_effect=lambda a: (0, outs[tuple(a)], '')):
            r = k.status(); self.assertEqual((r['disablesleep'], r['lid_awake']), (1, True))
        outs[('/usr/bin/pmset', '-g')] = 'System-wide power settings:\n'
        with patch.object(k, 'system', return_value='macos'), patch.object(k, 'read', side_effect=lambda a: (0, outs[tuple(a)], '')):
            self.assertFalse(k.status()['lid_awake'])
        def lread(a):
            if 'cat-config' in a:
                return 0, '[Login]\n#HandleLidSwitch=suspend\n# /etc/systemd/logind.conf.d/90-agentj-keep-awake.conf\nHandleLidSwitchExternalPower=ignore\n', ''
            return 0, 'masked', ''
        with patch.object(k, 'system', return_value='linux'), patch.object(k, 'read', side_effect=lread):
            r = k.status(); self.assertTrue(r['awake']); self.assertTrue(r['lid_awake'])

    def test_doctor_reminds_on_a_mac_whose_lid_stays_awake(self):
        with patch.object(k, 'status', return_value=mac(awake=True, lid_awake=True)):
            row = doctor.check_keep_awake()
        self.assertIn('主动关机', row['hint']); self.assertIn('shut', row['hint'])
        with patch.object(k, 'status', return_value=linux(awake=True, lid_awake=True)):
            self.assertNotIn('主动关机', doctor.check_keep_awake()['hint'])


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name) / 'state'); self.st.root.mkdir(parents=True)
        self.reqs, self.out = [], []

    def run_setup(self, kind, mode, answers=(), lid=None, card=None, after=None, linger='yes'):
        it = iter(answers)
        def ask(prompt):
            self.out.append(prompt); return next(it)
        def send(st, req):
            self.reqs.append(req); return {'ok': True, 'verified': True}
        st_after = after or (mac(awake=True, lid_awake=True, disablesleep=1) if kind == 'macos' else linux(awake=True, lid_awake=True))
        with patch.object(k, 'system', return_value=kind), patch('builtins.input', side_effect=ask), \
                patch.object(elevate, 'client_request', side_effect=send), patch.object(k, 'status', return_value=st_after), \
                patch.object(service, '_linger', return_value=linger), \
                patch('agentj.names.ctl_call', side_effect=lambda st, req, **kw: (self.reqs.append(req), card)[1]):
            return k.setup(self.st, mode, lid, 'zh', out=self.out.append)

    def test_terminal_enter_is_yes_linux_keeps_lid_and_linger(self):
        res = self.run_setup('linux', 'terminal', [''])
        self.assertTrue(res['ok']); self.assertEqual(self.reqs, [{'t': 'keep_awake', 'action': 'on', 'power': 'ac', 'dry_run': False, 'lid': True}])
        self.assertIn('[Y/n] 回车 = 是', self.out[0])
        text = '\n'.join(map(str, self.out))
        self.assertIn('agentj keep-awake off', text); self.assertIn('loginctl disable-linger', text)
        self.assertEqual(k.offer_answer(self.st)['answer'], 'yes')

    def test_mac_asks_a_second_time_with_the_shutdown_reminder(self):
        res = self.run_setup('macos', 'terminal', ['', ''])
        self.assertIn('主动关机', self.out[1]); self.assertTrue(self.reqs[0]['lid'])
        self.assertIn(k.REMIND['zh'], '\n'.join(map(str, self.out)))
        self.assertIn('FileVault', '\n'.join(map(str, self.out))); self.assertTrue(res['ok'])
        self.reqs.clear(); self.out.clear()
        res = self.run_setup('macos', 'terminal', ['y', 'n'], after=mac(awake=True))
        self.assertFalse(self.reqs[0]['lid']); self.assertTrue(res['ok']); self.assertNotIn(k.REMIND['zh'], '\n'.join(map(str, self.out[2:])))

    def test_no_changes_nothing_and_is_remembered(self):
        res = self.run_setup('linux', 'terminal', ['n'])
        self.assertEqual((res['answer'], self.reqs), ('no', []))
        self.assertEqual(k.offer_answer(self.st)['answer'], 'no')

    def test_mac_yes_without_lid_flag_keeps_lid_sleeping(self):
        self.run_setup('macos', 'yes', after=mac(awake=True))
        self.assertFalse(self.reqs[0]['lid'], 'an Agent cannot skip the Mac lid question')
        self.reqs.clear()
        self.run_setup('macos', 'yes', lid=True)
        self.assertTrue(self.reqs[0]['lid'])

    def test_phone_card_first_option_is_yes(self):
        card = {'result': 'answered', 'answers': [{'picked': [k.CARD['zh'][2]], 'typed_in_chat': False}]}
        res = self.run_setup('macos', 'phone', card=card)
        q = self.reqs[0]['questions'][0]
        self.assertEqual(self.reqs[0]['cmd'], 'ask_open'); self.assertFalse(q['other'])
        self.assertEqual([o['label'] for o in q['options']], [k.CARD['zh'][2], k.CARD['zh'][3], k.CARD['zh'][1]])
        self.assertIn('主动关机', q['question']); self.assertTrue(self.reqs[1]['lid']); self.assertTrue(res['ok'])
        from agentj import ask
        ask.build([q], 'zh')        # within the card's limits (labels ≤ 12 字 …)
        self.reqs.clear()
        card = {'result': 'answered', 'answers': [{'picked': [k.CARD['zh'][0]], 'typed_in_chat': False}]}
        self.run_setup('linux', 'phone', card=card)
        ask.build(self.reqs[0]['questions'], 'zh')
        self.assertTrue(self.reqs[1]['lid'])
        self.reqs.clear()
        res = self.run_setup('linux', 'phone', card={'result': 'answered', 'answers': [{'picked': [k.CARD['zh'][1]]}]})
        self.assertEqual((res['answer'], len(self.reqs)), ('no', 1))
        res = self.run_setup('linux', 'phone', card={'result': 'no_device'})
        self.assertIsNone(res['answer']); self.assertFalse(res['ok'])

    def test_wsl_prints_the_windows_steps_only(self):
        res = self.run_setup('wsl', 'terminal')
        self.assertEqual((res['answer'], self.reqs), ('guidance', []))
        self.assertIn('LIDACTION', self.out[0])

    def test_linger_falls_back_to_the_phone_password_card(self):
        states = iter(['no', 'yes'])
        with patch.object(k, 'system', return_value='linux'), patch.object(service, '_linger', side_effect=lambda: next(states)), \
                patch.object(reset, 'run_linger', return_value={'ok': False, 'why': 'denied'}), \
                patch.object(elevate, 'client_request', return_value={'result': 'done', 'code': 0}) as send, \
                patch.dict(k.os.environ, {'USER': 'owner'}):
            res = k.linger_on(self.st)
        self.assertEqual(res, {'ok': True, 'state': 'enabled'})
        req = send.call_args[0][1]
        self.assertEqual((req['t'], req['argv'][1:]), ('sudo', ['enable-linger', 'owner']))
        with patch.object(k, 'system', return_value='macos'):
            self.assertEqual(k.linger_on(self.st)['state'], 'not_applicable')

    def test_cli_not_a_terminal_only_explains(self):
        from agentj import cli
        printed = []
        with patch('sys.stdin.isatty', return_value=False), patch('builtins.print', side_effect=lambda *a, **kw: printed.append(a[0])), \
                patch.object(elevate, 'client_request') as send, patch.object(k, 'State', return_value=self.st):
            with self.assertRaises(SystemExit) as ended:
                cli.main(['keep-awake', 'setup', '--lang', 'zh'])
        self.assertEqual(ended.exception.code, 0); send.assert_not_called()
        self.assertIn('--phone', printed[0]); self.assertIsNone(k.offer_answer(self.st))

    def test_onboarding_skips_the_question_after_the_terminal_answered(self):
        import time
        rec = onboarding._blank(); now = int(time.time())
        rec.update(welcomed={"at": now, "device": "d", "kind": "computer"}, computer={"at": now, "device": "d"}, phone={"at": now, "device": "p"})
        onboarding._write(self.st, rec)
        k._remember(self.st, 'no', False, 'terminal')
        with patch.object(onboarding.cloud, "read_cloud", return_value={"tenant": {}}):
            self.assertNotIn('keep-awake', onboarding.turn_note(self.st, 'en'))


import test_f17 as f17      # noqa: E402 — the real host/socket/paired-phone fixture; no system settings change


class SocketFlows(unittest.TestCase):
    setUp = f17.Flows.setUp
    tearDown = f17.Flows.tearDown
    cards = f17.Flows.cards
    wait_card = f17.Flows.wait_card
    run_flow = f17.Flows.run_flow

    def test_lid_goes_through_its_own_signed_card(self):
        current, files = linux(targets={t: 'masked' for t in k.TARGETS}, awake=True), set()
        def apply(argv, password, cwd, timeout, sudo, cancel, watch=None):
            self.assertEqual(argv, ['/bin/sh', '-c', k.LID_SCRIPT]); files.add(k.LID_DROPIN)
            return {'result': 'done', 'code': 0, 'stdout': '', 'stderr': ''}
        async def phone(host, session, ph):
            card = await self.wait_card()
            self.assertIn('HandleLidSwitchExternalPower=ignore', card['cmd']); self.assertEqual(card['effect'], k.LID_EFFECT)
            await host._app(session, f17.answer(ph, card, True, f17.PW))
        def now():
            c = copy.deepcopy(current); c['lid_owned'] = c['lid_awake'] = k.LID_DROPIN in files
            return c
        with patch.object(k, 'status', side_effect=now), patch.object(elevate, 'run_sudo', side_effect=apply), \
                patch.object(k.os.path, 'lexists', side_effect=lambda p: p in files):
            res, _, _ = self.run_flow({'t': 'keep_awake', 'action': 'on', 'power': 'ac', 'dry_run': False, 'lid': True}, phone)
        self.assertTrue(res['verified'])

    def test_lid_must_be_boolean(self):
        res, _, _ = self.run_flow({'t': 'keep_awake', 'action': 'on', 'power': 'ac', 'dry_run': False, 'lid': 'yes'}, None)
        self.assertEqual(res['result'], 'refused'); self.assertEqual(self.cards(), [])


if __name__ == '__main__':
    unittest.main()
