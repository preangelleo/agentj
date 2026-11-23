"""0371 upgrader bootstrap — contract and security tests written before the implementation.

The design: an upgrade is prepared and installed by the upgrade executor that ships inside the *target* wheel, so a fix to
the executor reaches hosts that still run an older version. The running (old) version keeps three duties only: it verifies
the wheel's signature and version, it starts the executor from those verified bytes in an isolated child interpreter
(`python -I -S -B <private folder>`), and it rolls back and reports when that fails.

Two pieces do not exist yet; every test that needs one is skipped with WAITING until it does (no decorator to remove):

- the hand-over side `agentj/upgrade_bootstrap.py` — `run`, `members`, `extract`, `negotiate`, `parse_result`,
  `child_env`, `child_argv` and the constants used below;
- the executor folder `agentj/upgrader/` — `manifest.json`, `__main__.py`, `ajx_*.py`, standard library only.

The fixture (`FakeRelease`) builds signed stand-in wheels in a throw-away home with a signing key made in memory for one
test; the two fixture self-tests and the import guard at the end run against today's code.
"""
import _hermetic  # noqa: F401
import ast
import base64
import hashlib
import importlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from _testtmp import TEST_TMP
from agentj import auto_update
from agentj.state import State

HOST = Path(__file__).resolve().parents[1]
PACKAGE = HOST / 'agentj'
EXECUTOR = PACKAGE / 'upgrader'
CALLER = 'agentj.upgrade_bootstrap'
WAITING = 'waiting for the 0371 implementation (documentation/design/upgrader-bootstrap.md)'
PREFIX = 'agentj/upgrader/'
CURRENT, TARGET = '98.0.0', '99.0.1'


def caller():
    try:
        return importlib.import_module(CALLER)
    except ModuleNotFoundError as error:
        if error.name != CALLER:
            raise
        raise unittest.SkipTest(WAITING + ': ' + CALLER) from None


def executor_sources():
    if not (EXECUTOR / '__main__.py').is_file():
        raise unittest.SkipTest(WAITING + ': agentj/upgrader/')
    return sorted(EXECUTOR.glob('*.py'))


# What the stand-in executor does is fixed when its wheel is built. It writes what it saw to <request.store>/observed.json.
FAKE_MAIN = r'''
import json, os, signal, subprocess, sys, time
BEHAVIOUR = %(behaviour)r
request = json.loads(sys.stdin.buffer.read())
here = sys.path[0]
mask = os.umask(0o077); os.umask(mask)
loaded = {}
for name in ('agentj', 'cryptography', 'websockets'):
    try:
        __import__(name); loaded[name] = True
    except ImportError:
        loaded[name] = False
seen = {'behaviour': BEHAVIOUR, 'argv': sys.argv, 'path': sys.path, 'isolated': sys.flags.isolated,
        'no_site': sys.flags.no_site, 'no_bytecode': sys.flags.dont_write_bytecode, 'env': dict(os.environ),
        'umask': mask, 'cwd': os.getcwd(), 'request': request, 'loaded': loaded, 'leader': os.getpgid(0) == os.getpid(),
        'fds': sorted(int(n) for n in os.listdir('/proc/self/fd')) if os.path.isdir('/proc/self/fd') else None,
        'folder_mode': os.stat(here).st_mode & 0o7777, 'cwd_mode': os.stat('.').st_mode & 0o7777,
        'files': {n: os.stat(os.path.join(here, n)).st_mode & 0o7777 for n in os.listdir(here)}}
def note():
    with open(os.path.join(request['store'], 'observed.json'), 'w') as out:
        json.dump(seen, out)
def answer(code, **fields):
    sys.stdout.write(json.dumps({'contract': request['contract'], 'op': request['op'], **fields}) + '\n')
    sys.stdout.flush()
    os._exit(code)
if BEHAVIOUR == 'hang':
    child = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import time; time.sleep(45)'])
    seen['child'] = child.pid
    note()
    time.sleep(45)
note()
if BEHAVIOUR == 'ok':
    answer(0, result='ok', reason='prepared', live_touched=False, rollback_ready=True, retry_count=2,
           diag={'dependency_phase': 'download', 'elapsed_s': 7, 'cache_rebuilt': True}, later_field={'x': 1})
if BEHAVIOUR == 'failed':
    answer(1, result='failed', reason='dependency_download_failed', live_touched=False, diag={'uv_failure_class': 'timeout'})
if BEHAVIOUR == 'failed_silent':
    answer(1, result='failed', reason='uv_failed')
if BEHAVIOUR == 'refused':
    answer(3, result='refused', reason='contract_unsupported', live_touched=False)
if BEHAVIOUR == 'kill':
    os.kill(os.getpid(), signal.SIGKILL)
if BEHAVIOUR == 'exit70':
    os._exit(70)
if BEHAVIOUR == 'traceback':
    raise RuntimeError('stand-in executor bug')
if BEHAVIOUR == 'garbage':
    sys.stdout.write('Resolved 12 packages in 4ms\n'); sys.stdout.flush(); os._exit(0)
if BEHAVIOUR == 'flood':
    sys.stdout.write('x' * (4 << 20)); sys.stdout.flush(); os._exit(0)
'''
FAKE_PACKAGE = 'open(%(marker)r, "w").write("the target package was imported")\n'


class FakeRelease:
    """A throw-away home with the two folders the hand-over reads and writes, and signed stand-in wheels.

    `cache` is the download cache root (0700; wheels and receipts in `upgrade-cache/`, as auto_update._store leaves
    them), `state` the state directory that holds `install-artifacts/`, `seen` where the stand-in executor reports.
    The signing key is generated here and never written anywhere.
    """

    def __init__(self, case):
        folder = tempfile.TemporaryDirectory(dir=TEST_TMP, prefix='aj0371-')
        case.addCleanup(folder.cleanup)
        self.home = Path(folder.name) / 'home'
        self.cache = self.home / '.cache' / 'agentj-upgrade'
        self.state = self.home / '.local' / 'state' / 'agentj'
        self.live = self.home / '.local' / 'share' / 'uv' / 'tools' / 'agentj'
        self.seen = Path(folder.name) / 'seen'
        self.marker = Path(folder.name) / 'imported'
        for path in (self.cache / 'upgrade-cache', self.state, self.live, self.seen):
            path.mkdir(parents=True, mode=0o700)
            path.chmod(0o700)
        self.cache.chmod(0o700)
        (self.live / 'installed-version').write_text(CURRENT)
        self.key, self.key_id = Ed25519PrivateKey.generate(), os.urandom(8)
        public = base64.b64encode(b'Ed' + self.key_id + self.key.public_key().public_bytes_raw()).decode()
        self.keyring = {auto_update.minisign.key_id_hex(self.key_id): public}
        trust = mock.patch.object(auto_update.minisign, 'keyring', lambda role: dict(self.keyring))
        trust.start()
        case.addCleanup(trust.stop)
        home = mock.patch.dict(os.environ, {'HOME': str(self.home)})
        home.start()
        case.addCleanup(home.stop)

    def wheel(self, version=TARGET, behaviour='ok', *, executor=True, manifest=None, members=(), tail=b''):
        """`members`: extra (name or ZipInfo, bytes) entries, written as given. `tail`: bytes after the archive."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f'agentj-{version}.dist-info/METADATA', f'Metadata-Version: 2.1\nName: agentj\nVersion: {version}\n')
            archive.writestr('agentj/__init__.py', FAKE_PACKAGE % {'marker': str(self.marker)})
            archive.writestr('sitecustomize.py', FAKE_PACKAGE % {'marker': str(self.marker)})
            if executor:
                archive.writestr(PREFIX + 'manifest.json', json.dumps(
                    {'contract_min': 1, 'contract_max': 1, 'entry': '__main__.py'} if manifest is None else manifest))
                archive.writestr(PREFIX + '__main__.py', FAKE_MAIN % {'behaviour': behaviour})
                archive.writestr(PREFIX + 'ajx_helper.py', 'NAME = "helper"\n')
            for name, body in members:
                archive.writestr(name, body)
        return buffer.getvalue() + tail

    def signature(self, body, version, key=None, key_id=None):
        key, key_id = key or self.key, key_id or self.key_id
        signed = key.sign(hashlib.blake2b(body, digest_size=64).digest())
        comment = f'agentj-host/v1 version={version} sha256={hashlib.sha256(body).hexdigest()}'
        return ('untrusted comment: fixture\n' + base64.b64encode(b'ED' + key_id + signed).decode() + '\ntrusted comment: '
                + comment + '\n' + base64.b64encode(key.sign(signed + comment.encode())).decode() + '\n')

    def paths(self, version=TARGET):
        wheel = self.cache / 'upgrade-cache' / f'agentj-{version}-py3-none-any.whl'
        return wheel, wheel.with_suffix('.verified.json')

    def publish(self, body, version=TARGET, *, signature=None, receipt=None):
        """The cache as a finished, verified download leaves it: the wheel and its receipt, both 0600."""
        wheel, note = self.paths(version)
        record = {'sha256': hashlib.sha256(body).hexdigest(), 'signed': True,
                  'signature': self.signature(body, version) if signature is None else signature, **(receipt or {})}
        for path, data in ((wheel, body), (note, json.dumps(record).encode())):
            path.unlink(missing_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as out:
                out.write(data)
        return body

    def observed(self):
        try:
            return json.loads((self.seen / 'observed.json').read_text())
        except FileNotFoundError:
            return None

    def tree(self, outside_store=False):
        """Every path under the fake home with its type, mode and bytes — the old version and its state, untouched?
        `outside_store`: without install-artifacts/, the one folder the hand-over may create."""
        rows, store = {}, self.state / 'install-artifacts'
        for path in sorted(self.home.rglob('*')):
            if outside_store and (path == store or store in path.parents):
                continue
            info = path.lstat()
            rows[str(path.relative_to(self.home))] = (stat.S_IFMT(info.st_mode), info.st_mode & 0o7777,
                                                     path.read_bytes() if stat.S_ISREG(info.st_mode) else None)
        return rows

    def left_behind(self):
        """Files (not folders) still under install-artifacts/: the executor copy and its scratch must be gone."""
        return [str(p) for p in (self.state / 'install-artifacts').rglob('*') if not p.is_dir()]


class Recorder:
    """Stands in for the running version's own executor (the fallback)."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result or {'result': 'ok', 'reason': 'prepared', 'live_touched': False, 'diag': {'own': True}}

    def __call__(self, op, request):
        self.calls.append((op, request))
        return dict(self.result)


class Launching(unittest.TestCase):
    def setUp(self):
        self.b = caller()
        self.rel = FakeRelease(self)
        self.own = Recorder()

    def run_op(self, op='prepare', *, target=TARGET, current=CURRENT, deadline_s=60, **request):
        return self.b.run(op, cache_root=self.rel.cache, store_root=self.rel.state, target=target, current=current,
                          request={'store': str(self.rel.seen), 'install': {'kind': 'uv', 'prefix': str(self.rel.live)},
                                   **request},
                          deadline_s=deadline_s, own=self.own)

    def never_started(self, outcome, reason):
        self.assertEqual((outcome['result'], outcome['reason'], outcome['launched'], outcome['executor']),
                         ('refused', reason, False, 'none'))
        self.assertIs(outcome['live_touched'], False)
        self.assertIsNone(self.rel.observed(), 'the executor ran')
        self.assertEqual(self.own.calls, [], 'the fallback ran')
        self.assertFalse(self.rel.marker.exists())
        self.assertEqual(self.rel.left_behind(), [])


class FixtureSelfTest(unittest.TestCase):
    """Runs today: the stand-in release is accepted and refused by the verifier the hand-over will use."""

    def test_the_signed_stand_in_passes_todays_sealed_reader(self):
        rel = FakeRelease(self)
        body = rel.publish(rel.wheel())
        self.assertEqual(auto_update.signed_cached_bytes(State(rel.cache), TARGET), body)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertIn(PREFIX + '__main__.py', archive.namelist())

    def test_a_changed_byte_or_a_strange_key_fails_todays_sealed_reader(self):
        rel = FakeRelease(self)
        body = rel.publish(rel.wheel())
        wheel, _ = rel.paths()
        wheel.write_bytes(body[:-1] + bytes([body[-1] ^ 1]))
        self.assertIsNone(auto_update.signed_cached_bytes(State(rel.cache), TARGET))
        stranger = Ed25519PrivateKey.generate()
        rel.publish(body, signature=rel.signature(body, TARGET, stranger, os.urandom(8)))
        self.assertIsNone(auto_update.signed_cached_bytes(State(rel.cache), TARGET))

    def test_the_stand_in_executor_is_standard_library_only_and_runs_isolated(self):
        rel = FakeRelease(self)
        folder = rel.seen / 'upgrader'
        folder.mkdir()
        (folder / '__main__.py').write_text(FAKE_MAIN % {'behaviour': 'ok'})
        request = {'contract': 1, 'op': 'probe', 'store': str(rel.seen)}
        done = subprocess.run([sys.executable, '-I', '-S', '-B', str(folder)], input=json.dumps(request).encode(),
                              capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)['result'], 'ok')
        seen = rel.observed()
        self.assertEqual((seen['isolated'], seen['no_site'], seen['path'][0]), (1, 1, str(folder)))
        self.assertEqual(seen['loaded'], {'agentj': False, 'cryptography': False, 'websockets': False})


class Contract(unittest.TestCase):
    def setUp(self):
        self.b = caller()

    def test_version_one_stays_supported_and_the_names_are_fixed(self):
        self.assertEqual(self.b.CONTRACT_MIN, 1)
        self.assertGreaterEqual(self.b.CONTRACT_MAX, 1)
        self.assertEqual((self.b.PREFIX, self.b.MANIFEST, self.b.ENTRY), (PREFIX, 'manifest.json', '__main__.py'))
        self.assertEqual(tuple(self.b.OPS), ('probe', 'prepare', 'install', 'rollback'))

    def test_the_child_command_is_an_isolated_interpreter_on_one_folder(self):
        self.assertEqual(self.b.child_argv('/opt/py/bin/python3', Path('/s/x/upgrader')),
                         ['/opt/py/bin/python3', '-I', '-S', '-B', '/s/x/upgrader'])

    def test_negotiation_picks_the_highest_common_version(self):
        for low, high, mine, want in ((1, 1, (1, 1), 1), (1, 3, (1, 1), 1), (1, 2, (1, 3), 2), (2, 5, (1, 3), 3)):
            with mock.patch.multiple(self.b, CONTRACT_MIN=mine[0], CONTRACT_MAX=mine[1]):
                self.assertEqual(self.b.negotiate({'contract_min': low, 'contract_max': high, 'entry': '__main__.py'}), want)

    def test_no_common_version_is_the_fallback_reason(self):
        for low, high, mine in ((2, 3, (1, 1)), (1, 1, (2, 4))):
            with mock.patch.multiple(self.b, CONTRACT_MIN=mine[0], CONTRACT_MAX=mine[1]):
                with self.assertRaises(self.b.Refused) as refused:
                    self.b.negotiate({'contract_min': low, 'contract_max': high, 'entry': '__main__.py'})
                self.assertEqual(refused.exception.reason, 'contract_unsupported')
        self.assertIn('contract_unsupported', self.b.FALLBACK)

    def test_a_malformed_manifest_is_refused_not_guessed(self):
        good = {'contract_min': 1, 'contract_max': 1, 'entry': '__main__.py'}
        for bad in ([], 'x', None, {}, {**good, 'contract_min': True}, {**good, 'contract_max': '1'},
                    {**good, 'contract_min': 0}, {**good, 'contract_min': 2}, {**good, 'contract_max': 1.0},
                    {**good, 'contract_max': 10 ** 9}, {**good, 'entry': 'ajx_other.py'}, {**good, 'entry': '../x.py'},
                    {'contract_max': 1, 'entry': '__main__.py'}):
            with self.assertRaises(self.b.Refused, msg=repr(bad)) as refused:
                self.b.negotiate(bad)
            self.assertEqual(refused.exception.reason, 'executor_invalid', repr(bad))
        self.assertNotIn('executor_invalid', self.b.FALLBACK)

    def test_an_unknown_manifest_key_is_ignored(self):
        self.assertEqual(self.b.negotiate({'contract_min': 1, 'contract_max': 1, 'entry': '__main__.py', 'later': [1]}), 1)

    def line(self, **fields):
        return (json.dumps({'contract': 1, 'op': 'prepare', 'result': 'ok', 'reason': 'prepared', **fields}) + '\n').encode()

    def test_a_result_comes_back_with_the_known_fields_only(self):
        got = self.b.parse_result(self.line(live_touched=False, rollback_ready=True, retry_count=3, later={'a': 1},
                                            diag={'dependency_phase': 'resolve', 'elapsed_s': 12, 'rebuilt': True}),
                                  0, op='prepare', contract=1)
        self.assertEqual(got, {'contract': 1, 'op': 'prepare', 'result': 'ok', 'reason': 'prepared', 'live_touched': False,
                               'rollback_ready': True, 'retry_count': 3,
                               'diag': {'dependency_phase': 'resolve', 'elapsed_s': 12, 'rebuilt': True}})

    def test_missing_optional_fields_take_the_cautious_value(self):
        for op, touched in (('probe', False), ('prepare', False), ('install', True), ('rollback', True)):
            got = self.b.parse_result(self.line(op=op, result='failed', reason='uv_failed'), 1, op=op, contract=1)
            self.assertEqual((got['live_touched'], got['rollback_ready'], got['retry_count'], got['diag']),
                             (touched, False, 0, {}), op)
        got = self.b.parse_result(self.line(op='install', result='failed', reason='uv_failed', live_touched='no'), 1,
                                  op='install', contract=1)
        self.assertIs(got['live_touched'], True)

    def test_diagnostic_values_that_could_carry_a_path_or_text_are_dropped(self):
        diag = {'ok_word': 'cache_entry_invalid', 'ok_number': 3, 'ok_flag': False, 'ok_version': '0.14.2+b1',
                'path': '/home/owner/.cache/x', 'text': 'two words', 'mail': 'a@example.com', 'long': 'x' * 65,
                'nested': {'a': 1}, 'list': [1], 'float': 1.5, 'none': None, 'Bad-Key': 'x', 'k' * 33: 'x', 'huge': 2 ** 60}
        got = self.b.parse_result(self.line(diag=diag), 0, op='prepare', contract=1)
        self.assertEqual(got['diag'], {'ok_word': 'cache_entry_invalid', 'ok_number': 3, 'ok_flag': False,
                                       'ok_version': '0.14.2+b1'})
        many = self.b.parse_result(self.line(diag={f'k{i}': i for i in range(500)}), 0, op='prepare', contract=1)
        self.assertLessEqual(len(many['diag']), 40)
        self.assertEqual(self.b.parse_result(self.line(diag='text'), 0, op='prepare', contract=1)['diag'], {})

    def test_output_that_is_not_one_result_is_invalid(self):
        good = self.line()
        cases = {
            'empty': (b'', 0), 'not json': (b'Resolved 12 packages\n', 0), 'a list': (b'[1]\n', 0),
            'two lines': (good + good, 0), 'text before': (b'note\n' + good, 0), 'text after': (good + b'done\n', 0),
            'not utf-8': (b'\xff' + good, 0), 'too large': (good + b' ' * (1 << 20), 0),
            'another contract': (self.line(contract=2), 0), 'contract true': (self.line(contract=True), 0),
            'another op': (self.line(op='install'), 0), 'unknown result': (self.line(result='maybe'), 0),
            'reason with a path': (self.line(reason='/home/owner/x'), 0), 'reason with text': (self.line(reason='it broke'), 0),
            'reason missing': (json.dumps({'contract': 1, 'op': 'prepare', 'result': 'ok'}).encode(), 0),
            'ok but exit 1': (good, 1), 'failed but exit 0': (self.line(result='failed'), 0),
            'refused but exit 0': (self.line(result='refused'), 0), 'failed but exit 3': (self.line(result='failed'), 3),
            'retry count text': (self.line(retry_count='3'), 0), 'retry count negative': (self.line(retry_count=-1), 0),
        }
        for name, (raw, code) in cases.items():
            with self.assertRaises(self.b.Invalid, msg=name):
                self.b.parse_result(raw, code, op='prepare', contract=1)

    def test_exit_codes_agree_with_the_result(self):
        self.assertEqual((self.b.EXIT_OK, self.b.EXIT_FAILED, self.b.EXIT_USAGE, self.b.EXIT_CONTRACT), (0, 1, 2, 3))
        for result, code in (('ok', 0), ('failed', 1), ('refused', 2), ('refused', 3)):
            got = self.b.parse_result(self.line(result=result, reason='some_word'), code, op='prepare', contract=1)
            self.assertEqual(got['result'], result)

    def test_the_child_environment_is_an_allowlist(self):
        scratch = '/s/x/scratch'
        dirty = {'HOME': '/h', 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HTTPS_PROXY': 'http://127.0.0.1:1',
                 'https_proxy': 'http://127.0.0.1:1', 'NO_PROXY': 'localhost', 'SSL_CERT_FILE': '/etc/ca.pem',
                 'XDG_DATA_HOME': '/h/.local/share', 'TMPDIR': '/somewhere/else',
                 'UV_INDEX_URL': 'https://example.com/simple', 'UV_TOOL_DIR': '/elsewhere', 'UV_PYTHON': '3.9',
                 'PIP_INDEX_URL': 'https://example.com/simple', 'PIP_CONFIG_FILE': '/x', 'PYTHONPATH': '/evil',
                 'PYTHONSTARTUP': '/evil.py', 'PYTHONHOME': '/evil', 'PYTHONINSPECT': '1', 'LD_PRELOAD': '/evil.so',
                 'LD_LIBRARY_PATH': '/evil', 'DYLD_INSERT_LIBRARIES': '/evil.dylib', 'VIRTUAL_ENV': '/venv',
                 'CONDA_PREFIX': '/conda', 'AGENTJ_STATE_DIR': '/state', 'AGENTJ_UPDATE_URL': 'http://127.0.0.1:9',
                 'AGENTJ_JOB_LABEL': 'x', 'CLAUDECODE': '1', 'GIT_DIR': '/g', 'BASH_ENV': '/evil.sh', 'ENV': '/evil.sh',
                 'AJ0371_CANARY': 'not for the new code', 'SSH_AUTH_SOCK': '/run/agent', 'DBUS_SESSION_BUS_ADDRESS': 'unix:x'}
        env = self.b.child_env(dirty, scratch)
        self.assertLessEqual(set(env), set(self.b.ENV_ALLOW))
        for name in ('HOME', 'PATH', 'HTTPS_PROXY', 'https_proxy', 'NO_PROXY', 'SSL_CERT_FILE', 'XDG_DATA_HOME'):
            self.assertEqual(env[name], dirty[name], name)
        self.assertEqual((env['TMPDIR'], env['TEMP'], env['TMP']), (scratch,) * 3)
        for name in env:
            self.assertFalse(name.startswith(('UV_', 'PIP_', 'PYTHON', 'LD_', 'DYLD_', 'AGENTJ_', 'CONDA', 'GIT_')), name)
        for name in self.b.ENV_ALLOW:
            self.assertFalse(name.startswith(('UV_', 'PIP_', 'PYTHON', 'LD_', 'DYLD_', 'AGENTJ_', 'CONDA', 'GIT_')), name)
        self.assertNotIn('AJ0371_CANARY', env)
        self.assertNotIn('SSH_AUTH_SOCK', env)


class Extraction(unittest.TestCase):
    """The executor's files come out of the verified bytes by a fixed list of names; nothing is ever unpacked by path."""

    def setUp(self):
        self.b = caller()
        self.rel = FakeRelease(self)

    def refused(self, body, reason='executor_invalid'):
        with self.assertRaises(self.b.Refused) as refused:
            self.b.members(body)
        self.assertEqual(refused.exception.reason, reason)

    def test_the_executor_files_and_nothing_else(self):
        files = self.b.members(self.rel.wheel())
        self.assertEqual(sorted(files), ['__main__.py', 'ajx_helper.py', 'manifest.json'])
        self.assertEqual(files['ajx_helper.py'], b'NAME = "helper"\n')

    def test_a_wheel_without_an_executor_is_the_fallback_reason(self):
        self.refused(self.rel.wheel(executor=False), 'executor_missing')
        self.assertIn('executor_missing', self.b.FALLBACK)

    def test_names_that_leave_the_folder_are_refused(self):
        for name in (PREFIX + '../ajx_up.py', PREFIX + '../../sitecustomize.py', PREFIX + 'sub/ajx_deep.py',
                     PREFIX + '/ajx_abs.py', PREFIX + './ajx_dot.py', PREFIX + 'ajx_a.py/..', PREFIX + '..',
                     'agentj/upgrader\\ajx_back.py', 'agentj//upgrader/ajx_twice.py', './agentj/upgrader/ajx_rel.py',
                     'agentj/./upgrader/ajx_mid.py', 'agentj/other/../upgrader/ajx_round.py', 'AGENTJ/upgrader/ajx_case.py',
                     PREFIX + 'AJX_HELPER.py', PREFIX + 'json.py', PREFIX + 'ajx_.py', PREFIX + 'ajx_a.pyc',
                     PREFIX + 'ajx_a.so', PREFIX + '__init__.py', PREFIX + 'sitecustomize.py', PREFIX + 'ajx_é.py',
                     PREFIX + 'ajx_' + 'a' * 60 + '.py'):
            with self.subTest(name=name):
                self.refused(self.rel.wheel(members=[(zipfile.ZipInfo(name), b'X = 1\n')]))

    def test_a_link_an_encrypted_or_a_repeated_member_is_refused(self):
        link = zipfile.ZipInfo(PREFIX + 'ajx_link.py')
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.refused(self.rel.wheel(members=[(link, b'/etc/passwd')]))
        folder = zipfile.ZipInfo(PREFIX + 'ajx_dir.py')
        folder.external_attr = (stat.S_IFDIR | 0o755) << 16
        self.refused(self.rel.wheel(members=[(folder, b'')]))
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            self.refused(self.rel.wheel(members=[(PREFIX + 'ajx_helper.py', b'NAME = "second"\n')]))
            self.refused(self.rel.wheel(members=[(PREFIX + '__main__.py', b'raise SystemExit(9)\n')]))
        body = bytearray(self.rel.wheel())
        at = bytes(body).find(b'PK\x01\x02')
        self.assertNotEqual(at, -1)
        while at != -1:                                  # set the encrypted bit of every central directory entry
            body[at + 8] |= 1
            at = bytes(body).find(b'PK\x01\x02', at + 4)
        self.refused(bytes(body))

    def test_size_and_count_limits(self):
        self.refused(self.rel.wheel(members=[(PREFIX + 'ajx_big.py', b'#' * (self.b.MAX_FILE + 1))]))
        self.refused(self.rel.wheel(members=[(PREFIX + f'ajx_n{i}.py', b'X = 1\n') for i in range(self.b.MAX_FILES)]))
        self.assertLessEqual(self.b.MAX_FILE, 1 << 20)
        self.assertLessEqual(self.b.MAX_FILES, 64)

    def test_an_executor_without_its_entry_or_manifest_is_refused(self):
        for missing in ('__main__.py', 'manifest.json'):
            buffer = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(self.rel.wheel())) as src, zipfile.ZipFile(buffer, 'w') as dst:
                for item in src.infolist():
                    if item.filename != PREFIX + missing:
                        dst.writestr(item, src.read(item))
            self.refused(buffer.getvalue())

    def test_not_a_zip_is_refused(self):
        self.refused(b'not a wheel at all')

    def test_extraction_writes_a_fresh_private_folder(self):
        parent = self.rel.state / 'install-artifacts' / 'executor'
        parent.mkdir(parents=True, mode=0o700)
        with mock.patch.object(os, 'umask', wraps=os.umask):
            before = os.umask(0o002)                    # the 0.30.47-0.30.65 fault: modes must not follow the process
            try:
                folder = self.b.extract(self.rel.wheel(), parent)
                second = self.b.extract(self.rel.wheel(), parent)
            finally:
                os.umask(before)
        self.assertNotEqual(folder, second)
        self.assertEqual(folder.name, 'upgrader')
        self.assertEqual(folder.parent.parent, parent)
        for path in (folder, folder.parent):
            info = path.lstat()
            self.assertTrue(stat.S_ISDIR(info.st_mode))
            self.assertEqual((info.st_mode & 0o7777, info.st_uid), (0o700, os.getuid()))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['__main__.py', 'ajx_helper.py', 'manifest.json'])
        for path in folder.iterdir():
            info = path.lstat()
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual((info.st_mode & 0o7777, info.st_nlink), (0o400, 1))

    def test_extraction_refuses_a_parent_someone_else_could_write(self):
        parent = self.rel.state / 'install-artifacts' / 'executor'
        parent.mkdir(parents=True, mode=0o700)
        parent.chmod(0o770)
        with self.assertRaises(self.b.Refused) as refused:
            self.b.extract(self.rel.wheel(), parent)
        self.assertEqual(refused.exception.reason, 'install_store_unsafe')
        parent.chmod(0o700)
        real = self.rel.state / 'elsewhere'
        real.mkdir(mode=0o700)
        parent.rmdir()
        parent.symlink_to(real)
        with self.assertRaises(self.b.Refused):
            self.b.extract(self.rel.wheel(), parent)
        self.assertEqual(list(real.iterdir()), [])


class Verification(Launching):
    """Nothing from a wheel runs unless this process just verified the signature over all of its bytes."""

    def test_a_verified_newer_wheel_runs_its_own_executor(self):
        self.rel.publish(self.rel.wheel())
        before = self.rel.tree(outside_store=True)
        outcome = self.run_op('prepare', previous={'version': CURRENT})
        self.assertEqual(outcome, {'executor': 'target', 'fallback': None, 'launched': True, 'contract': 1, 'op': 'prepare',
                                   'result': 'ok', 'reason': 'prepared', 'live_touched': False, 'rollback_ready': True,
                                   'retry_count': 2,
                                   'diag': {'dependency_phase': 'download', 'elapsed_s': 7, 'cache_rebuilt': True}})
        seen = self.rel.observed()
        request = seen['request']
        self.assertEqual((request['contract'], request['op'], request['caller'], request['deadline_s']),
                         (1, 'prepare', CURRENT, 60))
        self.assertEqual((request['store'], request['install'], request['previous']),
                         (str(self.rel.seen), {'kind': 'uv', 'prefix': str(self.rel.live)}, {'version': CURRENT}))
        self.assertEqual(request['target']['version'], TARGET)
        self.assertEqual(request['target']['sha256'], hashlib.sha256(self.rel.paths()[0].read_bytes()).hexdigest())
        self.assertEqual(self.own.calls, [])
        self.assertEqual(self.rel.left_behind(), [])
        self.assertEqual(self.rel.tree(outside_store=True), before)

    def test_a_changed_wheel_never_starts_the_executor(self):
        body = self.rel.wheel()
        changed = body[:200] + bytes([body[200] ^ 0x20]) + body[201:]
        for name, prepare in {
            'a byte changed after the download': lambda: (self.rel.publish(body), self.rel.paths()[0].write_bytes(changed)),
            'bytes added after the archive': lambda: (self.rel.publish(body), self.rel.paths()[0].write_bytes(body + b'PK\x05\x06' + bytes(18))),
            'the receipt re-hashed for the changed bytes': lambda: self.rel.publish(changed, signature=self.rel.signature(body, TARGET)),
            'a second executor appended as a new archive': lambda: (
                self.rel.publish(body), self.rel.paths()[0].write_bytes(self.rel.wheel(behaviour='failed'))),
            'cut short': lambda: (self.rel.publish(body), self.rel.paths()[0].write_bytes(body[:-40])),
        }.items():
            with self.subTest(name=name):
                prepare()
                self.never_started(self.run_op(), 'artifact_unverified')

    def test_a_signature_this_host_does_not_trust_never_starts_the_executor(self):
        body = self.rel.wheel()
        stranger = Ed25519PrivateKey.generate()
        other = self.rel.wheel(version='99.0.2')
        for name, kwargs in {
            'signed by a key outside the release keyring': {'signature': self.rel.signature(body, TARGET, stranger, os.urandom(8))},
            'a strange key under the trusted key id': {'signature': self.rel.signature(body, TARGET, stranger, self.rel.key_id)},
            'a signature for another version': {'signature': self.rel.signature(body, '99.0.2')},
            'the signature of another wheel': {'signature': self.rel.signature(other, TARGET)},
            'a receipt that says unsigned': {'receipt': {'signed': False, 'signature': None}},
            'no signature in the receipt': {'receipt': {'signature': ''}},
            'the legacy un-prehashed form': {'signature': self.rel.signature(body, TARGET).replace(
                base64.b64encode(b'ED').decode()[:2], base64.b64encode(b'Ed').decode()[:2], 1)},
        }.items():
            with self.subTest(name=name):
                self.rel.publish(body, **kwargs)
                self.never_started(self.run_op(), 'artifact_unverified')

    def test_a_revoked_key_never_starts_the_executor(self):
        self.rel.publish(self.rel.wheel())
        revoked = frozenset({auto_update.minisign.key_id_hex(self.rel.key_id)})
        with mock.patch.object(auto_update.minisign, 'REVOKED', revoked):
            self.never_started(self.run_op(), 'artifact_unverified')

    def test_a_missing_wheel_or_receipt_never_starts_the_executor(self):
        self.never_started(self.run_op(), 'artifact_unverified')
        self.rel.publish(self.rel.wheel())
        self.rel.paths()[1].unlink()
        self.never_started(self.run_op(), 'artifact_unverified')

    def test_a_cache_other_accounts_could_write_never_starts_the_executor(self):
        self.rel.publish(self.rel.wheel())
        folder = self.rel.cache / 'upgrade-cache'
        folder.chmod(0o770)
        self.never_started(self.run_op(), 'artifact_unverified')
        folder.chmod(0o700)
        wheel, _ = self.rel.paths()
        body = wheel.read_bytes()
        wheel.unlink()
        (self.rel.cache / 'real.whl').write_bytes(body)
        wheel.symlink_to(self.rel.cache / 'real.whl')
        self.never_started(self.run_op(), 'artifact_unverified')

    def test_the_bytes_that_run_are_the_bytes_that_were_verified(self):
        """A swap on disk right after the signature check changes nothing: the files come from the verified bytes."""
        self.rel.publish(self.rel.wheel(behaviour='ok'))
        sealed = auto_update.signed_cached_bytes

        def then_swapped(st, version):
            data = sealed(st, version)
            wheel, _ = self.rel.paths()
            wheel.unlink()
            wheel.write_bytes(self.rel.wheel(behaviour='failed'))
            return data
        with mock.patch.object(auto_update, 'signed_cached_bytes', then_swapped):
            outcome = self.run_op()
        self.assertEqual((outcome['result'], outcome['executor']), ('ok', 'target'))
        self.assertEqual(self.rel.observed()['behaviour'], 'ok')

    def test_a_target_that_is_not_newer_is_refused_before_anything_is_read(self):
        for target, current, reason in ((CURRENT, CURRENT, 'not_newer'), ('97.9.9', CURRENT, 'not_newer'),
                                        ('98.0.0rc1', CURRENT, 'not_newer'), ('98.0.0.dev3', CURRENT, 'not_newer'),
                                        ('98.0.0+local1', CURRENT, 'not_newer'), ('latest', CURRENT, 'bad_version'),
                                        ('99.0.1; rm', CURRENT, 'bad_version'), ('../99.0.1', CURRENT, 'bad_version'),
                                        (TARGET, 'unknown', 'bad_version'), ('', CURRENT, 'bad_version')):
            with self.subTest(target=target, current=current):
                if auto_update.update.parse(target):
                    self.rel.publish(self.rel.wheel(version=target), version=target)
                with mock.patch.object(auto_update, 'signed_cached_bytes', side_effect=AssertionError('read')):
                    self.never_started(self.run_op(target=target, current=current), reason)

    def test_rollback_also_takes_its_executor_from_a_newer_wheel_only(self):
        """The rollback step reinstalls the previous version; the code that does it still comes from the newer wheel."""
        self.rel.publish(self.rel.wheel(version=CURRENT), version=CURRENT)
        self.never_started(self.run_op('rollback', target=CURRENT), 'not_newer')

    def test_an_unknown_operation_is_refused(self):
        self.rel.publish(self.rel.wheel())
        self.never_started(self.run_op('shell'), 'bad_op')

    def test_a_traversal_name_in_a_signed_wheel_stops_the_upgrade_without_fallback(self):
        self.rel.publish(self.rel.wheel(members=[(zipfile.ZipInfo(PREFIX + '../../sitecustomize.py'), b'X = 1\n')]))
        self.never_started(self.run_op(), 'executor_invalid')


class Isolation(Launching):
    def test_the_child_is_an_isolated_interpreter_that_cannot_see_the_old_environment(self):
        self.rel.publish(self.rel.wheel())
        self.run_op()
        seen = self.rel.observed()
        folder = Path(seen['path'][0])
        self.assertEqual((seen['isolated'], seen['no_site'], seen['no_bytecode']), (1, 1, 1))
        self.assertEqual(seen['argv'][0], str(folder))
        self.assertEqual(folder.name, 'upgrader')
        self.assertTrue(str(folder).startswith(str(self.rel.state / 'install-artifacts') + os.sep))
        self.assertFalse([p for p in seen['path'] if 'site-packages' in p or 'dist-packages' in p])
        self.assertEqual(seen['loaded'], {'agentj': False, 'cryptography': False, 'websockets': False})
        self.assertEqual((seen['folder_mode'], set(seen['files'].values())), (0o700, {0o400}))
        self.assertEqual(sorted(seen['files']), ['__main__.py', 'ajx_helper.py', 'manifest.json'])

    def test_the_running_process_never_imports_from_the_target_wheel(self):
        self.rel.publish(self.rel.wheel())
        modules, path, meta = set(sys.modules), list(sys.path), list(sys.meta_path)
        self.run_op()
        self.assertFalse(self.rel.marker.exists(), 'agentj/__init__.py or sitecustomize.py of the target wheel ran')
        self.assertEqual((sys.path, sys.meta_path), (path, meta))
        new = [name for name in set(sys.modules) - modules
               if name.startswith(('ajx_', 'upgrader', 'agentj.upgrader')) or 'install-artifacts' in str(
                   getattr(sys.modules[name], '__file__', ''))]
        self.assertEqual(new, [])

    def test_environment_umask_folder_and_descriptors_are_cleaned(self):
        self.rel.publish(self.rel.wheel())
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        self.addCleanup(os.close, writer)
        os.dup2(reader, 57, inheritable=True)
        self.addCleanup(os.close, 57)
        evil = self.rel.seen / 'evil'
        evil.mkdir()
        (evil / 'sitecustomize.py').write_text(FAKE_PACKAGE % {'marker': str(self.rel.marker)})
        (evil / 'json.py').write_text(FAKE_PACKAGE % {'marker': str(self.rel.marker)})
        dirty = {'UV_INDEX_URL': 'https://example.com/simple', 'UV_TOOL_DIR': str(evil), 'UV_CACHE_DIR': str(evil),
                 'PIP_INDEX_URL': 'https://example.com/simple', 'PYTHONPATH': str(evil), 'PYTHONSTARTUP': str(evil / 'json.py'),
                 'PYTHONHOME': str(evil), 'PYTHONUSERBASE': str(evil), 'VIRTUAL_ENV': str(evil),
                 'AGENTJ_STATE_DIR': str(evil), 'AJ0371_CANARY': 'not for the new code', 'TMPDIR': str(evil),
                 'HTTPS_PROXY': 'http://127.0.0.1:9', 'NO_PROXY': 'localhost'}
        before, cwd = os.umask(0o002), os.getcwd()     # Ubuntu's default: the umask the 0.30.47-0.30.65 updater ran with
        try:
            os.chdir(evil)
            with mock.patch.dict(os.environ, dirty):
                outcome = self.run_op()
            during = os.umask(0o002)
        finally:
            os.chdir(cwd)
            os.umask(before)
        self.assertEqual(outcome['result'], 'ok')
        self.assertFalse(self.rel.marker.exists())
        seen = self.rel.observed()
        env = seen['env']
        self.assertLessEqual(set(env), set(self.b.ENV_ALLOW))
        self.assertFalse([n for n in env if n.startswith(('UV_', 'PIP_', 'PYTHON', 'LD_', 'DYLD_', 'AGENTJ_'))])
        self.assertNotIn('AJ0371_CANARY', env)
        self.assertEqual((env['HOME'], env['HTTPS_PROXY'], env['NO_PROXY']), (str(self.rel.home), 'http://127.0.0.1:9', 'localhost'))
        self.assertEqual(seen['umask'], 0o077)
        scratch = seen['request']['scratch']
        self.assertEqual((seen['cwd'], env['TMPDIR'], seen['cwd_mode']), (scratch, scratch, 0o700))
        self.assertTrue(scratch.startswith(str(self.rel.state / 'install-artifacts') + os.sep))
        self.assertNotEqual(Path(scratch), Path(seen['path'][0]))
        self.assertTrue(seen['leader'], 'the child must lead its own process group so a timeout can stop its children')
        if seen['fds'] is not None:
            self.assertNotIn(57, seen['fds'])
            self.assertLessEqual(len(seen['fds']), 4)
        self.assertEqual(during, 0o002, 'the calling process changed its own umask')
        self.assertEqual(os.getcwd(), cwd)


class Failure(Launching):
    """Every failure leaves the old version as it was and says who has to act next."""

    def outcome(self, behaviour, op, **kw):
        self.rel.publish(self.rel.wheel(behaviour=behaviour))
        before = self.rel.tree(outside_store=True)
        outcome = self.run_op(op, **kw)
        self.assertEqual(self.rel.tree(outside_store=True), before, 'the hand-over changed something outside its own store')
        self.assertEqual(self.rel.left_behind(), [])
        self.assertFalse(self.rel.marker.exists())
        return outcome

    def test_a_wheel_without_an_executor_falls_back_to_the_running_versions_own(self):
        self.rel.publish(self.rel.wheel(executor=False))
        outcome = self.run_op('prepare')
        self.assertEqual((outcome['executor'], outcome['fallback'], outcome['launched'], outcome['result'], outcome['diag']),
                         ('own', 'executor_missing', False, 'ok', {'own': True}))
        (op, request), = self.own.calls
        self.assertEqual((op, request['op'], request['contract'], request['caller'], request['target']['version']),
                         ('prepare', 'prepare', self.b.CONTRACT_MAX, CURRENT, TARGET))
        self.assertIsNone(self.rel.observed())

    def test_an_executor_from_a_later_contract_falls_back_without_being_started(self):
        self.rel.publish(self.rel.wheel(manifest={'contract_min': 7, 'contract_max': 9, 'entry': '__main__.py'}))
        outcome = self.run_op('prepare')
        self.assertEqual((outcome['executor'], outcome['fallback'], outcome['launched']), ('own', 'contract_unsupported', False))
        self.assertIsNone(self.rel.observed())
        self.assertEqual(len(self.own.calls), 1)

    def test_before_the_install_a_broken_executor_falls_back(self):
        for behaviour, why in (('kill', 'executor_crashed'), ('exit70', 'executor_crashed'),
                               ('traceback', 'executor_output_invalid'), ('garbage', 'executor_output_invalid'),
                               ('flood', 'executor_output_invalid'), ('refused', 'executor_refused')):
            for op in ('probe', 'prepare'):
                with self.subTest(behaviour=behaviour, op=op):
                    self.own.calls.clear()
                    outcome = self.outcome(behaviour, op)
                    self.assertEqual((outcome['executor'], outcome['fallback'], outcome['launched'], outcome['result']),
                                     ('own', why, True, 'ok'))
                    self.assertEqual([c[0] for c in self.own.calls], [op])
                    self.assertIn(why, self.b.FALLBACK)

    def test_the_fallbacks_failure_is_reported_as_the_fallbacks(self):
        self.own.result = {'result': 'failed', 'reason': 'install_artifact_changed', 'live_touched': False, 'diag': {}}
        outcome = self.outcome('kill', 'prepare')
        self.assertEqual((outcome['executor'], outcome['fallback'], outcome['result'], outcome['reason']),
                         ('own', 'executor_crashed', 'failed', 'install_artifact_changed'))

    def test_a_failure_the_executor_reports_is_final(self):
        """The network is down, a dependency changed: the running version's own executor would meet the same thing."""
        outcome = self.outcome('failed', 'prepare')
        self.assertEqual(outcome, {'executor': 'target', 'fallback': None, 'launched': True, 'contract': 1, 'op': 'prepare',
                                   'result': 'failed', 'reason': 'dependency_download_failed', 'live_touched': False,
                                   'rollback_ready': False, 'retry_count': 0, 'diag': {'uv_failure_class': 'timeout'}})
        self.assertEqual(self.own.calls, [])

    def test_a_timeout_stops_the_executor_and_everything_it_started(self):
        with mock.patch.object(self.b, 'GRACE', 0.5):
            started = time.monotonic()
            outcome = self.outcome('hang', 'prepare', deadline_s=2)
            self.assertLess(time.monotonic() - started, 30)
        self.assertEqual((outcome['executor'], outcome['fallback'], outcome['launched'], outcome['result'],
                          outcome['reason'], outcome['live_touched']),
                         ('target', None, True, 'failed', 'executor_timeout', False))
        self.assertEqual(self.own.calls, [], 'the budget is spent: no second attempt')
        self.assertNotIn('executor_timeout', self.b.FALLBACK)
        child = self.rel.observed()['child']
        for _ in range(100):
            try:
                state = Path(f'/proc/{child}/stat').read_text().rsplit(')', 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError):
                state = 'gone'
            if state in ('gone', 'Z', 'X'):
                break
            time.sleep(0.1)
        else:
            os.kill(child, signal.SIGKILL)
            self.fail('a process the executor started outlived the timeout')

    def test_during_the_install_nothing_falls_back_and_the_old_version_is_restored(self):
        """Killed, crashed, silent or late in the install step: the live tool may be half replaced → roll back."""
        for behaviour, reason in (('kill', 'executor_crashed'), ('exit70', 'executor_crashed'),
                                  ('traceback', 'executor_output_invalid'), ('garbage', 'executor_output_invalid'),
                                  ('flood', 'executor_output_invalid')):
            for op in ('install', 'rollback'):
                with self.subTest(behaviour=behaviour, op=op):
                    outcome = self.outcome(behaviour, op)
                    self.assertEqual((outcome['executor'], outcome['fallback'], outcome['launched'], outcome['result'],
                                      outcome['reason'], outcome['live_touched']),
                                     ('target', None, True, 'failed', reason, True))
                    self.assertEqual(self.own.calls, [])
        with mock.patch.object(self.b, 'GRACE', 0.5):
            outcome = self.outcome('hang', 'install', deadline_s=2)
        self.assertEqual((outcome['reason'], outcome['live_touched'], self.own.calls), ('executor_timeout', True, []))

    def test_a_reported_install_failure_says_whether_the_live_tool_was_touched(self):
        outcome = self.outcome('failed', 'install')
        self.assertEqual((outcome['result'], outcome['reason'], outcome['live_touched'], outcome['fallback']),
                         ('failed', 'dependency_download_failed', False, None))
        outcome = self.outcome('failed_silent', 'install')
        self.assertEqual((outcome['reason'], outcome['live_touched']), ('uv_failed', True))
        outcome = self.outcome('refused', 'install')
        self.assertEqual((outcome['executor'], outcome['result'], outcome['live_touched'], self.own.calls),
                         ('target', 'refused', False, []))

    def test_an_interpreter_that_cannot_start_is_a_fallback_not_a_traceback(self):
        self.rel.publish(self.rel.wheel())
        outcome = self.b.run('prepare', cache_root=self.rel.cache, store_root=self.rel.state, target=TARGET, current=CURRENT,
                             request={'store': str(self.rel.seen)}, deadline_s=30, own=self.own,
                             python=str(self.rel.home / 'no-such-python'))
        self.assertEqual((outcome['executor'], outcome['fallback'], outcome['launched']), ('own', 'executor_crashed', False))
        self.assertEqual(self.rel.left_behind(), [])

    def test_a_store_that_cannot_be_trusted_stops_before_the_executor(self):
        self.rel.publish(self.rel.wheel())
        store = self.rel.state / 'install-artifacts'
        store.mkdir(mode=0o700)
        store.chmod(0o777)
        outcome = self.run_op()
        self.assertEqual((outcome['result'], outcome['reason'], outcome['launched']), ('refused', 'install_store_unsafe', False))
        self.assertIsNone(self.rel.observed())
        self.assertEqual(self.own.calls, [])


def imports_of(tree):
    """(module, line) for every import statement, relative ones as '.name'."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            yield '.' * node.level + (node.module or ''), node.lineno
            if not node.module:
                for alias in node.names:
                    yield '.' * node.level + alias.name, node.lineno


DYNAMIC = {'importlib', 'runpy', 'zipimport', 'pkgutil', 'imp', 'site', 'code', 'codeop'}
CALLS = {'exec', 'eval', 'compile', '__import__'}


def dynamic_loading(tree):
    found = [f'import {name}:{line}' for name, line in imports_of(tree) if name.split('.')[0] in DYNAMIC]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in CALLS:
            found.append(f'{node.func.id}():{node.lineno}')
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'sys' and node.attr in (
                'path', 'meta_path', 'path_hooks', 'modules', 'path_importer_cache'):
            found.append(f'sys.{node.attr}:{node.lineno}')
    return found


class CallerSource(unittest.TestCase):
    """The hand-over module cannot load code from the target wheel into this process — by construction."""

    def setUp(self):
        self.b = caller()
        self.tree = ast.parse(Path(self.b.__file__).read_text())

    def test_no_dynamic_loading_and_no_path_changes(self):
        self.assertEqual(dynamic_loading(self.tree), [])

    def test_no_unpacking_by_path_and_no_shell(self):
        found = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Attribute) and node.attr in ('extract', 'extractall', 'unpack_archive', 'system', 'popen'):
                if not (isinstance(node.value, ast.Name) and node.value.id == 'self'):
                    found.append(f'.{node.attr}:{node.lineno}')
            if isinstance(node, ast.keyword) and node.arg == 'shell':
                found.append(f'shell=:{node.lineno}')
        self.assertEqual(found, [])

    def test_the_child_is_started_with_every_isolation_option(self):
        popen = [node for node in ast.walk(self.tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in ('Popen', 'run', 'call', 'check_call', 'check_output')
                 and isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess']
        self.assertEqual(len(popen), 1, 'exactly one place starts a process')
        options = {k.arg: k.value for k in popen[0].keywords}
        self.assertLessEqual({'env', 'cwd', 'umask', 'close_fds', 'start_new_session', 'stdin', 'stdout', 'stderr'}, set(options))
        self.assertEqual(ast.literal_eval(options['umask']), 0o077)
        self.assertIs(ast.literal_eval(options['close_fds']), True)
        self.assertIs(ast.literal_eval(options['start_new_session']), True)
        self.assertNotIn('pass_fds', options)
        self.assertNotIn('preexec_fn', options)

    def test_the_trust_decisions_are_not_configurable(self):
        """No environment variable, file or request field can switch off the checks or widen the allowlist."""
        names = {node.value for node in ast.walk(self.tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertFalse([n for n in names if n.startswith('AGENTJ_')], 'an AGENTJ_* switch in the hand-over')
        self.assertIsInstance(self.b.ENV_ALLOW, frozenset)
        self.assertIsInstance(self.b.FALLBACK, frozenset)
        self.assertLessEqual(len(self.b.ENV_ALLOW), 40)


class NothingImportsTheExecutor(unittest.TestCase):
    """Runs today and stays: product code reaches the executor through the child process contract only."""

    def test_no_module_imports_the_executor_package(self):
        found = []
        for path in sorted(PACKAGE.rglob('*.py')):
            if EXECUTOR in path.parents:
                continue
            for name, line in imports_of(ast.parse(path.read_text())):
                parts = name.lstrip('.').split('.')
                if 'upgrader' in parts[:2] or parts[0].startswith('ajx_'):
                    found.append(f'{path.relative_to(PACKAGE)}:{line} {name}')
        self.assertEqual(found, [])


class ExecutorSource(unittest.TestCase):
    """The real executor: it must run on any interpreter an old host has, with nothing but the standard library."""

    OLDEST = (3, 11)        # requires-python of the oldest version that carries the hand-over

    def setUp(self):
        self.sources = executor_sources()

    def test_file_names_and_layout(self):
        import re
        names = sorted(p.name for p in EXECUTOR.iterdir() if p.name != '__pycache__')
        self.assertIn('manifest.json', names)
        self.assertIn('__main__.py', names)
        self.assertNotIn('__init__.py', names, 'a regular package could be imported by the running version')
        for name in names:
            self.assertTrue(re.fullmatch(r'__main__\.py|manifest\.json|ajx_[a-z0-9_]{1,40}\.py', name), name)
            self.assertNotIn(name[:-3], sys.stdlib_module_names)
        self.assertLessEqual(len(names), 24)
        self.assertFalse([p for p in EXECUTOR.iterdir() if p.is_dir() and p.name != '__pycache__'])

    def test_only_the_standard_library_and_its_own_files(self):
        own = {p.stem for p in self.sources}
        found = []
        for path in self.sources:
            tree = ast.parse(path.read_text(), feature_version=self.OLDEST)
            for name, line in imports_of(tree):
                top = name.split('.')[0]
                if name.startswith('.'):
                    found.append(f'{path.name}:{line} relative import {name}')
                elif top == '__main__' or (top not in sys.stdlib_module_names and top not in own):
                    found.append(f'{path.name}:{line} {name}')
            found += [f'{path.name} {item}' for item in dynamic_loading(tree) if not item.startswith('sys.path')]
        self.assertEqual(found, [])

    def test_no_module_newer_than_the_oldest_supported_interpreter(self):
        newer = {'annotationlib', 'compression', 'concurrent.interpreters', 'string.templatelib', 'pathlib.types'}
        found = [f'{p.name}:{line} {name}' for p in self.sources
                 for name, line in imports_of(ast.parse(p.read_text())) if name in newer or name.split('.')[0] in newer]
        self.assertEqual(found, [])

    def test_the_manifest_names_a_contract_range_that_includes_version_one(self):
        manifest = json.loads((EXECUTOR / 'manifest.json').read_text())
        self.assertEqual((manifest['contract_min'], manifest['entry']), (1, '__main__.py'))
        self.assertGreaterEqual(manifest['contract_max'], 1)
        self.assertEqual(caller().negotiate(manifest), caller().CONTRACT_MAX)

    def child(self, request, **kw):
        with tempfile.TemporaryDirectory(dir=TEST_TMP, prefix='aj0371-x-') as scratch:
            env = {'HOME': scratch, 'PATH': '/usr/bin:/bin', 'TMPDIR': scratch}
            raw = request if isinstance(request, bytes) else json.dumps({'scratch': scratch, **request}).encode()
            return subprocess.run([sys.executable, '-I', '-S', '-B', str(EXECUTOR)], input=raw, capture_output=True,
                                  env=env, cwd=scratch, timeout=60, **kw)

    def test_probe_answers_under_isolation_and_sets_its_own_umask(self):
        """The executor does not rely on the caller for its umask: an old caller's mistake must not come back."""
        done = self.child({'contract': 1, 'op': 'probe', 'caller': CURRENT, 'deadline_s': 30}, umask=0o002)
        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        result = caller().parse_result(done.stdout, done.returncode, op='probe', contract=1)
        self.assertEqual((result['result'], result['live_touched']), ('ok', False))
        self.assertEqual(result['diag'].get('umask'), '0077')

    def test_an_unknown_contract_or_request_is_refused_with_a_result(self):
        for request, code, reason in (({'contract': 99, 'op': 'probe'}, 3, 'contract_unsupported'),
                                      ({'contract': True, 'op': 'probe'}, 3, 'contract_unsupported'),
                                      ({'contract': 1, 'op': 'shell'}, 2, 'bad_request'),
                                      (b'not json', 2, 'bad_request'), (b'', 2, 'bad_request'),
                                      (b'[1]', 2, 'bad_request'), (b'{"contract": 1, "op": "probe"' + b' ' * (1 << 20) + b'}', 2, 'bad_request')):
            with self.subTest(request=request if isinstance(request, dict) else request[:20]):
                done = self.child(request)
                self.assertEqual(done.returncode, code, done.stderr[-400:])
                line = json.loads(done.stdout)
                self.assertEqual((line['result'], line['reason']), ('refused', reason))
                self.assertNotIn(b'Traceback', done.stderr)

    def test_unknown_request_fields_are_ignored(self):
        done = self.child({'contract': 1, 'op': 'probe', 'caller': CURRENT, 'deadline_s': 30, 'added_in_a_later_caller': {'a': [1]}})
        self.assertEqual((done.returncode, json.loads(done.stdout)['result']), (0, 'ok'))

    def test_the_wheel_that_ships_carries_an_executor_the_hand_over_accepts(self):
        """Build-side guard: packaging must not drop or rename the folder (hatch `packages`, export, slimming)."""
        b = caller()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for path in sorted(EXECUTOR.iterdir()):
                if path.is_file():
                    archive.write(path, PREFIX + path.name)
        files = b.members(buffer.getvalue())
        self.assertEqual(sorted(files), sorted(p.name for p in EXECUTOR.iterdir() if p.is_file()))
        self.assertLessEqual(sum(map(len, files.values())), b.MAX_TOTAL)


if __name__ == '__main__':
    unittest.main()
