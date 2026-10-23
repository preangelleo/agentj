"""P484 field acceptance, hostile diagnostic data, dependency learning path and docs contract."""
import _hermetic  # noqa: F401
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentj import field_tools as ft, host_diagnostics as hd, market, skill_onboarding as intro, skills_cli
from agentj.state import State


class Field(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.st = State(self.root)

    def collect(self):
        with patch('agentj.host_diagnostics.agent_side', return_value=False), \
             patch('agentj.doctor.run', return_value=[{'id': 'serve', 'status': 'fail', 'summary': 'PRIVATE PERSON', 'hint': 'PRIVATE TOKEN'},
                                                     {'id': 'PRIVATE ID', 'status': 'PRIVATE STATE'}]), \
             patch('agentj.service.status', return_value={'active': 'active', 'path': 'PRIVATE PATH'}), \
             patch('agentj.names.ctl_call', return_value={'ok': True, 'relay_up': False, 'identity_ok': 'PRIVATE KEY', 'agent': 'PRIVATE NAME'}):
            return ft.collect(self.st)

    def test_projection_drops_free_text_and_private_metadata(self):
        self.st.log_path.write_text(json.dumps({'ev': 'serve_start', 'name': 'PRIVATE PERSON', 'key': 'PRIVATE TOKEN'}) + '\n' + json.dumps({'ev': 'PRIVATE EVENT'}))
        result = self.collect()
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(result['doctor'], [{'check': 'serve', 'status': 'fail'}, {'check': 'other', 'status': 'unknown'}])
        self.assertEqual(result['status'], {'ok': True, 'relay_up': False, 'identity_ok': None})
        self.assertEqual(result['service_log_summary']['events'], {'serve_start': 1, 'other': 1})

    def test_log_missing_malformed_and_bounded(self):
        self.assertFalse(ft.log_summary(self.st.log_path)['available'])
        self.st.log_path.write_bytes(b'not json\n' * 30000 + b'{"ev":"error"}\n')
        result = ft.log_summary(self.st.log_path)
        self.assertLessEqual(result['tail_bytes'], 65536)
        self.assertEqual(result['events'], {'error': 1})

    def test_log_symlink_and_fifo_not_read(self):
        target = self.root / 'private'
        target.write_text('PRIVATE')
        self.st.log_path.symlink_to(target)
        self.assertFalse(ft.log_summary(self.st.log_path)['available'])
        self.st.log_path.unlink()
        os.mkfifo(self.st.log_path)
        self.assertFalse(ft.log_summary(self.st.log_path)['available'])

    def test_export_new_private_file_no_overwrite_or_links(self):
        dest = self.root / 'diagnostic.json'
        with patch.object(ft, 'collect', return_value={'format': 1}):
            ft.export(self.st, dest)
            self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                ft.export(self.st, dest)
            link = self.root / 'link'
            link.symlink_to(dest)
            with self.assertRaises(FileExistsError):
                ft.export(self.st, link)
        self.assertEqual(json.loads(dest.read_text()), {'format': 1})

    def test_unavailable_checks_export_unknown_without_exception_text(self):
        with patch('agentj.host_diagnostics.agent_side', return_value=False), \
             patch('agentj.doctor.run', side_effect=RuntimeError('PRIVATE')), \
             patch('agentj.service.status', side_effect=RuntimeError('PRIVATE')), \
             patch('agentj.names.ctl_call', side_effect=RuntimeError('PRIVATE')):
            result = ft.collect(self.st)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(result['service'], 'unavailable')

    def test_fenced_collection_fixed_request_no_paths(self):
        with patch('agentj.host_diagnostics.agent_side', return_value=True), \
             patch('agentj.elevate.client_request', return_value={'exit': 0, 'output': '{"format":1}'}) as req:
            self.assertEqual(ft.collect(self.st), {'format': 1})
        self.assertEqual(req.call_args.args[1], {'t': 'host_diagnostics', 'op': 'diag'})

    def test_fenced_unavailable_fails_closed(self):
        with patch('agentj.host_diagnostics.agent_side', return_value=True), \
             patch('agentj.elevate.client_request', return_value={'result': 'unavailable'}):
            with self.assertRaises(OSError):
                ft.collect(self.st)

    def test_server_rejects_export_path_and_executes_fixed_collect(self):
        self.assertEqual(hd.execute(self.st, {'op': 'diag', 'output': '/private'})['exit'], 1)
        with patch.object(ft, 'collect', return_value={'format': 1}) as collect:
            result = hd.execute(self.st, {'op': 'diag'})
        collect.assert_called_once_with(self.st, local=True)
        self.assertEqual(json.loads(result['output']), {'format': 1})

    def test_preflight_non_wsl_has_no_network(self):
        with patch('agentj.doctor._is_wsl', return_value=False), patch.object(ft, 'download_probe') as net:
            self.assertFalse(ft.preflight()['wsl'])
        net.assert_not_called()

    def test_wsl_missing_libraries_offline_modern_ubuntu_repair(self):
        with patch('agentj.doctor._is_wsl', return_value=True), \
             patch.object(ft.ctypes.util, 'find_library', return_value=None), \
             patch.object(Path, 'read_text', return_value='ID=ubuntu\nVERSION_ID="24.04"'), \
             patch('agentj.browser.manifest', return_value={'builds': {'linux-x64': {'url': 'https://example.org/browser'}}}), \
             patch.object(ft.platform, 'machine', return_value='x86_64'), patch.object(ft, 'download_probe') as net:
            result = ft.preflight(offline=True)
        net.assert_not_called()
        self.assertEqual(result['checks'][0]['status'], 'fail')
        self.assertIn('libasound2t64', result['repair'])
        self.assertIn('libatk1.0-0t64', result['repair'])
        self.assertIn('sudo apt-get update &&', result['repair'])
        self.assertEqual(result['next'], 'agentj browser setup')

    def test_wsl_old_ubuntu_no_t64_and_failed_download(self):
        with patch('agentj.doctor._is_wsl', return_value=True), \
             patch.object(ft.ctypes.util, 'find_library', return_value='found'), \
             patch.object(Path, 'read_text', return_value='ID=ubuntu\nVERSION_ID="22.04"'), \
             patch('agentj.browser.manifest', return_value={'builds': {'linux-arm64': {'url': 'https://example.org/browser'}}}), \
             patch.object(ft.platform, 'machine', return_value='aarch64'), \
             patch.object(ft, 'download_probe', side_effect=['ok', 'unreachable']) as net:
            result = ft.preflight()
        self.assertNotIn('t64', result['repair'])
        self.assertEqual(result['checks'][0]['status'], 'ok')
        self.assertEqual(result['checks'][2]['status'], 'unreachable')
        self.assertIn('arm64', net.call_args.args[0])

    def test_probe_head_timeout_and_sanitized_error(self):
        with patch.object(ft.urllib.request, 'urlopen', side_effect=OSError('PRIVATE')) as req:
            self.assertEqual(ft.download_probe('https://example.org/test'), 'unreachable')
        self.assertEqual(req.call_args.args[0].method, 'HEAD')
        self.assertEqual(req.call_args.kwargs['timeout'], 6)

    def test_levels_dependencies_first_task_and_unknown_old_receipt(self):
        items = [{'name': 'advanced', 'type': 'skill', 'requires': {'env': [{'name': 'EXAMPLE_KEY', 'secret': True}]}},
                 {'name': 'beginner', 'type': 'skill', 'requires': {}},
                 {'name': 'middle', 'type': 'workflow', 'requires': {'cli': [{'name': 'tool'}]}},
                 {'name': 'old', 'type': 'skill'}, {'name': 'card', 'type': 'card'}]
        with patch.object(market, 'read_installed', return_value=items):
            result = intro.guide(self.st)
        self.assertEqual([row['name'] for row in result['packages']], ['beginner', 'middle', 'advanced', 'old'])
        self.assertEqual(result['recommended'], 'beginner')
        self.assertIn('fictional', result['packages'][0]['first_task'])
        self.assertIn('old receipt', result['packages'][-1]['dependencies'])
        self.assertNotIn('target', intro.text(result))

    def test_old_receipt_recovers_matching_verified_cache_without_network(self):
        receipt = {'name': 'example', 'type': 'skill', 'version': '1', 'sha256': '0' * 64}
        manifest = {'name': 'example', 'type': 'skill', 'version': '1', 'requires': {'cli': [{'name': 'tool'}]}}
        with patch.object(market, '_cache_get', return_value=b'cached') as cache, \
             patch('agentj.bundle.parse', return_value=(manifest, {})):
            self.assertEqual(intro.dependencies(self.st, receipt)['cli'][0]['name'], 'tool')
        cache.assert_called_once_with(self.st, '0' * 64)
        manifest['version'] = '2'
        with patch.object(market, '_cache_get', return_value=b'cached'), patch('agentj.bundle.parse', return_value=(manifest, {})):
            self.assertIsNone(intro.dependencies(self.st, receipt))
        with patch.object(market, '_cache_get') as cache:
            self.assertIsNone(intro.dependencies(self.st, {'sha256': '../private'}))
        cache.assert_not_called()

    def test_empty_onboarding(self):
        result = intro.guide(self.st)
        self.assertIsNone(result['recommended'])
        self.assertIn('choose', intro.text(result))

    def test_market_preserves_dependencies_without_env_values(self):
        target = self.root / 'plaza'
        target.mkdir()
        (target / 'installed.json').write_text(json.dumps({'items': [{'name': 'example', 'requires': {'cli': []}, 'secret': 'PRIVATE'}]}))
        row = market.read_installed(self.st)[0]
        self.assertEqual(row['requires'], {'cli': []})
        self.assertNotIn('secret', row)
        summary = market.summarize({'requires': {'env': [{'name': 'EXAMPLE_KEY', 'value': 'PRIVATE'}]}})
        self.assertNotIn('PRIVATE', json.dumps(summary))

    def test_skills_rpc_and_cli_onboarding(self):
        with patch.object(intro, 'guide', return_value={'ok': True, 'packages': [], 'recommended': None, 'note': 'data'}) as guide:
            self.assertTrue(skills_cli.execute(self.st, {'op': 'onboarding'})['ok'])
        guide.assert_called_once_with(self.st)
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest='cmd')
        skills_cli.add_parser(sub)
        args = parser.parse_args(['skills', 'onboarding', '--json'])
        with patch.object(skills_cli, '_ask', return_value={'ok': True}), contextlib.redirect_stdout(io.StringIO()) as out:
            args.fn(args)
        self.assertTrue(json.loads(out.getvalue())['ok'])

    def test_cli_exit_failure(self):
        with patch.object(ft, 'preflight', return_value={'checks': [{'status': 'unreachable'}]}), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                ft.command(SimpleNamespace(cmd='preflight', offline=False))
        self.assertEqual(result.exception.code, 1)

    def test_upgrade_and_delivery_document_contract(self):
        product = Path(__file__).resolve().parents[2]
        private = product.parent / 'documentation/product'
        paths = [product / 'host/agentj/skills/agentj-update/SKILL.md',
                 private / 'install.md' if private.is_dir() else product / 'install.md']
        if private.is_dir():
            paths += [product / 'support/system_prompt.md', product / 'support/mail_prompt.md']
        for path in paths:
            text = path.read_text()
            self.assertIn('0.15.5a1', text)
            self.assertIn('agentj update apply', text)
            self.assertIn('same OS user and instance', text)
            self.assertIn('保留配置与绑定', text)
        # Delivery checklists are private project documents, not host distribution files.
        if private.is_dir():
            for lang in ('zh', 'en'):
                text = (private / f'FIELD_SERVICE.{lang}.md').read_text()
                for command in ('agentj preflight', 'agentj diag export', 'agentj skills onboarding', 'agentj terminal disable'):
                    self.assertIn(command, text)
                self.assertIn('P484', text)
