"""A dependency preparation that runs out of time is a slow network, not a broken install.

It is retried inside the same budget, leaves the running version alone, says so in plain words,
and reports where the time went with numbers and closed words only."""
import _hermetic  # noqa: F401
import errno
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agentj import auto_update as au, support, update, upgrade_diag, upgrade_wheels
from agentj import __version__, upgrade_uv
from agentj.state import State
import test_0369_stale_dependency_cache as stale
import test_g2jlt_upgrade_prepare as prepared

NEW, OLD = prepared.NEW, prepared.OLD

real_run = subprocess.run


class Clock:
    def __init__(self):
        self.now, self.sleeps = 100.0, []

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


class SilentResolverTimeout(unittest.TestCase):
    """upgrade_wheels.run_resolver: the installer said nothing before its time ran out."""

    def failure(self, **kw):
        def hang(args, **options):
            raise subprocess.TimeoutExpired(args, options["timeout"])
        with patch.object(upgrade_wheels.subprocess, "run", hang):
            with self.assertRaises(upgrade_wheels.PreparationFailure) as caught:
                upgrade_wheels.run_resolver(["uv", "pip", "compile"], None, 150, **kw)
        return caught.exception

    def test_network_selection_timeout_is_temporary(self):
        failure = self.failure()
        self.assertEqual(str(failure), "installer_timeout")
        self.assertTrue(failure.transient)
        self.assertEqual((failure.fields["dependency_phase"], failure.fields["uv_failure_class"]), ("resolve", "other"))

    def test_timeout_keeps_the_kind_of_source(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://127.0.0.1:9"}):
            self.assertEqual(self.failure().fields.get("dependency_source"), "proxy")

    def test_offline_check_timeout_is_not_retried_in_place(self):
        failure = self.failure(offline=True)
        self.assertEqual((str(failure), failure.transient), ("installer_timeout", False))


class RetryBudget(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.retry = upgrade_wheels.PreparationRetry(self.clock, self.clock.sleep)

    def timeout(self, spent):
        failure = upgrade_wheels.PreparationFailure("installer_timeout", "resolve", transient=True)
        failure.timed_out = True
        self.clock.now += spent
        return failure

    def test_a_timed_out_step_is_run_again_with_backoff_and_more_time(self):
        given = []

        def operation(timeout):
            given.append(timeout)
            if len(given) < 3:
                raise self.timeout(timeout)
            return "selected"

        self.assertEqual(self.retry.call(operation, 150, step="select"), "selected")
        self.assertEqual(self.clock.sleeps, list(au.INSTALL_BACKOFF[:2]))
        self.assertEqual(self.retry.count, 2)
        # 600 s in all: 150, then twice as long, then whatever is left.
        self.assertEqual(given, [150, 300, 130])

    def test_retries_stop_at_the_count_and_never_pass_the_deadline(self):
        given = []

        def operation(timeout):
            given.append(timeout)
            raise self.timeout(timeout)

        with self.assertRaises(upgrade_wheels.PreparationFailure) as caught:
            self.retry.call(operation, 20, step="select")
        failure = caught.exception
        self.assertEqual(str(failure), "installer_timeout")
        self.assertEqual(len(given), au.INSTALL_RETRIES + 1)
        self.assertEqual(self.retry.count, au.INSTALL_RETRIES)
        self.assertLessEqual(self.clock.now, self.retry.deadline)
        self.assertEqual(failure.fields["prepare_step"], "select")
        self.assertEqual(failure.fields["step_seconds"], 160)          # the last try: twice 20, three times over
        self.assertEqual(failure.fields["prepare_seconds"], int(self.clock.now - 100.0))

    def test_a_permanent_failure_is_not_retried(self):
        calls = []

        def operation(timeout):
            calls.append(timeout)
            raise upgrade_wheels.PreparationFailure("dependency_conflict", "resolve")

        with self.assertRaises(upgrade_wheels.PreparationFailure):
            self.retry.call(operation, 150, step="select")
        self.assertEqual((len(calls), self.clock.sleeps, self.retry.count), (1, [], 0))

    def test_an_exhausted_budget_names_the_step_that_could_not_start(self):
        self.retry.progress = (3, 17)
        self.clock.now = self.retry.deadline
        with self.assertRaises(upgrade_wheels.PreparationFailure) as caught:
            self.retry.call(lambda timeout: self.fail("launched after the deadline"), 90, phase="download", step="fetch")
        fields = caught.exception.fields
        self.assertEqual(str(caught.exception), "installer_timeout")
        self.assertEqual((fields["prepare_step"], fields["step_seconds"], fields["prepare_seconds"]), ("fetch", 0, 600))
        self.assertEqual((fields["wheels_fetched"], fields["wheels_needed"]), (3, 17))


@unittest.skipUnless(shutil.which("uv"), "requires uv")
class SlowNetworkUpdate(prepared.ResolverPreparation):
    """Both sealed entry points, with the real preparation code and a selection step that hangs."""

    def hanging(self, times):
        """The first `times` network selections time out without a word; everything else is the real uv."""
        self.resolver([])
        self.hung = []

        def run(args, **options):
            if list(args[1:3]) == ["pip", "compile"] and len(self.hung) < times:
                self.hung.append(options["timeout"])
                self.now += options["timeout"]
                raise subprocess.TimeoutExpired(args, options["timeout"])
            return real_run(args, **options)
        patcher = patch.object(upgrade_wheels.subprocess, "run", run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_manual_update_recovers_after_one_silent_timeout(self):
        self.hanging(1)
        rc, fields = self.manual()
        self.assertEqual((rc, fields.get("retry_count")), (0, 1))
        self.assertEqual(self.sleeps, [au.INSTALL_BACKOFF[0]])
        self.assertIn("--offline", self.launched.call_args.args[0])

    def test_manual_update_that_never_gets_through_changes_nothing(self):
        self.hanging(99)
        rc, fields = self.manual()
        self.assertNotEqual(rc, 0)
        self.assertEqual((fields["reason"], fields["dependency_phase"], fields["prepare_step"]),
                         ("installer_timeout", "resolve", "select"))
        self.assertTrue(fields["_untouched"])
        self.assertGreaterEqual(fields["retry_count"], 1)
        self.assertEqual(self.hung[:2], [150, 300])
        self.assertLessEqual(fields["prepare_seconds"], au.INSTALL_BUDGET - au.ROLLBACK_RESERVE)
        self.launched.assert_not_called()                 # the live tool was never started on

    def worker(self):
        job = {"attempt": "a", "from": OLD, "to": NEW, "install": self.info, "argv": ["agentj"], "phase": "prepared",
               "old": str(self.files[OLD]), "new": str(self.files[NEW]), "when": "night"}
        au.write(self.st, job, au.JOB)
        self.sent, self.ran = [], []

        def runner(argv):
            self.ran.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, f"agentj {OLD}", "")
        with patch.object(au, "__version__", OLD), patch.object(au, "enabled", return_value=True), \
                patch.object(au, "install_commands",
                             return_value=[au.Command([str(self.uv), "tool", "install", "--force", str(self.files[NEW])],
                                                      au.install_env(self.info))]), \
                patch.object(upgrade_diag, "after_failure", side_effect=lambda st, res, **kw: self.sent.append(res) or res):
            return au.run(self.st, runner=runner, restart=lambda v: None, sleep=self.sleep, clock=lambda: self.now)

    def test_nightly_update_that_never_gets_through_keeps_the_old_version(self):
        self.hanging(99)
        result = self.worker()
        self.assertEqual((result["result"], result["reason"], result["step"], result["phase"]),
                         ("failed", "installer_timeout", "install", "prepared"))
        self.assertGreaterEqual(result["retry_count"], 1)
        self.assertEqual(result["prepare_step"], "select")
        self.assertFalse([argv for argv in self.ran if "install" in argv])      # no install, so nothing to roll back
        body = upgrade_diag.metadata_body(self.sent[0])
        self.assertTrue(support.metadata_ok(body))
        lines = body.splitlines()
        self.assertIn("prepare_step: select", lines)
        self.assertTrue(any(line.startswith("prepare_seconds: ") for line in lines))
        self.assertTrue(any(line.startswith("step_seconds: ") for line in lines))
        self.assertNotIn(str(self.root), body)


def fixture_only(case, base):
    """Borrow a fixture: its own cases already run in their module."""
    for name in dir(base):
        if name.startswith("test_") and name not in vars(case):
            setattr(case, name, None)


fixture_only(SlowNetworkUpdate, prepared.ResolverPreparation)


class Wording(unittest.TestCase):
    RESULT = {"result": "failed", "reason": "installer_timeout", "step": "install", "phase": "prepared", "from": "0.30.62",
              "to": "0.30.68", "dependency_phase": "resolve", "uv_failure_class": "other", "retry_count": 3,
              "prepare_step": "select", "step_seconds": 130, "prepare_seconds": 600}

    def test_nightly_notice_says_nothing_changed_and_it_will_try_again(self):
        zh = au.notice({**self.RESULT, "auto_retry": True}, "zh")
        en = au.notice({**self.RESULT, "auto_retry": True}, "en")
        for text in (zh, en):
            self.assertNotIn("installer_timeout", text)
            self.assertIn("0.30.62", text)
            self.assertIn("/update", text)
            self.assertIn(upgrade_diag.UPGRADE_REPAIR_GUIDE, text)
        self.assertIn("准备依赖包超时", zh)
        self.assertIn("照常可用", zh)
        self.assertIn("自动再试", zh)
        self.assertIn("timed out", en)
        self.assertIn("working as before", en)
        self.assertIn("try again", en)

    def test_no_promise_of_another_try_when_automatic_updates_are_off(self):
        self.assertNotIn("自动再试", au.notice(dict(self.RESULT), "zh"))
        self.assertNotIn("自动再试", au.notice({**self.RESULT, "auto_retry": False}, "zh"))

    def test_phone_and_terminal_results_explain_the_timeout(self):
        sentence = upgrade_diag.owner_sentence(dict(self.RESULT), {"status": "off"}, "zh")
        self.assertIn("准备依赖包超时", sentence)
        self.assertIn("仍是 0.30.62，照常可用", sentence)
        self.assertIn(upgrade_diag.UPGRADE_REPAIR_GUIDE, sentence)
        block = update.result_block({**self.RESULT, "service": "unchanged", "exit": 1})
        self.assertIn("repair_guide: " + upgrade_diag.UPGRADE_REPAIR_GUIDE, block.splitlines())
        self.assertTrue(any(line.startswith("why: 准备依赖包超时") for line in block.splitlines()))
        self.assertIn("prepare_step: select", block.splitlines())

    def test_a_timeout_of_the_installer_itself_keeps_its_own_words(self):
        late = {"result": "failed", "reason": "installer_timeout", "step": "install", "from": "0.30.62", "to": "0.30.68"}
        self.assertEqual(upgrade_diag.dependency_message(late, "zh"), "")

    def test_another_try_is_promised_only_for_this_host_and_this_failure(self):
        class St:
            pass
        mine = {**self.RESULT, "from": __version__, "to": "99.0.0"}
        with patch.object(au, "schedules_installs", return_value=True):
            self.assertTrue(au.retry_scheduled(St(), mine))
            self.assertFalse(au.retry_scheduled(St(), {**mine, "from": "0.0.1"}))
            self.assertFalse(au.retry_scheduled(St(), {**mine, "reason": "dependency_conflict"}))
            self.assertFalse(au.retry_scheduled(St(), {**mine, "to": __version__}))
        with patch.object(au, "schedules_installs", return_value=False):
            self.assertFalse(au.retry_scheduled(St(), mine))


class ClosedFields(unittest.TestCase):
    def test_only_closed_words_and_small_numbers_are_projected(self):
        good = {"prepare_step": "fetch", "step_seconds": 90, "prepare_seconds": 600, "wheels_fetched": 3, "wheels_needed": 17}
        self.assertEqual(upgrade_diag.dependency_fields(good), good)
        bad = {"prepare_step": "/home/someone", "step_seconds": "90", "prepare_seconds": 10 ** 6, "wheels_fetched": -1,
               "wheels_needed": True}
        self.assertEqual(upgrade_diag.dependency_fields(bad), {})

    def test_metadata_lines_accept_exactly_those_values(self):
        for line in ("prepare_step: select", "prepare_step: probe", "step_seconds: 0", "prepare_seconds: 9999",
                     "wheels_fetched: 0", "wheels_needed: 999"):
            self.assertTrue(upgrade_diag.meta_line_ok(line), line)
        for line in ("prepare_step: other", "prepare_step: /tmp/x", "step_seconds: 12345", "step_seconds: -1",
                     "prepare_seconds: 1.5", "wheels_fetched: ١", "wheels_needed: 1000", "wheels_needed: abc"):
            self.assertFalse(upgrade_diag.meta_line_ok(line), line)


class AlreadyExplainedTimeout(unittest.TestCase):
    """The installer had named a permanent cause before its time ran out: that verdict stands."""

    def failure(self, words):
        def hang(args, **options):
            options["stderr"].write(words.encode())
            raise subprocess.TimeoutExpired(args, options["timeout"])
        with patch.object(upgrade_wheels.subprocess, "run", hang):
            with self.assertRaises(upgrade_wheels.PreparationFailure) as caught:
                upgrade_wheels.run_resolver(["uv", "pip", "compile"], None, 150)
        return caught.exception

    def test_tls_refused_address_and_missing_name_are_not_retried(self):
        for words, symptom in (("error: invalid peer certificate: UnknownIssuer", "tls"),
                               ("error: HTTP status client error (403 Forbidden) for url", "http_status"),
                               ("error: dns error: failed to lookup address information: NXDOMAIN", "dns")):
            with self.subTest(symptom=symptom):
                failure = self.failure(words)
                self.assertEqual((str(failure), failure.transient, failure.fields["uv_failure_class"]),
                                 ("dependency_download_failed", False, symptom))

    def test_a_leased_job_never_gets_more_than_its_lease_allows(self):
        clock = Clock()
        retry = upgrade_wheels.PreparationRetry(clock, clock.sleep, lease_end=clock.now + au.INSTALL_TIMEOUT + 400)
        given = []

        def operation(timeout):
            given.append(timeout)
            clock.now += timeout
            failure = upgrade_wheels.PreparationFailure("installer_timeout", "resolve", transient=True)
            failure.timed_out = True
            raise failure

        with self.assertRaises(upgrade_wheels.PreparationFailure):
            retry.call(operation, 150, step="select")
        self.assertEqual(given, [150, 245])                # twice 150 would be 300; only 245 s of the lease remain
        self.assertLessEqual(clock.now, retry.deadline)


class RepairInterpreter(unittest.TestCase):
    """upgrade_uv.repair_python: the download can be slow, the lookup that follows is local."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        (root / "upgrade-runtime").mkdir(mode=0o700)
        self.pin = types.SimpleNamespace(path=root / "agentj.whl")
        self.clock = Clock()
        self.retry = upgrade_wheels.PreparationRetry(self.clock, self.clock.sleep)
        self.ran = []

    def repair(self, hang):
        def run(args, **options):
            self.ran.append(args[2])
            if args[2] == hang:
                self.clock.now += options["timeout"]
                raise subprocess.TimeoutExpired(args, options["timeout"])
            return subprocess.CompletedProcess(args, 0, b"", b"")
        with patch.object(au, "check_artifact"), patch.object(upgrade_uv.subprocess, "run", run):
            with self.assertRaises(upgrade_wheels.PreparationFailure) as caught:
                upgrade_uv.repair_python(self.pin, "uv", self.retry)
        return caught.exception

    def test_a_slow_interpreter_download_is_tried_again(self):
        failure = self.repair("install")
        self.assertEqual((str(failure), failure.transient, failure.fields["prepare_step"]),
                         ("installer_timeout", True, "python"))
        self.assertGreater(len(self.ran), 1)
        self.assertEqual(set(self.ran), {"install"})
        self.assertEqual(self.clock.sleeps[0], au.INSTALL_BACKOFF[0])

    def test_a_stuck_local_lookup_is_not(self):
        failure = self.repair("find")
        self.assertEqual((str(failure), failure.transient, failure.fields["prepare_step"]),
                         ("installer_timeout", False, "local"))
        self.assertEqual((self.ran, self.clock.sleeps), (["install", "find"], []))


class RebuiltCache(stale.StaleDependencyCache):
    """0.30.69's rebuild of an old updater's leftover, on a slow link."""

    def rebuild(self, selections, fetch=None):
        self.old_updater_leftover()
        clock = Clock()
        self.retry = upgrade_wheels.PreparationRetry(clock, clock.sleep)
        self.clock = clock
        with patch(stale.PRIVATE, return_value=False), \
                patch.object(upgrade_wheels, "selected_wheels", side_effect=selections) as self.selector, \
                patch.object(upgrade_wheels, "download_dependency", side_effect=fetch or self.fetch):
            return upgrade_wheels.wheelhouse(self.pin, self.lock, "uv", prepare=True, retry=self.retry)

    def slow(self):
        failure = upgrade_wheels.PreparationFailure("installer_timeout", "resolve", transient=True)
        failure.timed_out = True
        return failure

    def test_discarded_entry_is_rebuilt_after_a_selection_timeout(self):
        self.assertEqual(self.rebuild([self.slow(), [self.name]]), self.house)
        self.assertEqual((self.selector.call_count, self.downloads, self.retry.count), (2, 1, 1))
        self.assertEqual((self.mode(), self.clock.sleeps), (0o600, [au.INSTALL_BACKOFF[0]]))

    def test_a_selection_that_never_gets_through_leaves_no_half_built_entry(self):
        with self.assertRaises(upgrade_wheels.PreparationFailure):
            self.rebuild([self.slow() for _ in range(9)])
        self.assertFalse(self.house.exists())                       # the old entry was discarded, nothing was published
        self.assertEqual([p.name for p in self.house.parent.iterdir()], [])

    def test_file_counts_do_not_outlive_a_failed_fetch(self):
        def refused(row, timeout):
            raise upgrade_wheels.PreparationFailure("dependency_download_failed", "download", "http_status")
        with self.assertRaises(upgrade_wheels.PreparationFailure) as caught:
            self.rebuild([[self.name]], fetch=refused)
        self.assertEqual((caught.exception.fields["wheels_fetched"], caught.exception.fields["wheels_needed"]), (0, 1))
        self.assertIsNone(self.retry.progress)


fixture_only(RebuiltCache, stale.StaleDependencyCache)


class SameVersionRepairWindow(unittest.TestCase):
    """update.repair_install has no rollback: an installer with only seconds left is never started."""

    def repair(self, left, retries):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        (root / "cache").mkdir()
        wheel = root / "cache" / "agentj.whl"
        wheel.write_bytes(b"signed bytes")
        command = au.Command(["uv", "tool", "install", "--force", str(wheel)], {})
        self.ran, self.snapshots = [], []

        def prepared_late(cmd, pin, previous, *, retry):
            retry.count = retries
            retry.deadline = retry.clock() + left            # what the selection retries left of the 300 s
            return cmd

        def runner(argv, **options):
            self.ran.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "agentj " + __version__, "")
        with patch.object(update, "require_native_install"), patch.object(update, "verified_wheel", return_value=wheel), \
                patch.object(au, "signed_cached_bytes", return_value=b"signed bytes"), \
                patch.object(update, "artifact_store", return_value=None), \
                patch.object(au, "pin_artifact", return_value=types.SimpleNamespace(path=wheel, root=root)), \
                patch.object(au, "install_commands", return_value=[command]), patch.object(au, "check_artifact"), \
                patch.object(upgrade_wheels, "prepare_update", side_effect=prepared_late), \
                patch.object(update, "_pre_snapshot", side_effect=self.snapshots.append):
            return update.repair_install(State(root / "state"), runner=runner, service_installed=False,
                                         info={"kind": "uv", "where": str(root)})

    def test_seconds_left_after_selection_retries_start_no_installer(self):
        result = self.repair(left=5, retries=2)
        self.assertEqual((result["result"], result["reason"], result["phase"], result["retry_count"]),
                         ("failed", "installer_timeout", "prepared", 2))
        self.assertEqual((self.ran, self.snapshots), ([], []))          # nothing ran: the installed program is as it was
        self.assertTrue(upgrade_diag.install_unchanged(result))
        self.assertIn("规定时间内没能完成", upgrade_diag.dependency_message(result, "zh"))

    def test_just_under_the_floor_is_refused_and_enough_time_installs(self):
        self.assertEqual(self.repair(left=au.INSTALL_MIN_ATTEMPT - 1, retries=1)["reason"], "installer_timeout")
        self.assertEqual(self.ran, [])
        result = self.repair(left=200, retries=1)
        self.assertEqual(result["reason"], "reinstalled")
        self.assertEqual(self.ran[0][:3], ["uv", "tool", "install"])


class HonestWording(unittest.TestCase):
    BASE = {"result": "failed", "reason": "installer_timeout", "step": "install", "phase": "prepared", "from": "0.30.62",
            "to": "0.30.68", "retry_count": 0}
    NETWORK = ("网络", "network")

    def texts(self, **fields):
        result = {**self.BASE, **fields}
        return [upgrade_diag.dependency_message(result, lang) + upgrade_diag.dependency_repair(lang, result)
                for lang in ("zh", "en")] + [au.notice(result, "zh"), au.notice(result, "en"),
                                             upgrade_diag.owner_sentence(result, {"status": "off"}, "zh")]

    def assert_not_blamed_on_network(self, texts):
        for text in texts:
            self.assertNotIn("installer_timeout", text)
            self.assertNotIn("网络", text)
            self.assertNotIn("network", text.lower())
            self.assertNotIn("connectivity", text.lower())
            self.assertIn(upgrade_diag.UPGRADE_REPAIR_GUIDE, text)
            self.assertIn("/update", text)

    def test_a_stuck_offline_check_is_a_local_matter(self):
        texts = self.texts(dependency_phase="resolve", prepare_step="probe", step_seconds=60, prepare_seconds=75)
        self.assert_not_blamed_on_network(texts)
        self.assertIn("磁盘太慢", texts[0])
        self.assertIn("disk too slow", texts[1])
        self.assertIn("仍是 0.30.62，照常可用", texts[2])
        self.assertIn("still on 0.30.62, working as before", texts[3])

    def test_a_stuck_local_helper_of_the_repair_is_a_local_matter(self):
        texts = self.texts(dependency_phase="resolve", prepare_step="local", step_seconds=60, prepare_seconds=200)
        self.assert_not_blamed_on_network(texts)
        self.assertIn("这台主机当时太忙", texts[0])

    def test_time_used_up_after_a_repair_blames_nothing_it_cannot_know(self):
        texts = self.texts(dependency_phase="resolve", prepare_seconds=590, retry_count=3)
        self.assert_not_blamed_on_network(texts)
        self.assertIn("规定时间内没能完成", texts[0])
        self.assertIn("ran out of time", texts[1])

    def test_a_step_that_could_not_start_blames_nothing_it_cannot_know(self):
        texts = self.texts(dependency_phase="download", prepare_step="fetch", step_seconds=0, prepare_seconds=601,
                           wheels_fetched=6, wheels_needed=17)
        self.assert_not_blamed_on_network(texts)

    def test_time_used_up_before_the_installer_has_plain_words_too(self):
        # the worker's own check after preparation: no dependency fields at all, only how long it took
        texts = self.texts(prepare_seconds=580, retry_count=2)
        self.assert_not_blamed_on_network(texts)
        self.assertIn("仍是 0.30.62，照常可用", texts[2])

    def test_a_cut_off_network_step_still_says_network(self):
        zh = upgrade_diag.dependency_message({**self.BASE, "dependency_phase": "resolve", "prepare_step": "python",
                                              "step_seconds": 150, "prepare_seconds": 150}, "zh")
        self.assertIn("网络太慢", zh)

    def test_an_installer_that_already_ran_keeps_its_own_words(self):
        late = {**self.BASE, "phase": "installing", "prepare_seconds": 580}
        self.assertEqual(upgrade_diag.dependency_message(late, "zh"), "")


class WorkerBudgetNotice(prepared.ResolverPreparation):
    def test_time_used_up_in_preparation_reaches_the_owner_in_plain_words(self):
        self.resolver([])
        real = upgrade_wheels.prepare_update

        def slow(cmd, pin, previous, *, retry):
            done = real(cmd, pin, previous, retry=retry)
            self.now = retry.deadline - 5                    # preparation succeeded with five seconds to spare
            return done
        with patch.object(upgrade_wheels, "prepare_update", side_effect=slow):
            result, _ = self.failed_worker()
        self.assertEqual((result["reason"], result["phase"]), ("installer_timeout", "prepared"))
        self.assertEqual(result["prepare_seconds"], au.INSTALL_BUDGET - au.ROLLBACK_RESERVE - 5)
        with patch.object(au, "__version__", result["from"]):
            for lang in ("zh", "en"):
                text = au.notice(result, lang)
                self.assertNotIn("installer_timeout", text)
                self.assertIn(result["from"], text)


fixture_only(WorkerBudgetNotice, prepared.ResolverPreparation)


class PromiseOnlyWhenTrue(unittest.TestCase):
    RESULT = {"result": "failed", "reason": "installer_timeout", "step": "install", "phase": "prepared",
              "from": __version__, "to": "99.0.0", "dependency_phase": "resolve", "uv_failure_class": "other",
              "retry_count": 3, "prepare_step": "select", "step_seconds": 130, "prepare_seconds": 600}

    def test_no_promise_once_the_program_was_touched(self):
        with patch.object(au, "schedules_installs", return_value=True):
            self.assertTrue(au.retry_scheduled(None, dict(self.RESULT)))
            self.assertFalse(au.retry_scheduled(None, {**self.RESULT, "result": "rolled_back"}))
            self.assertFalse(au.retry_scheduled(None, {**self.RESULT, "phase": "installing"}))
            self.assertFalse(au.retry_scheduled(None, {**self.RESULT, "phase": "rollback"}))

    def notice(self, switch):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        env = {"HOME": str(root), "XDG_CONFIG_HOME": str(root / "config"), "AGENTJ_STATE_DIR": str(root / "state")}
        with patch.dict("os.environ", env), patch("agentj.instance.current", return_value=1):
            st = State()
            st.init(relay="ws://127.0.0.1:1")
            from agentj import preferences
            stored = preferences.ensure()                        # the owner's own preference file
            stored.write_text(preferences.edit(stored.read_text(), "updates.auto_install", switch))
            self.assertEqual(au.enabled(st), switch)             # the stored preference, not a stand-in
            au.write(st, {"result": dict(self.RESULT), "notice_pending": True})
            return au.take_notice(st, "zh", harness=None)

    def test_the_stored_preference_decides_the_promise(self):
        self.assertIn("自动再试", self.notice(True))
        off = self.notice(False)
        self.assertNotIn("自动再试", off)
        self.assertIn("照常可用", off)


class ReportCap(unittest.TestCase):
    def test_the_new_lines_give_way_first_in_a_full_report(self):
        full = {"from": "0.30.62", "to": "0.30.68", "result": "failed", "reason": "installer_timeout", "step": "install",
                "exception_type": "URLError", "errno": errno.ETIMEDOUT, "failure_stage": "install",
                "artifact_stage": "wheel", "subreason": "internal_error", "cache_status": "miss",
                "artifact_sha256": "a" * 64, "stated_sha256": "b" * 64, "cache_path": "home",
                "cache_level": "upgrade_root", "cache_owner": "root", "cache_fallback": "tmp",
                "dependency_package": "cffi", "dependency_phase": "download", "dependency_source": "proxy",
                "uv_failure_class": "other", "retry_count": 1, "prepare_step": "fetch", "step_seconds": 0,
                "prepare_seconds": 601, "wheels_fetched": 6, "wheels_needed": 17}
        body = upgrade_diag.metadata_body(full, "uv")
        lines = body.splitlines()
        self.assertEqual(len(lines), upgrade_diag.META_MAX_LINES)
        self.assertTrue(support.metadata_ok(body))
        keys = [line.split(": ", 1)[0] for line in lines[1:]]
        for kept in ("from", "to", "result", "reason", "step", "dependency_phase"):
            self.assertIn(kept, keys)
        for cut in ("prepare_step", "step_seconds", "prepare_seconds", "wheels_fetched", "wheels_needed"):
            self.assertNotIn(cut, keys)


if __name__ == "__main__":
    unittest.main()
