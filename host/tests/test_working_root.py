"""Work-root migration and identity diagnostics: local fake HOME, no harness or network."""
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agentj import cli, doctor, main_identity, preferences, tasks, wizard, working_root
from agentj.state import State, DEFAULT_RELAY, DEFAULT_WEB


class WorkingRoot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-root-', dir='/var/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = patch.dict(os.environ, {'HOME':str(self.home), 'XDG_CONFIG_HOME':str(self.home/'.config'),
                                     'AGENTJ_STATE_DIR':str(self.home/'state'), 'PATH':'/usr/bin:/bin'})
        env.start(); self.addCleanup(env.stop)
        self.st = State()
        ctl = patch('agentj.names.ctl_call', return_value=None)
        ctl.start(); self.addCleanup(ctl.stop)

    def test_default_detected_confirmed_and_explicit_root(self):
        self.assertEqual(working_root.select(self.st), self.home/'coding')
        self.assertFalse((self.home/'coding').exists(), 'selection must not create a directory')
        candidate = self.home/'Projects'; candidate.mkdir()
        self.assertIn(str(candidate), working_root.candidates(self.st))
        with patch('builtins.input', return_value=str(candidate)), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(working_root.select(self.st, interactive=True), candidate)
        explicit = self.home/'custom-root'
        self.assertEqual(working_root.select(self.st, str(explicit)), explicit)
        for value in ('relative-root', str(self.st.root/'nested')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                working_root.select(self.st, value)
        file = self.home/'file'; file.write_text('untouched')
        with self.assertRaises(ValueError): working_root.select(self.st, str(file))
        self.assertEqual(file.read_text(), 'untouched')

    def test_record_and_state_choose_root_keep_append_language(self):
        self.st.init(); old = self.home/'legacy'; old.mkdir()
        self.st.set_agent_config('codex', str(old))
        preferences.ensure()
        raw = preferences.edit(preferences.read()[0], 'agent.instructions', 'First line\n第二行')
        raw = preferences.edit(raw, 'appearance.language', 'en')
        preferences.transact(self.st, raw)
        root = self.home/'coding'; working_root.record(self.st, root)
        cfg = self.st.agent_config()
        self.assertEqual(cfg['dir'], str(root))
        self.assertEqual(cfg['instructions'], 'First line\n第二行')
        self.assertEqual(cfg['language'], 'en')
        self.assertEqual(self.st.config()['agent']['dir'], str(old))
        self.assertEqual(working_root.select(self.st), root)

    def test_explicit_agent_setter_replaces_stale_preference_root(self):
        self.st.init()
        old = self.home/'previous'; old.mkdir()
        working_root.record(self.st, old)
        old.rmdir()
        chosen = self.home/'chosen'; chosen.mkdir()
        self.st.set_agent_config('codex', str(chosen))
        self.assertEqual(preferences.get(preferences.effective(self.st), 'agent.working_root'), str(chosen))
        self.assertEqual(self.st.agent_config()['dir'], str(chosen))
        self.assertEqual(self.st.agent_config()['working_root'], str(chosen))

    def test_existing_init_records_root_without_key_regen_or_move(self):
        self.st.init(); old = self.home/'old-project'; old.mkdir()
        sentinel = old/'RUN.md'; sentinel.write_text('owner business instructions')
        self.st.set_agent_config('claude', str(old))
        before = {p:p.read_bytes() for p in (self.st.x25519_path, self.st.ed25519_path, self.st.devices_path)}
        root = self.home/'coding'
        args = SimpleNamespace(working_root=str(root), force=False, relay=DEFAULT_RELAY, web=DEFAULT_WEB)
        with patch('agentj.cli._alias_auto'), patch('sys.stdin.isatty', return_value=False), patch('sys.stdout',new_callable=io.StringIO):
            cli.cmd_init(args)
        self.assertEqual({p:p.read_bytes() for p in before}, before)
        self.assertEqual(sentinel.read_text(), 'owner business instructions')
        self.assertEqual(preferences.get(preferences.effective(self.st), 'agent.working_root'), str(root))
        self.assertTrue((root/'AGENTS.md').is_file())
        self.assertFalse((root/'old-project').exists())

    def test_bootstrap_preserves_existing_files_and_is_idempotent(self):
        root = self.home/'coding'; root.mkdir()
        (root/'CLAUDE.md').write_bytes(b'Owner existing root\n')
        (root/'documentation').mkdir()
        (root/'documentation/ROLES.md').write_bytes(b'Owner roster\n')
        wizard.bootstrap_root(root)
        self.assertEqual((root/'CLAUDE.md').read_bytes(), b'Owner existing root\n')
        self.assertEqual((root/'documentation/ROLES.md').read_bytes(), b'Owner roster\n')
        self.assertIn('agentj:main-core', (root/'AGENTS.md').read_text())
        first = {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        wizard.bootstrap_root(root)
        self.assertEqual({str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}, first)

    def test_json5_append_multiline_core_and_unknown_locked_keys(self):
        text = 'Be brief\nUse English'
        parsed = preferences.parse(preferences.edit('{}', 'agent.instructions', text))
        self.assertEqual(preferences.get(parsed, 'agent.instructions'), text)
        prompt = main_identity.prompt({'instructions':text})
        self.assertTrue(prompt.index('chief of staff') < prompt.index(text))
        for raw in ('{agent:{core:"replace"}}', '{agent:{role:"business worker"}}', '{security:{e2e:false}}'):
            with self.subTest(raw=raw), self.assertRaises(preferences.ConfigError): preferences.parse(raw)

    def test_doctor_no_evidence_matching_and_stale_audit(self):
        self.st.init(); root = self.home/'coding'
        working_root.record(self.st, root); wizard.bootstrap_root(root)
        self.st.set_agent_config('claude', str(root))
        def rows(): return {x['id']:x for x in doctor.check_main_identity(self.st)}
        self.assertEqual(rows()['main-core']['status'], 'ok')
        self.assertIn('core v8:', rows()['main-core']['summary'])
        self.assertEqual(rows()['main-inject']['status'], 'warn')
        cfg = self.st.agent_config(); main_identity.audit(cfg, 'claude', self.st, 'fake-session')
        self.assertEqual(rows()['main-inject']['status'], 'ok')
        # A trusted package upgrade must not accept stale v1 launch metadata.
        stale = main_identity.expected(cfg, 'claude'); stale['version'] = 1
        self.st.log('agent_identity', agent='claude', identity_session='old-v1', **stale)
        self.assertEqual(rows()['main-inject']['status'], 'warn')
        main_identity.audit(cfg, 'claude', self.st, 'restarted-v2')
        self.assertEqual(rows()['main-inject']['status'], 'ok')
        raw = preferences.edit(preferences.read()[0], 'agent.instructions', 'new preference')
        preferences.transact(self.st, raw)
        self.assertEqual(rows()['main-inject']['status'], 'warn')

    def test_doctor_existing_unrelated_structure_warns_modern_invalid_fails(self):
        self.st.init(); root = self.home/'coding'; root.mkdir()
        (root/'documentation').mkdir()
        (root/'CLAUDE.md').write_text('Existing owner instructions')
        (root/'AGENTS.md').write_text('Existing owner instructions')
        structure = root/'documentation/STRUCTURE.json'
        structure.write_text('{"owner_custom_schema":1}')
        working_root.record(self.st, root)
        self.st.set_agent_config('claude', str(root))
        before = {p:p.read_bytes() for p in (structure, root/'CLAUDE.md', root/'AGENTS.md')}
        rows = doctor.check_main_identity(self.st)
        self.assertFalse(any(x['status']=='fail' for x in rows), rows)
        self.assertTrue(any(x['id']=='root-structure' and x['status']=='warn' for x in rows))
        self.assertEqual({p:p.read_bytes() for p in before}, before)
        # An explicit Agent J root adopts strict structural validation.
        structure.write_text('{"main_agent":true,"workflows":"invalid roster"}')
        rows = doctor.check_main_identity(self.st)
        self.assertTrue(any(x['id']=='root-structure' and x['status']=='fail' for x in rows), rows)
        # A referenced core also opts in, even if the manifest is malformed.
        (root/'AGENTS.md').write_text('<!-- agentj:main-core v1 -->')
        structure.write_text('not json')
        rows = doctor.check_main_identity(self.st)
        self.assertTrue(any(x['id']=='root-structure' and x['status']=='fail' for x in rows), rows)

    def test_doctor_foreign_structure_warns_even_with_main_core(self):
        self.st.init()
        root = self.home/'foreign-structure'; root.mkdir()
        self.st.set_agent_config('claude', str(root))
        (root/'AGENTS.md').write_text('<!-- agentj:main-core v1 -->')
        (root/'documentation').mkdir()
        (root/'documentation/STRUCTURE.json').write_text('{"my_documents": []}')
        rows = doctor.check_main_identity(self.st)
        row = next(x for x in rows if x['id']=='root-structure')
        self.assertEqual(row['status'], 'warn')
        self.assertIn('沿用你自己的文档结构', row['summary'])

    def test_doctor_tampered_core_fails(self):
        self.st.init()
        data = self.home/'tampered-core'; data.mkdir()
        for p in main_identity.DATA.iterdir(): (data/p.name).write_bytes(p.read_bytes())
        (data/'core.en.md').write_text('workflow CEO')
        with patch.object(main_identity, 'DATA', data):
            rows = doctor.check_main_identity(self.st)
        self.assertTrue(any(x['id']=='main-core' and x['status']=='fail' for x in rows))

    def test_config_ack_requires_restart_for_identity_and_root_changes(self):
        import asyncio
        from unittest.mock import AsyncMock
        from agentj.serve import Host
        self.st.init(); preferences.ensure()
        async def check():
            host = Host(self.st, read_stdin=False)
            host._send_ready = AsyncMock()
            for key, value in [('agent.instructions', 'personal style'), ('agent.working_root', str(self.home/'coding')), ('appearance.language', 'en')]:
                raw = preferences.edit(preferences.read()[0], key, value)
                result = await host.apply_preferences(raw)
                self.assertTrue(result['ok'])
                # A1 (0.15): the language is hot for the next turn (the identity line is re-injected), no serve restart
                hot = key == 'appearance.language'
                self.assertEqual(result['applied'], hot)
                self.assertEqual(result['needs'], [] if hot else ['restart serve'])
                self.assertEqual(preferences.get(preferences.effective(self.st), key), value)
        asyncio.run(check())

    def test_direct_and_legacy_tasks_discovery_duplicates_fail_closed(self):
        root = self.home/'coding'; root.mkdir()
        def task(folder, tid):
            folder.mkdir(parents=True)
            doc = {'v':1,'id':tid,'title':{'zh':'日报','en':'Daily'},'schedule':'0 8 * * *','tz':'local',
                   'prompt_file':'RUN.md','dry_run_prompt_file':'DRYRUN.md','mode':'normal','enabled':False,
                   'needs':[], 'outputs':['reports/report.md']}
            (folder/'task.json').write_text(json.dumps(doc))
            (folder/'RUN.md').write_text('CEO business task')
        task(root/'daily-report', 'daily-report')
        task(root/'workflows/restock-alert', 'restock-alert')
        rows = tasks.discover(str(root))
        self.assertEqual({r['relative_dir'] for r in rows}, {'daily-report','workflows/restock-alert'})
        self.assertTrue(all(not r['problems'] and r['tsha'] for r in rows))
        task(root/'workflows/daily-report', 'daily-report')
        duplicate = [r for r in tasks.discover(str(root)) if r['id']=='daily-report']
        self.assertEqual(len(duplicate), 2)
        self.assertTrue(all(r['problems'] and r['tsha'] is None for r in duplicate))


if __name__ == '__main__': unittest.main()
