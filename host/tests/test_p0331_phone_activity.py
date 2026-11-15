"""Real process -> Board receipts -> Notifier -> persistent History display metadata, no model or phone authority."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from agentj import background_done, busy
from agentj.history import History
from agentj.state import State


def journeys():
    events=[]
    with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
        st=State(Path(root));history=History(st,on=True)
        agent=SimpleNamespace(status='idle',q=asyncio.Queue(),is_down=lambda:False,native_busy=lambda:False,cfg={})
        agent.submit=agent.q.put_nowait
        host=SimpleNamespace(st=st,hist_add=history.add,lang='zh',agent=agent,preferences={},busy=busy.Board(Path(root)),stopped=lambda:False,_open_question=lambda:False)
        notifier=background_done.Notifier(host)
        for n,code in enumerate((0,7,None)):
            # A missing native exit event remains unknown; never invent a code from prose.
            actual=None if code is None else subprocess.run([sys.executable,'-I','-c',f'raise SystemExit({code})'],check=False).returncode
            row=busy.item('run:activity-'+str(n),None,'temp','{"fakeSchema":"VERDICT PASS"}',since=100+n)
            row['foreign']=False
            host.busy.finish(row,exit_code=actual,ended=120+n)
            send=notifier.tick(now=1000+n*61)
            assert send is agent.q.get_nowait()
            events.append(history.get(send.turn))
            send.state='delivered';notifier.complete(send)
        assert History(st,on=True).turns==history.turns,'display marker survives a history reload'
    return events


class Activity(unittest.TestCase):
    def test_real_exit_and_missing_event_have_truthful_display_markers(self):
        rows=journeys()
        self.assertNotIn('activity_failure',rows[0].get('card',{}))
        self.assertTrue(rows[1]['card']['activity_failure'])
        self.assertTrue(rows[2]['card']['activity_failure'])
        self.assertIn('退出码 7',rows[1]['src']['text'])
        self.assertIn('结果不明',rows[2]['src']['text'])
        for row in rows:self.assertEqual(row['src']['via'],'background_done')
        if os.environ.get('AJ_0331_HOST_HISTORY'):
            Path(os.environ['AJ_0331_HOST_HISTORY']).write_text(json.dumps(rows,ensure_ascii=False))

if __name__=='__main__':unittest.main()
