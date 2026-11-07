"""Synthetic signed bundles and Native OpenCode matrix; no network or install."""
import _hermetic
import asyncio
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import market_fixtures as fx
from agentj import bundle, initialization, market, minisign, setup_packages
from test_install_lane import schema

class BundleIntegration(unittest.TestCase):
    def test_skill_and_workflow_signed_roundtrip_and_no_unknown_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            for kind,maker in [('skill',fx.make_skill),('workflow',fx.make_workflow)]:
                root=maker(Path(tmp)/kind,initialization_schema=initialization.FILE)
                (root/initialization.FILE).write_text(json.dumps(schema()))
                data,m=bundle.build(root)
                signer=fx.TestKey()
                signature=signer.sign_package(data,m)
                self.assertTrue(minisign.verify_package(data,signature,m,signer.keyring))
                manifest,files=bundle.parse(data)
                self.assertIsNotNone(initialization.from_bundle(manifest,files))
                # Repacking an unknown command is not admitted by parsing/building.
                doc=schema(); doc['x-capabilities'][0]['providers'][0]['command']='echo forbidden'
                (root/initialization.FILE).write_text(json.dumps(doc))
                with self.assertRaises(bundle.BundleError): bundle.build(root)

    def test_actual_market_install_records_and_prints_readiness(self):
        env=fx.Env(); self.addCleanup(env.close)
        server=fx.PkgServer()
        root=fx.make_skill(env.root/'src',initialization_schema=initialization.FILE)
        (root/initialization.FILE).write_text(json.dumps(schema()))
        data,m=bundle.build(root); server.add(data)
        buf=io.StringIO()
        kw=dict(st=env.st,post=server,get=server.get,out=buf,harness='claude_code')
        self.assertEqual(market.run_install('demo-skill',**kw),0)
        digest=buf.getvalue().split('digest: ')[1].split()[0]
        result={'rows':[{'id':'writing','status':'missing','evidence':'unknown'}],'key_requests':[],'config':[]}
        with patch.object(initialization,'local_assess',return_value=result),patch.object(market,'run_verify',return_value=(0,'')):
            self.assertEqual(market.run_install('demo-skill',owner_confirmed=True,accept_unverified=True,digest=digest,**kw),0)
        self.assertEqual(market.read_installed(env.st)[-1]['initialization'],result)
        self.assertIn('initialization:',buf.getvalue())

    def test_admin_path_loaded_bundle_checks_same_schema(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('isolated_bundle',bundle.__file__)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        module.check_initialization({'type':'skill','initialization_schema':initialization.FILE},{initialization.FILE:json.dumps(schema()).encode()})

class Delivery(unittest.IsolatedAsyncioTestCase):
    async def test_only_current_signing_owner_receives_structured_card(self):
        owner=SimpleNamespace(device='owner'); other=SimpleNamespace(device='other')
        h=SimpleNamespace(sessions={'one':owner,'two':other},_paired_live=lambda s:True,
                          st=SimpleNamespace(sign_key=lambda d:b'public' if d=='owner' else None),send_app=AsyncMock())
        await initialization.deliver(h,'owner',[{'name':'demo-skill','rows':[]}])
        h.send_app.assert_awaited_once()
        self.assertIs(h.send_app.call_args.args[0],owner)
        h.send_app.reset_mock(); h._paired_live=lambda s:False
        await initialization.deliver(h,'owner',[{}]); h.send_app.assert_not_called()

class NativeOpenCode(unittest.TestCase):
    def test_linux_macos_without_node_mise_shim_with_node(self):
        # Synthetic official archive pins are scoped to this regression only.
        for system,arch in [('Linux','x86_64'),('Darwin','arm64')]:
            for node in ('absent','mise-shim','installed'):
                with self.subTest(system=system,node=node), tempfile.TemporaryDirectory() as tmp:
                    raw=io.BytesIO()
                    if system=='Linux':
                        with tarfile.open(fileobj=raw,mode='w:gz') as tar:
                            m=tarfile.TarInfo('opencode'); data=b'synthetic-native'; m.size=len(data); tar.addfile(m,io.BytesIO(data))
                    else:
                        import zipfile
                        with zipfile.ZipFile(raw,mode='w') as tar: tar.writestr('opencode',b'synthetic-native')
                    data=raw.getvalue(); commands=[]
                    def which(name):
                        if name in ('node','npm') and node!='absent': return '/fake/'+node+'/'+name
                        return None
                    def run(argv,**kw):
                        commands.append(argv)
                        output='ELF x86-64' if argv[0]=='file' and system=='Linux' else 'Mach-O arm64' if argv[0]=='file' else '2.2.5'
                        return subprocess.CompletedProcess(argv,0,stdout=output,stderr='')
                    target='linux-x64' if system=='Linux' else 'darwin-arm64'
                    with patch.object(setup_packages.platform,'system',return_value=system),patch.object(setup_packages.platform,'machine',return_value=arch),patch.object(setup_packages.shutil,'which',side_effect=which),patch.object(setup_packages.subprocess,'run',side_effect=run),patch.object(setup_packages.urllib.request,'urlopen',return_value=io.BytesIO(data)),patch.dict(setup_packages.OPENCODE_SHA256,{target:hashlib.sha256(data).hexdigest()}),patch.object(setup_packages.os,'access',return_value=True),patch.object(Path,'glob',return_value=[]),patch.object(setup_packages,'install_node',side_effect=AssertionError('Node must not be installed')):
                        self.assertTrue(setup_packages.install('opencode','official',Path(tmp)/'prefix',tmp))
                    self.assertTrue((Path(tmp)/'prefix/bin/opencode').is_file())
                    self.assertFalse(any(c[0] in ('node','npm','mise','curl','bash') for c in commands))
