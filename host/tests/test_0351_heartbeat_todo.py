"""Idle to-do selection and proof that the offered task was actually started by this beat."""
import asyncio
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from agentj import doctor, heartbeat, heartbeat_check, heartbeat_todo, jcards_todo as todo
from test_p394_todo import Base
import test_p156_heartbeat as scheduler_cases


class Candidates(Base):
    def test_start_prerequisite_cannot_be_overruled_by_due_end(self):
        now = todo.now_utc()
        self.add('prerequisite', owner='agent')
        dep = self.add('after prerequisite', owner='agent', start='after:t1', end='2000-01-01')
        free = self.add('needs evidence', owner='agent', start='signed fixture returned')
        later = self.add('time gate', owner='agent', start='2099-01-01', end='2000-01-01')
        ready = self.add('undated', owner='agent')
        self.add('remind me', owner='owner')
        for op in ['pending', 'cancel', 'done', 'delete']:
            if op != 'pending':
                self.assertTrue(todo.change(self.root, op, 't1')['ok'])
            ids, review = todo.heartbeat_candidates(self.root, now)
            self.assertEqual(set(review), {free})
            self.assertEqual(dep in ids, op == 'done')
            self.assertNotIn(later, ids)
            self.assertIn(ready, ids)
        self.assertEqual(todo.change(self.root, 'start', dep, heartbeat='c'*16)['why'], 'not_ready')

    def test_active_progress_review_does_not_authorize_a_second_pickup(self):
        iid = self.add('already under way', owner='agent')
        self.assertTrue(todo.change(self.root, 'start', iid)['ok'])
        plan = heartbeat_todo.offer(self.root, time.time())
        self.assertTrue(plan['active'])
        self.assertEqual((plan['ready'], plan['review']), ([], []))
        self.assertEqual(todo.change(self.root, 'start', iid, heartbeat=plan['beat'])['why'], 'not_ready')
        self.assertTrue(todo.change(self.root, 'done', iid)['ok'])
        self.assertFalse(heartbeat_todo.offer(self.root, time.time())['active'])

    def test_only_one_concurrent_pick_and_the_right_beat_gets_credit(self):
        iid = self.add('fixture task', owner='agent')
        plans = [heartbeat_todo.offer(self.root, time.time()) for _ in range(2)]
        with ThreadPoolExecutor(2) as pool:
            a, b = list(pool.map(lambda p: todo.change(self.root, 'start', iid, heartbeat=p['beat']), plans))
        win = 0 if a['ok'] else 1
        self.assertEqual([a['ok'], b['ok']].count(True), 1)
        todo.change(self.root, 'done', iid)
        self.assertEqual(heartbeat_todo.picked(self.root, plans[win]), iid)
        self.assertIsNone(heartbeat_todo.picked(self.root, plans[1-win]))

    def test_stale_offer_after_reassignment_is_refused(self):
        iid = self.add('fixture task', owner='agent')
        offer = heartbeat_todo.offer(self.root, time.time())
        todo.change(self.root, 'edit', iid, owner='owner')
        self.assertEqual(todo.change(self.root, 'start', iid, heartbeat=offer['beat'])['why'], 'not_ready')
        self.assertIsNone(heartbeat_todo.picked(self.root, offer))

    def test_unmodified_factory_example_is_not_work(self):
        self.write({'items': list(todo._seed_agent_items().values())})
        self.assertEqual(todo.heartbeat_candidates(self.root), ({}, {}))
        self.add('wait for later', owner='agent', start='2099-01-01', end='2000-01-01')
        host = SimpleNamespace(st=SimpleNamespace(root=self.root))
        now = time.time()
        reasons, unknown = heartbeat_check.collect(host, now, heartbeat_todo.offer(self.root, now))
        self.assertNotIn('todo_due', reasons)
        self.assertNotIn('todo', unknown)
        self.assertTrue(todo.change(self.root, 'edit', 't2', text='owner edited the demo')['ok'])
        self.assertIn('t2', todo.heartbeat_candidates(self.root)[0])


class Scheduling(unittest.TestCase):
    setUp = scheduler_cases.Decide.setUp
    due = scheduler_cases.Decide.due

    def test_active_background_suppresses_todo(self):
        self.host.busy = SimpleNamespace(refresh_sources=lambda: None, items=lambda: [{'kind':'task'}])
        self.assertEqual(asyncio.run(self.h.tick(self.due()))['why'], 'background_busy')
        self.assertFalse(self.agent.got)

    def test_three_unclaimed_idle_offers_go_to_doctor_and_morning_review(self):
        x = todo.change(self.st.root, 'add', text='an actionable test task', owner='agent')
        self.assertTrue(x['ok'])
        iid = x['item']['id']
        for n in (1, 2, 3):
            self.assertTrue(asyncio.run(self.h.tick(self.due()))['ok'])
            send = self.agent.got[-1]
            self.h.usage_start(send)
            self.h.usage_end()
            send.state = 'delivered'
            self.assertEqual(self.h._load()['todo_misses'], n)
        self.assertIn('3', doctor.check_heartbeat_pickup(self.st)['summary'])
        with patch.object(self.h, 'daily_is_due', return_value=True):
            self.assertTrue(asyncio.run(self.h.tick(self.due()))['ok'])
        send = self.agent.got[-1]
        self.assertIn('连续 3', send.text)
        self.h.usage_start(send)
        self.assertTrue(todo.change(self.st.root, 'start', iid, heartbeat=send.todo_offer['beat'])['ok'])
        self.assertTrue(todo.change(self.st.root, 'done', iid)['ok'])
        self.h.usage_end()
        self.assertIsNone(doctor.check_heartbeat_pickup(self.st))
        lines = [json.loads(x) for x in self.st.log_path.read_text().splitlines()]
        self.assertTrue(any(r.get('ev') == 'heartbeat_todo_pickup' and r['result']=='started' and r['id']==iid for r in lines))

    def test_only_waiting_work_is_silent(self):
        self.host.preferences['heartbeat']['daily_at'] = ''
        self.host.background_done.pending = {}
        heartbeat.ensure_prompt()
        self.assertTrue(todo.change(self.st.root,'add',text='future',owner='agent',start='2099-01-01')['ok'])
        with patch('agentj.heartbeat_check.collect', return_value=([], [])):
            self.assertEqual(asyncio.run(self.h.tick(self.due()))['why'], 'no_work')
        self.assertFalse(self.agent.got)
