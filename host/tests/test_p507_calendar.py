"""P503: isolated regressions for the stolen paired-device trust boundary."""
import _hermetic  # noqa: F401
import asyncio
import copy
import hashlib
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agentj import mail, passkey, serve, wire, elevate
from agentj.noise import Keypair
from agentj.state import State
from test_p57_passkey import Authenticator, pk_record


class HostExecution(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_l1 import _owner_host as _host, _ready, Phone
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st=State(pathlib.Path(self.tmp.name));self.st.init()
        self.sent=[];self.h=_host(self.st,self.sent);self.ph=Phone(self.st)
        self.auth=Authenticator();self.st.set_passkey(self.ph.pub,pk_record(self.auth))
        self.session=_ready(self.h,self.ph);self.session.p33=True

class CalendarFinalExecution(HostExecution):
    def calendar_fixture(self, op):
        from agentj import jcards, jcards_cal as cal
        from test_p428_calendar_sync import GOOGLE, T, Fake
        jcards._write_json(self.st.root, ('cards-data', 'calendar'), 'calendar.json',
                           {'schema':1, 'events':[], 'subscriptions':{'google':dict(GOOGLE)}})
        remote = Fake()
        out = cal.change(self.st.root, 'add', to='google', **T)
        ident = out['events'][0]['id']
        if op != 'create':
            def linked(doc):
                ev = doc['events'][0]
                ev.update(rid='fixture-remote', etag='"e1"', base='old')
                doc['outbox'] = []
            cal.transaction(self.st.root, linked)
            remote.put('fixture-remote')
            cal.change(self.st.root, 'delete' if op == 'delete' else 'edit', ident, **({} if op == 'delete' else {'title':'Moved'}))
        queued = cal.load(self.st.root)[0]['outbox'][0]
        return remote, queued

    async def test_offline_background_create_update_delete_cannot_execute(self):
        from agentj import jcards_cal_remote as remote
        for op in ('create','update','delete'):
            adapter, queued = self.calendar_fixture(op)
            await asyncio.to_thread(remote._push, self.st.root, 'google', adapter, queued)
            self.assertFalse(adapter.calls, op)

    async def test_each_final_calendar_effect_needs_its_own_uv(self):
        from agentj import jcards_cal_remote as remote
        await self.h.elevate.start()
        try:
            for op in ('create','update','delete'):
                adapter, queued = self.calendar_fixture(op)
                task = asyncio.create_task(asyncio.to_thread(remote._push, self.st.root, 'google', adapter, queued))
                try:
                    for _ in range(300):
                        active = [a for a in self.h.asks.values() if not a.fut.done()]
                        if active: break
                        if task.done(): break
                        await asyncio.sleep(.005)
                    self.assertTrue(active, op)
                    a = active[-1]
                    card = self.h._ask_msg(a,self.session)
                    self.assertIn(op,card['summary'])
                    obj = self.ph.answer(card,True)
                    await self.h._answer(self.session,obj)
                    self.assertFalse(adapter.calls)
                    ch = passkey.elevate_challenge(self.h.channel,self.ph.did,a.rid,a.pk_kind,a.rid,a.digest)
                    bad = self.auth.assert_(ch,b'0',nonce=b'0',flags=1)
                    await self.h._answer(self.session,{**obj,'fa':bad})
                    self.assertFalse(adapter.calls)
                    good = {**obj,'fa':self.auth.assert_(ch,b'0',nonce=b'0')}
                    await self.h._answer(self.session,good)
                    await asyncio.wait_for(task,3)
                    self.assertEqual(adapter.calls,[op])
                    await self.h._answer(self.session,good)
                    self.assertEqual(adapter.calls,[op])
                finally:
                    if not task.done():task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
        finally:
            await self.h.elevate.stop()


    async def test_final_calendar_rechecks_queue_content_and_destination_after_approval(self):
        from agentj import jcards_cal as cal, jcards_cal_remote as remote
        for change in ('content','destination','queue'):
            adapter, queued = self.calendar_fixture('create')
            def approve(action, target):
                self.assertEqual(action,'calendar.write')
                self.assertEqual(target['operation'],'create')
                def mutate(doc):
                    if change == 'content': doc['events'][0]['title'] = 'Changed during approval'
                    elif change == 'destination': doc['subscriptions']['google']['calendar'] = 'other@example.com'
                    else: doc['outbox'] = []
                cal.transaction(self.st.root,mutate)
                return True
            result = remote._push(self.st.root,'google',adapter,queued,authorize=approve)
            self.assertEqual(result,'approval_required',change)
            self.assertFalse(adapter.calls,change)



    async def test_no_passkey_cannot_execute_any_remote_effect(self):
        from test_l1 import _owner_host as _host, _ready, Phone
        from agentj import jcards_cal_remote as remote
        with tempfile.TemporaryDirectory() as d:
            self.st = State(pathlib.Path(d)); self.st.init()
            h = _host(self.st, []); ph = Phone(self.st); session = _ready(h, ph); session.p33 = True
            await h.elevate.start()
            try:
                for op in ('create', 'update', 'delete'):
                    adapter, queued = self.calendar_fixture(op)
                    task = asyncio.create_task(asyncio.to_thread(remote._push, self.st.root, 'google', adapter, queued))
                    try:
                        for _ in range(300):
                            active = [a for a in h.asks.values() if not a.fut.done()]
                            if active or task.done(): break
                            await asyncio.sleep(.005)
                        self.assertTrue(active, op)
                        a = active[-1]; card = h._ask_msg(a, session)
                        self.assertTrue(card['passkey_required'])
                        self.assertNotIn('fa', card)
                        await h._answer(session, ph.answer(card, True))
                        self.assertFalse(task.done()); self.assertFalse(adapter.calls)
                        await h._answer(session, ph.answer(card, False))
                        self.assertEqual(await asyncio.wait_for(task, 3), 'approval_required')
                        self.assertFalse(adapter.calls)
                    finally:
                        if not task.done(): task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            finally:
                await h.elevate.stop()

    async def test_host_sync_binds_actual_google_adapter_destination_across_pull_and_uv(self):
        from unittest.mock import patch
        from agentj import google, jcards_cal as cal, jcards_cal_remote as remote
        await self.h.elevate.start()
        try:
            for op in ('create','update','delete'):
                for drift in ('pull','approval','none'):
                    with self.subTest(op=op, drift=drift):
                        _, queued = self.calendar_fixture(op)
                        dest = cal.load(self.st.root)[0]['subscriptions']['google']['calendar']
                        writes=[]
                        def move():
                            cal.transaction(self.st.root, lambda d: d['subscriptions']['google'].update(calendar='other@example.com'))
                        def gog(st, argv, timeout):
                            self.assertEqual(argv[2], dest)
                            if argv[1] == 'events':
                                if drift == 'pull': move()
                                # Keep linked update/delete rows present without changing local values.
                                if op != 'create':
                                    return 0, [{'id':'fixture-remote','etag':'"e1"','summary':'Meeting',
                                        'start':{'dateTime':'2026-10-20T09:30:00+00:00'},
                                        'end':{'dateTime':'2026-10-20T10:30:00+00:00'}}]
                                return 0, []
                            writes.append(argv[1])
                            return 0, {'id':'fixture-remote','etag':'"e2"'}
                        with patch.object(google,'gog_calendar',side_effect=gog):
                            task=asyncio.create_task(asyncio.to_thread(remote.host_sync,self.st,'google'))
                            try:
                                active=[]
                                for _ in range(400):
                                    active=[a for a in self.h.asks.values() if not a.fut.done()]
                                    if active or task.done(): break
                                    await asyncio.sleep(.005)
                                if drift == 'pull':
                                    result=await asyncio.wait_for(task,3)
                                    self.assertEqual(result.get('why'),'approval_required',result)
                                    self.assertFalse(active)
                                else:
                                    self.assertTrue(active, task.result() if task.done() else op)
                                    a=active[-1]; card=self.h._ask_msg(a,self.session)
                                    self.assertEqual(a.tool_input['subscription']['calendar'],dest)
                                    self.assertIn(hashlib.sha256(json.dumps(a.tool_input,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),card['summary'])
                                    if drift == 'approval': move()
                                    obj=self.ph.answer(card,True)
                                    ch=passkey.elevate_challenge(self.h.channel,self.ph.did,a.rid,a.pk_kind,a.rid,a.digest)
                                    obj['fa']=self.auth.assert_(ch,b'0',nonce=b'0')
                                    await self.h._answer(self.session,obj)
                                    result=await asyncio.wait_for(task,3)
                                    self.assertEqual(result['pushed'],1 if drift == 'none' else 0,result)
                                self.assertEqual(writes,[op] if drift == 'none' else [])
                            finally:
                                if not task.done():
                                    for pending in list(self.h.asks.values()):
                                        if not pending.fut.done():
                                            await self.h._answer(self.session,self.ph.answer(self.h._ask_msg(pending,self.session),False))
                                    await asyncio.wait_for(asyncio.shield(task),3)
                                await asyncio.gather(task,return_exceptions=True)
        finally:
            await self.h.elevate.stop()

    async def test_oversized_calendar_summary_uses_calendar_bilingual_copy(self):
        for lang in ('zh','en'):
            self.h.lang=lang
            result=await self.h.ask('agentj calendar',{'note':'x'*24001},batch=False,require_passkey=True)
            self.assertEqual(result['behavior'],'deny')
            self.assertIn('日历内容过长',result['message'])
            self.assertIn('calendar content is too large',result['message'])
            self.assertNotIn('mail',result['message'])
            self.assertFalse(self.h.asks)
