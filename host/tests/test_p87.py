import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import _hermetic
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from agentj.serve import Host
from agentj.transcript_input import human_input
class EmptyReplies(unittest.TestCase):
    def test_empty_callbacks_do_not_emit_history_or_push(self):
        h=SimpleNamespace(hist_add=Mock(),hist_update=Mock(),emit=Mock(),turn_text=False)
        for text in ('', '  \n'):
            Host.agent_text(h,text);Host.desktop_text(h,text);Host.desktop_input(h,text)
        h.hist_add.assert_not_called();h.hist_update.assert_not_called();h.emit.assert_not_called()
        self.assertFalse(h.turn_text)
    def test_native_generated_parts_and_real_input(self):
        for r,c,t in [({'synthetic':True},[],'summary'), ({},[{'type':'text','synthetic':True}], 'hook'), ({},[{'type':'tool_result'}], 'tool'), ({},[], '<system-reminder>hook</system-reminder>')]:
            self.assertFalse(human_input(r,c,t))
        self.assertTrue(human_input({},[], 'Please explain Base directory for this skill:'))
        self.assertTrue(human_input({},[], '人类的输入'))

class VoiceBudget(unittest.IsolatedAsyncioTestCase):
    def test_duration_budget_and_true_reason(self):
        from agentj import compose
        self.assertGreater(compose.asr_budget(121), 60)
        self.assertEqual(compose.asr_budget(600), 3780)
        self.assertEqual(compose.asr_budget(None), compose.SAY_ASR_BUDGET)
        self.assertEqual(compose.asr_budget(float('nan')), compose.SAY_ASR_BUDGET)
        for lang in ('zh', 'en'):
            rendered = compose.render('', [{'path':'/tmp/take.wav','mime':'audio/wav','bytes':1,'asr':{'ok':False,'why':'timeout'}}], lang)
            self.assertIn('/tmp/take.wav', rendered)
            self.assertNotIn('60', rendered)

    async def test_fifo_second_recording_has_full_processing_budget(self):
        import asyncio
        from agentj import compose
        order=[]
        class Engine:
            def transcribe(self, path, timeout_s, **kwargs):
                import time
                order.append((path, timeout_s))
                time.sleep(.02)
                return {'ok': True, 'text': path}
        h=SimpleNamespace(asr_lock=None,asr=Engine(),preferences={},st=SimpleNamespace(root='/tmp'))
        results=await asyncio.gather(Host._transcribe(h,'long',compose.asr_budget(600)), Host._transcribe(h,'second',compose.asr_budget(10)))
        self.assertEqual(order, [('long',3780),('second',240)])
        self.assertEqual([r['text'] for r in results], ['long','second'])

    def test_phone_and_conversion_limits_cover_ten_minutes(self):
        from agentj import uploads, serve
        self.assertGreaterEqual(uploads.ASR_MAX, 44+600*16000*2)
        self.assertGreaterEqual(serve.FF_MAX_SECS, 600)

class OpenCodeMeta(unittest.TestCase):
    def test_owner_v2_does_not_mirror_skill_or_synthetic_input(self):
        from agentj.shared_opencode2 import OwnerOpenCodeV2Agent
        host=SimpleNamespace(desktop_input=Mock())
        a=SimpleNamespace(sid='s',phone_inbox=set(),phone_texts=[],desktop_end_if_open=Mock(),desktop_turn=False,host=host)
        for i,(text,extra) in enumerate([('Base directory for this skill: /tmp/skill',{}),('hook output',{'synthetic':True}),('human input',{})]):
            OwnerOpenCodeV2Agent.on_event(a,{'type':'session.inbox.enqueued','data':{'sessionID':'s','inboxID':str(i),'item':{'type':'user','payload':{'text':text},**extra}}})
        host.desktop_input.assert_called_once_with('human input')
