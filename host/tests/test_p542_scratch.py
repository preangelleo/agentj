"""P542 (FEEDBACK #306): throwaway OpenCode HOMEs live in the private cache and never outlive their run; serve's start sweeps
what a crash left (ours, owned by us, > 6 h, no process using them)."""
import _hermetic  # noqa: F401
import asyncio
import os
import pathlib
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agentj import cli, fallback, opencode_free, scratch

HOST = pathlib.Path(__file__).resolve().parents[1]
OLD = time.time() - scratch.MAX_AGE - 60


def npm_like(path):
    """What OpenCode's plugin install leaves: node_modules/.bin executables, a read-only package folder."""
    pkg = pathlib.Path(path, 'cache/node_modules/which/bin')
    pkg.mkdir(parents=True)
    exe = pkg / 'node-which'
    exe.write_text('#!/bin/sh\n')
    exe.chmod(0o755)
    pkg.chmod(0o555)
    return exe


def age(path, when=OLD):
    for d, dirs, files in os.walk(path):
        for n in dirs + files:
            os.utime(os.path.join(d, n), (when, when), follow_symlinks=False)
    os.utime(path, (when, when))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p542-')
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: [os.chmod(d, 0o700) for d, _, _ in os.walk(self.tmp.name)])
        self.root = os.path.join(self.tmp.name, 'scratch')
        env = patch.dict(os.environ, {'AGENTJ_SCRATCH_DIR': self.root})
        env.start()
        self.addCleanup(env.stop)


class Root(Base):
    def test_private_cache_not_tmp(self):
        path = scratch.make('agentj-fallback-home-')
        self.addCleanup(scratch.remove, path)
        self.assertEqual(os.path.dirname(path), self.root)
        self.assertFalse(path.startswith(tempfile.gettempdir() + os.sep + 'agentj-'))
        self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)

    def test_default_is_xdg_cache(self):
        with patch.dict(os.environ, {'XDG_CACHE_HOME': self.tmp.name}):
            os.environ.pop('AGENTJ_SCRATCH_DIR')
            self.assertEqual(scratch.root(), os.path.join(self.tmp.name, 'agentj-scratch'))

    def test_loose_mode_tightened_and_symlink_refused(self):
        os.makedirs(self.root, mode=0o755)
        os.chmod(self.root, 0o755)
        self.assertEqual(scratch.root(), self.root)
        self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode), 0o700)
        os.rmdir(self.root)
        os.symlink(self.tmp.name, self.root)
        self.assertIsNone(scratch.root())          # → system temp folder, still removed and swept

    def test_unknown_prefix_refused(self):
        with self.assertRaises(ValueError):
            scratch.make('other-')


class Removal(Base):
    def test_normal_and_exception_exit_remove(self):
        with scratch.home('agentj-fallback-home-') as h:
            npm_like(h)
        self.assertFalse(os.path.lexists(h))
        with self.assertRaises(RuntimeError), scratch.home('agentj-free-list-') as h2:
            npm_like(h2)
            raise RuntimeError
        self.assertFalse(os.path.lexists(h2))
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(scratch._live, set())

    def test_cleanup_live_is_the_exit_path(self):
        path = scratch.make('agentj-oc-probe-')
        npm_like(path)
        scratch.cleanup_live()
        self.assertFalse(os.path.lexists(path))

    def child(self, body):
        code = 'import sys; sys.path.insert(0, %r)\nfrom agentj import scratch\n' % str(HOST) + body
        return subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30,
                              env={**os.environ, 'AGENTJ_SCRATCH_DIR': self.root})

    def test_child_process_crash_paths(self):
        r = self.child('h = scratch.make("agentj-fallback-home-"); print(h, flush=True); raise SystemExit(3)')
        self.assertEqual(r.returncode, 3)
        self.assertFalse(os.path.lexists(r.stdout.strip()))              # atexit
        r = self.child('with scratch.home("agentj-fallback-home-") as h:\n print(h, flush=True); 1/0')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.lexists(r.stdout.strip()))              # uncaught exception
        self.assertEqual(os.listdir(self.root), [])

    def test_forced_serve_exit_cleans_first(self):
        path = scratch.make('agentj-oc2-probe-')
        with patch.object(os, '_exit') as ex:
            cli._forced_exit()
        ex.assert_called_once_with(0)
        self.assertFalse(os.path.lexists(path))

    def test_fallback_home_removed_when_opencode_fails(self):
        seen = []
        def run(argv, **kw):
            seen.append(kw['env']['HOME'])
            npm_like(kw['env']['HOME'])
            raise subprocess.TimeoutExpired(argv, 5)
        with patch.object(fallback.subprocess, 'run', side_effect=run), self.assertRaises(subprocess.TimeoutExpired):
            fallback.cloud_reply('oc', 'opencode/x', 'p')
        self.assertTrue(seen[0].startswith(self.root + os.sep + 'agentj-fallback-home-'))
        self.assertFalse(os.path.lexists(seen[0]))
        with patch.object(fallback.subprocess, 'run', side_effect=run), patch.dict(os.environ, {'AGENTJ_OC_FREE_LIST': 'on'}):
            self.assertIsNone(fallback.free_models('oc'))
        self.assertTrue(seen[1].startswith(self.root + os.sep + 'agentj-free-list-'))
        self.assertFalse(os.path.lexists(seen[1]))

    def test_probe_scratch_removed_when_serve_fails(self):
        seen = []
        def serve(exe, env, root, port, up_by, clock=None):
            seen.append(root)
            raise opencode_free.ProbeError('native_spawn_failed')
        with patch.object(opencode_free, 'serve', side_effect=serve), patch.object(opencode_free, '_port', return_value=1), \
                self.assertRaises(opencode_free.ProbeError):
            opencode_free.probe('oc', None, {}, 1, waits=())
        self.assertTrue(seen[0].startswith(self.root + os.sep + 'agentj-oc-probe-'))
        self.assertFalse(os.path.lexists(seen[0]))


class Sweep(Base):
    def leftover(self, prefix='agentj-fallback-home-', where=None, old=True):
        path = tempfile.mkdtemp(prefix=prefix, dir=where or scratch.root())
        npm_like(path)
        if old:
            age(path)
        return path

    def test_crash_leftover_is_swept(self):
        """Regression #306: a SIGKILLed run leaves its HOME (with node_modules/.bin); the next start removes it."""
        r = subprocess.Popen([sys.executable, '-c', 'import sys, time; sys.path.insert(0, %r)\nfrom agentj import scratch\n'
                              'h = scratch.make("agentj-fallback-home-"); print(h, flush=True); time.sleep(60)' % str(HOST)],
                             stdout=subprocess.PIPE, text=True, env={**os.environ, 'AGENTJ_SCRATCH_DIR': self.root})
        path = r.stdout.readline().strip()
        r.send_signal(signal.SIGKILL)
        r.wait(10)
        r.stdout.close()
        npm_like(path)
        self.assertTrue(os.path.isdir(path))                              # SIGKILL skips every exit path
        self.assertEqual(scratch.sweep(dirs=[self.root]), [])             # fresh: kept
        age(path)
        self.assertEqual(scratch.sweep(dirs=[self.root]), [path])
        self.assertFalse(os.path.lexists(path))

    def test_legacy_tmp_location_is_swept_too(self):
        legacy = os.path.join(self.tmp.name, 'tmp')
        os.mkdir(legacy)
        path = self.leftover(where=legacy)
        self.assertEqual(scratch.sweep(dirs=[legacy], used=set()), [path])

    def test_only_ours_old_unused(self):
        keep = [self.leftover(old=False),                                 # young
                self.leftover(prefix='agentj-report-'),                   # someone else's prefix (not swept)
                self.leftover()]                                          # in use (below)
        sub = os.path.join(keep[2], 'cache')
        target = self.leftover()
        link = os.path.join(self.root, 'agentj-oc-probe-link')
        os.symlink(target, link)                                          # a symlink is never followed or removed
        live = scratch.make('agentj-oc2-probe-')
        age(live)
        self.addCleanup(scratch.remove, live)
        gone = scratch.sweep(dirs=[self.root], used={sub + '/x'})
        self.assertEqual(gone, [target])
        for p in keep + [link, live]:
            self.assertTrue(os.path.lexists(p), p)

    def test_unknown_process_table_or_other_owner_removes_nothing(self):
        path = self.leftover()
        with patch.object(scratch, 'used_paths', return_value=None):
            self.assertEqual(scratch.sweep(dirs=[self.root]), [])
        with patch.object(scratch.os, 'getuid', return_value=os.getuid() + 1):
            self.assertEqual(scratch.sweep(dirs=[self.root], used=set()), [])
        self.assertTrue(os.path.isdir(path))

    @unittest.skipUnless(sys.platform.startswith('linux'), '/proc')
    def test_real_process_cwd_or_home_keeps_it(self):
        for env_home in (False, True):
            path = self.leftover()
            kw = {'env': {**os.environ, 'HOME': path}, 'cwd': self.tmp.name} if env_home else {'cwd': path}
            p = subprocess.Popen(['sleep', '30'], **kw)
            try:
                time.sleep(.1)
                self.assertEqual(scratch.sweep(dirs=[self.root]), [])
            finally:
                p.kill()
                p.wait()
            self.assertEqual(scratch.sweep(dirs=[self.root]), [path])

    def test_serve_start_sweeps_and_logs(self):
        from agentj import serve
        path = self.leftover()
        logs = []
        host = SimpleNamespace(st=SimpleNamespace(log=lambda e, **kw: logs.append((e, kw))))
        with patch.object(scratch, 'used_paths', return_value=set()), \
                patch.object(scratch.tempfile, 'gettempdir', return_value=os.path.join(self.tmp.name, 'none')):
            asyncio.run(serve.Host._scratch_sweep(host))
        self.assertFalse(os.path.lexists(path))
        self.assertEqual(logs, [('scratch_leftover_removed', {'result': 1})])
        with patch.object(scratch, 'sweep', side_effect=PermissionError):
            asyncio.run(serve.Host._scratch_sweep(host))
        self.assertEqual(logs[-1], ('scratch_sweep_failed', {'reason': 'PermissionError'}))


if __name__ == '__main__':
    unittest.main()
