"""0330: phone enqueue -> native acceptance; native launch -> transcript -> main Send.

The task transport is an isolated Claude-format transcript, never the owner's session.
A real local subprocess supplies the exit evidence; no completion registry is forged.
"""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP
import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentj import claude_inbound
import test_p262_done_notify as done_fixture
import test_p424_held as held_fixture


class NativeCompletion(unittest.TestCase):
    setUp = done_fixture.Completion.setUp

    def launch(self, tid='native-job'):
        board = self.h.busy
        board.trusted = lambda: True
        board.hook_event({'hook_event_name':'PostToolUse', 'session_id':'fixture', 'tool_name':'Bash',
            'tool_input':{'command':'python -c pass', 'description':'Login check', 'run_in_background':True},
            'tool_response':{'backgroundTaskId':tid}}, 'shared-claude')
        return board

    def record(self, status='completed', code=None):
        text = '<task-notification><task-id>native-job</task-id><status>' + status + '</status><summary>Login check</summary>'
        return text + (f'<exit-code>{code}</exit-code>' if code is not None else '') + '</task-notification>'

    def scan(self, records):
        path = self.root/'native.jsonl'
        path.write_text(''.join(json.dumps(r)+'\n' for r in records))
        self.h.busy.reconcile_claude('shared-claude:fixture', str(path))

    def test_native_completed_without_code_is_known_completion(self):
        self.launch()
        self.scan([{'type':'user', 'turnOrigin':'task_notification',
            'message':{'content':self.record()}}])
        send = self.n.tick(now=10**12)
        self.assertIsNotNone(send)
        self.assertNotIn('结果不明', send.text)
        self.assertIn('已结束', send.text)
        self.assertIn('退出码 未知', send.text)

    def test_real_exit_queue_consumption_reaches_main_once(self):
        board = self.launch()
        proc = subprocess.run([sys.executable, '-I', '-c', 'print("local login probe")'],
                              capture_output=True, text=True, check=True)
        content = self.record(code=proc.returncode)
        records = [{'type':'queue-operation','operation':op,'content':content,
                    'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat()}
                   for op in ('enqueue','remove')]
        self.scan(records[:1])
        self.assertEqual(len(board.mem), 1, 'queued does not mean completed')
        self.scan(records)
        self.assertFalse(board.mem, 'native dequeued completion settles the live item')
        send = self.n.tick(now=10**12)
        self.assertIsNotNone(send)
        self.assertIs(send, self.a.q.get_nowait())
        self.assertEqual(send.source['kind'], 'system')
        self.assertIn('退出码 0', send.text)
        self.assertNotIn('结果不明', send.text)
        send.state='delivered'; self.n.complete(send)
        self.scan(records)
        self.assertIsNone(self.n.tick(now=10**12+61), 'one completion across repeated transcript scans')

    def test_typed_peer_sidechain_unpaired_and_wrong_job_do_not_complete(self):
        self.launch()
        content=self.record(code=0)
        self.scan([
            {'type':'user','message':{'content':content}},
            {'type':'user','origin':{'kind':'peer'},'message':{'content':content}},
            {'type':'user','turnOrigin':'task_notification','isSidechain':True,'message':{'content':content}},
            {'type':'queue-operation','operation':'remove','content':content},
            {'type':'queue-operation','operation':'enqueue','content':content.replace('native-job','other-job')},
            {'type':'queue-operation','operation':'remove','content':content},
        ])
        self.assertEqual(len(self.h.busy.mem),1)
        self.assertIsNone(self.n.tick(now=10**12))


class QueuedPhone(unittest.IsolatedAsyncioTestCase):
    agent = held_fixture.Held.agent
    not_taken = held_fixture.Held.not_taken
    async def test_busy_notice_disappears_on_native_acceptance(self):
        for lang in ('zh','en'):
            with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
                host=held_fixture.Host424(root,lang); agent=self.agent(host)
                tid=await self.not_taken(host, agent, 'One queued phone instruction', reason=None)
                page=host.hist.get(tid)
                text=page['reply']['text']
                self.assertEqual(text, '')
                self.assertTrue(page['card']['queued'])
                for fragment in ('对话框','核实','desktop','dialog','尚未接收'):
                    self.assertNotIn(fragment,text)
                agent._held_seen(agent.held[0][1])
                self.assertEqual(host.hist.get(tid)['reply']['text'], '')
                self.assertEqual(host.hist.get(tid)['card'],{})

    def test_real_setting_hold_keeps_specific_diagnosis(self):
        with patch.object(claude_inbound,'hold_reason',return_value='managed'):
            self.assertIn('组织策略',claude_inbound.turn_notice('/fixture'))


if __name__=='__main__': unittest.main()
