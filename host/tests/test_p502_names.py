"""P502: naming guidance uses the installed seat name; owner names remain data."""
import _hermetic
import unittest
import json
from unittest.mock import patch
from types import SimpleNamespace
from agentj import host_diagnostics
from agentj import main_identity
from agentj.text import new_agent_name_problem

class Naming(unittest.TestCase):
    def test_all_harnesses_get_current_seat_lookup_and_return_wording(self):
        for lang in ('zh','en'):
            for harness in main_identity.MECHANISMS:
                prompt=main_identity.prompt({'language':lang},harness=harness)
                self.assertIn('`agentj status --json`',prompt)
                self.assertIn('agent_display_name',prompt)
                self.assertIn('请回 Agent J 网页跟 <agent_display_name> 沟通' if lang=='zh' else 'Return to the Agent J web app to talk with <agent_display_name>',prompt)
                self.assertNotIn('贾维斯',prompt)
                self.assertNotIn('Jarvis',prompt)
    def test_owner_chosen_name_is_not_a_reserved_brand(self):
        for name in ('贾维斯','Jarvis','Wren','小助手'):
            self.assertIsNone(new_agent_name_problem(name),name)

class FencedName(unittest.TestCase):
    def test_status_returns_current_registered_name_only_and_never_a_preset(self):
        name=['贾维斯']
        st=SimpleNamespace(agent_name=lambda:name[0])
        with patch('agentj.names.ctl_call',return_value={'ok':True,'agent':'claude','secret':'never project me'}):
            for current in ['贾维斯','Wren',None]:
                name[0]=current
                res=host_diagnostics.execute(st,{'t':'host_diagnostics','op':'status'})
                self.assertEqual(res['exit'],0)
                obj=json.loads(res['output']);self.assertEqual(obj['agent_name'],current)
                from agentj.text import agent_label
                self.assertEqual(obj['agent_display_name'],agent_label(current))
                self.assertNotIn('secret',obj)
            denied=host_diagnostics.execute(st,{'t':'host_diagnostics','op':'status','path':'/private'})
            self.assertEqual(denied['exit'],1)
    def test_fenced_cli_uses_existing_rpc_without_local_state_read(self):
        from agentj import cli
        with patch('agentj.host_diagnostics.agent_side',return_value=True), \
             patch('agentj.elevate.client_request',return_value={'exit':0,'output':'{"agent_name":"Wren"}\n'}) as request, \
             patch.object(cli,'_need_init',side_effect=AssertionError('must not read fenced state')):
            with self.assertRaises(SystemExit) as done:cli.cmd_status(SimpleNamespace(json=True))
            self.assertEqual(done.exception.code,0)
            self.assertEqual(request.call_args.args[1],{'t':'host_diagnostics','op':'status'})

class MailNames(unittest.TestCase):
    def test_mail_sender_and_bilingual_body_use_raw_seat_name_once(self):
        import email.utils
        from agentj import mail, mail_template as mt
        for name in ('Wren','贾维斯','Agent J-Wren',''):
            label='Agent J - '+name if name else 'Agent J'
            cfg={'address':'owner@example.com'}
            for message in [mail.build_reply(cfg,{'to':'recipient@example.com'},'ready',name,'en'),
                            mail.build_notes(cfg,['recipient@example.com'],'notes','ready',name)]:
                self.assertEqual(email.utils.parseaddr(str(message['From']))[0],label)
            for lang in ('zh','en'):
                subject,text,html=mt.render(lang=lang,agent=name,src=None,reply='ready')
                self.assertIn(label,text);self.assertIn(label,html)
                text,html,_=mt.render_send(lang=lang,agent=name,subject='notes',body='ready')
                self.assertIn(label,text);self.assertIn(label,html)

class DisplayNameRegression(unittest.TestCase):
    def test_display_name_is_idempotent_across_host_outputs(self):
        import email.utils
        from agentj import mail, mail_template, channel_avatar
        cases=[('', 'Agent J'), ('Agent J', 'Agent J'), ('Agent J - Father@TB', 'Agent J - Father@TB'), ('Father@TB', 'Agent J - Father@TB'), ('  aGeNt   j  ', 'Agent J'), (None, 'Agent J')]
        for name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(mail_template.agent_label(name), expected)
                self.assertEqual(mail_template.agent_label(expected), expected)
                host=SimpleNamespace(st=SimpleNamespace(agent_name=lambda:name))
                self.assertEqual(channel_avatar.handoff(host), f'已转给 {expected}，请回 Agent J 网页跟它确认')
                message=mail.build_reply({'address':'owner@example.com'}, {'to':'recipient@example.com'}, 'ready', name, 'en')
                self.assertEqual(email.utils.parseaddr(str(message['From']))[0], expected)

    def test_bundled_name_instructions_use_formatted_status_label(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]/'agentj'/'skills'
        for skill, prefix in (('agentj-config','Brand and name:'), ('agentj-wechat','For a held action,')):
            lines=[line for line in (root/skill/'SKILL.md').read_text().splitlines() if line.startswith(prefix)]
            self.assertEqual(len(lines),1)
            self.assertIn('agent_display_name',lines[0])
            self.assertNotIn('agent_name',lines[0])
            self.assertNotIn('Agent J - <',lines[0])
