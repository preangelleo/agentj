"""#520 independent QA of internal channel and one-shot file import gates."""
import _hermetic  # noqa: F401
import asyncio
from pathlib import Path
import tempfile
from unittest import mock
from agentj import approval_level, heartbeat, imessage, local_files, provenance
from agentj.compose import Send
from test_p551_confirm_levels import Base

class InternalChannels520(Base):
    def source(self, source, foreign=False):
        self.host.agent.cur_send = Send('source', 'source', 1, source=source)
        self.host.agent.untrusted_foreign = foreign
        self.host.agent.untrusted = lambda: foreign

    def test_signed_full_system_channel_changes_do_not_need_owner_reply(self):
        self.harness()
        self.source(provenance.system())
        hb = heartbeat.Heartbeat.__new__(heartbeat.Heartbeat); hb.host = self.host
        im = imessage.IMessage.__new__(imessage.IMessage); im.host = self.host
        self.host.set_pref = mock.AsyncMock(return_value={'ok': True})
        im.set = mock.AsyncMock(return_value={'ok': True})
        self.assertTrue(asyncio.run(hb.handle({'op': 'on'}))['ok'])
        self.host.set_pref.assert_awaited_once_with('heartbeat.enabled', True)
        self.assertTrue(asyncio.run(im.handle({'op': 'set','key':'interval','value':30}))['ok'])
        im.set.assert_awaited_once_with('interval',30)
        self.assertFalse(self.cards())

    def test_unknown_foreign_and_tainted_system_do_not_change_channels(self):
        self.harness()
        hb = heartbeat.Heartbeat.__new__(heartbeat.Heartbeat); hb.host = self.host
        im = imessage.IMessage.__new__(imessage.IMessage); im.host = self.host
        self.host.set_pref = mock.AsyncMock(return_value={'ok':True})
        im.set = mock.AsyncMock(return_value={'ok':True})
        for source, foreign in ((provenance.normalize(None),False),
                (provenance.email_guest('guest@example.com'),False), (provenance.system(),True)):
            with self.subTest(source=source,foreign=foreign):
                self.source(source,foreign)
                self.assertEqual(asyncio.run(hb.handle({'op':'on'}))['why'],'untrusted_turn')
                self.assertEqual(asyncio.run(im.handle({'op':'set','key':'interval','value':30}))['why'],'untrusted_turn')
        self.host.set_pref.assert_not_awaited(); im.set.assert_not_awaited()

    def setup_files(self):
        temp=tempfile.TemporaryDirectory(prefix='p520-import-'); self.addCleanup(temp.cleanup)
        root=Path(temp.name); work=root/'work'; work.mkdir(); downloads=root/'downloads'; downloads.mkdir()
        source=downloads/'plain.txt'; source.write_text('ordinary report')
        self.st.set_agent_config('claude',str(work),record_root=False)
        self.host.agent_cfg['dir']=str(work)
        patch=mock.patch.object(local_files.J,'hidden',return_value=[str(self.st.root)])
        patch.start(); self.addCleanup(patch.stop)
        return source,work,local_files.Files(self.host)

    def test_full_system_import_is_once_without_persistent_grant(self):
        self.harness(); self.source(provenance.system())
        source,work,svc=self.setup_files()
        result=asyncio.run(svc.fetch({'path':str(source)}))
        self.assertTrue(result['ok'],result)
        self.assertEqual(Path(result['path']).read_text(),'ordinary report')
        self.assertEqual(source.read_text(),'ordinary report')
        self.assertEqual(local_files.grants(self.st),[])
        self.assertFalse(self.host.questions)

    def test_unknown_foreign_and_tainted_system_cannot_import(self):
        self.harness(); source,work,svc=self.setup_files()
        for src, foreign in ((provenance.normalize(None),False),
                (provenance.email_guest('guest@example.com'),False),(provenance.system(),True)):
            with self.subTest(src=src,foreign=foreign):
                self.source(src,foreign)
                result=asyncio.run(svc.fetch({'path':str(source)}))
                self.assertEqual(result['why'],'owner_turn_required',result)
        self.assertEqual(list(work.iterdir()),[])

    def test_import_rechecks_source_after_path_validation(self):
        self.harness(); self.source(provenance.system()); source,work,svc=self.setup_files()
        original=local_files.allowed_path
        def changed(*args,**kwargs):
            checked=original(*args,**kwargs)
            self.source(provenance.email_guest('guest@example.com'))
            return checked
        with mock.patch.object(local_files,'allowed_path',side_effect=changed):
            result=asyncio.run(svc.fetch({'path':str(source)}))
        self.assertEqual(result['why'],'source_changed',result)
        self.assertEqual(list(work.iterdir()),[])

    def test_import_credential_content_never_publishes(self):
        self.harness(); self.source(provenance.system()); source,work,svc=self.setup_files()
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
        ephemeral = ed25519.Ed25519PrivateKey.generate()
        source.write_bytes(ephemeral.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()))
        result=asyncio.run(svc.fetch({'path':str(source)}))
        self.assertFalse(result['ok'],result)
        self.assertEqual(result['why'],'key_content')
        self.assertEqual(list(work.iterdir()),[])

    def test_copy_worker_rechecks_source_before_publish(self):
        self.harness(); self.source(provenance.system()); source,work,svc=self.setup_files()
        original=local_files.media._hash_and_scan
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            self.source(provenance.email_guest('guest@example.com'))
            return result
        with mock.patch.object(local_files.media,'_hash_and_scan',side_effect=changed):
            result=asyncio.run(svc.fetch({'path':str(source)}))
        self.assertFalse(result['ok'],result)
        self.assertEqual(list(work.iterdir()),[])

    def test_revoked_full_during_copy_does_not_publish(self):
        self.harness(); self.source(provenance.system()); source,work,svc=self.setup_files()
        original=local_files.media._hash_and_scan
        def revoked(*args,**kwargs):
            result=original(*args,**kwargs)
            approval_level.drop_signer(self.st, approval_level.KEY)
            return result
        with mock.patch.object(local_files.media,'_hash_and_scan',side_effect=revoked):
            result=asyncio.run(svc.fetch({'path':str(source)}))
        self.assertFalse(result['ok'],result)
        self.assertEqual(list(work.iterdir()),[])

    def test_full_switch_during_question_still_pins_later_source(self):
        self.source(provenance.terminal()); source,work,svc=self.setup_files()
        prefs=self.host.preferences
        self.harness(); full=self.host.preferences; self.host.preferences=prefs
        async def autonomous(*args,**kwargs):
            self.host.preferences=full
            return 'autonomous',None
        self.host.question=autonomous
        original=local_files.allowed_path
        calls=0
        def changed(*args,**kwargs):
            nonlocal calls
            checked=original(*args,**kwargs); calls+=1
            if calls==2:self.source(provenance.email_guest('guest@example.com'))
            return checked
        with mock.patch.object(local_files,'allowed_path',side_effect=changed):
            result=asyncio.run(svc.fetch({'path':str(source)}))
        self.assertFalse(result['ok'],result)
        self.assertEqual(list(work.iterdir()),[])
