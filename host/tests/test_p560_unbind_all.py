"""#560: the account's exact remote preview binds one all-remotes revocation."""
import _hermetic
import asyncio
import json
import os
import time
import unittest
from unittest.mock import AsyncMock, patch
from test_p78_remote_pair import Security, HostFlow
from agentj import wire, serve, remote_pair as rp
from agentj.state import State


class UnbindAll(Security, unittest.IsolatedAsyncioTestCase):
    asyncSetUp = HostFlow.asyncSetUp
    asyncTearDown = HostFlow.asyncTearDown

    def device(self):
        pub = os.urandom(32)
        did = self.st.add_device(pub, 'Test remote', os.urandom(32))
        records = self.st.devices()
        records[did]['pk'] = {'id': wire.b64u(os.urandom(32)), 'dev': did}
        records[did]['rt'] = {'i': wire.b64u(os.urandom(16))}
        self.st.write_private(self.st.devices_path, json.dumps(records).encode())
        return did, pub, records[did]

    def request(self, ids):
        self.q.update(id=wire.b64u(os.urandom(16)), op='unbind_all', target={'devices': sorted(ids)})
        return self.signed()

    def result(self):
        return self.h.remote_pair.results[self.q['id']][1]

    async def test_revoke_entire_snapshot_and_recovery_then_replay_is_noop(self):
        a, apub, arec = self.device(); b, bpub, brec = self.device()
        self.h.sessions[1] = serve.Session(cid=1, state='ready', device=a, pub=apub)
        self.h.sessions[2] = serve.Session(cid=2, state='ready', device=b, pub=bpub)
        before = self.st.config_path.read_bytes(), self.st.cloud_path.read_bytes()
        self.st.write_private(self.st.root/'keep-data', b'chat files remain')
        with patch.object(self.h.remote_pair, 'reply') as reply:
            env = self.request([a,b]); await self.h.remote_pair.execute(env)
            reply.assert_called_once_with(self.q, {'status':'unbound_all','count':2})
            await self.h.remote_pair.execute(env)
            self.assertEqual(reply.call_count, 1)
        self.assertEqual(self.st.devices(), {})
        self.assertFalse(self.st.is_allowed(apub)); self.assertFalse(self.st.is_allowed(bpub))
        self.assertIsNone(self.st.passkey_of(arec['pk']['id']))
        self.assertIsNone(self.st.ticket_of(brec['rt']['i']))
        self.assertEqual(self.h.sessions, {})
        self.assertEqual(before, (self.st.config_path.read_bytes(), self.st.cloud_path.read_bytes()))
        self.assertEqual((self.st.root/'keep-data').read_bytes(), b'chat files remain')
        self.assertIn('unbind_all', self.st.approvals_path.read_text())

    async def test_changed_snapshot_does_not_revoke_any_remote(self):
        a, _, _ = self.device(); b, _, _ = self.device()
        before = self.st.devices_path.read_bytes()
        with patch.object(self.h.remote_pair, 'reply') as reply:
            await self.h.remote_pair.execute(self.request([a]))
            reply.assert_called_once_with(self.q, {'status':'changed'})
        self.assertEqual(before, self.st.devices_path.read_bytes())
        self.assertEqual(set(self.st.devices()), {a,b})

    async def test_pending_pair_and_password_handshakes_end(self):
        a, _, _ = self.device()
        p = serve.Pairing(os.urandom(16), os.urandom(32), time.time()+60, time.monotonic()+60, None)
        self.h.pairing = p
        p.cid = 7
        self.h.sessions[7] = serve.Session(cid=7,state='pending',device=wire.device_id(os.urandom(32)))
        self.h.remote_pair.pass_keys['test'] = (None, self.now+60000)
        await self.h.remote_pair.execute(self.request([a]))
        self.assertIsNone(self.h.pairing); self.assertTrue(p.done.done())
        self.assertEqual(self.h.sessions, {}); self.assertEqual(self.h.remote_pair.pass_keys, {})

    async def test_offline_disabled_wrong_host_expired_do_not_revoke(self):
        a, _, _ = self.device()
        for mode in ('offline','disabled','wrong_host','expired'):
            with self.subTest(mode=mode):
                self.h.relay_up = mode!='offline'; rp.set_enabled(self.st, mode!='disabled')
                env=self.request([a]); old=self.q.copy()
                if mode=='wrong_host': self.q['host']='another'; env=self.signed()
                if mode=='expired': self.q['expires_at']=self.now-1; env=self.signed()
                await self.h.remote_pair.execute(env)
                self.assertIn(a,self.st.devices()); self.q=old

    async def test_bad_target_shapes_never_revoke(self):
        a, _, _ = self.device()
        for target in (None, {}, {'devices':[a,a]}, {'devices':['bad']}, {'devices':[a], 'all':True}, {'devices':[None]}):
            self.request([a]); self.q['target']=target
            await self.h.remote_pair.execute(self.signed())
            self.assertIn(a,self.st.devices())

    async def test_old_signed_pairing_request_is_invalid_after_unbind(self):
        a, _, _ = self.device()
        old = dict(self.q, id=wire.b64u(os.urandom(16)), op='start', target=None)
        await self.h.remote_pair.execute(self.request([a]))
        self.assertIsNone(rp.open_request(self.signed(old), self.st, self.now,[self.sk.public_key().public_bytes_raw()]))

    async def test_empty_list_is_explicit_noop_success(self):
        with patch.object(self.h.remote_pair, 'reply') as reply:
            await self.h.remote_pair.execute(self.request([]))
            reply.assert_called_once_with(self.q, {'status':'unbound_all','count':0})

    async def test_atomic_state_rechecks_snapshot_and_new_pair_admission_is_refused(self):
        a, _, _ = self.device()
        self.assertIsNone(self.st.remove_all_devices([]))
        self.assertIn(a, self.st.devices())
        self.h._unbinding_all = True
        s=serve.Session(cid=5,state='pending',device=wire.device_id(os.urandom(32)),pub=os.urandom(32))
        self.h.sessions[5]=s
        res=await self.h._grant_pair(s)
        self.assertEqual(res['ev'], 'denied'); self.assertEqual(set(self.st.devices()), {a})

    async def test_new_pair_just_before_locked_compare_aborts_every_revoke(self):
        a, _, _ = self.device()
        original = self.st.remove_all_devices
        late = []
        def raced(expected, cutoff):
            late.append(self.device()[0])
            return original(expected, cutoff)
        with patch.object(self.st, 'remove_all_devices', side_effect=raced), patch.object(self.h.remote_pair, 'reply') as reply:
            await self.h.remote_pair.execute(self.request([a]))
            reply.assert_called_once_with(self.q, {'status':'changed'})
        self.assertEqual(set(self.st.devices()), {a,late[0]})

    async def test_failed_disk_write_never_reports_success_and_releases_admission(self):
        a, _, _ = self.device()
        with patch.object(self.st, 'remove_all_devices', side_effect=OSError('fixture')), patch.object(self.h.remote_pair, 'reply') as reply:
            with self.assertRaises(OSError):
                await self.h.remote_pair.execute(self.request([a]))
            reply.assert_not_called()
        self.assertFalse(self.h._unbinding_all); self.assertIn(a,self.st.devices())

    async def test_detached_pending_grant_cannot_reappear_after_success(self):
        a, _, _ = self.device()
        pub=os.urandom(32)
        pending=serve.Session(cid=9,state='pending',device=wire.device_id(pub),pub=pub)
        self.h.sessions[9]=pending
        await self.h.remote_pair.execute(self.request([a]))
        res=await self.h._grant_pair(pending)
        self.assertEqual(res['ev'],'denied'); self.assertEqual(self.st.devices(),{})

    async def test_restore_and_new_socket_admission_are_closed_during_unbind(self):
        self.h._unbinding_all=True
        self.assertIsNone(self.h._new_session(42))
        s=serve.Session(cid=9,state='pk',pub=os.urandom(32))
        with patch.object(self.h, '_pk_fail', new=AsyncMock()) as fail:
            await self.h._pk_restore(s,{})
            await self.h._rt_restore(s,{})
            self.assertEqual(fail.await_count,2)

    def pending_seeds(self):
        pub=os.urandom(32)
        pending=serve.Session(cid=13,state='pending',device=wire.device_id(pub),pub=pub)
        p=serve.Pairing(os.urandom(16),os.urandom(32),time.time()+60,time.monotonic()+60,None)
        p.cid=13; self.h.pairing=p; self.h.sessions[13]=pending
        self.h.remote_pair.pass_keys['fixture']=(None,self.now+60000)
        return p,pending

    async def assert_old_seeds_invalid(self, pair, pending):
        self.assertIsNone(self.h.pairing); self.assertTrue(pair.done.done())
        self.assertEqual(self.h.remote_pair.pass_keys,{})
        self.assertNotIn(pending.cid,self.h.sessions)
        result=await self.h._grant_pair(pending)
        self.assertEqual(result['ev'],'denied')

    async def test_postwrite_note_failure_still_invalidates_all_old_seeds(self):
        did,pub,_=self.device(); pair,pending=self.pending_seeds()
        with patch.object(self.st,'note_removed',side_effect=OSError('fixture')), patch.object(self.h.remote_pair,'reply') as reply:
            with self.assertRaises(OSError):
                await self.h.remote_pair.execute(self.request([did]))
            reply.assert_not_called()
        self.assertFalse(self.h._unbinding_all); self.assertEqual(self.st.devices(),{})
        self.assertFalse(self.st.is_allowed(pub))
        await self.assert_old_seeds_invalid(pair,pending)
        self.assertEqual(self.st.devices(),{})

    async def test_journal_written_allowlist_write_failure_is_durably_fail_closed(self):
        did,pub,record=self.device(); pair,pending=self.pending_seeds()
        before=self.st.devices_path.read_bytes(); original=self.st.write_private
        def write(path, data):
            if path==self.st.devices_path: raise OSError('fixture allowlist write')
            return original(path,data)
        with patch.object(self.st,'write_private',side_effect=write), patch.object(self.h.remote_pair,'reply') as reply:
            with self.assertRaises(OSError):
                await self.h.remote_pair.execute(self.request([did]))
            reply.assert_not_called()
        self.assertEqual(self.st.devices_path.read_bytes(),before)
        restarted=State(self.st.root)
        self.assertEqual(restarted.devices(),{})
        self.assertFalse(restarted.is_allowed(pub))
        self.assertIsNone(restarted.passkey_of(record['pk']['id']))
        self.assertIsNone(restarted.ticket_of(record['rt']['i']))
        await self.assert_old_seeds_invalid(pair,pending)
        self.assertEqual(self.st.devices(),{})

    async def test_repeated_unbind_all_preserves_old_tombstones_and_allows_fresh_pairing(self):
        from agentj import credential_revoke
        first,pub,_=self.device()
        await self.h.remote_pair.execute(self.request([first]))
        self.q['created_at']=max(int(time.time()*1000),credential_revoke.read(self.st)['cutoff'])
        fresh,freshpub,_=self.device()
        self.assertTrue(self.st.is_allowed(freshpub)); self.assertFalse(self.st.is_allowed(pub))
        self.assertIn(first,credential_revoke.read(self.st)['devices'])
        await self.h.remote_pair.execute(self.request([fresh]))
        self.assertEqual(self.st.devices(),{})
        self.assertEqual(set(credential_revoke.read(self.st)['devices']),{first,fresh})

    async def test_actual_fresh_pair_grant_succeeds_after_old_remote_unbound(self):
        did,pub,_=self.device()
        await self.h.remote_pair.execute(self.request([did]))
        freshpub=os.urandom(32)
        fresh=serve.Session(cid=21,state='pending',device=wire.device_id(freshpub),pub=freshpub,
                            name='New paired remote',sign_pub=os.urandom(32))
        self.h.sessions[21]=fresh
        result=await self.h._grant_pair(fresh)
        self.assertEqual(result['ev'],'approved')
        self.assertTrue(self.st.is_allowed(freshpub)); self.assertFalse(self.st.is_allowed(pub))

    async def test_cancelled_mutation_keeps_admission_closed_until_thread_finishes(self):
        import threading
        did,_,_=self.device(); pair,pending=self.pending_seeds()
        started=asyncio.Event(); release=threading.Event(); loop=asyncio.get_running_loop()
        original=self.st.remove_all_devices
        def blocked(expected, cutoff):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(3): raise TimeoutError('fixture')
            return original(expected, cutoff)
        with patch.object(self.st,'remove_all_devices',side_effect=blocked), patch.object(self.h.remote_pair,'reply') as reply:
            task=asyncio.create_task(self.h.remote_pair.execute(self.request([did])))
            await asyncio.wait_for(started.wait(),1)
            task.cancel()
            try:
                with self.assertRaises(asyncio.CancelledError): await task
                self.assertTrue(self.h._unbinding_all)
                self.assertIsNone(self.h._new_session(90))
                self.assertIsNone(self.h.pairing); self.assertEqual(self.h.remote_pair.pass_keys,{})
                self.assertNotIn(pending.cid,self.h.sessions)
            finally:
                release.set()
            async def settled():
                while self.h._unbinding_all: await asyncio.sleep(.001)
            await asyncio.wait_for(settled(),2)
            reply.assert_not_called()
        self.assertEqual(self.st.devices(),{})

    def test_poll_advertises_capability(self):
        with patch('agentj.cloud.post_json',return_value=(200,{})) as post:
            self.h.remote_pair.poll(self.link, [])
            payload=json.loads(wire.unb64u(post.call_args.args[1]['body']))
            self.assertIs(payload['unbind_all'], True)
