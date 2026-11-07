"""#556: NOPASSWD command success does not validate a password; -v may verify a requested save."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import pathlib
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import diskq, elevate, term_store, term_vault as vault, wire
from test_f17 import PW
from test_p414_vault import CardRig, sudo_answer


class NopasswdSave(CardRig):
    def setUp(self):
        super().setUp()
        from agentj import approval_review, provenance
        self.host.desktop_turn = 1
        approval_review.remember(self.host, {'id': 1, 'ts': int(__import__('time').time()*1000),
                                            'src': {'k': 'terminal', 'text': 'Synthetic owner sudo validation'}},
                                 provenance.terminal())

    def sudo(self, mode="valid"):
        p = pathlib.Path(self.tmp.name) / "nopasswd"
        self.calls = p.with_suffix(".calls")
        p.write_text(f"""#!{sys.executable}
import json, pathlib, subprocess, sys, time
a=sys.argv[1:]
assert a[:3]==['-S','-k','-p']
with open({str(self.calls)!r}, 'a') as f: f.write(json.dumps(a[4:])+'\\n')
if a[4:]==['-v']:
    mode={mode!r}
    if mode=='no_prompt': sys.exit(0)
    sys.stderr.write(a[3]); sys.stderr.flush()
    if mode=='timeout': time.sleep(5); sys.exit(0)
    line=sys.stdin.buffer.readline().rstrip(b'\\n')
    if mode=='wrong' or line!={PW.encode()!r}:
        sys.stderr.write('Sorry, try again.\\n'+a[3]); sys.stderr.flush()
        sys.stdin.buffer.readline(); sys.exit(1)
    if mode=='nonzero': sys.exit(1)
    sys.exit(0)
assert a[4]=='--'
sys.exit(subprocess.run(a[5:]).returncode)
""")
        p.chmod(0o700)
        self.host.elevate.sudo = str(p)

    def run_card(self, *, save=True, argv=("true",)):
        async def go():
            task=asyncio.create_task(self.host.elevate.request(self.req(argv)))
            card=await self.card()
            await self.host._app(self.s, sudo_answer(self.ph,card,PW.encode(),save_prf=self.prf if save else None))
            return await asyncio.wait_for(task,15),card
        return asyncio.run(go())

    def reasons(self):
        diskq.flush()
        rows=[]
        for p in self.st.root.rglob('*.log'):
            for line in p.read_text().splitlines():
                r=json.loads(line)
                if r.get('ev')=='vault_save': rows.append(r.get('why'))
                self.assertNotIn(PW,line)
        return rows

    def test_nopasswd_then_validate_saves_and_does_not_rerun_command(self):
        self.sudo()
        res,card=self.run_card()
        self.assertEqual((res['result'],res['code'],res.get('saved')),('done',0,1))
        self.assertEqual(self.done(card['id'])[-1].get('saved'),1)
        self.assertIsNotNone(vault.wrap_meta(self.st,self.ph.did))
        self.assertEqual(bytes(term_store.load(self.st,self.ch,self.ph.did,wire.b64u(b'\x07'*16),self.prf)),PW.encode())
        self.assertEqual([json.loads(x) for x in self.calls.read_text().splitlines()],[['--','true'],['-v']])
        self.assertEqual(self.reasons(),[])

    def test_wrong_password_validation_keeps_command_success_but_never_saves(self):
        self.sudo('wrong')
        res,_=self.run_card()
        self.assertEqual((res['result'],res['code'],res.get('saved')),('done',0,None))
        self.assertIsNone(vault.wrap_meta(self.st,self.ph.did))
        self.assertEqual(self.reasons(),['verify:bad_password'])

    def test_validation_without_prompt_does_not_claim_password_verified(self):
        self.sudo('no_prompt')
        res,_=self.run_card()
        self.assertNotIn('saved',res)
        self.assertIsNone(vault.wrap_meta(self.st,self.ph.did))
        self.assertEqual(self.reasons(),['verify:unverified'])

    def test_without_save_never_validates_or_stores(self):
        self.sudo()
        res,_=self.run_card(save=False)
        self.assertEqual(res['result'],'done')
        self.assertEqual(len(self.calls.read_text().splitlines()),1)
        self.assertIsNone(vault.wrap_meta(self.st,self.ph.did))

    def test_validation_nonzero_keeps_original_exit_code(self):
        self.sudo('nonzero')
        res,_=self.run_card(argv=('false',))
        self.assertEqual((res['result'],res['code'],res.get('saved')),('done',1,None))
        self.assertEqual(self.reasons(),['verify:failed'])

    def test_validation_exception_is_private_and_command_still_done(self):
        self.sudo()
        with mock.patch.object(elevate,'verify_sudo_password',side_effect=OSError(PW)):
            res,_=self.run_card()
        self.assertEqual((res['result'],res['code'],res.get('saved')),('done',0,None))
        self.assertEqual(self.reasons(),['verify:error'])

    def test_cancelled_during_validation_never_saves(self):
        self.sudo()
        def cancel(*args):
            args[-1].set()
            return None
        with mock.patch.object(elevate,'verify_sudo_password',side_effect=cancel):
            res,_=self.run_card()
        self.assertNotIn('saved',res)
        self.assertIsNone(vault.wrap_meta(self.st,self.ph.did))
        # The live authority fence now rejects cancellation before vault-save
        # bookkeeping: completion is explicitly unknown and no value is stored.
        self.assertEqual(res['result'], 'unknown')
        self.assertEqual(self.reasons(),[])
        self.assertTrue(self.done(_['id']))
        self.assertTrue(all(row['result']=='unknown' for row in self.done(_['id'])))

    def test_verified_password_store_failure_preserves_command(self):
        self.sudo()
        with mock.patch.object(vault,'create',side_effect=OSError(PW)):
            res,_=self.run_card()
        self.assertEqual((res['result'],res['code'],res.get('saved')),('done',0,None))
        self.assertEqual(self.reasons(),['store:OSError'])

    def test_validation_timeout_never_stores(self):
        self.sudo('timeout')
        real=elevate.run_sudo
        def short(*a,**kw):
            if kw.get('_verify_only'): a=(*a[:3],.1,*a[4:])
            return real(*a,**kw)
        with mock.patch.object(elevate,'run_sudo',side_effect=short):
            res,_=self.run_card()
        self.assertEqual((res['result'],res['code'],res.get('saved')),('done',0,None))
        self.assertEqual(self.reasons(),['verify:timeout'])


if __name__=='__main__': unittest.main()
