"""P126 (ADR-A193 §1): the seat API workflow executor — strict schema subset, workflow loading/digest, the live sandbox
qualification probe, and a real bubblewrap job: isolation from HOME / env / loopback / host unix sockets / host files /
host /tmp, knowledge and out/ only when permitted, wall deadline, cancellation killing every descendant, memory and
output caps, secret-shaped output refused. Skipped (with the reason) only where qualify() says no verified sandbox."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import contextlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid

from agentj.bots.store import BotError
from agentj.seat_api import workflow as w

OBJ = lambda props, req=None: {'type': 'object', 'properties': props, 'required': list(req or props), 'additionalProperties': False}  # noqa: E731
TEXT_IN = OBJ({'text': {'type': 'string', 'maxLength': 1000}})
TEXT_OUT = OBJ({'text': {'type': 'string', 'maxLength': 2000}})
PERMS = {'knowledge_read': False, 'output_files': False, 'tools': [], 'network': []}
LIMITS = {'timeout_seconds': 20, 'cpu_seconds': 20, 'memory_mb': 256, 'output_bytes': 65536}
ECHO = 'import json,sys\nd=json.load(sys.stdin)\nprint(json.dumps({"text": d["text"].upper()}))\n'
FAKE_KEY_SRC = '"ajapi_"+"a"*32+"_"+"b"*32+"_"+"C"*43'


def make(root, name, main, input_schema=TEXT_IN, output_schema=TEXT_OUT, perms=None, limits=None, **extra):
    d = Path(root)/name
    d.mkdir()
    spec = {'title': 'Test workflow', 'input_schema': input_schema, 'output_schema': output_schema,
            'permissions': {**PERMS, **(perms or {})}, 'limits': {**LIMITS, **(limits or {})}, **extra}
    (d/'workflow.json').write_text(json.dumps(spec))
    (d/'main.py').write_text(main)
    return d


def code(fn):
    try:
        fn()
    except BotError as e:
        return str(e)
    return None


async def acode(coro):
    try:
        await coro
    except BotError as e:
        return str(e)
    return None


def procs_with(marker):
    hits = []
    for pid in filter(str.isdigit, os.listdir('/proc')):
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                if marker.encode() in f.read() and int(pid) != os.getpid():
                    hits.append(int(pid))
        except OSError:
            pass
    return hits


class Base:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='p126-')
        self.root = os.path.join(self.tmp, 'workflows')
        os.mkdir(self.root, 0o700)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class SchemaTest(unittest.TestCase):
    def test_object_must_be_closed(self):
        self.assertEqual(code(lambda: w.check_schema({'type': 'object', 'properties': {}})), 'invalid_schema')
        self.assertEqual(code(lambda: w.check_schema({'type': 'object', 'properties': {}, 'additionalProperties': True})),
                         'invalid_schema')
        self.assertEqual(code(lambda: w.check_schema(OBJ({'a': {'type': 'object', 'properties': {}}}))), 'invalid_schema')
        self.assertIsNone(code(lambda: w.check_schema(TEXT_IN)))

    def test_unknown_keywords_depth_size(self):
        for bad in ({'type': 'string', 'pattern': 'x'}, {'type': 'string', 'format': 'email'}, {'type': ['string', 'null']},
                    {'type': 'array'}, {'type': 'integer', 'minimum': True}, {'type': 'string', 'minLength': 3, 'maxLength': 1},
                    {'type': 'string', 'enum': [1]}, {'$ref': '#'}, OBJ({'a': {'type': 'string'}}, ['b']), 'string'):
            self.assertEqual(code(lambda: w.check_schema(bad)), 'invalid_schema', bad)
        s = {'type': 'string'}
        for _ in range(7):
            s = {'type': 'array', 'items': s}
        self.assertIsNone(code(lambda: w.check_schema(s)))                       # depth 8
        self.assertEqual(code(lambda: w.check_schema({'type': 'array', 'items': s})), 'invalid_schema')
        big = OBJ({f'k{i}': {'type': 'string', 'enum': ['x'*60]} for i in range(300)}, [])
        self.assertEqual(code(lambda: w.check_schema(big)), 'invalid_schema')

    def test_validate(self):
        s = OBJ({'n': {'type': 'integer', 'minimum': 1, 'maximum': 5}, 'tags': {'type': 'array', 'items': {'type': 'string'},
                 'maxItems': 2}, 'mode': {'type': 'string', 'enum': ['a', 'b']}, 'x': {'type': 'number'}, 'z': {'type': 'null'}},
                ['n'])
        self.assertIsNone(code(lambda: w.validate(s, {'n': 3, 'tags': ['a'], 'mode': 'b', 'x': 1.5, 'z': None})))
        secret = 'value-never-echoed'
        for bad in ({'n': True}, {'n': 9}, {'n': 1, 'tags': ['a', 'b', 'c']}, {'n': 1, 'mode': 'c'}, {}, {'n': 1, 'extra': secret},
                    {'n': 1, 'x': float('nan')}, {'n': 1, 'tags': [secret, 2]}, [1]):
            try:
                w.validate(s, bad)
            except BotError as e:
                self.assertEqual(str(e), 'schema_mismatch')
                self.assertNotIn(secret, repr(e))
            else:
                self.fail(bad)


class DefinitionTest(Base, unittest.TestCase):
    def test_load_and_digest(self):
        d = make(self.root, 'echo', ECHO)
        definition = w.load(self.root, 'echo')
        self.assertEqual(definition['title'], 'Test workflow')
        self.assertFalse(definition['knowledge'])
        first = w.digest(self.root, 'echo')
        self.assertEqual(first, w.digest(self.root, 'echo'))
        (d/'main.py').write_text(ECHO + '# changed\n')
        second = w.digest(self.root, 'echo')
        self.assertNotEqual(first, second)
        (d/'knowledge').mkdir()
        (d/'knowledge'/'a.txt').write_text('x')
        self.assertNotEqual(second, w.digest(self.root, 'echo'))

    def test_refusals(self):
        self.assertEqual(code(lambda: w.load(self.root, 'missing')), 'unknown_workflow')
        self.assertEqual(code(lambda: w.load(self.root, '../etc')), 'invalid_name')
        d = make(self.root, 'linky', ECHO)
        os.symlink(os.path.expanduser('~'), d/'home')
        self.assertEqual(code(lambda: w.load(self.root, 'linky')), 'unsafe_workflow')
        self.assertEqual(code(lambda: w.digest(self.root, 'linky')), 'unsafe_workflow')
        make(self.root, 'real', ECHO)
        os.symlink(os.path.join(self.root, 'real'), os.path.join(self.root, 'alias'))
        self.assertEqual(code(lambda: w.load(self.root, 'alias')), 'unsafe_workflow')
        d = make(self.root, 'big', ECHO)
        (d/'blob.bin').write_bytes(b'x' * (w.MAX_FILE + 1))
        self.assertEqual(code(lambda: w.load(self.root, 'big')), 'workflow_too_large')
        d = make(self.root, 'many', ECHO)
        for i in range(w.MAX_FILES):
            (d/f'f{i}.txt').write_text('x')
        self.assertEqual(code(lambda: w.load(self.root, 'many')), 'workflow_too_large')
        make(self.root, 'tools', ECHO, perms={'tools': ['shell']})
        self.assertEqual(code(lambda: w.load(self.root, 'tools')), 'workflow_permission_unsupported')
        make(self.root, 'net', ECHO, perms={'network': ['example.com']})
        self.assertEqual(code(lambda: w.load(self.root, 'net')), 'workflow_permission_unsupported')
        make(self.root, 'extra', ECHO, model='gpt')
        self.assertEqual(code(lambda: w.load(self.root, 'extra')), 'invalid_workflow')
        make(self.root, 'slow', ECHO, limits={'timeout_seconds': 601})
        self.assertEqual(code(lambda: w.load(self.root, 'slow')), 'invalid_workflow')
        make(self.root, 'open', ECHO, input_schema={'type': 'object', 'properties': {}, 'additionalProperties': True})
        self.assertEqual(code(lambda: w.load(self.root, 'open')), 'invalid_schema')
        d = make(self.root, 'nomain', ECHO)
        (d/'main.py').unlink()
        self.assertEqual(code(lambda: w.load(self.root, 'nomain')), 'invalid_workflow')


class QualifyTest(unittest.TestCase):
    def setUp(self):
        self.saved = w._QUALIFIED

    def tearDown(self):
        w._QUALIFIED = self.saved

    def test_platforms(self):
        w._QUALIFIED = None
        with mock.patch.object(sys, 'platform', 'darwin'):
            self.assertEqual(w.qualify(), {'supported': False, 'platform': 'darwin', 'reason': 'no_verified_sandbox'})
        w._QUALIFIED = None
        with mock.patch.object(w, '_bwrap', return_value=None):
            if sys.platform == 'linux':
                self.assertEqual(w.qualify()['reason'], 'bwrap_missing')

    @unittest.skipUnless(sys.platform == 'linux' and w._bwrap(), 'bubblewrap absent: no verified sandbox here')
    def test_linux_supported_and_probe_has_teeth(self):
        w._QUALIFIED = None
        q = w.qualify()
        self.assertEqual(q, {'supported': True, 'platform': 'linux', 'reason': None}, q)
        self.assertIs(w.qualify()['supported'], True)                           # cached
        real = w._argv

        def weaken(drop=(), add=()):
            def f(*a):
                opts, cmd = real(*a)
                opts = [x for x in opts if x not in drop]
                return opts[:1] + list(add) + opts[1:], cmd
            return f
        with mock.patch.object(w, '_argv', weaken(add=['--share-net'])):
            self.assertEqual(w._probe(), 'probe_tcp')
        with mock.patch.object(w, '_argv', weaken(drop=['--clearenv'])):
            self.assertEqual(w._probe(), 'probe_env')

        def leak_home(*a):
            opts, cmd = real(*a)
            home = os.path.expanduser('~')
            return opts[:-4] + ['--ro-bind', home, home] + opts[-4:], cmd
        with mock.patch.object(w, '_argv', leak_home):
            self.assertEqual(w._probe(), 'probe_home')

    def test_unsupported_run_refused(self):
        w._QUALIFIED = {'supported': False, 'platform': 'x', 'reason': 'no_verified_sandbox'}
        self.assertEqual(asyncio.run(acode(w.run('/nonexistent', 'echo', {'text': 'a'}))), 'workflow_unsupported')


Q = w.qualify()


@unittest.skipUnless(Q['supported'], f"no verified workflow sandbox on this host: {Q['reason']}")
class RunTest(Base, unittest.IsolatedAsyncioTestCase):
    async def test_echo_and_schema_both_ways(self):
        make(self.root, 'echo', ECHO)
        self.assertEqual(await w.run(self.root, 'echo', {'text': 'hi'}), {'output': {'text': 'HI'}, 'files': []})
        self.assertEqual(await acode(w.run(self.root, 'echo', {'text': 5})), 'schema_mismatch')
        self.assertEqual(await acode(w.run(self.root, 'echo', {'text': 'a', 'more': 1})), 'schema_mismatch')
        make(self.root, 'wrong', 'print(\'{"wrong": 1}\')')
        self.assertEqual(await acode(w.run(self.root, 'wrong', {'text': 'a'})), 'schema_mismatch')
        make(self.root, 'garbage', 'print("not json")')
        self.assertEqual(await acode(w.run(self.root, 'garbage', {'text': 'a'})), 'workflow_bad_output')
        make(self.root, 'boom', 'import sys\nsys.stderr.write("stderr-detail-xyz")\nsys.exit(3)\n')
        try:
            await w.run(self.root, 'boom', {'text': 'a'})
        except BotError as e:
            self.assertEqual(str(e), 'workflow_failed')
            self.assertNotIn('stderr-detail-xyz', repr(e))
        else:
            self.fail('no error')

    async def test_isolation(self):
        report = OBJ({k: {'type': 'boolean'} for k in ('home', 'home_list', 'env', 'tcp', 'unix', 'write_workflow',
                                                        'write_host', 'write_root', 'tmp', 'host_proc', 'out', 'knowledge')})
        params = OBJ({'home': {'type': 'string'}, 'port': {'type': 'integer'}, 'unix': {'type': 'string'},
                      'host': {'type': 'string'}, 'canary': {'type': 'string'}, 'pid': {'type': 'integer'}})
        main = r'''
import json, os, socket, sys
p = json.load(sys.stdin); r = {}
def t(k, f):
    try:
        v = f(); r[k] = v is not False
    except Exception:
        r[k] = False
def env():
    blobs = [repr(dict(os.environ))]
    for pid in os.listdir('/proc'):
        if pid.isdigit():
            try: blobs.append(open('/proc/%s/environ' % pid, 'rb').read().decode('latin-1'))
            except OSError: pass
    return any(p['canary'] in b for b in blobs)
t('home', lambda: open(p['home']).read())
t('home_list', lambda: os.listdir(os.path.dirname(p['home'])))
t('env', env)
t('tcp', lambda: socket.create_connection(('127.0.0.1', p['port']), timeout=2).close())
def unix():
    s = socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect(p['unix']); s.close()
t('unix', unix)
t('write_workflow', lambda: open('/workflow/x', 'w').write('x'))
t('write_host', lambda: open(p['host'], 'w').write('x'))
t('write_root', lambda: open('/x', 'w').write('x'))
t('tmp', lambda: len(os.listdir('/tmp')) > 0)
t('host_proc', lambda: any(b'unittest' in open('/proc/%s/cmdline' % q, 'rb').read() for q in os.listdir('/proc') if q.isdigit()))
t('out', lambda: open('/out/x', 'w').write('x'))
t('knowledge', lambda: bool(os.path.exists('/knowledge') or os.listdir('/workflow/knowledge')))
print(json.dumps(r))
'''
        d = make(self.root, 'evil', main, input_schema=params, output_schema=report)
        (d/'knowledge').mkdir()
        (d/'knowledge'/'secret.txt').write_text('k')
        canary = 'P126_CANARY_' + uuid.uuid4().hex
        fd, marker = tempfile.mkstemp(prefix='.p126-marker-', dir=os.path.expanduser('~'))
        os.close(fd)
        hostmark = tempfile.mkstemp(dir='/tmp', prefix='p126-')[1] if os.access('/tmp', os.W_OK) else None
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(4)
        srv.setblocking(False)
        usock = socket.socket(socket.AF_UNIX)
        usock.bind(os.path.join(self.tmp, 'u.sock'))
        usock.listen(4)
        try:
            with mock.patch.dict(os.environ, {canary: canary, 'P126_ENV': canary}):
                res = await w.run(self.root, 'evil', {'home': marker, 'port': srv.getsockname()[1], 'unix': os.path.join(self.tmp, 'u.sock'),
                                                      'host': os.path.join(self.tmp, 'written'), 'canary': canary, 'pid': os.getpid()})
            self.assertEqual(res['output'], {k: False for k in report['properties']}, res)
            self.assertFalse(os.path.exists(os.path.join(self.tmp, 'written')))
            self.assertFalse((d/'x').exists())
            with self.assertRaises(BlockingIOError):
                srv.accept()
        finally:
            srv.close()
            usock.close()
            os.unlink(marker)
            if hostmark:
                os.unlink(hostmark)

    async def test_knowledge_and_out_only_when_permitted(self):
        main = r'''
import json, os, sys
json.load(sys.stdin)
def w():
    try: open('/out/result.txt', 'w').write('hello'); return True
    except OSError: return False
print(json.dumps({"k": os.path.exists('/knowledge/a.txt'), "kw": os.path.exists('/workflow/knowledge/a.txt'), "out": w()}))
'''
        out = OBJ({'k': {'type': 'boolean'}, 'kw': {'type': 'boolean'}, 'out': {'type': 'boolean'}})
        for name, perms, expect in (('closed', {}, {'k': False, 'kw': False, 'out': False}),
                                    ('open', {'knowledge_read': True, 'output_files': True}, {'k': True, 'kw': False, 'out': True})):
            d = make(self.root, name, main, input_schema=OBJ({}), output_schema=out, perms=perms)
            (d/'knowledge').mkdir()
            (d/'knowledge'/'a.txt').write_text('fact')
            res = await w.run(self.root, name, {})
            self.assertEqual(res['output'], expect)
            if expect['out']:
                self.assertEqual(res['files'], [{'name': 'result.txt', 'bytes': 5,
                                                 'sha256': '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'}])
            else:
                self.assertEqual(res['files'], [])

    async def test_deadlines(self):
        make(self.root, 'spin', 'while True: pass\n', input_schema=OBJ({}), limits={'timeout_seconds': 1})
        t = time.monotonic()
        self.assertEqual(await acode(w.run(self.root, 'spin', {})), 'deadline')
        self.assertLess(time.monotonic() - t, 8)
        make(self.root, 'nap', 'import time\ntime.sleep(60)\n', input_schema=OBJ({}))
        t = time.monotonic()
        self.assertEqual(await acode(w.run(self.root, 'nap', {}, deadline_seconds=0.5)), 'deadline')
        self.assertLess(time.monotonic() - t, 8)

    async def test_cancel_kills_grandchild(self):
        marker = 'p126grandchild' + uuid.uuid4().hex
        main = r'''
import json, os, sys, time
m = json.load(sys.stdin)["text"]
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        os.execv('/usr/bin/python3', ['python3', '-I', '-S', '-c', 'import time; time.sleep(300)', m])
    os._exit(0)
time.sleep(300)
'''
        make(self.root, 'fork', main)
        before = set(os.listdir(tempfile.gettempdir()))
        task = asyncio.create_task(w.run(self.root, 'fork', {'text': marker}))
        for _ in range(200):
            if procs_with(marker):
                break
            await asyncio.sleep(0.05)
        self.assertTrue(procs_with(marker), 'grandchild never started')
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        for _ in range(100):
            if not procs_with(marker):
                break
            await asyncio.sleep(0.05)
        self.assertEqual(procs_with(marker), [])
        leftover = {n for n in set(os.listdir(tempfile.gettempdir())) - before if n.startswith('agentj-wf-')}
        self.assertEqual(leftover, set())

    async def test_resource_and_output_limits(self):
        make(self.root, 'hog', 'import json,sys\njson.load(sys.stdin)\nx = bytearray(512 << 20)\nprint("{}")\n',
             input_schema=OBJ({}), output_schema=OBJ({}), limits={'memory_mb': 64})
        self.assertEqual(await acode(w.run(self.root, 'hog', {})), 'workflow_failed')
        make(self.root, 'loud', 'import json\nprint(json.dumps({"text": "a" * 1000}))\n', input_schema=OBJ({}),
             limits={'output_bytes': 100})
        self.assertEqual(await acode(w.run(self.root, 'loud', {})), 'workflow_output_limit')
        make(self.root, 'flood', 'import sys\nwhile True: sys.stdout.write("x" * 4096)\n', input_schema=OBJ({}),
             limits={'output_bytes': 1000})
        self.assertEqual(await acode(w.run(self.root, 'flood', {})), 'workflow_output_limit')
        main = 'import json, os\nfor i in range(25): open("/out/f%d" % i, "w").write("x")\nprint("{}")\n'
        make(self.root, 'files', main, input_schema=OBJ({}), output_schema=OBJ({}), perms={'output_files': True})
        self.assertEqual(await acode(w.run(self.root, 'files', {})), 'workflow_output_limit')
        main = 'import os\nos.symlink("/etc/passwd", "/out/link")\nprint("{}")\n'
        make(self.root, 'outlink', main, input_schema=OBJ({}), output_schema=OBJ({}), perms={'output_files': True})
        self.assertEqual(await acode(w.run(self.root, 'outlink', {})), 'unsafe_output')

    async def test_secrets_refused(self):
        make(self.root, 'leak', f'import json\nprint(json.dumps({{"text": "here: " + {FAKE_KEY_SRC}}}))\n', input_schema=OBJ({}))
        self.assertEqual(await acode(w.run(self.root, 'leak', {})), 'safety_refused')
        make(self.root, 'echo', ECHO)
        fake = 'ajapi_' + 'a'*32 + '_' + 'b'*32 + '_' + 'C'*43
        self.assertEqual(await acode(w.run(self.root, 'echo', {'text': 'key ' + fake})), 'invalid_input')

    async def test_refused_before_start(self):
        make(self.root, 'net', ECHO, perms={'network': ['api.example.com']})
        self.assertEqual(await acode(w.run(self.root, 'net', {'text': 'a'})), 'workflow_permission_unsupported')
        d = make(self.root, 'linky', ECHO)
        os.symlink('/etc', d/'etc')
        self.assertEqual(await acode(w.run(self.root, 'linky', {'text': 'a'})), 'unsafe_workflow')


if __name__ == '__main__':
    unittest.main()
