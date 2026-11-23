"""0.30.70 candidate: what the 0.30.47-0.30.65 updater fault (it wrote its dependency cache with the process umask and then
refused its own files) taught the next updater.

- changed cache content still stops the upgrade, now with the one repair line; the mode-only rebuild is logged as one;
- nothing the preparation creates depends on the process umask;
- the same failure on the same host is reported to support once a week, not once a day; the owner hears it once;
- a cache / store failure tells the owner the host still works and where the repair steps are;
- the installation check names every path of a chain at once.
"""
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile
from unittest import TestCase, mock

from _testtmp import TEST_TMP
from agentj import auto_update, install_permissions, upgrade_diag, upgrade_wheels as wheels
from agentj.state import State
from test_0369_stale_dependency_cache import StaleDependencyCache


class ChangedBytesStillStop(StaleDependencyCache):
    """Changed, cut-short or stray content is not an old umask: it stops the upgrade untouched (test_r0018's contract) and
    now names the one repair line. Only the mode-only leftover is rebuilt, once, and logged."""

    def stopped(self, subreason):
        before = sorted((p.name, p.read_bytes()) for p in self.house.iterdir())
        for prepare in (True, False):
            error = self.failure(prepare=prepare)
            self.assertEqual((str(error), error.fields['artifact_stage'], error.fields['subreason']),
                             ('install_artifact_changed', 'dependency_cache', subreason))
            self.assertEqual(error.fields['fix'], 'rm -rf -- ' + shlex.quote(str(self.house)))
        self.assertEqual((self.downloads, sorted((p.name, p.read_bytes()) for p in self.house.iterdir())), (0, before))

    def test_changed_bytes_stop_with_the_repair_line(self):
        self.entry.chmod(0o600)
        self.entry.write_bytes(bytes(len(self.dep)))
        self.stopped('hash_mismatch')

    def test_truncated_entry_stops_with_the_repair_line(self):
        self.entry.chmod(0o600)
        self.entry.write_bytes(self.dep[:-1])
        self.stopped('size_mismatch')

    def test_stray_file_stops_with_the_repair_line(self):
        (self.house / 'left-over.whl').write_bytes(b'x')
        self.stopped('cache_entry_invalid')

    def test_the_mode_only_rebuild_is_logged_as_a_rebuild(self):
        from agentj.state import State
        State(self.root).init(relay='ws://127.0.0.1:1')
        self.old_updater_leftover()
        with mock.patch('agentj.install_permissions._private_group', return_value=False):
            self.assertEqual(self.build(), self.house)
        events = [json.loads(line) for line in (self.root / 'host.log').read_text().splitlines()]
        self.assertEqual([(e.get('status')) for e in events if e.get('ev') == 'upgrade_cache_rebuilt'], ['unsafe_store'])
        self.assertFalse([e for e in events if e.get('ev') == 'upgrade_permissions_hardened'])


class NoUmaskDependence(StaleDependencyCache):
    def test_everything_prepared_under_umask_zero_is_private(self):
        self.house.joinpath(self.name).unlink()
        self.house.rmdir()
        old = os.umask(0)
        try:
            house = self.build()
        finally:
            os.umask(old)
        loose = [str(p) for p in [house.parent, house, *house.iterdir()] if stat.S_IMODE(p.lstat().st_mode) & 0o077]
        self.assertEqual(loose, [])

    def test_preparation_runs_with_a_private_umask_and_puts_the_old_one_back(self):
        seen = []

        def inner(*a, **kw):
            seen.append(os.umask(0o077))
            return 'prepared'
        old = os.umask(0o002)
        try:
            with mock.patch.object(wheels, '_prepare_update', side_effect=inner), \
                    mock.patch.object(wheels, 'cached_runtime', side_effect=lambda cmd, pin: cmd):
                self.assertEqual(wheels.prepare_update(['uv', 'tool', 'install'], self.pin, '1.0.0'), 'prepared')
            self.assertEqual((seen, os.umask(0o002)), ([0o077], 0o002))
        finally:
            os.umask(old)

    def test_overlapping_preparations_leave_the_process_umask_as_it_was(self):
        """Two watchdog threads: A enters, B enters, A leaves, B leaves. Saving and restoring per call would end on 077."""
        import threading
        a_in, b_in, a_out = threading.Event(), threading.Event(), threading.Event()
        inside, errors = [], []

        def run(first):
            try:
                with wheels.private_umask():
                    (a_in if first else b_in).set()
                    self.assertTrue((b_in if first else a_out).wait(10))
                    current = os.umask(0o077)
                    os.umask(current)
                    inside.append(current)
                if first:
                    a_out.set()
            except Exception as error:  # noqa: BLE001
                errors.append(error)
        old = os.umask(0o002)
        try:
            a = threading.Thread(target=run, args=(True,))
            a.start()
            self.assertTrue(a_in.wait(10))
            b = threading.Thread(target=run, args=(False,))
            b.start()
            a.join(10)
            b.join(10)
            after = os.umask(0o002)
            self.assertEqual((errors, inside, after, wheels._umask_holders), ([], [0o077, 0o077], 0o002, 0))
            with self.assertRaises(RuntimeError), wheels.private_umask():
                raise RuntimeError('preparation failed')
            self.assertEqual(os.umask(0o002), 0o002)
        finally:
            os.umask(old)


FAILED = {'result': 'failed', 'reason': 'install_artifact_changed', 'step': 'install', 'from': '0.30.70', 'to': '0.30.71', 'phase': None,
          'artifact_stage': 'dependency_cache', 'subreason': 'cache_entry_invalid'}
DAY = 24 * 3600


class ReportedOncePerWeek(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP, prefix='aj0371-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = mock.patch.dict(os.environ, {'HOME': str(root), 'XDG_CONFIG_HOME': str(root / '.config'), 'AGENTJ_STATE_DIR': str(root / 'state'),
                                           'XDG_CACHE_HOME': str(root / '.cache'), upgrade_diag.REPORT_ENV: ''})
        env.start()
        self.addCleanup(env.stop)
        self.st = State()
        self.st.init(relay='ws://127.0.0.1:1')
        self.sent = 0

    def send(self, text):
        self.sent += 1
        return 0, 'st_' + f'{self.sent:022d}'

    def attempt(self, day, **change):
        """One nightly attempt: the record is written, then the failure reports itself."""
        res = {**FAILED, **change}
        now = 1_800_000_000 + day * DAY
        upgrade_diag.write(self.st, upgrade_diag.entry(res, now))
        with mock.patch.object(upgrade_diag, 'enabled', return_value=True):
            return upgrade_diag.report(self.st, res, send=self.send, now=now)['status']

    def test_the_same_failure_is_reported_once_in_seven_days(self):
        self.assertEqual([self.attempt(d) for d in (0, 1, 2, 6)], ['sent', 'already', 'already', 'already'])
        self.assertEqual(self.sent, 1)
        self.assertEqual(self.attempt(7.1), 'sent')

    def test_integrity_findings_keep_the_daily_report(self):
        for change in ({'subreason': 'hash_mismatch'}, {'subreason': 'size_mismatch'}, {'subreason': 'signature_invalid', 'artifact_stage': 'signature'},
                       {'reason': 'signature_or_hash_mismatch', 'subreason': None, 'artifact_stage': None},
                       {'reason': 'uv_failed', 'subreason': 'cache_entry_invalid'}):
            self.sent = 0
            (self.st.root / upgrade_diag.LAST).unlink(missing_ok=True)
            self.assertEqual([self.attempt(d, **change) for d in (0, 0.5, 1.1, 1.6, 2.2)], ['sent', 'already', 'sent', 'already', 'sent'], change)
        self.assertEqual(upgrade_diag.SELF_INFLICTED, {'cache_entry_invalid', 'unsafe_store'})

    def test_the_other_self_inflicted_kind_is_weekly_too(self):
        self.assertEqual([self.attempt(d, subreason='unsafe_store', artifact_stage='install_store') for d in (0, 3, 6.9, 7.1)],
                         ['sent', 'already', 'already', 'sent'])

    def test_another_cause_target_or_starting_version_is_a_new_report(self):
        self.assertEqual(self.attempt(0), 'sent')
        self.assertEqual(self.attempt(1, subreason='unsafe_store'), 'sent')
        self.assertEqual(self.attempt(2, to='0.30.72', subreason='unsafe_store'), 'sent')
        self.assertEqual(self.attempt(3, to='0.30.72', subreason='unsafe_store', **{'from': '0.30.69'}), 'sent')
        self.assertEqual(self.attempt(4, to='0.30.72', subreason='unsafe_store', **{'from': '0.30.69'}), 'already')
        self.assertEqual(self.sent, 4)

    def test_the_owner_hears_a_repeated_nightly_failure_once(self):
        result = {'result': 'failed', 'reason': 'install_artifact_changed', 'from': auto_update.__version__, 'to': '9.9.9',
                  'artifact_stage': 'dependency_cache', 'subreason': 'cache_entry_invalid', 'step': 'install'}
        heard = []
        for _ in range(3):
            with self.st.config_lock():
                auto_update._finish(self.st, dict(result))
            text = auto_update.take_notice(self.st, 'zh', harness=None)
            if text:
                heard.append(text)
        self.assertEqual(len(heard), 1)
        with self.st.config_lock():
            auto_update._finish(self.st, {**result, 'to': '9.9.10'})
        self.assertTrue(auto_update.take_notice(self.st, 'zh', harness=None))


class OwnerSentence(TestCase):
    def test_a_cache_failure_says_the_host_still_works_and_where_the_repair_steps_are(self):
        for stage in ('dependency_cache', 'install_store'):
            res = {**FAILED, 'artifact_stage': stage, 'subreason': 'unsafe_store'}
            zh = upgrade_diag.owner_sentence(res, {'status': 'off'}, 'zh')
            en = upgrade_diag.owner_sentence(res, {'status': 'off'}, 'en')
            for text in (zh, en):
                self.assertIn(upgrade_diag.UPGRADE_REPAIR_GUIDE, text)
            self.assertIn('仍是 0.30.70，照常可用', zh)
            self.assertIn('still on 0.30.70, working as before', en)
            self.assertIn(upgrade_diag.UPGRADE_REPAIR_GUIDE, auto_update.notice(res, 'zh', harness=None))
        other = upgrade_diag.owner_sentence({**FAILED, 'artifact_stage': 'signature', 'subreason': 'signature_invalid'}, {'status': 'off'}, 'zh')
        self.assertNotIn(upgrade_diag.UPGRADE_REPAIR_GUIDE, other)


class WholeChainAtOnce(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP, prefix='aj0371p-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.leaf = self.root / 'a' / 'b' / 'c'
        self.leaf.mkdir(parents=True)
        for path in (self.root / 'a', self.root / 'a' / 'b', self.leaf):
            path.chmod(0o775)

    def blocked(self, private=False):
        with mock.patch.object(install_permissions, '_private_group', return_value=private), \
                self.assertRaises(install_permissions.UnsafeInstallation) as caught:
            install_permissions.secure_paths([(self.leaf, True)])
        return caught.exception

    def test_every_group_writable_directory_of_the_chain_is_named_in_one_fix(self):
        error = self.blocked()
        chain = [str(self.root / 'a'), str(self.root / 'a' / 'b'), str(self.leaf)]
        self.assertEqual(error.paths, chain)
        self.assertEqual(error.fix, 'chmod go-w -- ' + ' '.join(chain))
        self.assertEqual([stat.S_IMODE(Path(p).stat().st_mode) for p in chain], [0o775] * 3)     # refused: nothing changed

    def test_the_walk_stops_at_what_another_account_could_have_replaced(self):
        (self.root / 'a').chmod(0o777)
        error = self.blocked(private=True)
        self.assertEqual(error.paths, [str(self.root / 'a')])

    def test_a_proven_private_group_is_still_tightened_not_refused(self):
        with mock.patch.object(install_permissions, '_private_group', return_value=True):
            install_permissions.secure_paths([(self.leaf, True)])
        self.assertEqual(stat.S_IMODE(self.leaf.stat().st_mode), 0o755)

    def test_the_tree_fix_is_one_recursive_line(self):
        many = {self.root / 'tool' / f'f{i}': 'mode' for i in range(40)}
        error = install_permissions.UnsafeInstallation(many, tree=self.root / 'tool')
        self.assertEqual(error.fix, 'chmod -R go-w -- ' + str(self.root / 'tool'))
        self.assertLessEqual(len(error.lines()), 12)
        self.assertIn('and 30 more', error.lines()[-2])

    def test_the_preflight_prints_one_line_for_everything_it_would_refuse(self):
        home = self.root / 'home'
        prefix = home / '.local/share/uv/tools/agentj'
        (prefix / 'bin').mkdir(parents=True)
        (home / '.local/bin').mkdir()
        (home / '.local/state/agentj').mkdir(parents=True)
        (prefix / 'bin/agentj').write_text('#!')
        (prefix / 'uv-receipt.toml').write_text('')
        (home / '.local/bin/agentj').symlink_to(prefix / 'bin/agentj')
        for folder, dirs, files in os.walk(home):
            for name in ('.', *dirs, *files):
                path = Path(folder) / name
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode | 0o020)
        home.chmod(0o755)
        import contextlib
        import io
        err = io.StringIO()
        state = mock.patch.dict(os.environ, {'AGENTJ_STATE_DIR': ''})      # the default state directory of this home
        state.start()
        self.addCleanup(state.stop)
        with mock.patch.object(install_permissions, '_private_group', return_value=False), contextlib.redirect_stderr(err):
            code = install_permissions.bootstrap_permissions([str(home), str(home / '.local/bin/agentj'), str(prefix), '/usr/bin/python3'])
        fix = [line for line in err.getvalue().splitlines() if line.startswith('fix')]
        self.assertEqual((code, len(fix)), (1, 1))
        chain = [home / '.local', home / '.local/bin', home / '.local/share', home / '.local/share/uv', home / '.local/share/uv/tools',
                 home / '.local/state', home / '.local/state/agentj']
        for path in chain:
            self.assertIn(' ' + str(path) + ' ', fix[0] + ' ')
        self.assertTrue(fix[0].endswith('&& chmod -R go-w -- ' + str(prefix)))
        import subprocess
        subprocess.run(fix[0].split(': ', 1)[1], shell=True, check=True)
        with mock.patch.object(install_permissions, '_private_group', return_value=False), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(install_permissions.bootstrap_permissions(
                [str(home), str(home / '.local/bin/agentj'), str(prefix), '/usr/bin/python3']), 0)

    def test_the_one_line_keeps_the_acl_removal_for_every_path_that_has_one(self):
        home = self.root / 'home'
        prefix = home / '.local/share/uv/tools/agentj'
        (prefix / 'bin').mkdir(parents=True)
        (prefix / 'bin/agentj').write_text('#!')
        for path in (home / '.local', home / '.local/share', prefix, prefix / 'bin', prefix / 'bin/agentj'):
            path.chmod(path.stat().st_mode | 0o020)
        marked = {(home / '.local/share').stat().st_ino, (prefix / 'bin').stat().st_ino, (prefix / 'bin/agentj').stat().st_ino}
        with mock.patch.object(install_permissions, '_linux_acl', side_effect=lambda fd: os.fstat(fd).st_ino in marked):
            line = install_permissions.repair_line([home, prefix], prefix)
        steps = line.split(' && ')
        self.assertEqual(steps[0], 'setfacl -b -- ' + ' '.join(str(p) for p in (home / '.local/share', prefix / 'bin', prefix / 'bin/agentj')))
        self.assertEqual(steps[1], 'setfacl -k -- ' + ' '.join(str(p) for p in (home / '.local/share', prefix / 'bin')))
        self.assertTrue(steps[2].startswith('chmod go-w -- '))
        self.assertIn('chmod go-w -- ' + str(home / '.local'), steps)
        self.assertEqual(steps[-1], 'chmod -R go-w -- ' + str(prefix))
        self.assertEqual([i for i, step in enumerate(steps) if step.startswith('setfacl')], [0, 1])      # ACLs go first

