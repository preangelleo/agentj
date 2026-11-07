"""Complete synthetic packages for the installer path; prior failed fixtures retained."""
import _hermetic
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import market_fixtures as fx
from agentj import bundle, initialization, market, minisign
from test_install_lane import schema

class Receipts(unittest.TestCase):
    def test_signed_skill_workflow_and_host_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            for kind,maker in [('skill',fx.make_skill),('workflow',fx.make_workflow)]:
                root=maker(Path(tmp)/kind,initialization_schema=initialization.FILE)
                (root/initialization.FILE).write_text(json.dumps(schema()))
                data,m=bundle.build(root)
                signer=fx.TestKey()
                self.assertTrue(minisign.verify_package(data,signer.sign_package(data,m),m,signer.keyring))
                parsed,files=bundle.parse(data)
                self.assertEqual(initialization.from_bundle(parsed,files)['type'],'object')
                evil=schema(); evil['x-capabilities'][0]['providers'][0]['dest']='/tmp/forbidden'
                (root/initialization.FILE).write_text(json.dumps(evil))
                with self.assertRaises(bundle.BundleError): bundle.build(root)

    def test_market_success_creates_presence_receipt_after_install(self):
        e=fx.Env(); self.addCleanup(e.close); server=fx.PkgServer()
        root=fx.make_skill(e.root/'src',initialization_schema=initialization.FILE)
        (root/initialization.FILE).write_text(json.dumps(schema()))
        data,m=bundle.build(root); server.add(data)
        out=io.StringIO()
        kw=dict(st=e.st,post=server,get=server.get,out=out,harness='claude_code')
        self.assertEqual(market.run_install('demo-skill',**kw),0)
        self.assertEqual(market.read_installed(e.st),[])
        dg=out.getvalue().split('digest: ')[1].split()[0]
        result=initialization.assess(schema(),env_names=set(),cli_names=set(),fonts=set())
        with patch.object(initialization,'local_assess',return_value=result),patch.object(market,'run_verify',return_value=(0,'')):
            self.assertEqual(market.run_install('demo-skill',owner_confirmed=True,accept_unverified=True,digest=dg,**kw),0)
        self.assertEqual(market.read_installed(e.st)[-1]['initialization'],result)
        self.assertIn('initialization:',out.getvalue())

    def test_invalid_schema_cannot_be_installed_from_repacked_bytes(self):
        e=fx.Env(); self.addCleanup(e.close)
        root=fx.make_skill(e.root/'src',initialization_schema=initialization.FILE)
        (root/initialization.FILE).write_text(json.dumps(schema()))
        data,m=bundle.build(root); parsed,files=bundle.parse(data)
        evil=schema(); evil['$ref']='#'
        files[initialization.FILE]=json.dumps(evil).encode()
        parsed['files']=bundle.files_list(files)
        with self.assertRaises(bundle.BundleError): bundle.parse(bundle.pack(parsed,files))
