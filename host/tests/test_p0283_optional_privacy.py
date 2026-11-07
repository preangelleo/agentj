"""Optional model privacy review never supplies source authority or human consent."""
import _hermetic
import io
import unittest
from unittest import mock
from agentj import privacy, support
import test_support as fixtures

class OptionalModel(unittest.TestCase):
    def test_missing_configuration_skips_all_network_without_claiming_model_ok(self):
        transport=mock.Mock(side_effect=AssertionError('unconfigured model must not be contacted'))
        v=privacy.layer2({'body':'EADDRINUSE'},env={},transport=transport)
        self.assertEqual(v.status,'unavailable')
        self.assertTrue(privacy.optional_review(v))
        self.assertIsNone(v.probability)
        transport.assert_not_called()

    def test_broker_authority_failures_are_never_optional(self):
        for why in ('peer_unverified','source_denied','session_changed','stopped','host_unavailable','bad_request'):
            self.assertFalse(privacy.optional_review(privacy.Verdict('unavailable',reason='privacy-check/'+why)))
        self.assertTrue(privacy.optional_review(privacy.Verdict('unavailable',reason='privacy-check/backend_unavailable')))

    def test_real_model_denial_is_not_optional(self):
        for kind in ('credential','personal_contact',None):
            self.assertFalse(privacy.optional_review(privacy.Verdict('blocked',kind=kind)))

class Delivery(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Support();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.api=fixtures.FakeAPI(self.f.st)

    def send(self,v,**kwargs):
        out=io.StringIO()
        with mock.patch.object(privacy,'layer2',return_value=v):
            rc=support.run_send('bug','EADDRINUSE after restart',ch=self.f.chan(self.api),env={},wait=0,
                identity=(fixtures.HOST,fixtures.USER),out=out,**kwargs)
        return rc,out.getvalue()

    def test_configured_model_outage_uses_local_rules_without_human_assertions(self):
        rc,out=self.send(privacy.Verdict('unavailable',reason='HTTP 503'))
        self.assertEqual(rc,support.EXIT_PENDING)
        self.assertIn(privacy.LOCAL_REVIEW_NOTE,self.api.sent[0]['body'])
        self.assertIn('EADDRINUSE',self.api.sent[0]['body'])
        self.assertNotIn('owner confirmed',out)

    def test_full_mode_does_not_override_model_deny(self):
        with mock.patch.object(support,'_full_access',return_value=True):
            rc,_=self.send(privacy.Verdict('blocked',kind='personal_contact'))
        self.assertEqual(rc,support.EXIT_BLOCKED)
        self.assertEqual(self.api.sent,[])

    def test_source_failure_never_uses_model_fallback_even_with_confirmation(self):
        rc,_=self.send(privacy.Verdict('unavailable',reason='privacy-check/source_denied'),owner_confirmed=True)
        self.assertEqual(rc,support.EXIT_UNAVAILABLE)
        self.assertEqual(self.api.sent,[])


class BrokerRace(unittest.IsolatedAsyncioTestCase):
    async def test_post_review_source_exception_never_becomes_optional_backend_outage(self):
        from types import SimpleNamespace
        from agentj import privacy_host, approval_material
        h=SimpleNamespace(st=SimpleNamespace(log=mock.Mock()),agent=None,stopped=lambda:False,agent_notice=mock.Mock())
        origin={'verified':True,'suspicious':False}
        req={'t':'privacy_check','gate':'feedback','draft':{'body':'EADDRINUSE'}}
        with mock.patch.object(approval_material,'verified_caller_origin',side_effect=[origin,RuntimeError('fixture-source-race')]), \
             mock.patch.object(privacy,'layer2',return_value=privacy.Verdict('ok')):
            result=await privacy_host.handle(h,req,peer_pid=123)
        self.assertEqual(result['reason'],'privacy-check/session_changed')
        self.assertFalse(privacy.optional_review(privacy.Verdict(**result)))

    async def test_real_model_outage_with_stable_source_remains_optional(self):
        from types import SimpleNamespace
        from agentj import privacy_host, approval_material
        h=SimpleNamespace(st=SimpleNamespace(log=mock.Mock()),agent=None,stopped=lambda:False,agent_notice=mock.Mock())
        origin={'verified':True,'suspicious':False}
        req={'t':'privacy_check','gate':'feedback','draft':{'body':'EADDRINUSE'}}
        with mock.patch.object(approval_material,'verified_caller_origin',return_value=origin), \
             mock.patch.object(privacy,'layer2',side_effect=OSError('fixture-model-outage')):
            result=await privacy_host.handle(h,req,peer_pid=123)
        self.assertTrue(privacy.optional_review(privacy.Verdict(**result)))
        h.agent_notice.assert_not_called()


class PackageLocalGate(unittest.TestCase):
    def test_known_host_secret_blocks_immutable_package_even_without_model(self):
        from agentj import market, peer_guard
        fixture='owner-chosen-fixture-value'
        with mock.patch.object(peer_guard.SecretIndex,'build',return_value=peer_guard.SecretIndex([fixture])):
            hits=market.layer1({}, {'README.md':('ordinary text '+fixture).encode()},('host','user'))
        self.assertIn(('README.md','secret_fragment',1),hits)
        self.assertNotIn(fixture,str(hits))

class StrictLocalIndex(unittest.TestCase):
    def test_absent_optional_sources_allow_empty_complete_index(self):
        import tempfile
        from agentj import peer_guard
        with tempfile.TemporaryDirectory() as home:
            idx=peer_guard.SecretIndex.build(environ={},home=home,service_env=False)
            self.assertIs(idx.require_complete(),idx)
            self.assertEqual(len(idx),0)

    def test_present_unreadable_source_is_incomplete_and_no_path_in_error(self):
        import tempfile,builtins
        from pathlib import Path
        from agentj import peer_guard
        with tempfile.TemporaryDirectory() as home:
            source=Path(home)/'.env';source.write_text('CUSTOM_SECRET=fixture-owner-chosen-value')
            opened=builtins.open
            def checked(path,*args,**kwargs):
                if str(path)==str(source):raise PermissionError('fixture unreadable')
                return opened(path,*args,**kwargs)
            with mock.patch('builtins.open',side_effect=checked):
                idx=peer_guard.SecretIndex.build(environ={},home=home,service_env=False)
            with self.assertRaises(OSError) as err:idx.require_complete()
            self.assertNotIn(home,str(err.exception))
            self.assertNotIn('fixture-owner-chosen-value',str(err.exception))

    def test_previous_success_cannot_hide_new_read_failure(self):
        import tempfile,builtins
        from pathlib import Path
        from agentj import peer_guard
        with tempfile.TemporaryDirectory() as home:
            source=Path(home)/'.env';source.write_text('CUSTOM_SECRET=fixture-owner-chosen-value')
            idx=peer_guard.SecretIndex.build(environ={},home=home,service_env=False)
            idx.require_complete()
            opened=builtins.open
            def checked(path,*args,**kwargs):
                if str(path)==str(source):raise PermissionError('fixture unreadable')
                return opened(path,*args,**kwargs)
            with mock.patch('builtins.open',side_effect=checked), self.assertRaises(OSError):idx.require_complete()

    def test_new_or_changed_source_invalidates_old_empty_index(self):
        import tempfile
        from pathlib import Path
        from agentj import peer_guard
        with tempfile.TemporaryDirectory() as home:
            idx=peer_guard.SecretIndex.build(environ={},home=home,service_env=False)
            (Path(home)/'.env').write_text('CUSTOM_SECRET=fixture-owner-chosen-value')
            with self.assertRaises(OSError):idx.require_complete()

    def test_overlimit_source_is_not_a_successful_empty_scan(self):
        import tempfile
        from pathlib import Path
        from agentj import peer_guard
        with tempfile.TemporaryDirectory() as home:
            with (Path(home)/'.env').open('wb') as file:file.truncate(peer_guard.MAX_FILE+1)
            idx=peer_guard.SecretIndex.build(environ={},home=home,service_env=False)
            with self.assertRaises(OSError):idx.require_complete()


    def test_every_original_alias_is_revalidated_even_with_same_resolved_source(self):
        import tempfile
        from pathlib import Path
        from agentj import peer_guard
        with tempfile.TemporaryDirectory() as home:
            root=Path(home);a=root/'first-source';b=root/'second-source';alias=root/'alias-source'
            a.write_text('CUSTOM_SECRET=fixture-first-secret-value')
            b.write_text('CUSTOM_SECRET=fixture-second-secret-value')
            (root/'.env').symlink_to(a);alias.symlink_to(a)
            idx=peer_guard.SecretIndex.build(environ={},home=home,files=[str(alias)],service_env=False)
            idx.require_complete()
            alias.unlink();alias.symlink_to(b)
            with self.assertRaises(OSError):idx.require_complete()

    def test_environment_capacity_overflow_marks_index_incomplete(self):
        from agentj import peer_guard
        with mock.patch.object(peer_guard,'MAX_VALUES',1):
            idx=peer_guard.SecretIndex(['fixture-first-secret-value','fixture-second-secret-value'])
        with self.assertRaises(OSError):idx.require_complete()
