"""Candidate Host admission behind the isolated browser's Noise transport.

Only displayed account metadata and the official login subprocess are fixtures.
AuthRescue, the request card, signatures, UV verification and audit stay real.
"""
import asyncio
import base64
import json
import os
import pathlib
import sys
import tempfile
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'host/tests'), str(ROOT / 'host')]
import _hermetic  # noqa: E402,F401
from agentj import accounts, approvals, wire  # noqa: E402
import test_l1 as l1  # noqa: E402
from test_p57_passkey import Authenticator, pk_record  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

os.environ['AGENTJ_TEST_PASSKEY_ORIGIN'] = 'loopback'


async def main():
    with tempfile.TemporaryDirectory(prefix='aj0300-account-') as td:
        host = None
        for_line = asyncio.to_thread
        while raw := await for_line(sys.stdin.readline):
            req = json.loads(raw)
            try:
                op = req['op']
                if op == 'setup':
                    if host is not None:
                        host.stopping.set()
                        for task in list(host.__dict__.get('_bg_tasks', ())):
                            task.cancel()
                        await asyncio.sleep(0)
                    st = l1._state(str(pathlib.Path(td) / str(req['serial'])))
                    cfg = st.config(); cfg['channel'] = req['channel']
                    st.config_path.write_text(json.dumps(cfg))
                    pub = wire.unb64u(req['pub'])
                    did = st.add_device(pub, 'Synthetic remote', wire.unb64u(req['sk']))
                    assert did == req['device']
                    sent = []
                    host = l1._host(st, sent)
                    host.channel = req['channel']
                    session = l1._ready(host, SimpleNamespace(pub=pub, did=did))
                    session.p33 = True
                    host.agent = SimpleNamespace(kind='codex', status='idle', cur_send=None, cfg={'dir': str(st.root)})
                    host.ask_ttl = 2 if req.get('expire') else 60
                    executions = []
                    ar = host.auth_rescue
                    account_view = {'codex': {'active': None, 'items': []}, 'claude': {'active': None, 'items': []}, 'conflict': []}
                    accounts.phone_view = lambda _: account_view
                    ar.view = lambda: {'harnesses': [], 'dot': False, 'flow': {k:v for k,v in (ar.flow or {}).items() if not k.startswith('_')} or None}
                    async def no_health(**kwargs):
                        pass
                    ar.refresh_health = no_health
                    async def login():
                        executions.append('official-device-login')
                        ar.flow = {**ar.flow, 'stage': 'device', 'url': 'https://auth.openai.com/device', 'code': 'SYNTHETIC', 'round':1, 'rounds':1, 'expires_at': 9999999999}
                        ar.broadcast()
                    ar._codex = login
                    auth = Authenticator()
                    if req.get('has_key'):
                        st.set_passkey(pub, pk_record(auth, rp='localhost'))
                    private = auth.key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
                    credential = {'credentialId':base64.b64encode(auth.id).decode(), 'privateKey':base64.b64encode(private).decode(), 'rpId':'localhost', 'isResidentCredential':True, 'userHandle':base64.b64encode(b'0').decode(), 'signCount':0}
                    result = {'credential': credential, 'pk_state': {'t':'pk_state','s':st.passkey_state(did)}}
                elif op == 'frame':
                    obj = req['frame']
                    await host._app(session, obj)
                    await asyncio.sleep(.08)
                    result = {}
                elif op == 'drain':
                    await asyncio.sleep(.02)
                    result = {}
                elif op == 'check_challenge':
                    fields = host.pkpeer.check_fields(session)
                    result = {'challenge': {'t':'pk_state', 's':st.passkey_state(session.device), **fields}}
                elif op == 'invalidate_check':
                    if req['how'] == 'expired':
                        host.pkpeer.checks[session.device] = [(n,host.pkpeer.clock()-1) for n,_ in host.pkpeer.checks.get(session.device,[])]
                    elif req['how'] == 'evicted':
                        for _ in range(4):
                            host.pkpeer.check_fields(session)
                    elif req['how'] == 'restarted':
                        host.pkpeer.checks.clear()
                    else:
                        raise ValueError(req['how'])
                    result = {}
                elif op == 'stop':
                    if host:
                        host.stopping.set()
                    print('0300RPC:' + json.dumps({'ok':True}), flush=True)
                    break
                else:
                    raise ValueError(op)
                frames = [obj for _,obj in sent]
                sent.clear()
                records = approvals.read_log(st)
                errors = []
                log = st.root / 'host.log'
                if log.exists():
                    for line in log.read_text().splitlines():
                        try:
                            event = json.loads(line)
                            if event.get('event',event.get('e')) == 'bg_task_failed':
                                errors.append({k:event.get(k) for k in ('why','exception_type')})
                        except ValueError:
                            pass
                result.update(ok=True, frames=frames, executions=len(executions), audits=[approvals.check_record(st,r) for r in records], pending=len(host.asks), bg_errors=errors)
                print('0300RPC:' + json.dumps(result, ensure_ascii=False), flush=True)
            except Exception as error:
                import traceback
                print('0300RPC:' + json.dumps({'ok':False,'error':type(error).__name__+': '+str(error),'trace':traceback.format_exc()}), flush=True)


asyncio.run(main())
