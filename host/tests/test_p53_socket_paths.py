"""Offline real AF_UNIX regression; no model or service required."""
import _hermetic
import asyncio
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock
from agentj.core import native_order, orders
from agentj import permtool

BRIEF = {'goal':'offline','scope':{'write':['result.txt']},'verify':['offline'],'timebox':1,'report':'head'}

class SocketPaths(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        # Original master socket fits even on Darwin's 104-byte sockaddr_un.
        self.master = self.base / ('m' * max(1, 79-len(os.fsencode(self.base))))
        self.master.mkdir()
        self.project = self.base/'project'; self.project.mkdir()

    def order(self):
        return orders.create(project=str(self.project), prompt_text='offline', title='socket',
            brief=BRIEF, harness='claude', model='synthetic', fence=True, st_root=self.master)

    async def test_real_socket_claim_and_resume(self):
        o = self.order(); h = native_order.Host(o, None, self.master)
        old = o.path/'native'/'agentperm'/'perm.sock'
        s = socket.socket(socket.AF_UNIX); self.addCleanup(s.close)
        with self.assertRaises(OSError): s.bind(str(old))
        master_sock = self.master/'agentperm'/'perm.sock'
        master_sock.parent.mkdir(); s.bind(str(master_sock))
        self.assertLessEqual(len(os.fsencode(h.st.perm_sock_path)),len(os.fsencode(master_sock)))
        stale_start = h.st.perm_dir/'task-claim-old-generation'
        stale_start.write_text('old generation')
        await h.start()
        self.assertFalse(stale_start.exists())
        try:
            env = h.new_perm_env(); old_claim = Path(env['AGENTJ_PERM_CLAIM'])
            # Exercise the actual permtool file consumer and wire claim over AF_UNIX.
            with mock.patch.dict(os.environ,env): token = permtool.claim_value(env['AGENTJ_PERM_SOCK'])
            with mock.patch.object(h,'descendant',return_value=True):
                r,w = await asyncio.open_unix_connection(env['AGENTJ_PERM_SOCK'])
                w.write((' {"t":"claim","token":"'+token+'"}\n').encode()); await w.drain()
                self.assertEqual(await r.readline(),b'{"t":"claimed"}\n')
                w.close(); await w.wait_closed()
            stale = Path(h.new_perm_env()['AGENTJ_PERM_CLAIM'])
            h.new_perm_env(); self.assertFalse(stale.exists())
        finally: await h.close()
        resumed = native_order.Host(o,None,self.master)
        self.assertEqual(resumed.st.perm_dir,h.st.perm_dir)
        await resumed.start(); await resumed.close()
        self.assertFalse(old_claim.exists())

    async def test_two_orders_and_master_bytes(self):
        sentinels = [self.master/'config-sentinel',self.master/'agent.json']
        for p in sentinels: p.write_bytes(b'unchanged\n')
        a,b = [native_order.Host(self.order(),None,self.master) for _ in range(2)]
        self.assertNotEqual(a.st.perm_dir,b.st.perm_dir)
        self.assertEqual(a.st.fence_state.root,self.master)
        self.assertEqual(a.st.fence_state.perm_dir,a.st.perm_dir)
        self.assertNotIn(b.st.perm_dir, a.st.perm_dir.parents)
        await a.start(); await b.start()
        try:
            ca=Path(a.new_perm_env()['AGENTJ_PERM_CLAIM']); cb=Path(b.new_perm_env()['AGENTJ_PERM_CLAIM'])
            self.assertNotIn(cb,list(a.st.perm_dir.iterdir()))
            self.assertTrue(ca.exists() and cb.exists())
        finally: await a.close(); await b.close()
        self.assertEqual([p.read_bytes() for p in sentinels],[b'unchanged\n']*2)

    async def test_bad_marker_and_symlinks_rejected(self):
        o=self.order(); st=native_order.NativeState(o,{})
        marker=st.perm_dir.parent/'owner'
        original=marker.read_bytes()
        marker.write_bytes(b'wrong order')
        with self.assertRaises(orders.OrderError): native_order.NativeState(o,{})
        marker.unlink(); marker.symlink_to(self.project)
        with self.assertRaises(orders.OrderError): native_order.NativeState(o,{})
        marker.unlink(); marker.write_bytes(original)
        st.perm_dir.rmdir(); st.perm_dir.symlink_to(self.project,target_is_directory=True)
        with self.assertRaises(orders.OrderError): native_order.NativeState(o,{})

    async def test_hash_collision_refuses_other_order(self):
        with mock.patch.object(native_order,'_namespace_digest',return_value='0'*6):
            native_order.NativeState(self.order(),{})
            with self.assertRaises(orders.OrderError): native_order.NativeState(self.order(),{})

    async def test_unknown_namespace_and_namespace_symlink_rejected(self):
        o=self.order()
        ns=self.master/('n'+native_order._namespace_digest(os.fsencode(str(o.path.absolute()))))
        ns.mkdir()
        sentinel=ns/'unknown'; sentinel.write_bytes(b'leave alone')
        with self.assertRaises(orders.OrderError): native_order.NativeState(o,{})
        self.assertEqual(sentinel.read_bytes(),b'leave alone')
        sentinel.unlink(); ns.rmdir(); ns.symlink_to(self.project,target_is_directory=True)
        with self.assertRaises(orders.OrderError): native_order.NativeState(o,{})

    async def test_wrong_filesystem_owner_rejected(self):
        from types import SimpleNamespace
        o=self.order(); native_order.NativeState(o,{})
        real_fstat=os.fstat
        def wrong_owner(fd):
            info=real_fstat(fd)
            return SimpleNamespace(st_uid=os.getuid()+1,st_mode=info.st_mode,st_nlink=info.st_nlink)
        with mock.patch.object(native_order.os,'fstat',side_effect=wrong_owner):
            with self.assertRaises(orders.OrderError): native_order.NativeState(o,{})

    async def test_overlong_master_fails_explicitly(self):
        self.master=self.master/('long'*15); self.master.mkdir()
        h=native_order.Host(self.order(),None,self.master)
        with self.assertRaises(orders.OrderError) as err: await h.start()
        self.assertEqual(err.exception.code,'native_perm_socket_path_too_long')
        self.assertIsNone(h.srv)
