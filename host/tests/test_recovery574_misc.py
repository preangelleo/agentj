import _hermetic
"""Offline component integration: real temporary files, no transports or shell commands."""
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agentj import avatar_log, docsrule, heartbeat, main_identity, peer_guard, peer_service, privacy, term_mask, wizard, workflows
from agentj.state import State

class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_99_runtime_identity_rejects_home_and_symlink_without_mutation(self):
        (self.root/'keep.txt').write_bytes(b'personal data\r\n')
        alias = self.root/'home-alias'; alias.symlink_to(self.root, target_is_directory=True)
        before = (self.root/'keep.txt').read_bytes()
        with patch('pathlib.Path.home', return_value=self.root), patch.dict(os.environ, {'HOME': str(self.root)}):
            for value in (str(self.root), str(alias), '~'):
                with self.subTest(value=value), self.assertRaisesRegex(main_identity.IdentityError, 'reselect'):
                    main_identity.validate_working_root({'dir':value}, None)
        self.assertEqual((self.root/'keep.txt').read_bytes(), before)
        self.assertTrue(alias.is_symlink())

    def test_99_selection_full_dependency_path(self):
        from agentj import working_root
        with patch('pathlib.Path.home', return_value=self.root):
            with self.assertRaisesRegex(ValueError, 'Home root'):
                working_root.select(SimpleNamespace(exists=lambda:False), str(self.root))

    def test_322_three_upgrades_replace_in_place_keep_all_user_bytes(self):
        path=self.root/'CLAUDE.md'
        blocks=[f'<!-- agentj:main-core v{v} -->\nold {v}\n<!-- /agentj:main-core -->'.encode() for v in (19,20)]
        outside=[b'Owner\r\n\t  \n',b'\r\nMiddle personal text  \n',b'\n\nTail  \r\n\n']
        path.write_bytes(outside[0]+blocks[0]+outside[1]+blocks[1]+outside[2]+docsrule.block('en').encode())
        manifest={'files':{}}
        for version in (21,22,27):
            reference=f'<!-- agentj:main-core v{version} -->\nnew {version}\n<!-- /agentj:main-core -->\n'.encode()
            wizard._ensure_docs_rule(self.root,manifest,['CLAUDE.md'],reference+b'\n'+docsrule.block('en').encode())
            actual=path.read_bytes()
            self.assertEqual(actual.count(b'<!-- agentj:main-core v'),1)
            pat=rb'<!-- agentj:main-core v[0-9]+ -->.*?<!-- /agentj:main-core -->'
            self.assertEqual(re.sub(pat,b'',actual,flags=re.S),b''.join(outside)+docsrule.block('en').encode())
        once=path.read_bytes()
        wizard._ensure_docs_rule(self.root,manifest,['CLAUDE.md'],reference+b'\n'+docsrule.block('en').encode())
        self.assertEqual(path.read_bytes(),once)

    def test_322_entry_symlink_not_followed(self):
        target=self.root/'personal.txt'; target.write_bytes(b'KEEP')
        (self.root/'CLAUDE.md').symlink_to(target)
        result=wizard._ensure_docs_rule(self.root,{'files':{}},['CLAUDE.md'],wizard.core_reference().encode()+docsrule.block().encode())
        self.assertEqual(result[0]['status'],'skipped');self.assertEqual(target.read_bytes(),b'KEEP')

    def test_339_real_file_v1_migration_bilingual_idempotent(self):
        for lang in ('zh','en'):
            path=self.root/(lang+'.md')
            custom='Owner personal text\r\n  \n'
            old='<!-- agentj:watchlist v1 -->\nold avatar instruction\n<!-- /agentj:watchlist -->'
            path.write_bytes((custom+old+'\nTail  \r\n').encode())
            self.assertTrue(heartbeat.ensure_prompt(lang,path))
            actual=path.read_bytes().decode()
            self.assertTrue(actual.startswith(custom))
            self.assertTrue(actual.endswith('\nTail  \r\n'))
            self.assertTrue(heartbeat.has_checklist(actual,lang))
            self.assertEqual(actual.count('<!-- agentj:watchlist v2 -->'),1)
            self.assertFalse(heartbeat.ensure_prompt(lang,path))
            self.assertEqual(path.read_bytes().decode(),actual)

    def test_339_custom_and_symlink_untouched(self):
        path=self.root/'custom.md';path.write_bytes(b'User note\r\n')
        self.assertFalse(heartbeat.ensure_prompt('en',path));self.assertEqual(path.read_bytes(),b'User note\r\n')
        link=self.root/'link.md';link.symlink_to(path)
        self.assertFalse(heartbeat.ensure_prompt('en',link));self.assertEqual(path.read_bytes(),b'User note\r\n')

    def test_339_legacy_records_remain_read_only_unknown(self):
        store=SimpleNamespace(data={'records':{'a':{'at':100,'id':'a','text':'Legacy request'}}})
        before=json.dumps(store.data,sort_keys=True)
        row=avatar_log.rows([store],0)[0]
        self.assertIsNone(row['reply']);self.assertEqual(row['delivery'],'unknown');self.assertEqual(row['outcome'],'unknown')
        self.assertEqual(json.dumps(store.data,sort_keys=True),before)

    def workflow(self,name='company'):
        d=self.root/name; (d/'documentation').mkdir(parents=True)
        (d/'documentation'/'STRUCTURE.json').write_text('{"bible_version":"1"}')
        (d/'documentation'/'IDENTITY.md').write_text('# Misleading CEO title\n\n我是 **知衡**。\n')
        return d

    def test_376_actual_scan_one_card_real_folder_and_roster_priority(self):
        self.workflow('z-company');(self.root/'A-old-name').symlink_to('z-company',target_is_directory=True)
        (self.root/'documentation').mkdir()
        (self.root/'documentation'/'ROLES.md').write_text('| CEO | folder |\n| 花名优先 | z-company/CLAUDE.md |\n')
        with patch.object(workflows,'global_dirs',return_value=[]):
            scanner=workflows.Scanner(); pic=scanner.scan(self.root)
            cards=[w for w in pic['items'] if w.get('wdb')]
            self.assertEqual(len(cards),1);self.assertEqual(cards[0]['folder'],'z-company');self.assertEqual(cards[0]['ceo'],'花名优先')
            self.assertEqual(set(scanner.folders),{'z-company','documentation'})

    def test_376_identity_name_without_roster(self):
        d=self.workflow()
        self.assertEqual(workflows.scan_folder(str(self.root),'company',[])['ceo'],'知衡')

    def test_376_outside_symlink_never_listed_or_opened(self):
        with tempfile.TemporaryDirectory() as outside:
            private=Path(outside);(private/'IDENTITY.md').write_text('DO NOT READ')
            (self.root/'escaped').symlink_to(private,target_is_directory=True)
            d=self.workflow(); (d/'documentation'/'IDENTITY.md').unlink(); (d/'documentation'/'IDENTITY.md').symlink_to(private/'IDENTITY.md')
            (self.root/'documentation').symlink_to(private,target_is_directory=True)
            real_open=open;real_scandir=os.scandir; real_listdir=os.listdir
            def guarded_open(path,*args,**kwargs):
                if isinstance(path,(str,bytes,os.PathLike)) and Path(path).resolve().is_relative_to(private):
                    raise AssertionError('private file read')
                return real_open(path,*args,**kwargs)
            def guarded_scan(path,*args,**kwargs):
                if Path(path).resolve().is_relative_to(private):raise AssertionError('private directory scanned')
                return real_scandir(path,*args,**kwargs)
            def guarded_list(path,*args,**kwargs):
                if Path(path).resolve().is_relative_to(private):raise AssertionError('private directory listed')
                return real_listdir(path,*args,**kwargs)
            with patch('builtins.open',guarded_open),patch('os.scandir',guarded_scan),patch('os.listdir',guarded_list),patch.object(workflows,'global_dirs',return_value=[]):
                pic=workflows.Scanner().scan(self.root)
            self.assertNotIn('escaped',[w['folder'] for w in pic['items']])

    def test_399_new_and_legacy_redaction_peer_and_stream(self):
        for prefix in ('AJTST','AJI','AJ','AJV'):
            code=prefix+'-ABCD-EFGH'
            with self.subTest(prefix=prefix):
                self.assertNotIn(code,privacy.redact(code))
                self.assertEqual(peer_guard.credential_shape(code),'agentj_access_code')
                masker=term_mask.Masker(); rendered=masker.feed(code[:8])+masker.feed(code[8:]+'\n')+masker.flush()
                self.assertNotIn(code,rendered)

    def test_399_full_public_peer_id_is_not_an_access_code_prefix(self):
        from agentj import peer
        public_id=peer.agent_id(bytes(range(32)),bytes(reversed(range(32))))
        self.assertIsNone(peer_guard.credential_shape(public_id))
        self.assertTrue(peer_guard.inbound('From same-owner device '+public_id).ok)
        self.assertEqual(peer_guard.credential_shape('AJ-ABCD-EFGH'),'agentj_access_code')

class Daily(unittest.TestCase):
    def test_334_actual_service_rollover_and_unknown_legacy(self):
        with tempfile.TemporaryDirectory() as d:
            st=State(Path(d)/'state');st.init(relay='ws://127.0.0.1:9')
            notices=[];now=[1700000000.0]
            host=SimpleNamespace(st=st,lang='zh',agent_notice=notices.append,push_notify=lambda *_:None)
            svc=peer_service.PeerService(host,clock=lambda:now[0])
            for key in ('n_receipt','n_receipt','n_in','n_auto','n_task'):
                svc._count(key)
            row=svc.store._read('service.json',{})['sum']
            self.assertEqual((row['n_in'],row['n_receipt'],row['n_task']),(1,2,1))
            now[0]+=86400;svc._count(None)
            self.assertIn('任务回执 2 条（不用回复）',notices[-1]);self.assertIn('已代回 1 条',notices[-1]);self.assertIn('需回复任务 1 条',notices[-1])
            with svc._svc() as data:data['sum']={'day':'old','n_in':2,'n_auto':0,'n_ask':0}
            svc._count(None)
            self.assertIn('未分类',notices[-1]);self.assertIn('未知',notices[-1]);self.assertNotIn('不用回复',notices[-1])

    def test_334_receipts_only_do_not_suggest_zero_replies(self):
        for lang in ('zh','en'):
            text=peer_service.friend_daily_summary({'day':'today','v':2,'n_receipt':2},lang)
            self.assertNotIn('自动回复 0',text);self.assertNotIn('0 answered',text)
            self.assertIn('不用回复' if lang=='zh' else 'no reply needed',text)

if __name__=='__main__':unittest.main()
