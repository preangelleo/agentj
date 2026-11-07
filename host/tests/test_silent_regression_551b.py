import _hermetic
import asyncio
import json
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from agentj import silent
from agentj.history import History
from agentj.readmark import ReadMark
from agentj.serve import Host
from agentj.shared import SharedClaudeAgent
from agentj.state import State

MARKERS = ('〔不回群〕', '〔静默消息〕', '[silent]')

class Regression(unittest.TestCase):
    def test_formal_web_suppresses_final_and_ready_history(self):
        # Preserve the exact released 0.27.1 reader and also check the merged reader.
        root = Path(__file__).resolve().parents[3]
        js = (root/'web/public/js/relay.js').read_text()
        predicate = re.search(r'const isSilent = [^;]+;', js).group()
        markers = re.search(r'const SILENT_MARKERS = [^;]+;', js)
        current = (markers.group() + '\n' if markers else '') + predicate
        legacy = 'const isSilent = text => typeof text === "string" && text.trim() === "\\u3014\\u4e0d\\u56de\\u7fa4\\u3015";'  # byte-exact source 6cc8b64754
        frames = []
        for marker in MARKERS:
            for text in (marker, marker+'body', 'body'+marker):
                t = {'id':7,'src':{'k':'phone','text':'input'},'reply':{'text':text},'end':'done'}
                for frame in ({'t':'hist_turn','turn':t}, {'t':'hist_page','turns':[t]}):
                    out = silent.public_message(frame)
                    row = out.get('turn') or out['turns'][0]
                    self.assertFalse(ReadMark.visible(t))
                    frames.append(row['reply']['text'])
        for reader in (legacy, current):
            with self.subTest(reader='legacy' if reader == legacy else 'current'):
                script = reader+'\nconst rows='+json.dumps(frames)+'; if (!rows.every(isSilent)) process.exit(1);'
                result = subprocess.run(['node','-e',script],capture_output=True,text=True)
                self.assertEqual(0,result.returncode,result.stderr)
        normal = {'t':'hist_turn','turn':{'reply':{'text':'ordinary answer'}}}
        self.assertEqual(normal,silent.public_message(normal))

    def test_real_shared_claude_committed_final_logs_every_silent_turn(self):
        for marker in MARKERS:
            for text in (marker, marker+'body', 'body'+marker):
                with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                    st = State(Path(tmp)); st.log = Mock()
                    hist = History(st,on=False)
                    page = hist.add({'k':'phone','text':'input'},'', 'open')
                    h = NS(st=st,hist=hist,cur_turn=page['id'],turn_text=False,turn_shown=False,jev_plan='',
                           wait_note=None,hist_emit=Mock(),emit=Mock(),_remember=Mock(),_post=Mock(),turn_queued_note=Mock())
                    h.hist_update = lambda tid,**kw: Host.hist_update(h,tid,**kw)
                    h.agent_text = lambda text: Host.agent_text(h,text)
                    a = NS(untrusted_state=None,transcript=None,end_claimed=None,phone_turn=True,phone_seen=True,
                           phone_claim=False,streamed=set(),host=h,done=asyncio.Event(),_limit_check=Mock())
                    # Real Stop's last_assistant_message is a plain string (the #7271 shape).
                    SharedClaudeAgent._finish_stop(a,text)
                    self.assertTrue(hist.get(page['id'])['silent'])
                    self.assertFalse(ReadMark.visible(hist.get(page['id'])))
                    self.assertFalse(h.turn_text)
                    self.assertTrue(a.done.is_set())
                    folded = [c for c in st.log.call_args_list if c.args and c.args[0]=='silent_folded']
                    self.assertEqual(1,len(folded))
                    h._remember.assert_not_called()

    def test_desktop_empty_turn_and_background_final_are_audited(self):
        for tid in (7, None):
            h = NS(desktop_turn=tid,hist=NS(parts={},get=lambda tid:{'reply':{'text':''}}),
                   hist_update=Mock(),hist_add=Mock(return_value={'id':8}),_agent_src=lambda:{'k':'agent','text':''},
                   st=NS(log=Mock()),media_later=Mock())
            Host.desktop_text(h,'〔不回群〕')
            h.st.log.assert_called_once_with('silent_folded',turn=tid if tid else 8)
            h.media_later.assert_not_called()

    def test_projection_preserves_local_raw_and_opaque_signed_fields(self):
        row = {'id':7,'silent':True,'reply':{'text':'body〔静默消息〕','pre':'notes'}}
        out = silent.public_message({'t':'hist_turn','turn':row})
        self.assertEqual('body〔静默消息〕',row['reply']['text'])
        self.assertEqual('〔不回群〕',out['turn']['reply']['text'])
        self.assertTrue(out['turn']['silent'])
        for marker in MARKERS:
            self.assertFalse(silent.is_silent('before'+marker+'after'))
            self.assertEqual('beforeafter',silent.public_message({'t':'msg','text':'before'+marker+'after'})['text'])
        signed = {'silent':True,'reply':{'text':'signed original'}}
        self.assertIs(signed,silent.public_message({'t':'hist_turn','goal_add':signed})['goal_add'])

    def test_agent_final_without_existing_page_logs_actual_id(self):
        h = NS(cur_turn=None,turn_shown=False,jev_plan='',turn_text=False,
               hist_add=Mock(return_value={'id':81}),st=NS(log=Mock()),emit=Mock())
        Host.agent_text(h,'〔不回群〕')
        h.st.log.assert_called_once_with('silent_folded',turn=81)
        h.emit.assert_not_called()
