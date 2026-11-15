"""Synthetic signed phone -> actual Host card -> atomic directory/registry uninstall.

Transport is a fixture; package files, registry, backup and crash recovery are real isolated files.
"""
import _hermetic  # noqa: F401
import asyncio
import json
import os
from pathlib import Path
from unittest import mock

from agentj import application_ingress, approvals, bundle, market, plaza_deps, plaza_uninstall as u
from test_plaza_install_card import CardFixture


class Journey(CardFixture):
    def setUp(self):
        super().setUp()
        # An existing installation is a fixture; uninstall still uses a real signed phone decision.
        data,manifest=self.package()
        _,files=bundle.parse(data)
        rendered=market.install_files(manifest,files,'codex',{},None,'2026-11-14')
        self.target=self.e.home/'installed/demo-flow';self.target.parent.mkdir()
        market.write_tree(self.target,rendered)
        self.row={'name':'demo-flow','version':'1.2.0','type':'workflow','harness':'codex',
                  'target':str(self.target),'sha256':bundle.sha256(data),'installed_at':'2026-11-14T01:00:00Z',
                  'certified':False,'verify':{'paths':sorted(rendered),'files':plaza_deps.tree_sha(rendered)}}
        market._record_install(self.st,self.row)
        self.host.ask_ttl=1
        self.sent.clear()

    def run_uninstall(self, outcome='allow', during=None, **kw):
        async def go():
            async def call():
                writer=mock.Mock();writer.is_closing.return_value=False
                with application_ingress._enter(self.host,writer,{},lambda:True,'terminal'):
                    return await u.handle(self.host,{'name':'demo-flow','harness':'codex',**kw})
            task=asyncio.create_task(call())
            for _ in range(500):
                await asyncio.sleep(.005)
                if self.cards() or task.done(): break
            if self.cards():
                card=self.cards()[-1]
                if os.environ.get('AJ_UNINSTALL_CARD_OUT'):
                    Path(os.environ['AJ_UNINSTALL_CARD_OUT']).write_text(json.dumps(card,ensure_ascii=False))
                if during: during()
                if outcome in ('allow','deny','unsigned','replay'):
                    answer=self.last_answer if outcome=='replay' else self.phone.answer(card,outcome!='deny')
                    if outcome=='allow': self.last_answer=answer
                    if outcome=='unsigned': answer.pop('sig')
                    await self.host._app(self.s,answer)
            return await asyncio.wait_for(task,4)
        return asyncio.run(go())

    def test_signed_allow_moves_original_tree_and_tombstones_once(self):
        before={str(p.relative_to(self.target)):p.read_bytes() for p in self.target.rglob('*') if p.is_file()}
        result=self.run_uninstall()
        self.assertTrue(result['ok'],result)
        self.assertFalse(self.target.exists())
        self.assertEqual(market.read_installed(self.st),[])
        raw=json.loads((self.st.root/'plaza/installed.json').read_text())
        tomb=raw['uninstalled'][0]
        backup=Path(tomb['backup'])
        self.assertEqual(before,{str(p.relative_to(backup)):p.read_bytes() for p in backup.rglob('*') if p.is_file()})
        self.assertEqual(approvals.check_record(self.st,approvals.read_log(self.st)[-1]),'ok')
        cards=len(self.cards())
        self.assertEqual(self.run_uninstall()['why'],'already_uninstalled')
        self.assertEqual(len(self.cards()),cards)

    def test_denied_unsigned_timeout_leave_files_and_registry_intact(self):
        before=(self.st.root/'plaza/installed.json').read_bytes()
        for outcome in ('deny','unsigned','timeout'):
            self.sent.clear()
            self.assertFalse(self.run_uninstall(outcome)['ok'])
            self.assertTrue(self.target.is_dir())
            self.assertEqual((self.st.root/'plaza/installed.json').read_bytes(),before)

    def test_target_change_after_card_refuses_without_moving_anything(self):
        result=self.run_uninstall(during=lambda:(self.target/'AGENTS.md').write_text('changed by user'))
        self.assertFalse(result['ok']);self.assertTrue(self.target.is_dir())
        self.assertEqual(len(market.read_installed(self.st)),1)

    def test_target_symlink_swap_and_revocation_refuse(self):
        saved=self.target.with_name('kept-original')
        def swap():
            self.target.rename(saved);self.target.symlink_to(saved,target_is_directory=True)
        result=self.run_uninstall(during=swap)
        self.assertFalse(result['ok']);self.assertTrue(saved.is_dir());self.assertTrue(self.target.is_symlink())
        self.target.unlink();saved.rename(self.target);self.sent.clear()
        result=self.run_uninstall(during=lambda:self.st.remove_device(self.phone.did))
        self.assertFalse(result['ok']);self.assertTrue(self.target.is_dir())

    def test_registry_failure_rolls_tree_back_and_recovers_consistent_state(self):
        real=u._atomic
        def fail(fd,name,data):
            if name=='installed.json': raise OSError('synthetic atomic failure')
            return real(fd,name,data)
        with mock.patch.object(u,'_atomic',side_effect=fail):
            result=self.run_uninstall()
        self.assertFalse(result['ok']);self.assertTrue(self.target.is_dir())
        self.assertEqual(len(market.read_installed(self.st)),1)
        self.sent.clear()
        self.assertTrue(self.run_uninstall()['ok'])

    def test_crash_between_move_and_registry_restores_before_next_request(self):
        real=u._atomic
        def crash(fd,name,data):
            if name=='installed.json': raise KeyboardInterrupt('synthetic crash')
            return real(fd,name,data)
        # Direct executor uses an already prepared snapshot only to inject a process-level crash.
        snap=u.prepare(self.st,'demo-flow','codex')
        with mock.patch.object(u,'_atomic',side_effect=crash),self.assertRaises(KeyboardInterrupt):
            u.commit(self.st,snap,lambda:True)
        self.assertFalse(self.target.exists())
        self.assertEqual(len(market.read_installed(self.st)),1)
        again=u.prepare(self.st,'demo-flow','codex')
        self.assertEqual(again['record']['name'],'demo-flow')
        self.assertTrue(self.target.is_dir())
        self.assertFalse((self.st.root/'plaza/uninstall-journal.json').exists())

    def test_unknown_target_or_harness_ambiguity_never_opens_card(self):
        self.row['harness']='claude_code';market._record_install(self.st,self.row)
        result=self.run_uninstall(harness=None)
        self.assertEqual(result['why'],'ambiguous_install');self.assertFalse(self.cards())
        self.sent.clear()
        result=self.run_uninstall(name='not-installed')
        self.assertEqual(result['why'],'not_installed');self.assertFalse(self.cards())

    def test_secret_path_is_hidden_in_real_card_and_success_receipt(self):
        value=''.join(['fixture','rosepetals','over','thegarden'])
        target=self.target.parent/value/'demo-flow';target.parent.mkdir();self.target.rename(target)
        self.row['target']=str(target);self.target=target
        path=self.st.root/'plaza/installed.json'
        path.write_text(json.dumps({'items':[self.row]}));path.chmod(0o600)
        with mock.patch.dict(os.environ,{'UNINSTALL_PATH_TOKEN':value}):
            result=self.run_uninstall()
        self.assertTrue(result['ok'],result)
        card=self.cards()[-1]
        self.assertNotIn(value,json.dumps(card,ensure_ascii=False))
        self.assertNotIn(value,json.dumps(result,ensure_ascii=False))

    def test_preview_is_read_only_and_outside_source_is_refused(self):
        before=(self.st.root/'plaza/installed.json').read_bytes()
        self.assertEqual(self.run_uninstall(preview=True)['why'],'preview')
        self.assertFalse(self.cards());self.assertTrue(self.target.is_dir())
        self.assertEqual(before,(self.st.root/'plaza/installed.json').read_bytes())
        # No authenticated application frame: an external caller cannot borrow the owner-labelled Agent.
        self.assertFalse(asyncio.run(u.handle(self.host,{'name':'demo-flow','harness':'codex'}))['ok'])
        self.assertFalse(self.cards());self.assertTrue(self.target.is_dir())

    def test_stale_reverify_cannot_resurrect_uninstalled_record(self):
        self.assertTrue(self.run_uninstall()['ok'])
        with self.assertRaises(market.PlazaError): market._record_install(self.st,self.row)
        self.assertEqual(market.read_installed(self.st),[])

    def test_old_signed_answer_cannot_authorize_a_new_uninstall(self):
        self.assertTrue(self.run_uninstall()['ok'])
        document=json.loads((self.st.root/'plaza/installed.json').read_text())
        backup=Path(document['uninstalled'][0]['backup']);backup.rename(self.target)
        (self.st.root/'plaza/installed.json').write_text(json.dumps({'items':[self.row]}))
        self.sent.clear()
        self.assertFalse(self.run_uninstall('replay')['ok'])
        self.assertTrue(self.target.is_dir());self.assertEqual(len(market.read_installed(self.st)),1)

    def test_cli_routes_to_real_host_handler_and_never_exposes_unsigned_bypass(self):
        import io
        from contextlib import redirect_stdout
        from agentj import cli,plaza_perms
        requests=[]
        def call(st,req,timeout):
            requests.append(req)
            return self.run_uninstall(**{k:v for k,v in req.items() if k in ('preview','harness')})
        output=io.StringIO()
        with mock.patch.object(plaza_perms,'_call',side_effect=call),redirect_stdout(output):
            try: cli.main(['plaza','uninstall','demo-flow','--harness','codex'])
            except SystemExit as e: self.assertEqual(e.code,0)
        self.assertEqual(requests[0]['cmd'],'plaza_uninstall')
        self.assertFalse(self.target.exists());self.assertEqual(len(self.cards()),1)
        self.assertIn('备份',output.getvalue())

    def test_preview_does_not_recover_or_move_an_interrupted_transaction(self):
        real=u._atomic;snapshot=u.prepare(self.st,'demo-flow','codex')
        def crash(fd,name,data):
            if name=='installed.json': raise KeyboardInterrupt('synthetic crash')
            return real(fd,name,data)
        with mock.patch.object(u,'_atomic',side_effect=crash),self.assertRaises(KeyboardInterrupt):
            u.commit(self.st,snapshot,lambda:True)
        result=self.run_uninstall(preview=True)
        self.assertEqual(result['why'],'recovery_required')
        self.assertFalse(self.target.exists());self.assertFalse(self.cards())
        self.assertTrue((self.st.root/'plaza/uninstall-journal.json').exists())

    def test_secret_parent_replaced_or_unresolvable_is_fixed_refusal_before_card(self):
        value=''.join(['fixture','privatepath','garden','boundary'])
        parent=self.target.parent/value/'holder';parent.mkdir(parents=True)
        self.target.rename(parent/self.target.name);self.target=parent/self.target.name
        self.row['target']=str(self.target)
        registry=self.st.root/'plaza/installed.json'
        registry.write_text(json.dumps({'items':[self.row]}));registry.chmod(0o600)
        before=registry.read_bytes();kept=parent.with_name('kept-original-parent');parent.rename(kept)
        for kind in ('file','loop'):
            if kind=='file': parent.write_text('unrelated user file')
            else: parent.symlink_to(parent.name)
            for preview in (True,False):
                with mock.patch.dict(os.environ,{'UNINSTALL_PATH_TOKEN':value}):
                    result=self.run_uninstall(preview=preview)
                self.assertEqual(result['why'],'target_changed',result)
                self.assertFalse(result['ok']);self.assertFalse(self.cards())
                self.assertEqual(before,registry.read_bytes())
                self.assertTrue((kept/'demo-flow/AGENTS.md').is_file())
                self.assertNotIn(value,json.dumps(result))
                self.assertNotIn(value,self.st.log_path.read_text())
            parent.unlink()

    def _rename_boundary_swap(self, *, conflict=False, content_only=False):
        original=os.rename;target=self.target;before=(self.st.root/'plaza/installed.json').read_bytes()
        kept=target.with_name('approved-original');state={'fired':False,'backup':None}
        def swap(src,dst,*args,**kw):
            moving=src==target.name and isinstance(dst,str) and '-uninstall-' in dst and kw.get('src_dir_fd') is not None
            if moving and not state['fired']:
                state['fired']=True;state['backup']=dst
                if not content_only:
                    original(target,kept);target.mkdir();(target/'AGENTS.md').write_text('unapproved replacement')
                result=original(src,dst,*args,**kw)
                if content_only:
                    (self.st.root/'plaza/backups'/dst/'AGENTS.md').write_text('same inode changed contents')
                if conflict:
                    target.mkdir();(target/'conflict.txt').write_text('do not overwrite this new directory')
                return result
            return original(src,dst,*args,**kw)
        with mock.patch.object(u.os,'rename',side_effect=swap):result=self.run_uninstall()
        self.assertTrue(state['fired']);self.assertFalse(result['ok'],result)
        self.assertEqual((self.st.root/'plaza/installed.json').read_bytes(),before)
        self.assertEqual(len(market.read_installed(self.st)),1)
        return result,kept,state

    def test_actual_rename_boundary_swap_never_commits_an_unapproved_directory(self):
        result,kept,state=self._rename_boundary_swap()
        self.assertEqual(result['why'],'target_changed')
        self.assertTrue((kept/'AGENTS.md').is_file())
        self.assertEqual((self.target/'AGENTS.md').read_text(),'unapproved replacement')
        self.assertFalse((self.st.root/'plaza/backups'/state['backup']).exists())
        self.assertFalse((self.st.root/'plaza/uninstall-journal.json').exists())

    def test_postmove_content_change_is_refused_and_restored_without_success(self):
        result,_,state=self._rename_boundary_swap(content_only=True)
        self.assertEqual(result['why'],'target_changed')
        self.assertEqual((self.target/'AGENTS.md').read_text(),'same inode changed contents')
        self.assertFalse((self.st.root/'plaza/backups'/state['backup']).exists())

    def test_swap_rollback_never_overwrites_a_new_conflicting_directory(self):
        result,kept,state=self._rename_boundary_swap(conflict=True)
        self.assertEqual(result['why'],'recovery_required')
        self.assertEqual((self.target/'conflict.txt').read_text(),'do not overwrite this new directory')
        self.assertEqual((self.st.root/'plaza/backups'/state['backup']/'AGENTS.md').read_text(),'unapproved replacement')
        self.assertTrue((kept/'AGENTS.md').is_file())
        self.assertTrue((self.st.root/'plaza/uninstall-journal.json').is_file())
        with self.assertRaisesRegex(u.Refused,'recovery_required'):u.prepare(self.st,'demo-flow','codex')
        self.assertEqual((self.target/'conflict.txt').read_text(),'do not overwrite this new directory')
