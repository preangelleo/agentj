"""Recovered state-authority vectors at the current real Host/list-approval entry.

Synthetic isolated State/devices only. No legacy classifier, ambient authority,
parked effect grants, production service, or feature switch is enabled.
"""
import _hermetic
import asyncio
import shutil
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from agentj import approvals, list_approval, peer_guard, uv_list
from test_l1 import _state
import test_uv_list as current


class StateSubject(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = current.ListApproval.asyncSetUp
    open = current.ListApproval.open
    close = current.ListApproval.close
    review_intent = current.ListApproval.review_intent
    question_approve = current.ListApproval.question_approve

    def other_state(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        return _state(tmp.name)

    async def test_custom_literal_root_alias_and_caller_root_are_distinct(self):
        old = self.st.root
        literal = old.with_name('state$literal[1]')
        old.rename(literal)
        alias = old.with_name('state-alias')
        alias.symlink_to(literal, target_is_directory=True)
        self.st.root = alias
        # Literal metachar operands are currently refused, not silently run.
        denied = await self.h.ask('Edit', {'file_path': str(literal / 'devices.json')})
        self.assertEqual(denied['behavior'], 'deny')
        self.assertFalse(self.h.asks)
        for tool, inp in (('Edit', {'file_path': str(alias / 'devices.json')}),):
            task, card = await self.open(tool, inp)
            self.assertTrue(card.require_passkey)
            await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(card, self.s), False))
            self.assertEqual((await task)['behavior'], 'deny')
        other = self.other_state()
        self.assertFalse(uv_list.required('Edit', {'file_path':str(other.devices_path),
            'state_root':str(other.root), 'authority':{'root':str(other.root)},
            'env':{'AGENTJ_STATE_DIR':str(other.root)}},
            cwd=(self.h.agent_cfg or {}).get('dir'), env=uv_list.host_environment(self.h)))
        self.assertTrue(uv_list.required('Edit', {'file_path':str(self.st.devices_path)},
            cwd=(self.h.agent_cfg or {}).get('dir'), env=uv_list.host_environment(self.h)))

    async def test_custom_root_private_reads_never_open_a_card(self):
        for path in (self.st.ed25519_path, self.st.devices_path):
            answer = await self.h.ask('Read', {'file_path':str(path)})
            self.assertEqual(answer['behavior'], 'deny')
            self.assertFalse(self.h.asks)
        # Current contract explicitly exempts the redacted host.log.
        answer = await self.h.ask('Read', {'file_path':str(self.st.log_path)})
        self.assertEqual(answer['behavior'], 'allow')
        self.assertFalse(self.h.asks)

    async def test_pending_host_request_never_writes_another_states_audit(self):
        task, card = await self.open()
        other = self.other_state()
        before = other.approvals_path.read_bytes() if other.approvals_path.exists() else b''
        original = self.h.st
        self.h.st = other
        try:
            card.fut.set_result(('deny', None, None, None))
            self.assertEqual((await task)['behavior'], 'deny')
            after = other.approvals_path.read_bytes() if other.approvals_path.exists() else b''
            self.assertEqual(after, before, 'pending request wrote the replacement State audit')
        finally:
            self.h.st = original

    def replace_root(self):
        root = self.st.root
        moved = root.with_name('retired-state')
        root.rename(moved)
        # Deliberately preserve synthetic identities: a changed inode must not
        # inherit a request solely because the device/channel bytes still match.
        shutil.copytree(moved, root)
        return root, moved

    async def test_root_replacement_after_card_cannot_grant_or_write_new_audit(self):
        task, card = await self.open()
        root, moved = self.replace_root()
        before = self.st.approvals_path.read_bytes() if self.st.approvals_path.exists() else b''
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(card, self.s), True))
        if not card.fut.done():
            card.fut.set_result(('deny', None, None, None))
        result = await task
        after = self.st.approvals_path.read_bytes() if self.st.approvals_path.exists() else b''
        self.assertEqual((result['behavior'], after == before), ('deny', True),
                         'replacement root must inherit neither pending grant nor audit')

    async def test_root_replacement_during_inventory_cannot_open_card_or_write_new_audit(self):
        entered, release = threading.Event(), threading.Event()
        build = peer_guard.SecretIndex.build
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('synthetic inventory barrier')
            return build(*args, **kwargs)
        task = None
        try:
            with mock.patch.object(peer_guard.SecretIndex, 'build', side_effect=blocked):
                task = asyncio.create_task(self.h.ask('agentj fn run', {'name':'synthetic'}))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.replace_root()
                before = self.st.approvals_path.read_bytes() if self.st.approvals_path.exists() else b''
                release.set()
                for _ in range(200):
                    if task.done() or self.h.asks:
                        break
                    await asyncio.sleep(.002)
                opened = bool(self.h.asks)
                for card in list(self.h.asks.values()):
                    if not card.fut.done():
                        card.fut.set_result(('deny', None, None, None))
                result = await task
                self.assertEqual(result['behavior'], 'deny')
                self.assertFalse(opened, 'changed root reached a new approval card after awaited inventory')
                after = self.st.approvals_path.read_bytes() if self.st.approvals_path.exists() else b''
                self.assertEqual(after, before)
        finally:
            release.set()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_current_question_lease_cannot_move_to_another_state(self):
        card, resume = await self.question_approve('agentj fn run', {'name':'synthetic'})
        original = self.h.st
        self.h.st = self.other_state()
        try:
            self.assertIsNone(list_approval.take_resume(self.h, card.tool, card.tool_input, True,
                                                       card.list_resume_context, None))
            self.assertFalse(self.h.__dict__.get('_uv_list_resumes'))
        finally:
            self.h.st = original

    async def test_current_question_lease_dies_when_root_inode_changes(self):
        card, resume = await self.question_approve('agentj fn run', {'name':'synthetic'})
        self.replace_root()
        self.assertIsNone(list_approval.take_resume(self.h, card.tool, card.tool_input, True,
                                                   card.list_resume_context, None))
        self.assertFalse(self.h.__dict__.get('_uv_list_resumes'))

    async def test_normal_uv_allow_and_deny_preserve_signed_audit(self):
        for allowed in (True, False):
            task, card = await self.open()
            await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(card, self.s), allowed))
            result = await task
            self.assertEqual(result['behavior'], 'allow' if allowed else 'deny')
            record = next(row for row in approvals.read_log(self.st) if row['id'] == card.rid)
            self.assertEqual(record['channel'], self.h.channel)
            self.assertEqual(record['decision'], result['behavior'])
            self.assertEqual(approvals.check_record(self.st, record), 'ok')

    async def test_cancelled_request_releases_no_ambient_state_authority(self):
        task, card = await self.open()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertNotIn(card.rid, self.h.asks)
        other = self.other_state()
        self.assertFalse(uv_list.required('Edit', {'file_path':str(other.devices_path)},
            env=uv_list.host_environment(self.h)))
        fresh, new = await self.open()
        self.assertIsNot(card, new)
        self.assertTrue(new.require_passkey)

    async def test_root_replacement_in_reservation_cannot_settle_uv(self):
        from agentj.bounded_plan import OperationOnce
        task, card = await self.open()
        entered, release = threading.Event(), threading.Event()
        real = OperationOnce(self.st.root / 'approval-operation-once.sqlite3')
        class Ledger:
            def reserve(self, operation, rid):
                value = real.reserve(operation, rid)
                entered.set()
                if not release.wait(5):
                    raise OSError('synthetic reservation barrier')
                return value
        self.h._approval_operation_once = Ledger()
        answer = asyncio.create_task(self.h._answer(self.s,
            self.ph.answer(self.h._ask_msg(card, self.s), True)))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            self.replace_root()
            before = self.st.approvals_path.read_bytes() if self.st.approvals_path.exists() else b''
        finally:
            release.set()
        await answer
        self.assertEqual((await task)['behavior'], 'deny')
        after = self.st.approvals_path.read_bytes() if self.st.approvals_path.exists() else b''
        self.assertEqual(after, before)
        self.assertFalse(self.h.__dict__.get('_uv_list_resumes'))

    async def test_parked_callback_root_replacement_never_audits_or_resumes(self):
        task, card = await self.open()
        await current.approval_review.on_phone(self.h, self.s, self.review_intent(card, 'question'))
        self.assertTrue((await task)['review_pending'])
        self.replace_root()
        before = self.st.approvals_path.read_bytes()
        calls = self.h.agent.submit.call_count
        # Simulate a previously scheduled settlement reaching its callback after
        # the root changes. The real callback, not a replacement implementation.
        card.fut.set_result(('allow', self.ph.did, self.st.sign_key(self.ph.did), b'synthetic'))
        await asyncio.sleep(0)
        self.assertEqual(self.st.approvals_path.read_bytes(), before)
        self.assertEqual(self.h.agent.submit.call_count, calls)
        self.assertNotIn(card.rid, self.h.asks)

    async def test_audit_open_checks_inode_after_concurrent_root_replace(self):
        subject = list_approval.StateSubject.capture(self.h)
        original_open = os.open
        swapped = False
        def opening(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == subject.root and not swapped:
                swapped = True
                self.replace_root()
            return original_open(path, flags, *args, **kwargs)
        with mock.patch.object(os, 'open', side_effect=opening):
            result = subject.record(self.h, rid='synthetic', agent='synthetic', tool='synthetic',
                input_sha256='0'*64, shown_sha256='0'*64, decision='deny', reason='synthetic')
        self.assertIsNone(result)
        self.assertFalse(self.st.approvals_path.exists())

    async def test_audit_symlink_refused_without_writing_foreign_file(self):
        target = self.st.root.parent / 'unrelated-audit'
        target.write_bytes(b'synthetic-existing')
        task, card = await self.open()
        self.st.approvals_path.unlink(missing_ok=True)
        self.st.approvals_path.symlink_to(target)
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(card, self.s), True))
        self.assertEqual((await task)['behavior'], 'deny')
        self.assertEqual(target.read_bytes(), b'synthetic-existing')
