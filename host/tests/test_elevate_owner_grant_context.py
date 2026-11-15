"""Owner-only admin reuse remains bound through real executor scheduling."""
import _hermetic
import asyncio
import types
import contextlib
from unittest import mock
from agentj import elevate, provenance, approval_review
from test_p393_elevate_grant import run, A, REAL

async def race_case(r, kind):
    with mock.patch.object(elevate, 'run_sudo', wraps=elevate.run_sudo) as execute:
        card, res = await r.approve(A)
        assert res.get('result') == 'done' and execute.call_count == 1 and (len(r.host.elevate.grants.grants) == 1)
        reuse_card, reuse_result = await r.approve(A)
        assert reuse_card is None and reuse_result.get('auto') and (reuse_result.get('result') == 'done') and (execute.call_count == 2)
        before = execute.call_count
        real = asyncio.to_thread
        book = r.host.elevate.grants

        async def boundary(fn, *args, **kwargs):
            if getattr(fn, '__name__', '') != 'execute':
                return await real(fn, *args, **kwargs)
            context = contextlib.nullcontext()
            if kind == 'source':
                r.host.agent.cur_send = types.SimpleNamespace(source=provenance.email_guest('guest@example.com'))
            elif kind == 'agent':
                r.host.agent = types.SimpleNamespace(kind='claude', cur_send=None, native_source=provenance.terminal())
            elif kind == 'session':
                r.st.set_agent_session('claude', 'synthetic-other-session')
            elif kind == 'task':
                turn = r.host.hist.add({'k': 'terminal', 'text': 'new synthetic owner turn', 'name': 'owner'}, '', 'open')
                approval_review.remember(r.host, turn, provenance.terminal())
                r.host.desktop_turn = turn['id']
            elif kind == 'revoke':
                book.revoke(None, 'revoked')
            elif kind == 'expire':
                next(iter(book.grants.values())).expires = book.clock() - 1
            elif kind == 'key':
                context = mock.patch.object(r.st, 'sign_key', return_value=b'changed-test-key-never-valid')
            elif kind == 'child':
                context = provenance.shared_child_request(r.host.agent, provenance.email_guest('guest@example.com'))
            elif kind == 'request':
                context = provenance.request_source(provenance.email_guest('guest@example.com'))
            elif kind == 'taskscope':
                context = provenance.task_request({'live': True, 'owner': False})
            with context:
                return await real(fn, *args, **kwargs)
        with mock.patch.object(asyncio, 'to_thread', new=boundary):
            card, res = await r.deny_next(A)
        assert res.get('result') != 'done' and execute.call_count == before

async def create_race(r):
    with mock.patch.object(elevate, 'run_sudo', wraps=elevate.run_sudo) as execute:
        book = r.host.elevate.grants
        original = book.create

        def moved(*args, **kwargs):
            r.host.agent.cur_send = types.SimpleNamespace(source={'kind': 'friend_task', 'role': 'task'})
            return original(*args, **kwargs)
        with mock.patch.object(book, 'create', side_effect=moved):
            card, res = await r.approve(A)
        assert res.get('result') == 'done' and execute.call_count == 1
        assert len(book.grants) == 0

async def suspicious_owner(r):
    with mock.patch.object(elevate, 'run_sudo', wraps=elevate.run_sudo) as execute:
        card, res = await r.approve(A)
        assert res.get('result') == 'done' and len(r.host.elevate.grants.grants) == 1
        card, res = await r.approve(A)
        assert card is None and res.get('auto') and (execute.call_count == 2)
        r.host._approval_origins[r.host.desktop_turn]['suspicious'] = True
        before = execute.call_count
        card, res = await r.deny_next(A)
        assert card is not None and (not card.get('grant')) and (res.get('result') == 'denied') and (not res.get('auto')) and (execute.call_count == before)
import unittest

@unittest.skipUnless(REAL, 'needs existing root-owned read-only gzip fixture')
class OwnerGrantBoundary(unittest.TestCase):

    def test_executor_revalidates_owner_and_grant_at_actual_thread_boundary(self):
        for kind in ['source', 'agent', 'session', 'task', 'revoke', 'expire', 'key', 'child', 'request', 'taskscope']:
            with self.subTest(kind=kind):
                run(lambda r, kind=kind: race_case(r, kind))

    def test_source_moved_before_creation_cannot_mint_owner_grant(self):
        run(create_race)

    def test_suspicious_owner_gets_fresh_card_and_never_reuses_grant(self):
        run(suspicious_owner)
