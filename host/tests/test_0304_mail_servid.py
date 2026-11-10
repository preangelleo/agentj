"""0304 A4: the trusted check server (auth_servid) pins itself for a known provider, keyed by the EXACT IMAP host the owner
configured (imap.gmail.com → mx.google.com). Only narrows: never overwrites a value, never twice on one host, never Outlook
(no authserv-id) or a custom server. Synthetic @example.com addresses; isolated State."""
import _hermetic  # noqa: F401
import asyncio
import email
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import action_policy, doctor, mail as M, provenance
from agentj.state import State

OWNER = 'owner@example.com'


class Servid(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.st = State(pathlib.Path(tmp.name) / 'state')
        self.st.init()
        self.logs = []
        log = mock.patch.object(self.st, 'log', side_effect=lambda what, **kw: self.logs.append((what, kw)))
        log.start()
        self.addCleanup(log.stop)
        self.host = SimpleNamespace(st=self.st, lang='zh', agent=object(), stopping=asyncio.Event(), stopped=lambda: False,
                                    hist_add=lambda *a, **k: None, hist=SimpleNamespace(get=lambda i: None))
        self.m = M.Mail(self.host)

    def cfg(self, preset='gmail', **kw):
        cfg = M.apply_preset(M.blank_config(), preset, OWNER)
        cfg['allow'] = [{'addr': OWNER, 'role': 'owner'}]
        for side in ('imap', 'smtp'):
            if side in kw:
                cfg[side] = {**cfg[side], 'host': kw.pop(side)}
        cfg.update(kw)
        return cfg

    def pins(self):
        return [kw for what, kw in self.logs if what == 'mail_servid_autopin']

    def save(self, raw):
        """A phone / CLI save: the one effective merge (action_policy.mail_config) then execution, as serve.on_mail does."""
        return self.m.set_config(action_policy.mail_config(M.read_config(self.st), raw))

    def test_gmail_autopin_from_imap_host(self):
        M.save_config(self.st, self.cfg())                        # an upgraded host: Gmail, nothing pinned yet
        self.assertEqual(self.m.view()['servid_hint'], 'unpinned_known')
        self.m._migrate()
        self.assertEqual(M.read_config(self.st)['auth_servid'], 'mx.google.com')
        self.assertEqual(self.pins(), [{'host': 'imap.gmail.com', 'servid': 'mx.google.com', 'via': 'upgrade'}])
        v = self.m.view()
        self.assertEqual((v['servid_hint'], v['servid_auto']), (None, True))
        # Setup on the phone / CLI: choosing the Gmail preset pins too (host compared after normalization).
        M.save_config(self.st, M.blank_config())
        cfg = self.save(self.cfg(imap='IMAP.Gmail.com'))
        self.assertEqual(cfg['auth_servid'], 'mx.google.com')
        self.assertEqual(self.pins()[-1]['via'], 'save')
        self.assertEqual(M.KNOWN_SERVID, {'imap.gmail.com': 'mx.google.com'}, 'Gmail only until a real sample of another')

    def test_forged_lower_ar_header_ignored(self):
        def msg(*ar):
            head = ''.join(f'Authentication-Results: {h}\n' for h in ar)
            return email.message_from_string(f'{head}From: {OWNER}\nTo: agent@example.com\nSubject: x\n\nbody\n')
        google = 'mx.google.com; spf=fail smtp.mailfrom=evil.example; dmarc=fail (p=REJECT) header.from=example.com'
        for forged in ('mx.google.com; dmarc=pass header.from=example.com; dkim=pass header.d=example.com',
                       'x.mx.google.com; dmarc=pass header.from=example.com',
                       'dmarc=pass header.from=example.com'):
            m = msg(google, forged)
            self.assertEqual(M.authenticate(m, OWNER, 'standard', 'mx.google.com'), (False, 'fail'), forged)
        # Gmail's own topmost header decides the real owner mail.
        ok = msg('mx.google.com; dkim=pass header.i=@example.com; spf=pass smtp.mailfrom=owner@example.com; '
                 'dmarc=pass (p=NONE) header.from=example.com', 'mx.google.com; dmarc=fail header.from=example.com')
        passed, how = M.authenticate(ok, OWNER, 'standard', 'mx.google.com')
        self.assertEqual((passed, how), (True, 'dmarc'))
        v = SimpleNamespace(ok=True, role='owner', auth=how, addr=OWNER)
        self.assertEqual(M.owner_source(v, servid='mx.google.com'), provenance.email_owner(OWNER))
        # F7: a pin that the topmost header does not carry fails outright; a matching lower header never stands in.
        self.assertEqual(M.authenticate(msg('mx.example.net; dmarc=fail header.from=example.com',
                                            'mx.google.com; dmarc=pass header.from=example.com'),
                                        OWNER, 'standard', 'mx.google.com'), (False, 'servid'))

    def test_custom_imap_host_not_autopinned(self):
        for host in ('imap.gmail.com.example.com', 'gmail.example.com', 'imap.googlemail.com', 'mail.example.com',
                     'imap-gmail.com'):
            M.save_config(self.st, self.cfg('custom', imap=host, smtp='smtp.example.com'))
            self.m._migrate()
            cfg = M.read_config(self.st)
            self.assertEqual(cfg['auth_servid'], '', host)
            self.assertEqual(self.m.view()['servid_hint'], 'unpinned_custom', host)
            self.assertEqual(self.m.set_config(cfg)['auth_servid'], '', host)
        self.assertEqual(self.pins(), [])

    def test_migration_fills_only_empty(self):
        M.save_config(self.st, self.cfg(auth_servid='mx.example.com'))
        self.m._migrate()
        self.assertEqual(M.read_config(self.st)['auth_servid'], 'mx.example.com', 'a manual pin is never overwritten')
        v = self.m.view()
        self.assertEqual((v['servid_hint'], v['servid_auto']), (None, False))
        self.assertEqual(self.m.set_config(self.cfg(auth_servid='mx.example.com'))['auth_servid'], 'mx.example.com')
        self.assertEqual(self.pins(), [])

    def test_migration_idempotent(self):
        M.save_config(self.st, self.cfg())
        for _ in range(3):
            self.m._migrate()
        self.assertEqual(len(self.pins()), 1)
        self.assertEqual(M.read_config(self.st)['auth_servid'], 'mx.google.com')
        # The owner clears the pin on the same server: neither a later save nor the next start pins it again.
        self.save({'auth_servid': ''})
        self.save({'poll_minutes': M.read_config(self.st)['poll_minutes']})
        self.m._migrate()
        self.assertEqual(M.read_config(self.st)['auth_servid'], '')
        self.assertEqual(len(self.pins()), 1)
        v = self.m.view()
        self.assertEqual((v['servid_hint'], v['servid_auto']), ('unpinned_known', False))
        # The owner changes the pin by hand (even to the auto value): "manual", no longer "auto".
        self.save({'auth_servid': 'mx.google.com'})
        self.assertFalse(self.m.view()['servid_auto'])
        self.assertEqual(self.m.view()['servid_status']['source'], 'manual')
        self.save({'auth_servid': 'google.com'})
        self.assertFalse(self.m.view()['servid_auto'])

    def test_outlook_not_autopinned(self):
        M.save_config(self.st, self.cfg('outlook'))
        self.m._migrate()
        self.assertEqual(M.read_config(self.st)['auth_servid'], '')
        self.assertEqual(self.m.set_config(self.cfg('outlook'))['auth_servid'], '')
        self.assertEqual(self.m.view()['servid_hint'], 'unpinned_outlook')
        self.assertEqual(M.parse_ar('spf=pass (sender IP is 192.0.2.1) smtp.mailfrom=example.com; dmarc=pass '
                                    'action=none header.from=example.com')['servid'], '', 'Outlook writes no authserv-id')
        self.assertEqual(self.pins(), [])

    def test_mail_view_reports_unpinned_hint(self):
        self.assertIn(self.m.view()['servid_hint'], ('unpinned_custom',))
        self.assertIs(self.m.view()['servid_auto'], False)
        for preset, hint in (('gmail', 'unpinned_known'), ('outlook', 'unpinned_outlook'), ('qq', 'unpinned_custom'),
                             ('icloud', 'unpinned_custom')):
            cfg = {**self.cfg(preset), 'enabled': True}
            self.assertEqual(M.servid_hint(cfg), hint, preset)
            with mock.patch.object(M, 'read_config', lambda st, c=cfg: c):
                row = doctor.check_mail_owner(self.st)
            self.assertEqual(row['id'], 'mail-owner')
            if hint == 'unpinned_outlook':
                self.assertIn('Outlook', row['hint'])
                self.assertNotIn('只认这台服务器写的校验结果', row['hint'])
            else:
                self.assertIn('只认这台服务器写的校验结果', row['hint'])
                self.assertIn('Gmail pins itself' if hint == 'unpinned_known' else 'pinned by hand', row['hint'])
        self.assertIsNone(M.servid_hint({**self.cfg(), 'auth_servid': 'mx.google.com'}))


    # ---- F7: with a pin, only the TOPMOST header counts and its servid must equal the pin exactly
    @staticmethod
    def msg(*ar):
        head = ''.join(f'Authentication-Results: {h}\n' for h in ar)
        return email.message_from_string(f'{head}From: {OWNER}\nTo: agent@example.com\nSubject: x\n\nbody\n')

    def test_pinned_only_forged_header_present_fails(self):
        passing = 'dkim=pass header.d=example.com; spf=pass smtp.mailfrom=example.com; dmarc=pass header.from=example.com'
        for forged in ('evil.mx.google.com', 'x.mx.google.com', 'google.com', 'mx.google.com.example.net', 'mx.example.net'):
            self.assertEqual(M.authenticate(self.msg(f'{forged}; {passing}'), OWNER, 'standard', 'mx.google.com'),
                             (False, 'servid'), forged)
            self.assertEqual(M.auth_detail(self.msg(f'{forged}; {passing}'), 'mx.google.com'),
                             {'spf': None, 'dkim': None, 'dmarc': None}, forged)
        self.assertEqual(M.authenticate(self.msg(f'MX.Google.COM; {passing}'), OWNER, 'standard', 'mx.google.com'), (True, 'dmarc'),
                         'case-insensitive exact match')
        self.assertEqual(M.authenticate(self.msg(f'mx.google.com; {passing}'), OWNER, 'standard', 'MX.GOOGLE.COM'), (True, 'dmarc'))
        # No suffix matching at all: a pin on the parent domain does not accept the provider's host either.
        self.assertEqual(M.authenticate(self.msg(f'mx.google.com; {passing}'), OWNER, 'standard', 'google.com'), (False, 'servid'))

    def test_pinned_forged_header_above_provider_fails(self):
        real = 'mx.google.com; dkim=pass header.d=example.com; dmarc=pass header.from=example.com'
        for forged in ('mx.example.net; dmarc=pass header.from=example.com', 'sub.mx.google.com; dmarc=pass header.from=example.com',
                       'dmarc=pass header.from=example.com'):
            self.assertEqual(M.authenticate(self.msg(forged, real), OWNER, 'standard', 'mx.google.com'), (False, 'servid'), forged)
            self.assertEqual(M.auth_detail(self.msg(forged, real), 'mx.google.com')['dmarc'], None, forged)
        self.assertEqual(M.auth_detail(self.msg(real, 'mx.example.net; dmarc=fail'), 'mx.google.com')['dmarc'], 'pass')
        # Unpinned keeps the old rule: the topmost header.
        self.assertEqual(M.authenticate(self.msg(real, 'mx.example.net; dmarc=fail'), OWNER, 'standard'), (True, 'dmarc'))

    # ---- servid_status (read-only, Settings → Mail) and the owner's choice markers
    def test_servid_status_field_contract(self):
        none = {'detected': None, 'pinned': False, 'value': '', 'source': None, 'sample': None}
        self.assertEqual(self.m.view()['servid_status'], none)
        M.save_config(self.st, self.cfg('custom', imap='imap.example.com', smtp='smtp.example.com'))
        self.assertEqual(self.m.view()['servid_status'], none)
        # L3: the sample counts travel with `detected`, so the page can refuse a one-tap pin when some mail had no header.
        s = M.read_state(self.st); s['auth_seen'] = {'checked': 5, 'with_results': 3, 'servid': 'mx.example.com'}; M.save_state(self.st, s)
        self.assertEqual(self.m.view()['servid_status']['sample'], {'checked': 5, 'with_results': 3})
        s = M.read_state(self.st); s['auth_seen'] = {'checked': 5, 'with_results': 5, 'servid': 'mx.example.com'}; M.save_state(self.st, s)
        self.assertEqual(self.m.view()['servid_status']['detected'], 'mx.example.com', 'the latest connection sample')
        self.save({'auth_servid': 'mx.example.com'})
        self.assertEqual(self.m.view()['servid_status'], {'detected': 'mx.example.com', 'pinned': True, 'value': 'mx.example.com',
                                                          'source': 'manual', 'sample': {'checked': 5, 'with_results': 5}})
        self.assertIn(('mail_servid_set', {'host': 'imap.example.com', 'pinned': True}), self.logs)
        # Gmail: auto pin; detected falls back to the known provider value when no sample exists; survives a restart.
        M.save_config(self.st, self.cfg())
        s = M.read_state(self.st); s['auth_seen'] = None; M.save_state(self.st, s)
        self.m._migrate()
        again = M.Mail(self.host).view()['servid_status']
        self.assertEqual(again, {'detected': 'mx.google.com', 'pinned': True, 'value': 'mx.google.com', 'source': 'auto',
                                 'sample': None})
        self.assertIs(M.Mail(self.host).view()['servid_auto'], True)
        # A value pinned before 0.30.5 (no marker at all) reads as manual.
        s = M.read_state(self.st); s['servid_pin'] = s['servid_auto'] = None; M.save_state(self.st, s)
        self.assertEqual(self.m.view()['servid_status']['source'], 'manual')

    def test_manual_pin_never_overwritten_and_clear_is_an_opt_out(self):
        # A Gmail owner pinned by hand before 0.30.5 (no auto record): neither start nor save replaces it.
        M.save_config(self.st, self.cfg(auth_servid='mx.example.com'))
        self.m._migrate()
        self.save({'poll_minutes': 12})
        self.assertEqual(M.read_config(self.st)['auth_servid'], 'mx.example.com')
        self.assertEqual(self.m.view()['servid_status']['source'], 'manual')
        # The owner clears it: recorded as the owner's choice on this host; no later start or save pins Gmail again.
        self.save({'auth_servid': ''})
        self.assertEqual(M.read_state(self.st)['servid_pin'], {'host': 'imap.gmail.com', 'value': '', 'source': 'manual'})
        self.assertEqual(M.read_config(self.st)['servid_optout'], ['imap.gmail.com'])
        for _ in range(2):
            M.Mail(self.host)._migrate()
            self.save({'poll_minutes': 13})
        self.assertEqual(M.read_config(self.st)['auth_servid'], '')
        self.assertEqual(self.pins(), [])
        self.assertEqual(self.m.view()['servid_status'], {'detected': 'mx.google.com', 'pinned': False, 'value': '', 'source': None,
                                                          'sample': None})
        # Adding the pin back by hand still works on an opted-out host (manual), and is the owner's choice.
        self.save({'auth_servid': 'mx.google.com'})
        self.assertEqual(self.m.view()['servid_status']['source'], 'manual')

    def test_optout_survives_a_host_round_trip(self):
        # security r1 L1: gmail (cleared) → custom → gmail must not pin again.
        M.save_config(self.st, self.cfg())
        self.m._migrate()
        self.save({'auth_servid': ''})
        self.save({'preset': 'custom', 'address': OWNER, 'imap': {'host': 'imap.example.com', 'port': 993, 'security': 'ssl'},
                   'smtp': {'host': 'smtp.example.com', 'port': 465, 'security': 'ssl'}})
        self.assertEqual(M.read_config(self.st)['imap']['host'], 'imap.example.com')
        cfg = self.save({'preset': 'gmail', 'address': OWNER})
        self.assertEqual((cfg['imap']['host'], cfg['auth_servid'], cfg['servid_optout']), ('imap.gmail.com', '', ['imap.gmail.com']))
        self.assertEqual(len(self.pins()), 1, 'only the upgrade pin')
        # Dropping the opt-out is a widening change (agentj mail card), never silent.
        self.assertIn('servid_optout', M.widening(cfg, {**cfg, 'servid_optout': []}))
        # Without an opt-out a fresh Gmail setup on a known host pins (visible in the preview, see test_0304_mail_sign_js).
        M.save_config(self.st, M.blank_config())
        self.assertEqual(self.save({'preset': 'gmail', 'address': OWNER})['auth_servid'], 'mx.google.com')

    def test_raw_ctl_clear_records_optout(self):
        # security r2 N1: serve's mail_cfg handler runs the same merge, so a raw ctl clear (no CLI merge) still opts out.
        import inspect, pathlib as _p
        from agentj import serve
        src = inspect.getsource(serve)
        self.assertIn('mail_mod.servid_merge(old, mail_mod.validate({**old, **{k: v for k, v in (req.get("cfg") or {}).items()', src)
        M.save_config(self.st, self.cfg())
        self.m._migrate()
        old = M.read_config(self.st)
        self.assertEqual(old['auth_servid'], 'mx.google.com')
        raw = {'auth_servid': ''}                      # what a socket client sends without the CLI's merge
        new = M.validate(M.servid_merge(old, M.validate({**old, **raw})))
        self.assertEqual((new['auth_servid'], new['servid_optout']), ('', ['imap.gmail.com']))
        again = M.validate(M.servid_merge(new, M.validate({**new})))   # idempotent: later saves keep it cleared
        self.assertEqual((again['auth_servid'], again['servid_optout']), ('', ['imap.gmail.com']))

    def test_cli_setup_card_shows_the_pin_it_applies(self):
        # security r1 M1: the `agentj mail` CLI sends the merged config, so the ctl card's before/after, widening() and the
        # executed set_config all carry the same auth_servid.
        import argparse
        import io
        from contextlib import redirect_stderr, redirect_stdout
        sent = []
        p = argparse.ArgumentParser(); sub = p.add_subparsers(); M.add_parser(sub)
        a = p.parse_args(['mail', 'setup', OWNER, '--preset', 'gmail', '--json'])   # run() maps --address onto it
        with mock.patch('agentj.state.State', lambda *x, **k: self.st), \
                mock.patch('agentj.names.ctl_call', side_effect=lambda st, req, *x: sent.append(req) or {'ok': True, 'applied': True}), \
                redirect_stdout(out := io.StringIO()), redirect_stderr(io.StringIO()):
            M.cmd(a)
        self.assertEqual(len(sent), 1, out.getvalue())
        new = sent[0]['cfg']
        old = M.read_config(self.st)
        self.assertEqual(new['auth_servid'], 'mx.google.com')
        self.assertIn('auth_servid', M.widening(old, new))
        card = action_policy.summary('agentj mail', {'changes': M.widening(old, new), 'before': old, 'after': new},
                                     lang='zh', presentation=True)['summary']
        self.assertIn('可信邮件服务器: 未设置 → mx.google.com', card)
        saved = self.m.set_config(new)
        self.assertEqual({k: v for k, v in saved.items() if k != 'generation'}, {k: v for k, v in new.items() if k != 'generation'})
        self.assertEqual(self.pins()[-1]['via'], 'save')
        self.assertEqual(self.m.view()['servid_status']['source'], 'auto')
        # The raw set_config (mail_allow, contacts) never adds a pin on its own.
        M.save_config(self.st, M.blank_config())
        self.assertEqual(self.m.set_config(self.cfg())['auth_servid'], '')

    def test_autopin_tells_the_owner_once_and_owner_mail_counts(self):
        # security r1 M2: the pin ENABLES owner recognition (owner_source needs it) — one note, a doctor line while auto.
        notes = []
        self.host.hist_add = lambda item, text, status: notes.append(text)
        M.save_config(self.st, {**self.cfg(), 'enabled': True})
        good = self.msg('mx.google.com; dkim=pass header.d=example.com; spf=pass smtp.mailfrom=example.com; '
                        'dmarc=pass header.from=example.com')
        def source():
            cfg = M.read_config(self.st)
            passed, how = M.authenticate(good, OWNER, cfg['auth'], cfg['auth_servid'])
            v = SimpleNamespace(ok=passed, role='owner', auth=how, addr=OWNER)
            return M.owner_source(v, servid=cfg['auth_servid'])
        self.assertEqual(source(), provenance.email_guest(OWNER), 'unpinned: owner-address mail is ordinary mail')
        self.m._migrate()
        self.assertEqual(source(), provenance.email_owner(OWNER), 'after the autopin: owner mail is the owner')
        self.assertEqual(len(notes), 1)
        self.assertIn('已按收信服务器自动固定校验服务器 mx.google.com', notes[0])
        self.assertIn('设置→邮件清除', notes[0])
        self.assertIn(('mail_servid_notice', {'host': 'imap.gmail.com', 'via': 'upgrade'}), self.logs)
        row = doctor.check_mail_owner(self.st)
        self.assertEqual((row['id'], row['status']), ('mail-owner', doctor.OK))
        self.assertIn('mx.google.com', row['summary'])
        self.assertIn('pinned automatically', row['summary'])
        # Not twice for the same host (a later save-path autopin after the pin was dropped without an opt-out).
        M.save_config(self.st, {**M.read_config(self.st), 'auth_servid': ''})
        self.save({'poll_minutes': 9})
        self.assertEqual(M.read_config(self.st)['auth_servid'], 'mx.google.com')
        self.assertEqual(len(notes), 1)
        # A manual pin: no info line; no owner row: no note.
        self.save({'auth_servid': ''}); self.save({'auth_servid': 'mx.google.com'})
        self.assertIsNone(doctor.check_mail_owner(self.st))
        st = State(pathlib.Path(tempfile.mkdtemp(dir=self.st.root)) / 's'); st.init()
        m2 = M.Mail(SimpleNamespace(**{**vars(self.host), 'st': st}))
        M.save_config(st, {**self.cfg(), 'allow': [{'addr': 'guest@example.com', 'role': 'guest'}]})
        m2._migrate()
        self.assertEqual(M.read_config(st)['auth_servid'], 'mx.google.com')
        self.assertEqual(len(notes), 1)

    def test_migration_runs_once_per_install(self):
        # security r1 M1: after the upgrade, a config written without the previewed merge is never pinned at a later start.
        self.m._migrate()                                         # an empty install upgrades first
        M.save_config(self.st, self.cfg())
        self.m._migrate()
        self.assertEqual(M.read_config(self.st)['auth_servid'], '')
        self.assertEqual(self.m.view()['servid_hint'], 'unpinned_known')

    def test_status_cli_prints_pin_and_source(self):
        import argparse
        import io
        from contextlib import redirect_stderr, redirect_stdout
        M.save_config(self.st, self.cfg())
        self.m._migrate()
        p = argparse.ArgumentParser(); sub = p.add_subparsers(); M.add_parser(sub)
        a = p.parse_args(['mail', 'status', '--json'])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch('agentj.state.State', lambda *x, **k: self.st), mock.patch('agentj.names.ctl_call', return_value=None), \
                redirect_stdout(out), redirect_stderr(err):
            M.cmd(a)
        import json
        self.assertEqual(json.loads(out.getvalue())['servid_status']['source'], 'auto')
        self.assertIn('mx.google.com (自动 / auto)', err.getvalue())


if __name__ == '__main__':
    unittest.main()
