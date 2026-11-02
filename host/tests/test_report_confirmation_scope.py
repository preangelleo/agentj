"""Closed local report lane: native wrapper, real installed engine, no approval."""
import _hermetic
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest.mock import patch
from _testtmp import TEST_TMP
from agentj import report

SOURCE = pathlib.Path.home() / '.claude/skills/web-page-report'

class LocalReports(unittest.TestCase):
    def setUp(self):
        if not (SOURCE / report.ENGINE).is_file():
            self.skipTest('reviewed plaza engine fixture is not installed')
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.engine = self.root / 'engine'
        shutil.copytree(SOURCE, self.engine, ignore=shutil.ignore_patterns('__pycache__'))
        self.data = self.root / 'data.json'
        self.data.write_bytes((self.engine / 'examples/r15.json').read_bytes() if (self.engine / 'examples/r15.json').is_file() else b'{}')
        self.approval = patch('agentj.action_policy.confirm_cli', return_value=False).start()
        self.addCleanup(patch.stopall)

    def test_templates_need_no_action_card(self):
        result = report._run(self.engine, ['templates', '--json'], capture=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('templates', json.loads(result.stdout))
        self.approval.assert_not_called()

    def test_replaced_engine_still_needs_approval(self):
        (self.engine / report.ENGINE).write_text('raise RuntimeError("replaced")')
        result = report._run(self.engine, ['templates'], capture=True)
        self.assertEqual(result.returncode, 1)
        self.approval.assert_called_once()

    def custom(self, page='<h1>{{ title }}</h1>'):
        target = self.root / 'custom'
        target.mkdir()
        (target / 'template.json').write_text(json.dumps({'schema':'web-page-report.template/v1','id':'local-test'}))
        (target / 'schema.json').write_text(json.dumps({'type':'object', 'properties':{'title':{'type':'string'},'image':{'type':'string'}}}))
        (target / 'example.json').write_text('{"title":"snapshot"}')
        (target / 'page.html').write_text(page)
        (target / 'email.html').write_text(page)
        return str(target)

    def test_schema_example_validate_and_email(self):
        for args in (['schema','R15'], ['example','R15']):
            r=report._run(self.engine,args,capture=True)
            self.assertEqual(r.returncode,0,r.stderr)
            value=json.loads(r.stdout)
            if args[0]=='example': self.data.write_text(r.stdout)
        r=report._run(self.engine,['validate',str(self.data),'--json'],capture=True)
        self.assertEqual(r.returncode,0,r.stderr)
        out=self.root/'email.html'
        r=report._run(self.engine,['render',str(self.data),'--email','--out',str(out)],capture=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertTrue(out.is_file())
        self.approval.assert_not_called()

    def test_custom_email_is_bound_and_script_free(self):
        self.data.write_text('{"title":"snapshot"}')
        out=self.root/'custom.html'
        r=report._run(self.engine,['render',str(self.data),'--email','--out',str(out),'--template',self.custom()],capture=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(out.read_text(),'<h1>snapshot</h1>')
        self.approval.assert_not_called()

    def test_custom_dynamic_remote_resource_refused(self):
        self.data.write_text('{"title":"snapshot","image":"https://example.invalid/x.png"}')
        out=self.root/'custom.html'
        with self.assertRaisesRegex(ValueError,'external resource'):
            report._run(self.engine,['render',str(self.data),'--email','--out',str(out),'--template',self.custom('<img src={{image|url}}>')],capture=True)
        self.assertFalse(out.exists())
        self.approval.assert_not_called()

    def test_custom_script_and_external_css_refused(self):
        self.data.write_text('{"title":"snapshot"}')
        for text in ('<script>alert(1)</script>', '<p onclick="x()">x</p>', '<style>@import "https://example.invalid/a";</style>'):
            with self.subTest(text=text):
                target=self.root/'custom'
                if target.exists():shutil.rmtree(target)
                out=self.root/'bad.html'
                r=report._run(self.engine,['render',str(self.data),'--email','--out',str(out),'--template',self.custom(text)],capture=True)
                self.assertNotEqual(r.returncode,0)
                self.assertFalse(out.exists())
        self.approval.assert_not_called()

    def test_web_publish_scaffold_and_unknown_engine_retain_approval(self):
        for args in (['render',str(self.data),'--out',str(self.root/'web')], ['publish',str(self.data)], ['new-template',str(self.root/'new')]):
            with self.subTest(args=args):
                self.approval.reset_mock()
                self.assertEqual(report._run(self.engine,args,capture=True).returncode,1)
                self.approval.assert_called_once()

    def test_edited_registry_or_template_loses_trust(self):
        file=self.engine/'templates/registry.json'
        file.write_text(file.read_text()+'\n')
        self.assertEqual(report._run(self.engine,['templates'],capture=True).returncode,1)
        self.approval.assert_called_once()

    def test_outputs_symlinks_existing_files_and_broad_roots_refused(self):
        from agentj import report_local as local
        target=self.root/'outside'
        target.mkdir()
        link=self.root/'link';link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):local.export_email(str(link/'out.html'),b'x')
        existing=self.root/'exists.html';existing.write_text('original')
        with self.assertRaises(FileExistsError):local.export_email(str(existing),b'x')
        self.assertEqual(existing.read_text(),'original')
        for path in ('/etc/out.html',str(pathlib.Path.home()/'out.html'),str(pathlib.Path.home()/'.ssh/out.html'),str(pathlib.Path.home()/'coding/credentials/out.html')):
            with self.subTest(path=path),patch('pathlib.Path.cwd',return_value=pathlib.Path('/')),patch.dict(os.environ,{'AGENT_WORKSPACE':'/'}):
                with self.assertRaises(ValueError):local.safe_path(path)

    def test_html_postcheck_closes_active_and_custom_resource_forms(self):
        from agentj.report_local import check_email
        for text in ('<iframe src="https://example.invalid"></iframe>', '<img src=https://example.invalid/x>', '<p onclick=alert(1)>x</p>', '<style>body{background:url(https://example.invalid)}</style>', '<a href="java&#115;cript:alert(1)">x</a>'):
            with self.subTest(text=text), self.assertRaises(ValueError):check_email(text.encode())
        check_email(b'<img src="https://example.invalid/static.png">',allow_https_images=True)
        check_email(b'<a href="https://example.invalid">ordinary link</a>')

    def test_sandbox_has_no_network_home_or_inherited_secrets(self):
        from agentj import report_local as local
        # Confinement negative control deliberately pins a hostile test fixture;
        # production still admits only its compiled reviewed whole-package hash.
        (self.engine/report.ENGINE).write_text('''import json, os, pathlib, socket
results={"secret_env": "TEST_SECRET_CANARY" in os.environ}
try:
    socket.create_connection(("198.51.100.1",9), timeout=.2)
    results["network_blocked"]=False
except OSError:
    results["network_blocked"]=True
try:
    pathlib.Path("/etc/passwd").read_bytes()
    results["outside_blocked"]=False
except OSError:
    results["outside_blocked"]=True
print(json.dumps(results))
''')
        with patch.object(local,'TRUSTED_SHA256',local.digest(self.engine)),patch.dict(os.environ,{'TEST_SECRET_CANARY':'fixture'}):
            r=report._run(self.engine,['templates','--json'],capture=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(json.loads(r.stdout),{'secret_env':False,'network_blocked':True,'outside_blocked':True})
        self.approval.assert_not_called()

    def test_mutating_original_after_snapshot_cannot_change_data_or_engine(self):
        from agentj import report_local as local
        template=self.custom()
        self.data.write_text('{"title":"frozen"}')
        out=self.root/'frozen.html'
        original=local.run
        def mutate(*args,**kwargs):
            self.data.write_text('{"title":"changed"}')
            (self.engine/report.ENGINE).write_text('raise RuntimeError("live replacement")')
            return original(*args,**kwargs)
        with patch.object(local,'run',side_effect=mutate):
            r=report._run(self.engine,['render',str(self.data),'--email','--out',str(out),'--template',template],capture=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(out.read_text(),'<h1>frozen</h1>')
        self.approval.assert_not_called()

    def test_sandbox_failure_never_falls_back_to_unsandboxed_execution(self):
        import subprocess
        from agentj import report_local as local
        with patch.object(local.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','sandbox unavailable')) as run:
            r=report._run(self.engine,['templates'],capture=True)
        self.assertEqual(r.returncode,1)
        self.assertEqual(run.call_count,1)
        self.approval.assert_not_called()


    def test_path_cannot_replace_sandbox_helper(self):
        fake=self.root/'bwrap'
        marker=self.root/'helper-ran'
        fake.write_text('#!/bin/sh\ntouch '+str(marker)+'\n')
        fake.chmod(0o700)
        with patch.dict(os.environ,{'PATH':str(self.root)}):
            result=report._run(self.engine,['templates','--json'],capture=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(marker.exists())
        self.approval.assert_not_called()
