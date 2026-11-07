"""Native adapters with fake effects, real schema/paired signatures; offline only."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from agentj import setup_runtime
import test_install_assist_host as boundary

class Native(unittest.IsolatedAsyncioTestCase):
    setUp = boundary.Boundary.setUp
    tearDown = boundary.Boundary.tearDown
    request = boundary.Boundary.request

    def native(self):
        self.h._paired_live = lambda s: self.runner.live(s)
        self.h._send_device = AsyncMock()
        self.h.elevate = SimpleNamespace(request=AsyncMock(return_value={'result':'saved', 'receipt':{'verify':'skipped'}}))
        self.platform = setup_runtime.NativePlatform(self.h)
        self.runner.runtime = setup_runtime.Runtime(self.platform)

    async def test_sys_check_is_offline_redacted(self):
        self.native()
        result = await self.runner.run(self.s, self.request('sys.check'))
        self.assertTrue(result['ok'], result)
        self.assertNotIn('/work', str(result))
        self.assertNotIn('environ', str(result))

    async def test_secret_card_targets_only_live_owner(self):
        self.native()
        with patch('agentj.setup_native.secret_card', return_value={'kind':'secret','name':'OPENAI_API_KEY'}) as make:
            result = await self.runner.run(self.s, self.request('ai.key', {'provider':'openai'}))
        self.assertEqual(result['result'], {'provider':'openai','saved':True})
        self.assertEqual(self.h.elevate.request.call_args.args[0]['only'], 'owner')
        make.assert_called_once_with(self.h.st, 'openai')
        self.assertNotIn('receipt', str(result))

    async def test_no_fake_saved_on_card_failure(self):
        self.native()
        for reason in ('denied','cancelled','timeout','stopped','gone'):
            self.h.elevate.request.return_value = {'result':reason}
            with patch('agentj.setup_native.secret_card', return_value={'kind':'secret'}):
                result = await self.runner.run(self.s, self.request('ai.key', {'provider':'openai'}))
            self.assertFalse(result['ok'], result)
            self.assertNotIn('saved', str(result.get('result')))

    async def test_pairing_removed_during_card(self):
        self.native()
        async def card(c):
            self.h.st.allowed = False
            return {'result':'saved'}
        self.h.elevate.request.side_effect = card
        with patch('agentj.setup_native.secret_card', return_value={'kind':'secret'}):
            result = await self.runner.run(self.s, self.request('ai.key', {'provider':'openai'}))
        self.assertFalse(result['ok'])

    async def test_install_uses_existing_worker_without_manifest(self):
        self.native()
        with patch.object(self.platform, '_worker', AsyncMock(return_value=True)) as worker:
            result = await self.runner.run(self.s, self.request('ai.install', {'cli':'opencode'}))
        self.assertTrue(result['ok'], result)
        worker.assert_awaited_once_with('install', 'opencode', 'official')

    async def test_browser_reuses_fixed_worker(self):
        self.native()
        with patch.object(self.platform, '_worker', AsyncMock(return_value=True)) as worker:
            result = await self.runner.run(self.s, self.request('browser.setup', item='browser'))
        self.assertTrue(result['ok'], result)
        worker.assert_awaited_once_with('browser')

    async def test_paid_probe_and_connect_do_not_call_model(self):
        self.native()
        for op in ('ai.probe', 'ai.connect'):
            result = await self.runner.run(self.s, self.request(op, {'cli':'codex'}))
            self.assertEqual(result['why'], 'unavailable')

if __name__ == '__main__':
    unittest.main()

class SealedOwner(unittest.IsolatedAsyncioTestCase):
    setUp = boundary.Boundary.setUp
    tearDown = boundary.Boundary.tearDown
    request = boundary.Boundary.request

    async def test_real_sealed_signed_uv_card_only_owner_delivery(self):
        import hashlib, json, time
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from agentj import elevate, passkey, wire
        h, st = self.h, self.h.st
        h._paired_live = lambda s: self.runner.live(s)
        h._approvers = lambda: [self.s]
        h._online_approvers = h._approvers
        h.push_notify = lambda *a: None
        st.log = lambda *a, **kw: None
        uv = ec.generate_private_key(ec.SECP256R1())
        pk = {'id':wire.b64u(b'synthetic-credential'), 'alg':passkey.ALG_ES256,
              'rp':'m.agentj.app', 'spki':wire.b64u(uv.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))}
        st.passkey_of_device = lambda device: pk if device=='owner' else None
        st.passkey_count = lambda *a: None
        helper = SimpleNamespace(device='helper', pub=b'paired', cid='helper', state='ready')
        h.sessions['helper'] = helper
        sent = []
        async def send(s, obj):
            sent.append((s.device, obj))
        h.send_app = send
        # Use the real lifecycle; __new__ omitted review_requests/operations and
        # failed before any sealed card could be emitted.
        el = elevate.Elevator(h)
        # This fixture represents the live paired owner's current setup task,
        # using real history/origin bookkeeping and the actual Host task identity.
        from agentj import approval_review, provenance
        from agentj.history import History
        from agentj.serve import Host
        h.hist=History(st,on=False)
        turn=h.hist.add({'k':'phone','dev':self.s.device,'text':'Provide my API key for setup'},'','done')
        h.agent=SimpleNamespace(kind='fixture',native_source=provenance.phone())
        h.cur_turn,h.desktop_turn=turn['id'],turn['id']
        h.agent_cfg={'dir':str(st.root)}
        h._task_id=Host._task_id.__get__(h)
        approval_review.remember(h,turn,provenance.phone())
        el._reload_saved_env = AsyncMock()  # storage boundary; never touch service env
        h.elevate = el
        self.runner.runtime = setup_runtime.Runtime(host=h)
        captured = []
        card = {'kind':'secret','name':'OPENAI_API_KEY','purpose':'Synthetic test only',
                'dest':'fake sink', 'dest_spec':{'mode':'login','path':'','key':'OPENAI_API_KEY'},
                'verify':'','cwd':'/','verify_url':'','verify_cmd':'','verify_header':''}
        def sink(value):
            captured.append(hashlib.sha256(value).hexdigest())
            return {'result':'saved','receipt':{'verify':'skipped'}}
        with patch('agentj.setup_native.secret_card', return_value=card), patch.object(elevate,'remember'), patch.object(elevate,'audit'):
            task = asyncio.create_task(self.runner.run(self.s, self.request('ai.key', {'provider':'openai'})))
            for _ in range(100):
                if sent or task.done(): break
                await asyncio.sleep(.001)
            if task.done():
                self.fail(f'Sealed setup finished before delivery: {await task!r}')
            self.assertTrue(sent)
            self.assertEqual([d for d,m in sent], ['owner'])
            self.assertEqual(el.open_msgs(helper), [])
            c = next(iter(el.cards.values()))
            c['sink'] = sink  # storage-only fixture seam, after serializable request binding
            self.assertFalse(el._sees(c, helper))
            value = b'synthetic-only-value'
            aad = elevate.seal_aad(h.channel,'owner',c['id'],'secret',c['nonce'],c['digest'])
            epk, blob = elevate.seal(c['epk'], value, aad)
            ts = int(time.time()*1000)
            digest = hashlib.sha256(blob).hexdigest()
            sig = st.key.sign(elevate.signed_message(h.channel,'owner',c['id'],'secret','allow',c['nonce'],ts,c['digest'],digest))
            challenge = passkey.elevate_challenge(h.channel,'owner',c['id'],'secret',c['nonce'],c['digest'])
            cd = json.dumps({'type':'webauthn.get','challenge':wire.b64u(challenge),'origin':'https://m.agentj.app','crossOrigin':False}).encode()
            ad = hashlib.sha256(pk['rp'].encode()).digest()+bytes([5])+bytes(4)
            fa = {'id':pk['id'],'cd':wire.b64u(cd),'ad':wire.b64u(ad),
                  'sig':wire.b64u(uv.sign(ad+hashlib.sha256(cd).digest(),ec.ECDSA(hashes.SHA256())))}
            obj = {'t':'elev_answer','id':c['id'],'ok':True,'n':c['nonce'],'ts':ts,'sig':wire.b64u(sig),'epk':wire.b64u(epk),'ct':wire.b64u(blob),'fa':fa}
            tampered = {**obj, 'ct':wire.b64u(blob[:-1]+bytes([blob[-1]^1]))}
            self.assertEqual(el._check(self.s,c,tampered), 'bad_signature')
            await el.on_phone(helper,obj)
            self.assertEqual(captured, [])
            await el.on_phone(self.s,obj)
            result = await asyncio.wait_for(task, 3)
        self.assertTrue(result['ok'], result)
        self.assertEqual(captured, [hashlib.sha256(value).hexdigest()])
        self.assertEqual([d for d,m in sent if m.get('t') in ('elev','elev_done')], ['owner','owner'])
        self.assertEqual(el.completed_msgs(helper), [])
        self.assertNotIn(value.decode(), str(result)+str(self.runner.events)+str(sent))

class WorkerCleanup(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_kills_only_owned_process_group_and_reaps(self):
        from agentj.setup_native import owned_process
        entered = asyncio.Event()
        waits = 0
        async def wait():
            nonlocal waits
            waits += 1
            if waits==1:
                entered.set()
                await asyncio.Event().wait()
            return 0
        proc = SimpleNamespace(pid=7654321, wait=wait)
        spawn = AsyncMock(return_value=proc)
        with patch('agentj.setup_native.os.killpg') as kill:
            task = asyncio.create_task(owned_process(['/fixed/fake'],env={},cwd='/work',timeout=30,spawn=spawn))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        self.assertEqual([c.args[0] for c in kill.call_args_list], [proc.pid,proc.pid])
        self.assertGreaterEqual(waits, 2)

    async def test_timeout_reaps_and_returns_controlled_error(self):
        from agentj.setup_native import owned_process
        waits = 0
        async def wait():
            nonlocal waits
            waits += 1
            if waits==1: await asyncio.Event().wait()
            return 0
        proc = SimpleNamespace(pid=7654321, wait=wait)
        with patch('agentj.setup_native.os.killpg'):
            with self.assertRaises(setup_runtime.RuntimeErrorCode) as raised:
                await owned_process(['/fixed/fake'],env={},cwd='/work',timeout=.001,spawn=AsyncMock(return_value=proc))
        self.assertEqual(raised.exception.code,'timeout')
        self.assertGreaterEqual(waits, 2)

class FixedPackages(unittest.TestCase):
    def test_mirror_mismatch_stops_before_npm(self):
        import io, json, subprocess
        from agentj import setup_packages
        calls=[]
        def fetch(req, **kw):
            url=req.full_url
            doc={'version':'1.2.3','dist':{'integrity':'sha512-'+('A' if 'npmjs.org' in url else 'B')*88,
                 'tarball':'https://registry.npmmirror.com/fake.tgz'}}
            return io.BytesIO(json.dumps(doc).encode())
        def which(name): return None if name=='claude' else '/fixed/'+name
        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv,0,stdout='',stderr='')
        with patch.object(setup_packages.shutil,'which',side_effect=which), patch.object(setup_packages.urllib.request,'urlopen',side_effect=fetch), patch.object(setup_packages.subprocess,'run',side_effect=run):
            with self.assertRaises(ValueError):
                setup_packages.install('claude','mirror','/work/.jarvis/install-host-qa/never-install','/work/.jarvis/install-host-qa')
        self.assertFalse(any(c[0]=='npm' for c in calls))

    def test_node_wrong_checksum_never_extracts_or_installs(self):
        import io
        from pathlib import Path
        from agentj import setup_packages
        class Response(io.BytesIO):
            def geturl(self): return 'https://nodejs.org/fake'
        def fetch(req,**kw):
            if req.full_url.endswith('SHASUMS256.txt'):
                return Response(('0'*64+'  node-v22.1.0-linux-x64.tar.xz\n').encode())
            return Response(b'synthetic untrusted bytes')
        with patch.object(setup_packages.platform,'system',return_value='Linux'), patch.object(setup_packages.platform,'machine',return_value='x86_64'), patch.object(setup_packages.urllib.request,'urlopen',side_effect=fetch), patch.object(setup_packages.tarfile,'open') as extract:
            with self.assertRaisesRegex(ValueError,'checksum'):
                setup_packages.install_node('official',Path('/work/.jarvis/install-host-qa/never-node'),'/work/.jarvis/install-host-qa')
        extract.assert_not_called()

class LoginNative(unittest.IsolatedAsyncioTestCase):
    setUp = boundary.Boundary.setUp
    tearDown = boundary.Boundary.tearDown
    request = boundary.Boundary.request
    native = Native.native

    async def test_fixed_claude_login_owner_card_and_no_model_probe(self):
        self.native()
        class Process:
            def __init__(self,*args): self.rc=None; self.closed=False; self.args=args
            def read(self): return 'https://claude.ai/oauth?synthetic=1'
            def done(self): return self.rc
            def write(self,value): self.rc=0
            def close(self): self.closed=True
        proc=Process()
        async def card(c):
            self.assertEqual(c['only'],'owner')
            return c['sink'](bytearray(b'synthetic-code'))
        self.h.elevate.request.side_effect=card
        with patch('agentj.setup_login.LoginProcess',return_value=proc) as launch, patch('agentj.binaries.resolve',return_value={'path':'/fixed/claude'}), patch('agentj.harness.claude_login',return_value=('ok',None)), patch('agentj.harness.probe',side_effect=AssertionError('no model')):
            result=await self.runner.run(self.s,self.request('ai.login',{'cli':'claude'}))
        self.assertTrue(result['ok'],result)
        self.assertTrue(proc.closed)
        self.assertEqual(launch.call_args.args[0],['/fixed/claude','auth','login'])
        self.assertEqual(self.h._send_device.call_args.args[0],'owner')
        self.assertNotIn('synthetic-code',str(result)+str(self.runner.events))

    async def test_login_url_rejects_lookalike_and_credentials(self):
        from agentj.setup_login import login_url
        self.assertIsNone(login_url('https://claude.ai.evil.invalid/login'))
        self.assertIsNone(login_url('https://user:pass@claude.ai/login'))

class NativeWorker(unittest.IsolatedAsyncioTestCase):
    setUp = boundary.Boundary.setUp
    tearDown = boundary.Boundary.tearDown
    request = boundary.Boundary.request
    native = Native.native

    async def test_worker_cleanup_after_cancel_no_install_runs(self):
        from pathlib import Path
        from agentj import setup_native
        self.native()
        entered=asyncio.Event()
        paths=[]
        async def effect(argv, **kw):
            paths.append(Path(kw['cwd']))
            self.assertTrue(paths[-1].is_dir())
            self.assertEqual(argv[1:4], ['-m','agentj.setup_native','install'])
            self.assertEqual(argv[-2:], ['codex','official'])
            self.assertFalse(any('KEY' in k or 'TOKEN' in k for k in kw['env']))
            entered.set()
            await asyncio.Event().wait()
        with patch.object(setup_native,'owned_process',side_effect=effect):
            task=asyncio.create_task(self.platform._worker('install','codex','official'))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(paths[0].exists())

    async def test_browser_worker_calls_existing_setup_only(self):
        from agentj import setup_native, browser
        with patch.object(browser,'setup',return_value={'ok':True}) as setup:
            rc=setup_native.worker(['browser',str(self.h.st.root),str(self.h.st.root)])
        self.assertEqual(rc,0)
        self.assertEqual(setup.call_args.kwargs,{'own':True})
        self.assertEqual(setup.call_args.args[0].root,self.h.st.root)

    async def test_fixed_provider_cards_use_real_normalizer(self):
        from agentj import setup_native
        root=self.h.st.root
        state=SimpleNamespace(root=root/'state')
        state.root.mkdir()
        with patch('agentj.service.env_file',return_value=str(root/'synthetic.env')), patch('agentj.service.name',return_value='agentj'):
            for provider,name in setup_native.KEY_NAMES.items():
                card=setup_native.secret_card(state,provider)
                self.assertEqual(card['name'],name)
                self.assertEqual(card['dest_spec']['key'],name)
                self.assertEqual(card['dest_spec']['path'],str(root/'synthetic.env'))
                self.assertEqual(card['verify_url'],'')
        self.assertFalse((root/'synthetic.env').exists())

class PackageSuccess(unittest.TestCase):
    def test_fixed_npm_version_integrity_and_native_verification(self):
        import io, json, subprocess, tempfile
        from pathlib import Path
        from agentj import setup_packages
        commands=[]
        with tempfile.TemporaryDirectory() as scratch:
            prefix=Path(scratch)/'npm'
            def which(name): return None if name=='claude' else '/fixed/'+name
            def fetch(req,**kw):
                self.assertTrue(req.full_url.startswith('https://registry.npmjs.org/'))
                return io.BytesIO(json.dumps({'version':'1.2.3','dist':{'integrity':'sha512-'+'A'*88}}).encode())
            def run(argv,**kw):
                commands.append(argv)
                if argv[0]=='npm':
                    self.assertIn('@anthropic-ai/claude-code@1.2.3',argv)
                    self.assertEqual(argv[-2:],['--registry','https://registry.npmjs.org'])
                    (prefix/'bin').mkdir(parents=True)
                    (prefix/'bin/claude').write_bytes(b'synthetic never executed')
                return subprocess.CompletedProcess(argv,0,stdout='ELF x86-64' if argv[0]=='file' else '1.2.3',stderr='')
            with patch.object(setup_packages.shutil,'which',side_effect=which), patch.object(setup_packages.urllib.request,'urlopen',side_effect=fetch), patch.object(setup_packages.subprocess,'run',side_effect=run), patch.object(setup_packages.os,'access',return_value=True), patch.object(Path,'glob',return_value=[]):
                self.assertTrue(setup_packages.install('claude','official',prefix,scratch))
        self.assertTrue(any(c[-1]=='--help' for c in commands))
        self.assertTrue(any(c[0]=='file' for c in commands))

class NativeInstallSelection(unittest.TestCase):
    def test_worker_preserves_existing_binary_without_install(self):
        from agentj import setup_native
        with patch('agentj.binaries.resolve',return_value={'path':'/fixed/existing/claude'}), patch('agentj.setup_packages.install') as install, patch('agentj.setup_env.env_edit') as edit:
            self.assertEqual(setup_native.worker(['install','/work/.jarvis/install-host-qa','/work/.jarvis/install-host-qa','claude','official']),0)
        install.assert_not_called()
        edit.assert_not_called()

    def test_worker_new_install_pins_only_fixed_binary(self):
        from pathlib import Path
        from agentj import setup_native
        with patch('agentj.binaries.resolve',return_value={'path':None}), patch('agentj.setup_packages.install',return_value=True) as install, patch('agentj.setup_env.env_edit') as edit, patch('agentj.service.env_file',return_value='/work/.jarvis/install-host-qa/fake.env'), patch('agentj.service.name',return_value='agentj'), patch.object(Path,'home',return_value=Path('/work/.jarvis/install-host-qa')):
            self.assertEqual(setup_native.worker(['install','/work/.jarvis/install-host-qa','/work/.jarvis/install-host-qa','codex','official']),0)
        install.assert_called_once()
        edit.assert_called_once_with('/work/.jarvis/install-host-qa/fake.env',{'AGENTJ_CODEX_BIN':'/work/.jarvis/install-host-qa/.local/share/agentj-setup/npm/bin/codex'})

class NativeCardCancellation(unittest.IsolatedAsyncioTestCase):
    setUp = boundary.Boundary.setUp
    tearDown = boundary.Boundary.tearDown
    request = boundary.Boundary.request
    native = Native.native

    async def test_native_card_unbind_estop_timeout_cancel_never_saved(self):
        from agentj import install_assist
        self.native()
        for reason in ('unbind','estop','timeout','cancel'):
            self.h.st.allowed=True
            self.h.halt=False
            entered,cancelled=asyncio.Event(),asyncio.Event()
            async def card(c):
                entered.set()
                try: await asyncio.Event().wait()
                finally: cancelled.set()
            self.h.elevate.request.side_effect=card
            with patch('agentj.setup_native.secret_card',return_value={'kind':'secret'}), patch.dict(install_assist.TIMEOUT,{'ai.key':.02 if reason=='timeout' else 10}):
                task=asyncio.create_task(self.runner.run(self.s,self.request('ai.key',{'provider':'openai'})))
                await entered.wait()
                if reason=='unbind': self.h.st.allowed=False
                if reason=='estop': self.h.halt=True
                if reason=='cancel': self.runner.active[1].cancel()
                result=await asyncio.wait_for(task,1)
            self.assertEqual(result['why'],{'unbind':'no_device','estop':'stopped','timeout':'timeout','cancel':'denied'}[reason])
            self.assertFalse(result['ok'])
            self.assertTrue(cancelled.is_set())
            self.assertIsNone(self.runner.active)
