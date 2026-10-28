"""P567: domain visitors, compact confirmation and credential boundaries (offline)."""
import _hermetic  # noqa: F401
import email
import email.policy
import unittest
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest import mock
from agentj import mail as M, privacy, action_policy, cmdrun

class Domains(unittest.TestCase):
    def cfg(self, rows, auth='off'):
        return M.validate({**M.blank_config(), 'allow': rows, 'auth': auth})

    def msg(self, addr='no-reply-random@mail.anthropic.com', ar='mx.example; dkim=pass header.d=anthropic.com'):
        return email.message_from_string(f'From: {addr}\nMessage-ID: <one@example>\nAuthentication-Results: {ar}\n\nNotice', policy=email.policy.default)

    def test_old_format(self):
        self.assertEqual(self.cfg(['OWNER@EXAMPLE.COM'])['allow'], [{'addr':'OWNER@example.com', 'role':'owner'}])

    def test_disk_roundtrip_with_legacy_and_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            from agentj.state import State
            st=State(Path(tmp))
            c=self.cfg(['owner@example.com', {'addr':'@anthropic.com','role':'guest','subdomains':True}])
            M.save_config(st,c)
            self.assertEqual(M.read_config(st),c)

    def test_domain_normalized_guest(self):
        self.assertEqual(self.cfg([{'addr':'@Anthropic.COM','role':'guest'}])['allow'], [{'addr':'@anthropic.com','role':'guest'}])
        with self.assertRaises(M.MailError): self.cfg([{'addr':'@anthropic.com','role':'owner'}])

    def test_public_and_malformed(self):
        for dom in ['gmail.com','mail.gmail.com','googlemail.com','qq.com','163.com','126.com','outlook.com','hotmail.com','live.com','icloud.com','yahoo.com','foxmail.com','sina.com','com','bad..example','-bad.example']:
            with self.subTest(dom=dom), self.assertRaises(M.MailError): self.cfg([{'addr':'@'+dom,'role':'guest'}])

    def test_subdomains_opt_in_and_boundaries(self):
        c=self.cfg([{'addr':'@anthropic.com','role':'guest'}])
        self.assertFalse(M.judge(self.msg(),c,[]).ok)
        c=self.cfg([{'addr':'@anthropic.com','role':'guest','subdomains':True}])
        v=M.judge(self.msg(),c,[])
        self.assertTrue(v.ok); self.assertEqual(v.role,'guest')
        for addr in ['n@evilanthropic.com','n@anthropic.com.evil.example']:
            self.assertFalse(M.judge(self.msg(addr),c,[]).ok)

    def test_strict_even_when_off(self):
        c=self.cfg([{'addr':'@anthropic.com','role':'guest','subdomains':True}])
        for ar in ['mx; spf=pass smtp.mailfrom=anthropic.com','mx; dkim=pass header.d=evil.example','mx; dmarc=fail header.from=anthropic.com','mx; none']:
            self.assertFalse(M.judge(self.msg(ar=ar),c,[]).ok)
        for ar in ['mx; dmarc=pass header.from=anthropic.com','mx; dkim=pass header.d=mail.anthropic.com']:
            self.assertTrue(M.judge(self.msg(ar=ar),c,[]).ok)

    def test_preview_only_diff(self):
        before=self.cfg(['old@example.com'])
        summary=action_policy._mail_summary({'kind':'mail_set','before':before,'cfg':{'allow':before['allow']+[{'addr':'new@example.com','role':'guest'}]}},'en')
        self.assertNotIn('old@example.com',summary); self.assertIn('Added:',summary); self.assertIn('new@example.com',summary)

    def test_cli_confirmation_also_omits_history(self):
        before=self.cfg(['old@example.com'])
        after=self.cfg(['old@example.com', {'addr':'new@example.com','role':'guest'}])
        shown=action_policy.summary('agentj mail', {'before':before,'after':after,'changes':['allow+new@example.com']}, lang='en', presentation=True)
        self.assertIn('Added:',shown['summary'])
        self.assertNotIn('old@example.com',str(shown))
        self.assertIn('Request SHA-256:',shown['details'])

class Boundaries(unittest.TestCase):
    def test_placeholder_and_backtick(self):
        for value in ['她的新Key','新Key','<your-key>','说明文字']:
            s=f'`EXAMPLE_KEY={value}`，保存后重启服务。'
            self.assertEqual(privacy.owner_text(s),s)
        for quote in [chr(34), chr(39), chr(96)]:
            value = 'EXAMPLE_KEY=' + quote + '<your-key>' + quote
            self.assertEqual(privacy.owner_text(value),value)
        s='`EXAMPLE_KEY=Ab9!Cd8?Ef7%Gh6`，保存后重启服务。'
        self.assertEqual(privacy.owner_text(s),'`EXAMPLE_KEY=<redacted>`，保存后重启服务。')

    def test_installed_only(self):
        from agentj import harness
        with mock.patch.object(harness, 'detect', return_value={'harnesses':[
                {'name':'kimi','installed':True,'supported':True},
                {'name':'codex','installed':False,'supported':True}]}):
            names=cmdrun.available_commands()
        self.assertIn('switch_kimi',names); self.assertNotIn('switch_codex',names)
        self.assertIn('doctor',names)

    def test_six_fixed_force_commands(self):
        for h in ['claude','codex','opencode','kimi','qwen','dsh']:
            name='switch_'+h
            self.assertEqual(cmdrun.RUN_CMDS[name][0],('agent','switch',h,'--force'))
            self.assertNotIn(name,cmdrun.READ_CMDS)
            self.assertEqual(cmdrun.argv(name)[-4:],['agent','switch',h,'--force'])
