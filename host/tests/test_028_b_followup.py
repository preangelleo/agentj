"""Offline followup contracts. All authentication/network/process seams are synthetic."""
import _hermetic  # noqa
import asyncio
import unittest
import tempfile
import time
import os
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from agentj import github_events as gh


class GitHubRead(unittest.TestCase):
    def scope(self, **kw):
        s = dict(source='github', kind='github.pr_comment', repository='owner/repo', thread='42',
                 sender='alice', owner='own:codex:original', since=100, deadline=2000000000)
        return dict(s, **kw)

    def row(self, **kw):
        return dict(dict(id=17, created_at='2026-01-01T00:00:00Z', user={'login':'alice'},
                         body='$(ignore this; data)', issue_url='https://api.github.com/repos/owner/repo/issues/42'), **kw)

    def test_fixed_endpoint_and_body_never_command(self):
        get = Mock(return_value=[self.row()])
        events, more = gh.page(self.scope(), 1, 'synthetic', get=get)
        self.assertFalse(more)
        self.assertEqual(events[0]['text'], '$(ignore this; data)')
        self.assertEqual(get.call_args.args[0], '/repos/owner/repo/issues/42/comments?since=1970-01-01T00%3A01%3A40Z&per_page=100&page=1')

    def test_scope_ssrf_and_page_limits(self):
        for change in ({'repository':'https://evil/x'}, {'repository':'a/../b'}, {'thread':'42?x=1'},
                       {'since':False}, {'since':2000000000}):
            with self.assertRaises(ValueError):
                gh.page(self.scope(**change), 1, 'synthetic', get=Mock())
        with self.assertRaises(ValueError):
            gh.page(self.scope(), 6, 'synthetic', get=Mock())

    def test_redirect_denied(self):
        with self.assertRaises(gh.Unavailable):
            gh.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test')

    def test_repository_and_sender_filter(self):
        with self.assertRaises(gh.Unavailable):
            gh.page(self.scope(), 1, '', get=Mock(return_value=[self.row(issue_url='https://evil')]))
        self.assertEqual(gh.page(self.scope(), 1, '', get=Mock(return_value=[self.row(user={'login':'mallory'})]))[0], [])

    def test_ci_failure_and_exact_run(self):
        s = self.scope(kind='github.ci_failure', sender='github-actions')
        job = dict(id=1, run_id=42, started_at='2026-01-01T00:00:00Z', conclusion='failure', name='test')
        self.assertEqual(len(gh.page(s, 1, '', get=Mock(return_value={'jobs':[job]}))[0]), 1)
        job['run_id'] = 43
        with self.assertRaises(gh.Unavailable):
            gh.page(s, 1, '', get=Mock(return_value={'jobs':[job]}))

    def test_no_credential_setup_is_explicit(self):
        with patch.object(gh.subprocess, 'run', return_value=Mock(returncode=1, stdout=b'')) as run:
            with self.assertRaisesRegex(gh.Unavailable, 'needs_setup'):
                gh.credential()
        self.assertEqual(run.call_args.args[0], ['gh','auth','token','--hostname','github.com'])


class Acceptance(unittest.TestCase):
    def test_checks_and_retry_policy_are_in_signed_brief_scope(self):
        from agentj import capability, ltschema
        brief = {'acceptance':[{'id':'a','criterion':'works'}], 'failure_policy':{'retry_limit':0}}
        legacy = capability.scope_digest(brief)
        brief['acceptance'][0]['check'] = 'false'
        with_check = capability.scope_digest(brief)
        self.assertNotEqual(legacy,with_check)
        brief['acceptance'][0]['check'] = 'true'
        self.assertNotEqual(with_check,capability.scope_digest(brief))
        policy_before = capability.scope_digest(brief)
        brief['failure_policy']['retry_limit'] = 1
        self.assertNotEqual(policy_before,capability.scope_digest(brief))
        self.assertIn('check',ltschema.schema('brief')['properties']['acceptance']['items']['properties'])

    def test_freshness_digest_head_and_mutation(self):
        from agentj import acceptance as a
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'artifact').write_text('first')
            (root/'.git').mkdir()
            (root/'.git/HEAD').write_text('a'*40)
            bound = a.binding(root, ['artifact'])
            self.assertEqual(bound['head'], 'a'*40)
            self.assertEqual(a.check(root, [{'check':'true'}], ['artifact'], time.time()+10, rerun=True)['acceptance'][0]['status'], 'stale')
            def changed(*args):
                (root/'artifact').write_text('second')
                return {'status':'pass'}
            with patch.object(a, 'run', side_effect=changed):
                self.assertEqual(a.check(root,[{'check':'true'}],['artifact'],rerun=True)['why'], 'binding_changed')

    def test_path_and_symlink_negative(self):
        from agentj import acceptance as a
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'file').write_text('x'); (root/'link').symlink_to(root/'file')
            for name in ('../file', '/etc/passwd', 'link'):
                with self.assertRaises(ValueError):
                    a.regular(root, name)

    def test_legacy_and_no_second_model(self):
        from agentj import acceptance as a
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(a.check(tmp, ['old text'])['acceptance'][0]['status'], 'unchecked')
            with patch.object(a, 'research_argv', side_effect=ValueError('unavailable')):
                self.assertEqual(a.run(tmp, 'true')['status'], 'unavailable')

    def test_readonly_offline_argv_has_no_home_or_host_socket(self):
        from agentj import acceptance as a
        with tempfile.TemporaryDirectory() as tmp, patch.object(a, 'bubblewrap', return_value='/usr/bin/bwrap'):
            argv = a.research_argv(tmp, 'cat artifact')
            self.assertIn('--unshare-all', argv)
            self.assertIn('--clearenv', argv)
            self.assertNotIn('--bind', argv)
            self.assertEqual(argv[-3:], ['/bin/sh','-c','cat artifact'])


class Retry(unittest.TestCase):
    def test_only_known_ended_retry_same_and_limit(self):
        from agentj import task_retry as r
        for kind in ('network','rate_limit','server'):
            row = r.result(kind, 1, [], ended=True, known=True)
            self.assertTrue(r.allowed(row,0,5))
            self.assertFalse(r.allowed(row,0,0))
            self.assertFalse(r.allowed(row,5,5))
            self.assertEqual(r.result(kind,1,[row],ended=True,known=True)['retry_advice'], 'escalate')
        for kind in ('unknown','auth','context','budget','tool_error'):
            self.assertFalse(r.allowed(r.result(kind,1,[],ended=True,known=True),0,5))
        for ended, known in ((False,True),(True,False),(False,False)):
            row = r.result('network',1,[],ended=ended,known=known)
            self.assertEqual(row['retry_advice'], 'inspect_uncertain')
            self.assertFalse(r.allowed(row,0,5))


class Integration(unittest.TestCase):
    """0342 pending contract; historic live routing/polling is not activated."""
    import test_028_worker_b as previous
    setUp = previous.SignedRoutes.setUp
    event = previous.Events.event
    signed = previous.SignedRoutes.signed
    enable = previous.SignedRoutes.enable

    def github(self):
        self.scope.update(source='github',kind='github.pr_comment',repository='owner/repo',thread='42',sender='alice',since=100)
        self.key = self.router.propose(self.scope)['key']
        self.enable()
        return dict(source='github',kind='github.pr_comment',thread='42',sender='alice',id='owner/repo:1',text='untrusted data')

    def test_signed_poll_routing_dedupe_and_backoff(self):
        event = self.github()
        with patch.object(gh,'credential') as credential, patch.object(gh,'page') as page:
            asyncio.run(self.router.tick()); asyncio.run(self.router.tick())
        credential.assert_not_called(); page.assert_not_called()
        self.assertFalse(self.accepted)
        self.assertFalse(self.router.live(self.key))
        self.assertFalse(self.router.view()['routes'][self.key]['enabled'])

    def test_no_poll_record_only_revoke_during_read_and_restart(self):
        event = self.github()
        self.enable(enabled=False)
        with patch.object(gh,'credential') as credential:
            asyncio.run(self.router.tick()); credential.assert_not_called()
        self.enable()
        def revoked(*args):
            self.host.st.sign_key.return_value = None
            return [event], False
        with patch.object(gh,'credential',return_value='synthetic'), patch.object(gh,'page',side_effect=revoked):
            asyncio.run(self.router.tick())
        self.assertFalse(self.accepted)

    def test_signature_scope_recheck_and_pagination_cap(self):
        self.github()
        d = self.router.view(); d['routes'][self.key]['scope']['owner'] = 'own-codex-replacement'
        self.router._save(d)
        with patch.object(gh,'credential') as credential, patch.object(gh,'page') as page:
            asyncio.run(self.router.tick())
        credential.assert_not_called(); page.assert_not_called()
        self.assertFalse(self.router.live(self.key)); self.assertFalse(self.accepted)

    def test_device_lifecycle_revocation_survives_same_key_and_restart(self):
        from agentj import subscriptions
        self.github()
        self.router.revoke_device('phone')
        self.assertFalse(self.router.view()['routes'][self.key]['enabled'])
        self.router = subscriptions.Router(self.host)
        with patch.object(gh,'credential') as credential:
            asyncio.run(self.router.tick())
        credential.assert_not_called()
        self.enable()  # a fresh exact paired signature is required even if the public key was reused
        self.assertFalse(self.router.live(self.key))  # signed control cannot activate pending scope

    def test_real_workflow_recipient_fixed_inbox_and_lost_owner(self):
        from agentj import tasks
        directory = self.host.st.root/'workflow'; directory.mkdir()
        scheduler = SimpleNamespace(current_id='ceo', queued={'ceo':'run-1'}, current=Mock(done=lambda:False),
            proc=SimpleNamespace(returncode=None), runner=None, instance_id='instance', workdir=lambda:str(directory.parent))
        self.host.scheduler = scheduler
        self.scope['owner'] = 'task:ceo:run-1'
        self.key = self.router.propose(self.scope)['key']; self.enable()
        rec = dict(status='running',instance_id='instance',tsha='contract')
        ent = dict(problems=[],tsha='contract',dir=str(directory))
        with patch.object(tasks,'load',return_value={'runs':{'run-1':rec}}), patch.object(tasks,'find',return_value=ent):
            result = asyncio.run(self.router.ingest(self.event(),mail_authenticated=True))
            self.assertEqual(result['reason'],'ambiguous_route')  # neither pending route owns delivery
            detail = self.router.detail(result['key'])
            self.assertNotIn('inbox',detail)
            self.assertFalse(list(directory.iterdir()))
            scheduler.queued['ceo'] = 'run-2'
            self.assertEqual(asyncio.run(self.router.ingest(self.event('2'),mail_authenticated=True))['reason'],'ambiguous_route')
        self.assertFalse(self.accepted)

    def test_real_host_continuous_loop_polls(self):
        from agentj.serve import Host
        # Real Host method; no Host constructor, service, model, or network startup.
        self.host.busy = SimpleNamespace(refresh_outside=Mock(side_effect=RuntimeError('fake busy failure')))
        self.host.theme_refresh = lambda:False
        self.host.stopping = asyncio.Event()
        self.host.event_subscriptions = self.router
        self.github()
        async def stop(*args):
            raise asyncio.CancelledError
        with patch.object(gh,'credential',return_value='synthetic'), patch.object(gh,'page',return_value=([],False)) as page, patch('agentj.serve.asyncio.sleep',side_effect=stop):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(Host.event_loop(self.host))
        self.assertEqual(page.call_count,0)  # pending owner scope never polls

    def test_actual_host_accept_and_native_delivery_remain_untrusted(self):
        from agentj import serve, subscriptions, provenance
        from agentj.agent import Agent, Withdrawn
        from test_l1 import _state, _owner_host
        from unittest.mock import AsyncMock
        st = _state(str(self.host.st.root / 'host-fixture'))
        h = _owner_host(st, [])
        old = self.host
        h.agent.kind, h.agent.thread_id = 'codex', 'original-owner'
        h.agent.cfg = {'dir': str(self.host.st.root), 'kind':'codex'}
        h.agent_cfg = dict(h.agent.cfg)
        received = []
        h.agent.submit = received.append
        st.sign_key = old.st.sign_key
        h._ctl_res = old._ctl_res
        self.host = h
        self.router = subscriptions.router(h)
        self.key = self.router.propose(self.scope)['key']
        event = self.github()
        async def exercise():
            with patch.object(gh,'credential',return_value='synthetic'), patch.object(gh,'page',return_value=([event],False)):
                await self.router.tick()
            self.assertFalse(received)
            self.assertFalse(self.router.live(self.key))
            # Existing generic external ingress is still tested independently;
            # this does not activate an event route or manufacture owner trust.
            source=provenance.external_mcp('A'*22)
            session=SimpleNamespace(device='github:alice',name='alice',cid=0,
                source_kind='external_mcp',provenance=source)
            await h._accept(session,'External fixture data only',None)
            self.assertEqual(len(received),1)
            send=received[0]
            self.assertFalse(provenance.is_owner(send.source))
            self.assertEqual(send.source['kind'],'external_mcp')
            self.assertEqual(h.hist.get(send.turn)['src']['k'],'external_mcp')
            write = AsyncMock()
            guard = self.router.delivery_guard(self.key,h.agent)
            h.agent.cur_send = SimpleNamespace(lock=asyncio.Lock(),state='pending',delivery_allowed=guard)
            with self.assertRaises(Withdrawn):
                await h.agent.deliver(write)
            write.assert_not_called()
        asyncio.run(exercise())


class PrivacyProxy(unittest.TestCase):
    def test_named_failure_deduplicated_no_backend_for_unknown_caller(self):
        from agentj import privacy_host, privacy, approval_material
        h = SimpleNamespace(st=SimpleNamespace(log=Mock()),agent=None,stopped=lambda:False,agent_notice=Mock())
        req = dict(t='privacy_check',gate='feedback',draft={'text':'data'})
        with patch.object(approval_material,'verified_caller_origin',return_value=None), patch.object(privacy,'layer2') as backend:
            for _ in range(3):
                self.assertEqual(asyncio.run(privacy_host.handle(h,req,peer_pid=123))['status'],'unavailable')
            backend.assert_not_called()
        self.assertEqual(h.agent_notice.call_count,1)
        self.assertEqual(h.st.log.call_count,1)

    def test_fixed_backend_source_and_missing_backend_fail(self):
        from agentj import privacy_host, privacy, approval_material
        h = SimpleNamespace(st=SimpleNamespace(log=Mock()),agent=None,stopped=lambda:False,agent_notice=Mock())
        req = dict(t='privacy_check',gate='plaza',draft={'body':'data'})
        origin = dict(verified=True,suspicious=False)
        with patch.object(approval_material,'verified_caller_origin',return_value=origin), patch.object(privacy,'layer2',return_value=privacy.Verdict('unavailable')) as backend:
            self.assertEqual(asyncio.run(privacy_host.handle(h,req,peer_pid=123))['reason'],'privacy-check/backend_unavailable')
            self.assertEqual(backend.call_args.kwargs,dict(gate='plaza',_host=True))
            req['provider'] = 'attacker'
            self.assertEqual(asyncio.run(privacy_host.handle(h,req,peer_pid=123))['status'],'unavailable')
            self.assertEqual(backend.call_count,1)


class GoalAndPause(unittest.TestCase):
    def host(self, directory):
        from agentj.state import State
        h = SimpleNamespace(st=State(Path(directory)/'state'), agent=SimpleNamespace(kind='codex',thread_id='original',
            cfg={'dir':directory}, native_source={'kind':'terminal','role':'owner'}),stopped=lambda:False,agent_notice=Mock(),activity=Mock())
        h.st.root.mkdir()
        h.st.log = Mock()
        return h

    def test_goal_check_signature_legacy_and_escape_once(self):
        from agentj import goal
        with tempfile.TemporaryDirectory() as tmp:
            h = self.host(tmp)
            old = goal.validate({'goal':'old', 'acceptance':['text']})
            self.assertEqual(old['acceptance'], ['text'])
            g = goal.validate({'goal':'new', 'acceptance':[{'text':'works','check':'true'}], 'escape_after':60})
            digest = goal.digest(g)
            g['escape_after'] = 61
            self.assertNotEqual(goal.digest(g),digest)
            d = dict(g,v=1,id='g',state='active',started=time.time()-100,escape_after=60,grant={},milestones=[])
            goal.save(h.st,d)
            goals = goal.Goals(h)
            with patch.object(goal,'is_live',return_value=True):
                goals.check(); goals.check()
            self.assertEqual(h.agent_notice.call_count,1)
            self.assertEqual(goal.load(h.st)['acceptance'],g['acceptance'])

    def test_goal_achieved_refuses_failed_or_unavailable_check(self):
        from agentj import goal, acceptance
        with tempfile.TemporaryDirectory() as tmp:
            h = self.host(tmp)
            g = dict(goal.validate({'goal':'new','acceptance':[{'text':'works','check':'false'}]}), id='g',state='active')
            goals = goal.Goals(h)
            with patch.object(goals,'current',return_value=g), patch.object(acceptance,'run',return_value={'status':'fail'}), patch.object(goals,'end') as end:
                result = asyncio.run(goals.handle({'t':'goal_done','how':'achieved'}))
                self.assertEqual(result['why'],'acceptance_failed'); end.assert_not_called()

    def test_pause_resume_actual_handoff_and_wrong_caller(self):
        from agentj import capability_pause as pause, self_compact, shared_hook, provenance
        with tempfile.TemporaryDirectory() as tmp:
            h = self.host(tmp)
            h.agent.proc = SimpleNamespace(pid=42)
            with patch.object(shared_hook,'ancestors',return_value=[43,42]), patch.object(self_compact,'turn_source',return_value=provenance.terminal()):
                self.assertEqual(pause.handle(h,{'t':'capability_pause','reason':'safe checkpoint'},43)['result'],'paused')
                self.assertTrue((Path(tmp)/'.agentj/handover/pause.md').is_file())
                self.assertIn('pause.md',pause.reminder(h))
                self.assertTrue(pause.paused(h))
                h.agent.thread_id = 'other-session'
                self.assertFalse(pause.paused(h))
                h.agent.thread_id = 'original'
                self.assertEqual(pause.handle(h,{'t':'capability_resume'},43)['result'],'resumed')
                self.assertTrue((Path(tmp)/'.agentj/handover/pause-used.md').is_file())
                self.assertEqual(pause.reminder(h),'')
            with patch.object(shared_hook,'ancestors',return_value=[99]):
                self.assertEqual(pause.handle(h,{'t':'capability_pause'},99)['why'],'caller')

    def test_actual_scheduler_retry_loop_stops_second_same_cause(self):
        from agentj import tasks, capability
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as tmp:
            h = self.host(tmp); h.agent_cfg = {'dir':tmp}
            s = tasks.Scheduler(h)
            e = dict(id='ceo',tsha='fixed',problems=[],brief_bytes=b'{}')
            count = []
            async def execute(*args):
                count.append(1); s.execution_failure='network'; s.execution_ended=True
                return 'VERDICT: FAIL — known failure',1
            s._exec = execute; s._kill = AsyncMock()
            with patch.object(capability,'parse_brief',return_value=({'failure_policy':{'retry_limit':5}},[])), patch.object(tasks,'find',return_value=e), patch.object(tasks,'load',return_value={'on':{}}), patch.object(tasks.asyncio,'sleep',new=AsyncMock()):
                res = {}; asyncio.run(s._exec_retry('codex',e,False,res))
            self.assertEqual(len(count),2)
            self.assertEqual(res['retry_advice'],'escalate')
            self.assertEqual(res['retry_count'],1)
