"""#562: automatic approval is an audit entry, never an owner notification."""
import _hermetic  # noqa: F401
import ast
import asyncio
import json
from types import SimpleNamespace
from pathlib import Path
from unittest import mock
import unittest
from agentj import activity, approval_audit, danger
from test_p551_confirm_levels import Base

STOP = 'systemctl --user stop agentj-background-example'
DISPATCH = 'systemd-run --user --collect --unit=agentj-background-example codex exec --sandbox workspace-write "check the report"'
BRIEF = "cat > PROMPT-background.md <<'EOF'\nDo not change passwords or API_TOKEN.\nEOF"
MIXED = STOP + '; ' + BRIEF + '\n' + DISPATCH


class AuditLabels(unittest.TestCase):
    def setUp(self):
        import os, tempfile
        folder = tempfile.TemporaryDirectory(prefix="audit-labels-562-")
        self.addCleanup(folder.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(folder.name)

    def presentation(self, command, lang='zh', effects=()):
        inp = {'command': command}
        return approval_audit.presentation({'effects': list(effects)}, 'Bash', inp, lang=lang)

    def test_service_legacy_category_does_not_become_effect(self):
        self.assertIn('credentials', danger.classify('Bash', {'command': DISPATCH}).cats)
        for command, text in ((STOP, '停止后台活'), (DISPATCH, '派出后台活'), (BRIEF, '写派单文件'),
                              (MIXED, '重新派出后台活 · 写派单文件')):
            with self.subTest(command=command):
                result = self.presentation(command)
                self.assertEqual(result, {'summary': text, 'cats': [], 'effects': []})
        self.assertEqual(self.presentation(MIXED, 'en')['summary'], 'Dispatch the background job again · Write a job brief')

    def test_literal_service_words_do_not_supply_job_labels(self):
        result = self.presentation("printf '%s' 'systemctl --user stop job; systemd-run codex exec' > report.md")
        self.assertEqual(result['summary'], '写文件')
        self.assertEqual(result['cats'], [])

    def test_service_data_operands_and_foreground_harness_do_not_claim_background_stop(self):
        for command in ('systemctl --user status stop', 'systemctl --user --property stop status ordinary',
                        'systemctl --user --property=stop status ordinary', 'codex exec "check the report"'):
            with self.subTest(command=command):
                self.assertEqual(self.presentation(command)['summary'], '执行普通命令')
        self.assertEqual(self.presentation('systemctl --user --no-block stop ordinary')['summary'], '停止后台活')

    def test_adjacent_real_secret_keeps_its_real_effect_label(self):
        result = self.presentation(DISPATCH + '; cat ~/.ssh/id_rsa', effects=['sensitive_storage'])
        self.assertEqual(result['effects'], ['sensitive_storage'])
        self.assertEqual(result['cats'], ['credentials'])
        self.assertEqual(result['summary'], '访问或修改密码、密钥')

    def test_all_closed_effects_are_human_labels(self):
        self.assertEqual(set(approval_audit._EFFECT), {'sensitive_storage','sensitive_delivery','money','irreversible_destroy','approval_root'})
        for effect in approval_audit._EFFECT:
            for lang in ('en', 'zh'):
                result = approval_audit.presentation({'effects': [effect]}, 'Bash', {}, lang=lang)
                self.assertTrue(result['summary'])
                self.assertEqual(result['effects'], [effect])
                self.assertNotIn('Bash', result['summary'])

    def test_no_auto_wire_dispatch_remains_in_host(self):
        from agentj import serve
        tree = ast.parse(Path(serve.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('_post', 'send_app'):
                for child in ast.walk(node):
                    if isinstance(child, ast.Dict):
                        self.assertFalse(any(isinstance(k, ast.Constant) and k.value == 't' and
                                             isinstance(v, ast.Constant) and v.value == 'auto' for k, v in zip(child.keys, child.values)))


class HostAudit562(Base):
    def test_real_full_access_keeps_audit_but_sends_no_auto_message(self):
        self.harness()
        self.sent.clear()
        for command, expected in ((STOP, '停止后台活'), (BRIEF, '写派单文件')):
            with self.subTest(command=command):
                self.sent.clear()
                result = self.ask({'cmd': command}, tool='exec_command')
                self.assertEqual(result['behavior'], 'allow', result)
                self.assertEqual(self.last()['reason'], 'level_harness')
                rows = activity.all_since(self.st)
                row = [r for r in rows if r.get('k') == 'auto'][-1]
                self.assertEqual(row['summary'], expected)
                self.assertEqual(row['cats'], [])
                self.assertEqual(row['effects'], [])
                self.assertNotIn('approval_receipt', row, 'presentation is not a closed-effect proof')
                self.assertFalse(any(msg.get('t') in ('auto', 'agent_notice', 'ask') for _, msg in self.sent))

    def test_registered_bash_dispatch_is_silent_and_still_audited(self):
        self.harness()
        self.sent.clear()
        with mock.patch.object(self.host, '_dispatch_check', return_value=('registered.service', 'ok')), \
                mock.patch.object(self.host, '_peer_launch_note', new=mock.AsyncMock()):
            self.assertEqual(self.ask(DISPATCH)['behavior'], 'allow')
        self.assertEqual(self.last()['reason'], 'dispatch')
        row = [r for r in activity.all_since(self.st) if r.get('k') == 'auto'][-1]
        self.assertEqual(row['summary'], '派出后台活')
        self.assertEqual(row['cats'], [])
        self.assertFalse(any(msg.get('t') in ('auto', 'agent_notice', 'ask') for _, msg in self.sent))

    def test_agent_dispatch_registry_route_retains_one_audit_row(self):
        # Standard mode reaches the registry branch, before any Full access path.
        result = self.ask({'description': 'Check a report', 'prompt': 'Read the ordinary report'}, tool='Agent')
        self.assertEqual(result['behavior'], 'allow', result)
        self.assertEqual(self.last()['reason'], 'dispatch')
        rows = [r for r in activity.all_since(self.st) if r.get('k') == 'auto']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['summary'], rows[0]['by'], rows[0]['cats']), ('派出后台活', 'dispatch', []))
        self.assertFalse(any(msg.get('t') in ('auto', 'agent_notice', 'ask') for _, msg in self.sent))

    def test_same_owner_authorized_route_retains_one_audit_row(self):
        # The established peer verifier is a boundary seam, not a new grant:
        # crypto verification has its own peer-plan suite. Exercise Host.ask's
        # existing exact-proof allow route with the verifier's contract value.
        proof = {'id': '562-proof', 'task_scope': json.dumps({'uv': False})}
        authorized = mock.Mock(return_value=proof)
        self.host.peers = SimpleNamespace(
            same_owner=SimpleNamespace(explicit_plan=lambda data: False, authorized=authorized),
            xfer=SimpleNamespace(task_scope=lambda: None))
        inp = {'file_path': str(Path(self.tmp.name) / 'work' / 'PROMPT-background.md'), 'content': 'Check the report.'}
        with mock.patch('agentj.serve._same_owner_turn', return_value=True):
            result = self.ask(inp, tool='Write')
        self.assertEqual(result['behavior'], 'allow', result)
        self.assertEqual(self.last()['reason'], 'same_owner')
        self.assertEqual(self.last()['peer_proof'], proof)
        authorized.assert_called_once()
        rows = [r for r in activity.all_since(self.st) if r.get('k') == 'auto']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['summary'], rows[0]['by'], rows[0]['cats']), ('写派单文件', 'same_owner', []))
        self.assertFalse(any(msg.get('t') in ('auto', 'agent_notice', 'ask') for _, msg in self.sent))

    def test_unregistered_dispatch_keeps_its_existing_uv_boundary(self):
        self.harness()
        result = self.ask({'cmd': DISPATCH}, tool='exec_command')
        # Later1125/1140/1205 explicitly permit ordinary systemd user CEO dispatch.
        self.assertEqual(result['behavior'], 'allow')
        self.assertEqual(self.cards(), [])
        self.assertTrue(any(r.get('k') == 'auto' for r in activity.all_since(self.st)))

    def test_presentation_failure_cannot_change_permission(self):
        self.harness()
        with mock.patch('agentj.approval_audit.presentation', side_effect=ValueError('display failure')):
            self.assertEqual(self.ask({'cmd': STOP}, tool='exec_command')['behavior'], 'allow')
        self.assertEqual(self.ask('cat ~/.ssh/id_rsa')['behavior'], 'deny')
        self.assertTrue(self.cards()[-1]['passkey_required'])

    def test_existing_signed_root_is_not_changed_by_audit_projection(self):
        self.harness()
        self.ask({'cmd': STOP}, tool='exec_command')
        original = danger.classify('Bash', {'command': DISPATCH})
        self.assertIn('credentials', original.cats)  # policy input is untouched
        self.assertEqual(self.last()['cats'], self.host._classify('exec_command', {'cmd': STOP}).cats)
        row = [r for r in activity.all_since(self.st) if r.get('k') == 'auto'][-1]
        self.assertEqual(row['cats'], [])


if __name__ == '__main__':
    unittest.main()
