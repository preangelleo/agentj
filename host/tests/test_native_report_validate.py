"""The additional reviewed R15 build is available only to an admitted native run."""
import _hermetic
import base64
import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agentj import report, report_host, report_local
from _testtmp import TEST_TMP


class NativeReportValidate(unittest.TestCase):
    def test_only_the_exact_builtin_validation_command(self):
        good = ['validate', '/input/input-data/test.json', '--template', 'R15']
        for args in (good, good + ['--json']):
            self.assertTrue(report_local.workflow_r15(args))
        for args in (good[:-1], good[:-1] + ['/tmp/R15'], good + ['--out', '/tmp/file'],
                     ['render', *good[1:]], good + ['--json', '--json']):
            self.assertFalse(report_local.workflow_r15(args))

    def test_additional_build_goes_to_host_even_with_working_local_helpers(self):
        original = ['validate', 'test.json', '--template', 'R15', '--json']
        with patch.object(report_local, 'digest', return_value=report_local.WORKFLOW_R15_SHA256), \
                patch.object(report_host, 'request', return_value='socket') as request:
            self.assertEqual(report_local.run(Path('/tmp'), original, original, Path('/tmp')), 'socket')
            self.assertTrue(request.call_args.kwargs['workflow'])
            self.assertIsNone(report_local.run(Path('/tmp'), ['render'], ['render'], Path('/tmp')))

    def test_source_requires_same_live_host_run_and_positive_trigger(self):
        scheduler = SimpleNamespace(mail_scope={'live': True, 'mail_admission': {'run_id': 'run'}},
                                    completion_caller_known=lambda peer: peer == os.getpid())
        host = SimpleNamespace(scheduler=scheduler, st=object())
        run = {'trigger': 'manual'}
        with patch('agentj.tasks.load', return_value={'runs': {'run': run}}), \
                patch('agentj.workflow_mail.source', return_value={'workflow_id':'fixture'}) as source:
            verify = report_host.workflow_caller(host, os.getpid(), os.getuid())
            self.assertTrue(verify())
            for trigger in ('dispatch', 'unknown', None):
                run['trigger'] = trigger
                self.assertFalse(verify())
            run['trigger'] = 'manual'
            self.assertFalse(report_host.workflow_caller(host, os.getpid(), os.getuid()+1)())
            with patch.object(scheduler, 'completion_caller_known', return_value=False):
                self.assertFalse(verify())
            with patch.object(scheduler, 'mail_scope', dict(scheduler.mail_scope)):
                self.assertFalse(verify())
            source.return_value = None
            self.assertFalse(verify())

    def test_unverified_socket_never_starts_executor(self):
        req = {'t':'report_workflow_r15','args':['validate','/input/input-data/test.json','--template','R15'],
               'files':{'input-data/test.json':'e30='}}
        with patch.object(report_host, 'bounded_run') as execute:
            self.assertEqual(report_host.execute(req, workflow_check=lambda: False)['code'], 'authorization_source_refused')
            self.assertEqual(report_host.execute(req)['code'], 'authorization_source_refused')
            extra = dict(req, files={**req['files'], 'input-template/x': 'e30='})
            self.assertEqual(report_host.execute(extra, workflow_check=lambda: True)['code'], 'shape')
            execute.assert_not_called()

    @unittest.skipUnless(shutil.which('bwrap') and shutil.which('prlimit'), 'Linux report sandbox')
    def test_host_rechecks_package_and_run_immediately_before_execution(self):
        req = {'t': 'report_workflow_r15',
               'args': ['validate', '/input/input-data/test.json', '--template', 'R15'],
               'files': {'input-data/test.json': 'e30='}}
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            engine = Path(directory)
            (engine / 'scripts').mkdir()
            script = engine / report.ENGINE
            script.write_text('print("validated")')
            fingerprint = report_local.digest(engine)
            with patch.object(report, 'find_engine', return_value=engine), \
                    patch.object(report_local, 'WORKFLOW_R15_SHA256', fingerprint), \
                    patch.object(report_host, 'bounded_run') as execute:
                check = Mock(side_effect=[True, False])
                self.assertEqual(report_host.execute(req, workflow_check=check)['code'], 'authorization_source_refused')
                self.assertEqual(check.call_count, 2)
                script.write_text('print("changed")')
                self.assertEqual(report_host.execute(req, workflow_check=lambda: True)['code'], 'integrity')
                execute.assert_not_called()

    @unittest.skipUnless(shutil.which('bwrap') and shutil.which('prlimit'), 'Linux report sandbox')
    def test_old_build_reselection_requires_current_native_run(self):
        req = {'t': 'report_local',
               'args': ['validate', '/input/input-data/test.json', '--template', 'R15'],
               'files': {'input-data/test.json': 'e30='}}
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            default, alternate = root / 'default', root / 'codex' / report.PACKAGE
            for engine, result in ((default, 'other'), (alternate, 'selected')):
                (engine / 'scripts').mkdir(parents=True)
                (engine / report.ENGINE).write_text(f'print("{result}")')
            with patch.object(report, 'find_engine', return_value=default), \
                    patch('agentj.market.skills_root', side_effect=lambda name: root / name), \
                    patch.object(report_local, 'TRUSTED_SHA256', report_local.digest(alternate)):
                self.assertEqual(report_host.execute(req, workflow_check=lambda: False)['code'],
                                 'authorization_source_refused')
                admitted = report_host.execute(req, workflow_check=lambda: True)
                self.assertEqual(admitted.get('stdout'), 'selected\n', admitted)
                (alternate / report.ENGINE).write_text('print("changed")')
                self.assertEqual(report_host.execute(req, workflow_check=lambda: True)['code'], 'integrity')

    def test_r15_render_accepts_only_the_public_cli_email_operation(self):
        original = report._engine_args(SimpleNamespace(data='data.json', data_opt=None,
                template='R15', out='mail.html', email=True, json=True), 'render')
        self.assertTrue(report_local.workflow_r15(original))
        self.assertTrue(report_local.workflow_r15(original[:-1]))
        for args in (original[:-2], [*original[:-2], '--json'], original + ['--email'],
                     ['render', 'data.json', '--email', '--out', 'file', '--template', 'R15'],
                     ['send', *original[1:]], [*original[:3], 'R14', *original[4:]]):
            self.assertFalse(report_local.workflow_r15(args))

    @unittest.skipUnless(shutil.which('bwrap') and shutil.which('prlimit'), 'Linux report sandbox')
    def test_email_bytes_are_checked_and_caller_must_stay_live_to_receive_them(self):
        req = {'t': 'report_workflow_r15',
               'args': ['render', '/input/input-data/test.json', '--template', 'R15',
                        '--out', '/output/email.html', '--email', '--json'],
               'files': {'input-data/test.json': 'e30='}}
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            engine = Path(directory)
            (engine / 'scripts').mkdir()
            script = engine / report.ENGINE
            for html, success in (('<h1>R15</h1>', True), ('<script>0</script>', False)):
                script.write_text(f'from pathlib import Path\nPath("/out/email.html").write_text({html!r})')
                with patch.object(report, 'find_engine', return_value=engine), \
                        patch.object(report_local, 'WORKFLOW_R15_SHA256', report_local.digest(engine)):
                    result = report_host.execute(req, workflow_check=lambda: True)
                    self.assertEqual(result['ok'], success, result)
                    if success:
                        self.assertEqual(base64.b64decode(result['email']), html.encode())
                        revoked = report_host.execute(req, workflow_check=Mock(side_effect=[True, True, False]))
                        self.assertEqual(revoked['code'], 'authorization_source_refused')
                        self.assertNotIn('email', revoked)
                    else:
                        self.assertNotIn('email', result)
            forged = {**req, 'args': [*req['args'][:5], '/tmp/any.html', *req['args'][6:]]}
            self.assertEqual(report_host.execute(forged, workflow_check=lambda: True)['code'], 'shape')

    def test_client_never_exports_over_existing_file_link_or_output_limit(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            home = Path(tmp)
            root = home / 'workspace'
            frozen = root / 'input-data' / 'payload.json'
            frozen.parent.mkdir(parents=True)
            frozen.write_text('{}')
            dest = root / 'email.html'
            wire_result = {'ok': True, 'code': 'executed', 'returncode': 0,
                           'email': base64.b64encode(b'<h1>mail</h1>').decode()}
            args = ['render', str(frozen), '--template', 'R15', '--out', str(dest), '--email']
            def request():
                return report_host.request(root, args, args, root, capture=True, workflow=True)
            with patch('agentj.elevate.client_request', return_value=wire_result), \
                    patch.object(Path, 'home', return_value=home), \
                    patch.dict(os.environ, {'AGENT_WORKSPACE': str(root)}):
                dest.write_bytes(b'keep')
                self.assertNotEqual(request().returncode, 0)
                self.assertEqual(dest.read_bytes(), b'keep')
                dest.unlink()
                dest.symlink_to(frozen)
                self.assertNotEqual(request().returncode, 0)
                self.assertEqual(frozen.read_bytes(), b'{}')
                dest.unlink()
                with patch.object(report_host, 'MAX_OUTPUT', 4):
                    self.assertEqual(json.loads(request().stderr)['code'], 'output_limits')
                    self.assertFalse(dest.exists())
                self.assertEqual(request().returncode, 0)
                self.assertEqual(dest.read_bytes(), b'<h1>mail</h1>')
                self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
