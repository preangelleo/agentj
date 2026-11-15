"""Friends IPC regression: bounded unique inventory and real isolated serve/CLI processes."""
import _hermetic  # noqa: F401
import contextlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from agentj import peer_guard

HOST = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent


class UniqueInventory(unittest.TestCase):
    def test_duplicates_do_not_exhaust_inventory_and_last_secret_is_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = 'synthetic_material_00000000001'
            for n in range(12):
                (root / f'.env.{n:02}').write_text(''.join(f'KEY_{i}={value}\n' for i in range(450)))
            last = 'synthetic_tail_material_999999'
            (root / '.env.zz').write_text('KEY_LAST=' + last)
            idx = peer_guard.SecretIndex.build(environ={'API_KEY': value}, home=tmp, workdir=tmp, service_env=False)
            self.assertTrue(idx.complete)
            self.assertEqual(idx.count, 2)
            idx.require_complete()
            self.assertTrue(idx.hits(last))
            self.assertNotIn(last, repr(vars(idx)))

    def test_full_unique_inventory_accepts_duplicate_and_short_tail_but_not_new_value(self):
        values = [f'synthetic_secret_value_{n:08}' for n in range(peer_guard.MAX_VALUES)]
        idx = peer_guard.SecretIndex(values)
        idx.add(values[-1])
        idx.add('  ' + values[-1] + '\n')
        idx.add('short')
        self.assertTrue(idx.complete)
        self.assertEqual(idx.count, peer_guard.MAX_VALUES)
        idx.add('synthetic_secret_value_overflow')
        self.assertFalse(idx.complete)
        with self.assertRaises(OSError):
            idx.require_complete()

    def test_file_at_unique_limit_keeps_scanning_duplicate_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env'
            values = [f'synthetic_secret_value_{n:08}' for n in range(peer_guard.MAX_VALUES)]
            path.write_text(''.join(f'API_KEY_{n}={v}\n' for n,v in enumerate(values)) + 'API_KEY_DUP='+values[0])
            idx = peer_guard.SecretIndex.build(environ={},home=tmp,service_env=False)
            self.assertTrue(idx.complete)
            idx.require_complete()
            with path.open('a') as f:
                f.write('\nAPI_KEY_NEW=synthetic_tail_new_material_012345\n')
            idx = peer_guard.SecretIndex.build(environ={},home=tmp,service_env=False)
            self.assertFalse(idx.complete)


class ServeJourney(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-fi-', dir=os.environ.get('AGENTJ_TEST_TMP') or os.environ.get('TEST_TMP'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'; self.home.mkdir()
        self.env = {k:os.environ[k] for k in ('PATH','LANG','LC_ALL','LC_CTYPE','TZ') if k in os.environ}
        self.env.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.root/'config'),
                        XDG_DATA_HOME=str(self.root/'data'), XDG_STATE_HOME=str(self.root/'xdg-state'),
                        AGENTJ_STATE_DIR=str(self.root/'state'), AGENTJ_TEST='1',
                        AGENTJ_SKILL_LINK='off', AGENTJ_HARNESS_PREPARE='off', AGENTJ_GOOGLE_PREINSTALL='off',
                        AGENTJ_BROWSER_DETECT='off', AGENTJ_UPGRADE_REPORT='off', AGENTJ_REACH_PROBE='off',
                        PYTHONPATH=os.pathsep.join((str(HOST),str(TESTS))))
        self.log = open(self.root/'serve.log','w+')
        self.addCleanup(self.log.close)
        self.proc = None
        self.addCleanup(self.stop)
        # Real host State and friend inventory. Only the seat entitlement is synthetic;
        # the mailbox is loopback-only and its durable outbox uses the real PeerNet.
        setup = '''from agentj.state import State
from agentj.peer import PeerKeys, sign_card
from agentj.friends import FriendStore
from agentj import wire
from agentj.noise import Keypair
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
st=State();st.init(relay="ws://127.0.0.1:9")
k=PeerKeys(Keypair.generate(),Ed25519PrivateKey.generate())
s=FriendStore(st);s.set_setting("on",True)
s.add_friend(k.id,wire.b64u(k.x_pub),wire.b64u(k.ed_pub),k.mbox,card=sign_card(k,{"name":"Fixture","owner":"","intro":"","lang":"en","caps":["chat"]}))
print(k.id)
'''
        r = subprocess.run([sys.executable,'-c',setup], env=self.env,cwd=self.root,capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stderr)
        self.friend = r.stdout.strip()

    def cli(self,*args):
        r = subprocess.run([sys.executable,'-m','agentj.cli',*args],env=self.env,cwd=self.root,
                           capture_output=True,text=True,timeout=30)
        return json.loads(r.stdout),r

    def serve_errors(self):
        self.log.flush(); self.log.seek(0)
        # This fixture has synthetic credentials only. Keep the subprocess failure
        # visible on an assertion failure instead of discarding it during cleanup.
        return self.log.read()[-8000:]

    def start(self):
        code = 'from agentj.serve import Host; Host.seat_refusal=lambda self,*a: None; from agentj.cli import main; main()'
        self.proc = subprocess.Popen([sys.executable,'-c',code,'serve','--events','quiet','--no-stdin'],
                                     env=self.env,cwd=self.root,stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log)
        end = time.monotonic()+30
        while time.monotonic()<end:
            if self.proc.poll() is not None:
                self.log.seek(0);self.fail(self.log.read())
            if (self.root/'state/agentperm/friends.sock').exists():
                out,_ = self.cli('friends','asks','--json')
                if out.get('ok'): return
            time.sleep(.05)
        self.fail('isolated serve not ready')

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try: self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill();self.proc.wait(5)

    def test_real_serve_duplicate_inventory_then_unavailable_and_recovery(self):
        value='synthetic_repeated_material_000000'
        (self.home/'.env').write_text(''.join(f'API_KEY_{n}={value}\n' for n in range(5100)))
        self.start()
        out,_=self.cli('friends','tell',self.friend,'普通测试消息','--json')
        self.assertTrue(out.get('ok'),(out,self.serve_errors() if not out.get('ok') else ''))
        self.assertEqual(out['friend'],self.friend)
        # Rotated inventory must be rebuilt and still block its new canary.
        canary='synthetic_rotated_material_987654'
        (self.home/'.env').write_text('API_KEY='+canary)
        out,_=self.cli('friends','tell',self.friend,canary,'--json')
        self.assertEqual((out.get('why'),out.get('rule')),('gate','secret_fragment'))
        (self.home/'.env').unlink();(self.home/'.env').mkdir()
        out,_=self.cli('friends','tell',self.friend,'普通测试消息','--json')
        self.assertEqual(out,{'ok':False,'why':'local_secret_scan_unavailable'})
        history,_=self.cli('friends','history',self.friend,'--json')
        self.assertEqual(len(history['items']),1,'refused content must not reach the outgoing history')
        with contextlib.closing(sqlite3.connect(self.root/'state/peer/outbox.sqlite')) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM outbox WHERE kind='msg'").fetchone()[0],1,
                             'only the successful ordinary send may enter the durable outbox')
        asks,_=self.cli('friends','asks','--json')
        self.assertTrue(asks['ok'])
        say,_=self.cli('friends','say','普通测试消息','--json')
        self.assertEqual(say.get('why'),'no_devices')
        mail,_=self.cli('mail','send','--to','fixture@example.invalid','--subject','fixture','--body','fixture','--json')
        self.assertNotIn(mail.get('why'),('not_running','serve_not_running'))
        # Recovery requires no restart and never disables the scanner.
        (self.home/'.env').rmdir();(self.home/'.env').write_text('API_KEY='+value)
        out,_=self.cli('friends','tell',self.friend,'恢复测试','--json')
        self.assertTrue(out.get('ok'),out)
        self.stop()
        out,_=self.cli('friends','tell',self.friend,'离线测试','--json')
        self.assertEqual(out,{'ok':False,'why':'serve_not_running'})


if __name__ == '__main__':
    unittest.main()
