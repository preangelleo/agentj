"""Offline aggregation contract; all scheduler IO is injected."""
import _hermetic  # noqa: F401
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from agentj import jcards, jcards_data, systasks, tasks


def native(n, src='systemd'):
    return [dict(id=f'job-{i}.timer', src=src, name='Job', when=[], scope='user') for i in range(n)]


def task(**extra):
    return dict(id='daily', title={'en': 'Daily'}, enabled=False, tsha='a'*64,
                problems=[], stale=False, uncertain=False, **extra)


class Aggregation(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='aggregation369-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cfg = {'dir': str(self.root), 'kind': 'codex'}
        # No production scanner, manager, or native recovery journal is consulted.
        self.start_patch('agentj.jcards.find', return_value={'on': True, 'doc': {}})
        self.scan = self.start_patch('agentj.systasks.scan', return_value=native(401))
        self.start_patch('agentj.native_scheduler_control.Controller.restore_rows', side_effect=lambda r: r)
        self.start_patch('agentj.native_scheduler_control.Controller.describe',
                         return_value={'supported': False, 'reason': 'read_only'})
        self.start_patch('agentj.sysmigrate.migrated', return_value={})
        self.start_patch('agentj.tasks.rows', return_value=[task()])
        self.start_patch('agentj.jcards_data._run', side_effect=AssertionError('real IO'))

    def start_patch(self, *a, **kw):
        p = patch(*a, **kw)
        self.addCleanup(p.stop)
        return p.start()

    def page(self, page=0):
        return jcards.phone_get(self.root, 'automation', 'en', self.cfg, automation_page=page)

    def test_all_rows_once_across_pages(self):
        pages = [self.page(i) for i in range(3)]
        self.assertTrue(all(p['ok'] for p in pages))
        rows = [r for p in pages for r in p['items']]
        self.assertEqual(len(rows), 402)
        self.assertEqual(len({r['id'] for r in rows}), 402)
        self.assertEqual(pages[0]['pagination'], dict(page=0, total=402, page_size=200, pages=3, scan_limited=True))
        self.assertEqual(pages[2]['items'], self.page(2)['items'])
        target = rows[-1]['control']['target']
        self.assertEqual(target, dict(kind='agentj-task', id='daily', tsha='a'*64, enabled=False))
        self.assertEqual(rows[-1]['source'], 'agentj')
        self.assertTrue(all(r['control'].get('target', {}).get('kind') != 'agentj-task' for r in rows[:-1]))

    def test_page_bounds(self):
        for value in (True, False, -1, 3, 10**100, '1', 1.0, None, {}, []):
            with self.subTest(value=value):
                result = self.page(value)
                self.assertFalse(result['ok'])
                self.assertEqual(result['why'], 'bad_automation_page')
        self.assertGreater(len(jcards.phone_get(self.root, 'automation', 'en', self.cfg)['items']), 0)
        self.assertLessEqual(len(jcards.phone_get(self.root, 'automation', 'en', self.cfg)['items']), 200)
        self.scan.return_value = []
        with patch('agentj.tasks.rows', return_value=[]):
            self.assertEqual(self.page()['pagination']['pages'], 1)
            self.assertFalse(self.page(1)['ok'])

    def test_bad_contracts_and_migration(self):
        for changes in ({'problems':['invalid']}, {'stale':True}, {'uncertain':True},
                        {'tsha':None}, {'tsha':'A'*64}, {'id':'../escape'}, {'enabled':1}):
            row = task(); row.update(changes)
            with self.subTest(changes=changes), patch('agentj.tasks.rows', return_value=[row]):
                control = self.page(2)['items'][-1]['control']
                self.assertFalse(control['supported'])
                self.assertNotIn('target', control)
        with patch('agentj.sysmigrate.migrated', return_value={'daily':'native.timer'}):
            control = self.page(2)['items'][-1]['control']
            self.assertEqual(control, {'supported':False, 'reason':'migrated'})

    def test_unresolved_migration_is_unsupported(self):
        with patch('agentj.sysmigrate.migrated', side_effect=OSError('fixture refusal')):
            self.assertEqual(self.page(2)['items'][-1]['control'],
                             {'supported':False, 'reason':'unresolved'})

    def test_duplicate_invalid_tasks_still_have_distinct_row_ids(self):
        row = task(); row['problems'] = ['duplicate workflow id']
        with patch('agentj.tasks.rows', return_value=[row, row]):
            rows = self.page(2)['items'][-2:]
        self.assertEqual(len({r['id'] for r in rows}), 2)
        self.assertTrue(all(not r['control']['supported'] for r in rows))

    def test_agentj_projection_is_not_a_native_target(self):
        from agentj.native_scheduler_control import validate, Refused
        target = self.page(2)['items'][-1]['control']['target']
        with self.assertRaises(Refused):
            validate(target)

    def test_real_fixture_contract_hash(self):
        from agentj.state import State
        folder = self.root / 'workflows' / 'daily'
        folder.mkdir(parents=True)
        spec = dict(v=1, id='daily', title={'zh':'每日', 'en':'Daily'}, schedule='0 9 * * *',
                    tz='UTC', prompt_file='PROMPT.md', dry_run_prompt_file='DRY.md', mode='normal',
                    enabled=False, needs=[], outputs=['reports/result.md'])
        raw = json.dumps(spec).encode(); prompt = b'Offline fixture only.'
        (folder/'task.json').write_bytes(raw)
        (folder/'PROMPT.md').write_bytes(prompt)
        (folder/'DRY.md').write_text('Offline dry run.')
        # Original implementation, with only its journal IO injected.
        with patch('agentj.tasks.load', return_value={'on':{}, 'last':{}, 'runs':{}}):
            rows = self.real_rows(State(self.root), str(self.root))
        self.assertEqual(rows[0]['problems'], [])
        with patch('agentj.tasks.rows', return_value=rows):
            target = self.page(2)['items'][-1]['control']['target']
        self.assertEqual(target['tsha'], tasks.contract_sha(raw, prompt))

    real_rows = staticmethod(tasks.rows)

    def test_recovery_merge_does_not_reapply_legacy_cap(self):
        from agentj.native_scheduler_control import Controller
        with patch.object(Controller, 'restore_rows', self.real_restore), \
             patch.object(Controller, 'journal', return_value={}):
            self.assertEqual(self.page(2)['pagination']['total'], 402)

    real_restore = staticmethod(__import__('agentj.native_scheduler_control', fromlist=['Controller']).Controller.restore_rows)

    def test_native_cannot_project_agentj_control(self):
        spoof = dict(id='daily', src='agentj', name='Spoof', when=[], scope='user',
                     control={'supported':True, 'target':dict(kind='agentj-task', id='daily', tsha='a'*64, enabled=False)})
        self.scan.return_value = [spoof]
        self.assertFalse(self.page()['items'][0]['control']['supported'])
        spoof['src'] = 'systemd'
        with patch('agentj.native_scheduler_control.Controller.describe', return_value=spoof['control']):
            self.assertFalse(self.page()['items'][0]['control']['supported'])

    def test_host_readonly_forwarding(self):
        try:
            from agentj.serve import Host
        except ModuleNotFoundError as exc:
            self.skipTest(f'complete host dependencies unavailable: {exc.name}')
        host = Host.__new__(Host)
        host.st = SimpleNamespace(root=self.root, log=Mock())
        host.lang = 'en'; host.agent_cfg = self.cfg; host.send_app = AsyncMock()
        sender = SimpleNamespace(device='fixture')
        with patch('agentj.jcards.phone_get', return_value={'ok':True}) as get:
            asyncio.run(host.on_jcard(sender, 'jcard_get', {'r':'page369', 'id':'automation', 'automation_page':2}))
        self.assertEqual(get.call_args.kwargs, {'automation_page':2})
        self.assertIs(host.send_app.call_args.args[0], sender)


    def test_each_page_fits_the_real_p33_wire_budget(self):
        from agentj import wire
        first = self.page()
        rows = []
        for index in range(first['pagination']['pages']):
            data = self.page(index)
            message = {'t': 'jcard_get', **data, 'r': 'fixture-page'}
            wire.pad_json(message, wire.MAX_JSON_P33)
            self.assertLessEqual(len(data['items']), jcards.ITEMS_MAX)
            rows.extend(data['items'])
        self.assertEqual(len(rows), 402)
        self.assertEqual(len({r['id'] for r in rows}), 402)

    def test_utf8_metadata_and_supported_targets_stay_in_budget(self):
        from agentj import wire
        rows = native(160)
        for row in rows:
            row['name'] = '每日自动化任务🧪' * 40
            row['when'] = [{'k': 'OnCalendar', 'v': '每天运行🧪' * 20}]
        self.scan.return_value = rows
        def control(row):
            return {'supported': True, 'target': {'platform': 'linux', 'source': 'systemd',
                    'scope': 'user', 'id': row['id'], 'snapshot': 'b'*64, 'op': 'disable'}}
        with patch('agentj.native_scheduler_control.Controller.describe', side_effect=control):
            first = self.page(); got = []
            self.assertGreater(first['pagination']['pages'], 1)
            for index in range(first['pagination']['pages']):
                data = self.page(index)
                wire.pad_json({'t': 'jcard_get', **data, 'r': 'fixture-page'}, wire.MAX_JSON_P33)
                got.extend(data['items'])
        self.assertEqual(len(got), 161)
        self.assertEqual(len({r['id'] for r in got}), 161)

    def test_one_oversized_row_fails_explicitly_instead_of_disappearing(self):
        from agentj import wire
        with patch('agentj.jcards_data.automations', return_value=[{'id':'oversized', 'description':'🧪'*wire.MAX_JSON_P33}]):
            data = self.page()
        self.assertFalse(data['ok'])
        self.assertEqual(data['why'], 'automation_row_too_large')


class NativeAggregate(unittest.TestCase):
    def test_families_have_full_bounded_chance(self):
        with patch('agentj.systasks.scan_systemd', side_effect=[native(200), native(200)]), \
             patch('agentj.systasks.scan_cron', return_value=native(200, 'cron')), \
             patch('agentj.systasks.scan_system_cron', return_value=native(200, 'anacron')):
            result = systasks.scan_snapshot(platform='linux', run=lambda _: self.fail('real IO'))
        self.assertEqual(len(result['items']), 800)
        self.assertTrue(result['scan_limited'])
        self.assertEqual({r['src'] for r in result['items']}, {'systemd','cron','anacron'})

    def test_legacy_scan_still_bounded(self):
        with patch('agentj.systasks.scan_systemd', return_value=native(200)), \
             patch('agentj.systasks.scan_cron', return_value=[]), \
             patch('agentj.systasks.scan_system_cron', return_value=[]):
            self.assertEqual(len(systasks.scan(platform='linux', run=lambda _: '')), 200)

class SystemCronFamilies(unittest.TestCase):
    def test_run_parts_cap_does_not_hide_anacron_or_system_crontabs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for family in ('cron.d','cron.hourly','cron.daily','cron.weekly','cron.monthly'):
                (root/family).mkdir()
            for family in ('cron.hourly','cron.daily','cron.weekly','cron.monthly'):
                for number in range(3):(root/family/('job%d'%number)).write_text('fixture')
            (root/'crontab').write_text('0 1 * * * root /fixture\n')
            (root/'anacrontab').write_text('1 5 daily /fixture\n')
            (root/'cron.d'/'fixture').write_text('0 2 * * * root /fixture\n')
            with patch.object(systasks,'MAX',2), patch.object(systasks,'scan_systemd',return_value=[]), \
                 patch.object(systasks,'scan_cron',return_value=[]):
                rows=systasks.scan('linux',run=Mock(side_effect=AssertionError('no OS IO')),
                                   system_directory=directory,aggregate=True)
                ids={row['id'] for row in rows}
                self.assertIn('anacrontab:1',ids)
                self.assertIn('crontab:1',ids)
                self.assertIn('cron.d/fixture:1',ids)
                for family in ('cron.hourly','cron.daily','cron.weekly','cron.monthly'):
                    self.assertEqual(sum(ident.startswith(family+'/') for ident in ids),2)
