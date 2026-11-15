"""0310: local daily review is an actual Host heartbeat, not only protocol prose."""
import _hermetic  # noqa: F401
import asyncio
import datetime as dt
import os
import time
import unittest
from unittest.mock import patch

from agentj import heartbeat as hb, preferences
import test_p156_heartbeat as fixture
import test_p262_done_notify as completion_fixture
from agentj import busy


class DailyReview(unittest.TestCase):
    setUp=fixture.Decide.setUp

    def clock(self, hour=8):
        return dt.datetime(2026,11,14,hour,0).timestamp()

    def arrange(self, hour=8):
        at=self.clock(hour)
        self.h.started=self.h.last_activity=at-120
        self.host.background_done.pending={}
        return at

    def tick(self, at):
        with patch('agentj.heartbeat_check.collect',return_value=([],[])):
            return asyncio.run(self.h.tick(at))

    def test_default_local_daily_review_reaches_main_without_due_signal(self):
        with patch.dict(os.environ,{'TZ':'Asia/Tokyo'}):
            time.tzset()
            try:
                at=self.arrange()
                self.assertEqual(self.h.daily_at(),'08:00')
                self.assertIsNone(self.tick(at-1))
                self.assertTrue(self.tick(at)['ok'])
                send=self.agent.got[-1]
                self.assertEqual(send.by,'heartbeat')
                self.assertEqual(send.source['kind'],'system')
                self.assertIn('agentj card todo list --json',send.text)
                self.assertIn('agentj card cal list --json',send.text)
                self.assertIn('七天',send.text)
                self.assertIn('证据',send.text)
                self.assertIn('daily_schedule',send.text)
                self.assertEqual(len(self.host.hist.turns),1)
                send.state='delivered';self.h.inflight=None
                self.assertIsNone(self.tick(at+30))
                restarted=hb.Heartbeat(self.host)
                restarted.started=restarted.last_activity=at+30
                self.assertFalse(restarted.daily_is_due(at+60))
            finally:
                os.environ.pop('TZ',None);time.tzset()

    def test_late_start_catches_up_and_off_stays_off(self):
        at=self.arrange(11)
        self.assertTrue(self.tick(at)['ok'],'missed review runs on next available wake-up')
        self.assertIn('agentj card todo list --json',self.agent.got[-1].text)
        self.h.inflight=None
        self.host.preferences=preferences.merge(self.host.preferences,{'heartbeat':{'daily_at':''}})
        self.assertEqual(self.h.status()['daily_at'],'')
        self.h.last_activity=at+60
        self.assertIsNone(self.tick(at+61))
        self.host.preferences=preferences.merge(self.host.preferences,{'heartbeat':{'enabled':False,'daily_at':'08:00'}})
        self.assertIsNone(self.tick(at+86400))

    def test_busy_waits_then_delivers_the_daily_protocol(self):
        at=self.arrange()
        self.agent.status='working'
        self.assertEqual(self.tick(at)['why'],'busy')
        self.assertFalse(self.agent.got)
        self.agent.status='idle'
        self.assertTrue(self.tick(at+60)['ok'])
        self.assertIn('agentj card todo list --json',self.agent.got[-1].text)


class LinkedCompletion(unittest.TestCase):
    setUp=completion_fixture.Completion.setUp

    def test_completion_names_linked_todo_without_closing_it(self):
        row=busy.item('run:linked',None,'temp','Accepted report',since=100,todo='t42')
        row['foreign']=False
        self.h.busy.finish(row,exit_code=0,ended=120)
        send=self.n.tick(now=1000)
        self.assertIsNotNone(send)
        self.assertIn('t42',send.text)
        self.assertIn('验收',send.text)
        self.assertIn('agentj card todo done --entry t42',send.text)
        self.assertEqual(self.n.pending[next(iter(self.n.pending))]['todo'],'t42')

    def test_untrusted_metadata_cannot_inject_a_todo_command(self):
        row=busy.item('run:other',None,'temp','Report',since=100,todo='t1; bogus')
        row['foreign']=False
        self.h.busy.finish(row,exit_code=0,ended=120)
        send=self.n.tick(now=1000)
        self.assertNotIn('bogus',send.text)
        self.assertNotIn('todo done --entry',send.text)


if __name__=='__main__':unittest.main()
