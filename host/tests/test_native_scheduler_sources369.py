"""Hermetic exact-source regression: never query a real scheduler."""
import pathlib
import tempfile
import unittest
from agentj import native_scheduler_control as native
from test_native_scheduler_control369 import FakeState

class Sources(unittest.TestCase):
    def test_exact_cron_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = root / 'crontab'
            original = b'# header\r\nTOKEN=private\r\n0 1 * * * root /bin/job a\r\n0 2 * * * root /bin/job b\n'
            path.write_bytes(original)
            backend = native.Backend(run=lambda argv: self.fail(str(argv)), platform='linux', home=tmp, system_directory=tmp)
            ctl = native.Controller(FakeState(tmp), backend)
            target = dict(platform='linux', source='cron-system', scope='system', id='crontab:3', snapshot='0'*64, op='disable')
            target['snapshot'] = backend.snapshot(target)['snapshot']
            ctl.apply(target, lambda: None)
            self.assertEqual(path.read_bytes().splitlines(keepends=True)[3], original.splitlines(keepends=True)[3])
            self.assertTrue(path.read_bytes().splitlines()[2].startswith(b'# agentj-disabled:'))
            target.update(op='restore', snapshot=backend.snapshot(target)['snapshot'])
            ctl.apply(target, lambda: None)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(ctl.journal(), {})

import asyncio
import json
import os
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import controls, systasks, wire


class ExactSources(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.path = self.root / 'crontab'
        self.raw = b'# keep\nENV=private-fixture\n0 1 * * * root /bin/job first\n0 1 * * * root /bin/job second\n'
        self.path.write_bytes(self.raw)
        self.backend = native.Backend(run=lambda a: self.fail('OS command forbidden'), platform='linux', home=tmp.name, system_directory=tmp.name)
        self.st = FakeState(tmp.name)
        self.ctl = native.Controller(self.st, self.backend)
        self.target = dict(platform='linux', source='cron-system', scope='system', id='crontab:3', snapshot='0'*64, op='disable')
        self.refresh()

    def refresh(self, op=None):
        if op:
            self.target['op'] = op
        self.target['snapshot'] = self.backend.snapshot(self.target)['snapshot']

    def test_crond_and_anacron_roundtrips(self):
        (self.root / 'cron.d').mkdir()
        for ident, source, raw in [('cron.d/job:2', 'cron-system', b'# keep\n@reboot root /bin/job\n'), ('anacrontab:2', 'anacron', b'# keep\n1 5 daily /bin/job\n')]:
            with self.subTest(ident=ident):
                path = self.root / ident.split(':')[0]
                path.write_bytes(raw)
                self.target.update(source=source, id=ident, op='disable')
                self.refresh()
                self.ctl.apply(self.target, lambda: None)
                self.refresh('restore')
                self.ctl.apply(self.target, lambda: None)
                self.assertEqual(path.read_bytes(), raw)

    def test_runparts_roundtrip_without_permission_changes(self):
        folder = self.root / 'cron.daily'
        folder.mkdir()
        script = folder / 'job'
        fd = os.open(script, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o700)
        os.write(fd, b'#!/bin/sh\n# fixture only; never execute\n')
        os.close(fd)
        identity = script.stat()
        raw = script.read_bytes()
        self.target.update(id='cron.daily/job')
        self.refresh()
        self.ctl.apply(self.target, lambda: None)
        self.assertFalse(script.exists())
        retained = folder / '.agentj-disabled-job'
        self.assertEqual(retained.read_bytes(), raw)
        self.assertEqual(retained.stat().st_mode, identity.st_mode)
        self.refresh('restore')
        self.ctl.apply(self.target, lambda: None)
        self.assertEqual(script.read_bytes(), raw)
        self.assertEqual(script.stat().st_ino, identity.st_ino)
        self.assertEqual(script.stat().st_mode, identity.st_mode)

    def test_external_edit_stale_target(self):
        self.path.write_bytes(self.raw + b'# concurrent\n')
        with self.assertRaisesRegex(native.Refused, 'changed'):
            self.ctl.apply(self.target, lambda: None)
        self.assertFalse(self.ctl.path.exists())

    def test_replace_after_journal_before_effect(self):
        write = self.st.write_private
        replacement = self.root / 'replacement'
        replacement.write_bytes(self.raw)
        def race(path, data):
            write(path, data)
            os.replace(replacement, self.path)
        self.st.write_private = race
        with self.assertRaisesRegex(native.Refused, 'changed'):
            self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertTrue(self.ctl.journal())

    def test_edit_after_disable_never_overwritten_on_restore(self):
        self.ctl.apply(self.target, lambda: None)
        changed = self.path.read_bytes() + b'# external\n'
        self.path.write_bytes(changed)
        self.refresh('restore')
        with self.assertRaisesRegex(native.Refused, 'changed'):
            self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.path.read_bytes(), changed)

    def test_original_is_immutable_across_failed_restore(self):
        self.ctl.apply(self.target, lambda: None)
        record = self.ctl.journal()[self.ctl.key(self.target)]
        self.refresh('restore')
        with patch.object(native.os, 'replace', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):
                self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.ctl.journal()[self.ctl.key(self.target)]['bytes'], record['bytes'])
        self.refresh('restore')
        self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_recover_after_rename_before_post_journal(self):
        write = self.st.write_private
        calls = []
        def fail_post(path, data):
            calls.append(1)
            if len(calls) == 2:
                raise OSError('crash after rename')
            write(path, data)
        self.st.write_private = fail_post
        with self.assertRaises(OSError):
            self.ctl.apply(self.target, lambda: None)
        self.st.write_private = write
        self.assertEqual(self.ctl.describe(dict(src='cron-system', scope='system', id='crontab:3'))['target']['op'], 'restore')
        self.refresh('restore')
        self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_recover_failed_first_effect_without_mutation(self):
        with patch.object(native.os, 'replace', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):
                self.ctl.apply(self.target, lambda: None)
        self.refresh('restore')
        self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(self.ctl.journal(), {})

    def test_marker_spoof_no_restore_or_disable(self):
        self.path.write_bytes(b'# a\n# b\n# agentj-disabled:forged 0 1 * * * root /bin/job\n')
        self.refresh()
        with self.assertRaisesRegex(native.Refused, 'marker_spoof'):
            self.ctl.apply(self.target, lambda: None)
        self.assertFalse(self.ctl.path.exists())

    def test_symlink_file_and_parent_denied(self):
        self.path.unlink()
        self.path.symlink_to(self.root / 'missing')
        with self.assertRaises(OSError):
            self.backend.snapshot(self.target)
        (self.root / 'elsewhere').mkdir()
        (self.root / 'elsewhere' / 'job').write_bytes(self.raw)
        (self.root / 'cron.d').symlink_to(self.root / 'elsewhere')
        self.target.update(id='cron.d/job:3')
        with self.assertRaises(OSError):
            self.backend.snapshot(self.target)

    def test_path_traversal_and_wrong_source_denied(self):
        for ident in ('../crontab:3', '/etc/crontab:3', 'cron.d/../crontab:3', 'cron.d/.agentj-stage-x:1', 'anacrontab:1'):
            with self.subTest(ident=ident), self.assertRaises(native.Refused):
                native.validate(dict(self.target, id=ident))

    def test_user_crontab_fails_closed_without_query(self):
        target = dict(self.target, source='cron', scope='user', id='crontab:1')
        self.backend.crontab_run = lambda *a, **k: b'unknown backend'
        with self.assertRaisesRegex(native.Refused, 'backend_semantics_unknown'):
            self.backend.snapshot(target)

    def test_unknown_format_and_foreign_os_identity(self):
        for line in (b'# comment\n', b'ENV=value\n', b'unknown extension\n', b'0 1 * * * root /bin/job\\\n'):
            self.path.write_bytes(b'#a\n#b\n' + line)
            with self.assertRaisesRegex(native.Refused, 'unknown_format'):
                self.backend.snapshot(self.target)
        self.backend.uid = os.getuid() + 1
        with self.assertRaisesRegex(native.Refused, 'os_privilege_required'):
            self.backend.snapshot(self.target)

    def test_collision_free_physical_user_line_ids(self):
        rows = systasks.scan_cron(lambda a: '# header\n0 1 * * * /bin/job a\n0 1 * * * /bin/job b\n')
        self.assertEqual([r['control_id'] for r in rows], ['crontab:2', 'crontab:3'])
        self.assertEqual({r['control']['reason'] for r in rows}, {'backend_unavailable'})

    def test_anacrontab_basename_in_crond_not_anacron(self):
        (self.root / 'cron.d').mkdir()
        (self.root / 'cron.d' / 'anacrontab').write_bytes(b'0 1 * * * root /bin/job\n')
        rows = systasks.scan_system_cron(str(self.root))
        row = next(r for r in rows if r['id'] == 'cron.d/anacrontab:1')
        self.assertEqual(row['src'], 'cron-system')

    def test_private_content_never_in_public_snapshots_or_recovery_rows(self):
        self.ctl.apply(self.target, lambda: None)
        public = [self.backend.snapshot(self.target), self.ctl.restore_rows([])]
        self.assertNotIn('private-fixture', json.dumps(public))
        self.assertNotIn('/bin/job', json.dumps(public))

    def test_busy_controller_fails_without_effect(self):
        native._LOCK.acquire()
        try:
            with self.assertRaisesRegex(native.Refused, 'busy'):
                self.ctl.apply(self.target, lambda: None)
        finally:
            native._LOCK.release()
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_journal_capacity_refused_before_effect(self):
        write = self.st.write_private
        self.st.write_private = lambda p, d: write(p, d)
        with patch.object(self.ctl, 'journal', return_value={'padding': 'x' * (512 * 1024)}):
            with self.assertRaisesRegex(native.Refused, 'journal_capacity'):
                self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(list(self.root.glob('.agentj-stage-*')))

    def test_second_job_same_table_refused_until_exact_restore(self):
        self.ctl.apply(self.target, lambda: None)
        original_target = dict(self.target)
        self.target.update(id='crontab:4', op='disable')
        self.refresh()
        with self.assertRaisesRegex(native.Refused, 'source_has_pending_control'):
            self.ctl.apply(self.target, lambda: None)
        self.target = original_target
        self.refresh('restore')
        self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_cooperating_process_lock_is_not_external_cas(self):
        import fcntl
        fd = os.open(self.st.root / 'native-scheduler-controls.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(native.Refused, 'busy'):
                self.ctl.apply(self.target, lambda: None)
        finally:
            os.close(fd)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_private_journal_with_real_state_writer(self):
        from agentj.state import State
        ctl = native.Controller(State(self.root), self.backend)
        ctl.apply(self.target, lambda: None)
        self.assertEqual(ctl.path.stat().st_mode & 0o777, 0o600)
        self.refresh('restore')
        ctl.apply(self.target, lambda: None)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_corrupt_original_refuses_restore(self):
        import base64
        self.ctl.apply(self.target, lambda: None)
        journal = self.ctl.journal()
        journal[self.ctl.key(self.target)]['bytes'] = base64.b64encode(b'corrupt').decode()
        self.st.write_private(self.ctl.path, json.dumps(journal).encode())
        self.refresh('restore')
        disabled = self.path.read_bytes()
        with self.assertRaisesRegex(native.Refused, 'corrupt_original'):
            self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.path.read_bytes(), disabled)


class SourceLive(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, change, real_host=False):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'crontab'
            raw = b'0 1 * * * root /bin/job\n'
            path.write_bytes(raw)
            st = FakeState(tmp)
            pk = {'id': 'fixture-pk', 'spki': 'fixture-public', 'sc': 0}
            st.passkey_of_device = lambda d: dict(pk)
            st.passkey_count = lambda *a: None
            backend = native.Backend(run=lambda a: self.fail('real OS runner'), platform='linux', home=tmp, system_directory=tmp)
            ctl = native.Controller(st, backend)
            session = NS(state='ready', device='device', pub=b'fixture', cid='cid', p33=True)
            values = dict(st=st, channel='fixture-channel', sessions={'cid': session}, nonces=controls.Nonces(), native_scheduler_controller=ctl,
                          _ctl_res=AsyncMock(), task_epoch='initial', hist=NS(epoch=1), _pairing_epoch=0,
                          agent=NS(kind='fixture'), agent_cfg={}, cur_turn='turn1', estop={'on': False})
            if real_host:
                from agentj.serve import Host
                host = Host.__new__(Host)
                host.__dict__.update(values)
            else:
                host = NS(**values)
                host._task_id = lambda: host.cur_turn
            target = dict(platform='linux', source='cron-system', scope='system', id='crontab:1', op='disable', snapshot='0'*64)
            target['snapshot'] = backend.snapshot(target)['snapshot']
            msg = dict(t=native.ACTION, r='1'*32, n='c'*32, ts=int(time.time()*1000), fa={}, **target)
            msg['sig'] = wire.b64u(st.key.sign(controls.signed_message(host.channel, 'device', native.ACTION, msg['n'], msg['ts'], controls.object_digest(native.ACTION, target))))
            entered, release, done = threading.Event(), threading.Event(), threading.Event()
            write = st.write_private
            def pause(p, data):
                write(p, data)
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('fixture timed out')
            st.write_private = pause
            apply = ctl.apply
            def finished(*a):
                try:
                    return apply(*a)
                finally:
                    done.set()
            ctl.apply = finished
            with patch('agentj.passkey.approval_fields', return_value={'id': pk['id']}), patch('agentj.passkey.verify_assertion', return_value=1):
                if real_host:
                    # The actual reader now returns after dispatch so it can
                    # receive the same phone's sealed password/UV response.
                    # Await/cancel the real tracked worker, not the completed reader.
                    before=set(getattr(host, '_bg_tasks', ()))
                    await Host._app(host, session, msg)
                    workers=set(host._bg_tasks)-before
                    self.assertEqual(len(workers),1)
                    task=workers.pop()
                else:
                    task=asyncio.create_task(native.on_phone(host, session, msg))
                try:
                    for _ in range(300):
                        if entered.is_set() or task.done():
                            break
                        await asyncio.sleep(.005)
                    self.assertTrue(entered.is_set())
                    if change == 'cancel':
                        task.cancel()
                        await asyncio.sleep(0)
                    elif change == 'disconnect':
                        session.state = 'closed'
                    elif change == 'unbind':
                        host._unbinding_all = True
                    elif change == 'epoch':
                        host.hist.epoch += 1
                    elif change == 'agent':
                        host.agent = NS(kind='fixture')
                    elif change == 'task':
                        host.cur_turn = 'turn2'
                    elif change == 'estop':
                        host.estop['on'] = True
                    elif change == 'history':
                        host.hist = NS(epoch=1)
                    elif change == 'task_epoch':
                        host.task_epoch = 'replacement'
                    elif change == 'key':
                        st.key = Ed25519PrivateKey.generate()
                    elif change == 'passkey':
                        pk['spki'] = 'replacement-public'
                    elif change == 'session':
                        host.sessions['cid'] = NS()
                    elif change == 'controller':
                        host.native_scheduler_controller = NS()
                    elif change == 'allowlist':
                        st.allowed = False
                finally:
                    release.set()
                if change == 'cancel':
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    await task
                for _ in range(300):
                    if done.is_set():
                        break
                    await asyncio.sleep(.005)
                self.assertTrue(done.is_set())
                if change == 'positive':
                    self.assertNotEqual(path.read_bytes(), raw)
                    target.update(op='restore', snapshot=backend.snapshot(target)['snapshot'])
                    msg.update(target, n='d'*32, ts=int(time.time()*1000))
                    msg['sig'] = wire.b64u(st.key.sign(controls.signed_message(host.channel, 'device', native.ACTION, msg['n'], msg['ts'], controls.object_digest(native.ACTION, target))))
                    if real_host:
                        before=set(host._bg_tasks)
                        await Host._app(host, session, msg)
                        workers=set(host._bg_tasks)-before
                        self.assertEqual(len(workers),1)
                        await asyncio.gather(*workers)
                    else:
                        await native.on_phone(host, session, msg)
                    self.assertTrue(host._ctl_res.call_args.kwargs['ok'])
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(bool(ctl.journal()), change != 'positive')
            self.assertFalse(list(path.parent.glob('.agentj-stage-*')))

    async def test_signed_cron_disable_and_exact_restore(self):
        await self.exercise('positive')

    async def test_live_authority_changes_before_effect(self):
        for change in ('agent', 'task', 'estop', 'history', 'disconnect', 'unbind', 'epoch', 'task_epoch', 'key', 'passkey', 'session', 'controller', 'allowlist', 'cancel'):
            with self.subTest(change=change):
                await self.exercise(change)

    async def test_real_host_dispatch_live_boundaries(self):
        # Full dependency Host route; no Host construction/start, sockets, or real state.
        for change in ('positive', 'agent', 'task', 'estop', 'history', 'disconnect', 'unbind', 'epoch', 'key', 'passkey', 'session', 'cancel'):
            with self.subTest(change=change):
                await self.exercise(change, real_host=True)
