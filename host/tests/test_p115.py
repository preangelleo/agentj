"""P115 (0.18.0): P91 Google tools foundation + P92 first-run checklist.

Google (google.py): pins for every host platform, pinned download → SHA-256 → only the one regular file `gog` leaves the
archive (links / extra paths refused) → version probe; an isolated GOG_HOME with nobody else's token variables; "off" removes
only our copy; existing tools are detected by presence only (file contents never read); purposes ask for minimum scopes and
are never `ready` in 0.18.0 (P86 projection with closed fields); doctor warns at most; a source checkout never downloads.
First run (first_run.py): read-only status never creates the journal; --resume creates it 0700/0600; required items cannot be
"unused"; a stale revision is `changed`; "later" stays deferred, never unused; unused items are not probed; remote_ready only when
every selected item is verified and fresh plus a phone round trip in the armed window; snapshots carry no device ids / paths.
Serve: setup_get / setup_mark over the E2E session — unsigned or wrongly signed marks change nothing, a valid one changes the
journal and pushes the card to every remote; a phone message during the armed leaving check is its evidence.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import io
import json
import os
import pathlib
import stat
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import controls, first_run, google, main_identity, onboarding, serve, wire  # noqa: E402

from test_l1 import Phone, _host, _state  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
DESIGN = HERE.parents[2] / "documentation" / "product"
P86_SCHEMA_FIELDS = {"id", "kind", "title", "source_ref", "scope", "provides", "depends_on", "status", "credential_name", "check"}
P86_STATUS = {"discovered", "missing", "unverified", "ready", "stale", "unavailable", "conflict"}
P86_CHECK = {"ok", "auth_required", "not_found", "permission_denied", "offline", "timeout", "unsupported"}


def fake_gog(version="v0.43.0", extra=None, link=False) -> bytes:
    """A tar.gz shaped like the real release (./ + ./gog) whose gog prints a version and an empty account list."""
    script = ("#!/bin/sh\ncase \"$*\" in\n  --version) echo '" + version + " (fake)';;\n"
              "  *auth\\ list*) echo '{\"accounts\": []}';;\n  *) echo '{}';;\nesac\n").encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        d = tarfile.TarInfo("./"); d.type = tarfile.DIRTYPE; tf.addfile(d)
        if link:
            ln = tarfile.TarInfo("./gog"); ln.type = tarfile.SYMTYPE; ln.linkname = "/bin/sh"; tf.addfile(ln)
        else:
            ti = tarfile.TarInfo("./gog"); ti.size = len(script); ti.mode = 0o755; tf.addfile(ti, io.BytesIO(script))
        for name, data in (extra or {}).items():
            ti = tarfile.TarInfo(name); ti.size = len(data); tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def fetcher(blob: bytes, calls: list):
    def fetch(urls, dest, size, sha, out, label):
        calls.append(list(urls))
        dest.write_bytes(blob)
        return urls[0]
    return fetch


class GooglePins(unittest.TestCase):
    def test_every_host_platform_is_pinned_and_mirrors_keep_the_pin(self):
        self.assertEqual(set(google.GOG_ASSETS), {("darwin", "arm64"), ("darwin", "x86_64"), ("linux", "x86_64"), ("linux", "aarch64")})
        for name, size, sha in google.GOG_ASSETS.values():
            self.assertTrue(name.startswith("gogcli_0.43.0_") and name.endswith(".tar.gz"))
            self.assertGreater(size, 10_000_000)
            self.assertRegex(sha, r"^[0-9a-f]{64}$")
        # the linux amd64 pin is the one P91 downloaded and verified
        ev = DESIGN / "p91/evidence/install.json"
        if ev.exists():                  # design evidence lives in the private repo only
            gog = next(e for e in json.loads(ev.read_text()) if e["repo"] == "openclaw/gogcli")
            self.assertEqual(google.GOG_ASSETS[("linux", "x86_64")][1:], (gog["archive_bytes"], gog["sha256"]))
        with mock.patch.dict(os.environ, {google.URLS_ENV: ""}):
            u = google.urls("gogcli_0.43.0_linux_amd64.tar.gz")
        self.assertEqual(u[0], "https://agentj.app/dl/tools/gog/0.43.0/gogcli_0.43.0_linux_amd64.tar.gz")
        self.assertTrue(u[1].startswith("https://github.com/openclaw/gogcli/releases/download/v0.43.0/"))

    def test_purposes_are_minimum_scopes_never_all_or_full(self):
        for pid, p in google.PURPOSES.items():
            self.assertTrue(p["scopes"], pid)
            for s in p["scopes"]:
                self.assertTrue(s.startswith("https://www.googleapis.com/auth/"), s)
                self.assertNotIn(s.rsplit("/", 1)[1], ("gmail.modify", "mail.google.com", "drive", "calendar", "youtube",
                                                       "webmasters", "cloud-platform"))
            for flag in p["gog"] or []:
                self.assertNotIn(flag, ("all", "user", "--full"))
        plan = google.plan(["gmail-read", "youtube-publish"])
        self.assertEqual(plan["apis"], ["gmail.googleapis.com", "youtube.googleapis.com"])
        self.assertIn("Submit for verification", plan["never"])
        with self.assertRaises(google.GoogleError):
            google.plan(["everything"])


class GoogleInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def pinned(self, blob):
        import hashlib
        return mock.patch.object(google, "asset", return_value=("gog.tar.gz", len(blob), hashlib.sha256(blob).hexdigest()))

    def test_install_pins_isolates_probes_and_is_idempotent(self):
        blob = fake_gog()
        with self.pinned(blob):
            res = google.install(self.st, fetch=fetcher(blob, self.calls))
            self.assertEqual((res["ok"], res["result"]), (True, "installed"), res)
            self.assertEqual(google.install(self.st, fetch=fetcher(blob, self.calls))["result"], "already")
            self.assertEqual(len(self.calls), 1)
            b = google.binary(self.st)
            self.assertTrue(stat.S_ISREG(b.lstat().st_mode))
            for d in (google.root(self.st), google.root(self.st) / "tools", google.tool_dir(self.st)):
                self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700, d)
            self.assertEqual(stat.S_IMODE(google._state_path(self.st).stat().st_mode), 0o600)
            self.assertFalse((google.root(self.st) / "download" / "gog.tar.gz").exists(), "archive removed after extraction")
            s = google.check(self.st)
            self.assertEqual((s["tool"]["state"], s["tool"]["fresh"], s["tool"]["accounts"]), ("installed", True, 0))
            row = google.doctor_row(self.st)
            self.assertEqual(row[0], "ok")

    def test_isolated_environment_drops_other_credentials(self):
        with mock.patch.dict(os.environ, {"GOG_ACCESS_TOKEN": "x", "GOOGLE_APPLICATION_CREDENTIALS": "/a", "GOG_HOME": "/elsewhere",
                                          "GOG_ACCOUNT": "a@b", "CLOUDSDK_CONFIG": "/c"}):
            e = google.env(self.st)
        self.assertEqual(e["GOG_HOME"], str(google.gog_home(self.st)))
        for k in ("GOG_ACCESS_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS", "GOG_ACCOUNT", "CLOUDSDK_CONFIG"):
            self.assertNotIn(k, e)

    def test_bad_archives_and_versions_are_refused(self):
        for blob, why in ((fake_gog(link=True), "symlink"), (fake_gog(version="v9.9.9"), "version"),
                          (fake_gog(extra={"../evil": b"x"}), "extra member is ignored")):
            st = _state(tempfile.mkdtemp(dir=self.tmp.name))
            with self.pinned(blob):
                res = google.install(st, fetch=fetcher(blob, self.calls))
            if why == "extra member is ignored":
                self.assertTrue(res["ok"], res)
                self.assertFalse((pathlib.Path(st.root).parent / "evil").exists())
                continue
            self.assertFalse(res["ok"], why)
            self.assertFalse(google.installed(st), why)
            self.assertEqual(google.read(st)["attempt"]["result"], "failed")
            self.assertEqual(google.doctor_row(st)[0], "warn", "optional tool: warn, never fail")
        # a fetch that hands back other bytes than the pin
        blob = fake_gog()
        with mock.patch.object(google, "asset", return_value=("gog.tar.gz", len(blob), "0" * 64)):
            res = google.install(_state(tempfile.mkdtemp(dir=self.tmp.name)), fetch=fetcher(blob, self.calls))
        self.assertEqual((res["ok"], res.get("reason")), (False, "sha256"))

    def test_off_removes_only_our_copy_and_on_reinstalls(self):
        blob = fake_gog()
        with self.pinned(blob):
            google.install(self.st, fetch=fetcher(blob, self.calls))
            (google.gog_home(self.st) / "keep.txt").write_text("owner data")
            self.assertEqual(google.set_preinstall(self.st, False)["result"], "off")
            self.assertFalse(google.binary(self.st).exists())
            self.assertTrue((google.gog_home(self.st) / "keep.txt").exists(), "gog-home is never deleted")
            self.assertEqual(google.install(self.st, fetch=fetcher(blob, self.calls))["result"], "disabled")
            self.assertEqual(google.doctor_row(self.st)[0], "ok")
            self.assertFalse(google.wanted_background(self.st))
            google.set_preinstall(self.st, True)
            self.assertEqual(google.install(self.st, fetch=fetcher(blob, self.calls))["result"], "installed")

    def test_background_preinstall_rules(self):
        with mock.patch.dict(os.environ, {google.PREINSTALL_ENV: "off"}):
            self.assertFalse(google.wanted_background(self.st))
        with mock.patch.dict(os.environ, {google.PREINSTALL_ENV: ""}):
            self.assertFalse(google.wanted_background(self.st), "a source checkout never downloads by itself")
        with mock.patch.dict(os.environ, {google.PREINSTALL_ENV: "on"}):
            self.assertEqual(google.wanted_background(self.st), google.asset() is not None)
            google._attempt(self.st, {"result": "failed", "reason": "download"})
            self.assertFalse(google.wanted_background(self.st), "retried at most once a day")
            self.assertTrue(google.wanted_background(self.st, now=int(time.time()) + google.RETRY_SECS + 1) or google.asset() is None)


class GoogleReadOnly(unittest.TestCase):
    def test_existing_tools_presence_only_and_registry_never_ready(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            cfg = pathlib.Path(d) / "cfg"
            (cfg / "gogcli").mkdir(parents=True)
            secret = cfg / "gogcli" / "config.json"
            secret.write_text('{"refresh_token": "SECRET-NEVER-READ"}')
            os.chmod(secret, 0)          # unreadable: presence detection must not need to open it
            try:
                with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(cfg), "PATH": d}):
                    s = google.status(st)
            finally:
                os.chmod(secret, 0o600)
            self.assertTrue(s["existing"]["gog"]["config"])
            self.assertFalse(s["existing"]["gcloud"]["on_path"])
            self.assertNotIn("SECRET", json.dumps(s))
            self.assertTrue(all(p["api_state"] == "existing_unverified" for p in s["purposes"]))
            entries = google.registry_entries(st, s)
            self.assertEqual(entries[0]["id"], "google-cli")
            for e in entries:
                self.assertEqual(set(e), P86_SCHEMA_FIELDS)
                self.assertIn(e["status"], P86_STATUS)
                self.assertIn(e["check"]["status"], P86_CHECK)
                self.assertRegex(e["id"], r"^[a-z0-9][a-z0-9.-]{0,79}$")
                if e["kind"] == "credential":
                    self.assertRegex(e["credential_name"], r"^[A-Z][A-Z0-9_]{0,79}$")
                    self.assertNotEqual(e["status"], "ready", "no Google API purpose is ready in 0.18.0")
                    self.assertEqual(e["check"]["status"], "unsupported")
                else:
                    self.assertIsNone(e["credential_name"])
            # gcloud alone says nothing about Gmail / YouTube grants
            with mock.patch.object(google, "detect_existing", return_value={"gog": {"on_path": False, "config": False},
                                   "gcloud": {"on_path": True, "config": True}, "gws": {"on_path": False, "config": False}}):
                self.assertTrue(all(p["api_state"] == "not_configured" for p in google.status(st)["purposes"]))


# ------------------------------------------------------------------ first run
def fake_probes(states: dict):
    def probe(st, iid, ctx):
        return states.get(iid, ("verified", None))
    return mock.patch.object(first_run, "probe", side_effect=probe)


class Checklist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_is_the_designed_thirty_items_without_cycles(self):
        self.assertEqual(len(first_run.ITEMS), 30)
        inv = DESIGN / "p92/inventory.json"
        if inv.exists():
            self.assertEqual({i[0] for i in first_run.ITEMS}, {i["id"] for i in json.loads(inv.read_text())["items"]})
        self.assertEqual({i[1] for i in first_run.ITEMS}, set(first_run.GROUPS))
        seen, order = set(), first_run._order()
        for iid in order[:-1]:
            for dep in first_run.BY_ID[iid][4]:
                self.assertIn(dep, first_run.BY_ID)
        self.assertEqual(order[-1], "exit")
        self.assertEqual(set(first_run.REQUIRED), {"seat", "harness", "desktop", "phone", "passkey", "admin", "awake", "service",
                                                   "updates", "network", "exit"})

        def visit(i, path=()):
            self.assertNotIn(i, path, "dependency cycle")
            for d in first_run.BY_ID[i][4]:
                visit(d, path + (i,))
        for i in first_run.BY_ID:
            visit(i)

    def test_status_is_read_only_and_resume_creates_a_private_journal(self):
        res = first_run.status_(self.st)
        self.assertEqual(res["result"], "needs_setup")
        self.assertFalse(first_run.path(self.st).exists(), "read-only never creates the journal")
        with fake_probes({"exit": ("pending", "start_check")}):
            res = first_run.resume(self.st)
        p = first_run.path(self.st)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
        self.assertEqual(res["result"], "in_progress")
        self.assertEqual(res["current"], "exit")
        text = json.dumps(res, ensure_ascii=False)
        for did in self.st.devices():
            self.assertNotIn(did, text)
        self.assertNotIn(str(self.st.root), text)
        self.assertNotIn(str(self.st.root), p.read_text())

    def test_choices_cas_required_later_unused(self):
        with fake_probes({"tg": ("pending", "not_configured"), "awake": ("waiting_local", "sleep_enabled")}):
            rev = first_run.resume(self.st)["revision"]
            with self.assertRaises(first_run.SetupError) as e:
                first_run.mark(self.st, "awake", "unused", rev, "n1")
            self.assertEqual(e.exception.reason, "invalid", "required items cannot be unused")
            with self.assertRaises(first_run.SetupError) as e:
                first_run.mark(self.st, "tg", "unused", rev - 1, "n1")
            self.assertEqual(e.exception.reason, "changed")
            snap = first_run.mark(self.st, "tg", "unused", rev, "n1")
            tg = next(i for i in snap["items"] if i["id"] == "tg")
            self.assertEqual(tg["choice"], "unused")
            snap = first_run.mark(self.st, "awake", "later", snap["revision"], "n2")
            aw = next(i for i in snap["items"] if i["id"] == "awake")
            self.assertEqual((aw["choice"], aw["state"]), ("selected", "deferred"), "later is unfinished, never unused")
            snap = first_run.check(self.st)
            self.assertEqual(next(i for i in snap["items"] if i["id"] == "awake")["state"], "deferred")
            self.assertFalse(snap["remote_ready"])
            self.assertIn("awake", snap["blocking"])
        with fake_probes({}):            # awake now passes; tg is unused, so it is not probed nor blocking
            snap = first_run.check(self.st)
        aw = next(i for i in snap["items"] if i["id"] == "awake")
        self.assertEqual(aw["state"], "verified")
        self.assertNotIn("tg", snap["blocking"])
        rec = first_run.read(self.st)
        self.assertEqual(rec["items"]["tg"]["owner_decision_ref"], "n1")

    def test_remote_ready_needs_every_selected_item_fresh_and_a_phone_round_trip(self):
        with fake_probes({}):
            first_run.resume(self.st)
        rec = first_run.read(self.st)
        self.assertEqual(first_run.remote_ready(rec), (True, []), "the fake probes verify everything, exit included")
        # with the real exit adapter: not ready until the armed phone round trip
        real = first_run.probe
        with mock.patch.object(first_run, "probe", side_effect=lambda st, i, c: real(st, i, c) if i == "exit" else ("verified", None)):
            snap = first_run.check(self.st)
            self.assertEqual(snap["blocking"], ["exit"])
            self.assertIsNone(first_run.phone_round_trip(self.st, "phone"), "not armed: no evidence")
            snap = first_run.mark(self.st, "exit", "start_exit", snap["revision"], "n3")
            self.assertTrue(snap["exit_armed"])
            self.assertIsNone(first_run.phone_round_trip(self.st, "computer"), "only a phone counts")
            snap = first_run.phone_round_trip(self.st, "phone", "cellular")
            self.assertTrue(snap["remote_ready"])
            self.assertIn("从现在起，你可以离开电脑", first_run.leave_text(first_run.read(self.st), "zh"))
            rec = first_run.read(self.st)
            self.assertEqual(rec["exit"]["evidence"]["net"], "cellular")
            # stale: verified items expire (browser 15 min, others 24 h) and the round trip is only good for 2 h
            later = int(time.time()) + first_run.EXIT_FRESH + 10
            with mock.patch.object(first_run, "_now", return_value=later):
                self.assertFalse(first_run.remote_ready(first_run.read(self.st), later)[0])
        txt = first_run.leave_text(first_run.read(self.st), "en")
        self.assertTrue(txt.startswith("You can now leave"), txt)

    def test_armed_window_expires(self):
        with fake_probes({}):
            rev = first_run.resume(self.st)["revision"]
            first_run.mark(self.st, "exit", "start_exit", rev, "n")
        with mock.patch.object(first_run, "_now", return_value=int(time.time()) + first_run.EXIT_WINDOW + 5):
            self.assertIsNone(first_run.phone_round_trip(self.st, "phone"))

    def test_adapter_registry_and_unsupported_rows(self):
        res = first_run.resume(self.st)
        rows = {i["id"]: i for i in res["items"]}
        self.assertEqual(rows["gmail"]["state"], "attention")
        self.assertEqual(rows["gmail"]["reason"], "browser_not_running")
        self.assertEqual(rows["google"]["state"], "unsupported")
        self.assertEqual(rows["google"]["reason"], "api_in_0.18.1")
        first_run.register_adapter("gmail", lambda st, ctx: ("verified", None))
        try:
            rows = {i["id"]: i for i in first_run.check(self.st, ["gmail"])["items"]}
            self.assertEqual(rows["gmail"]["state"], "verified")
        finally:
            first_run._ADAPTERS.clear()
        with self.assertRaises(ValueError):
            first_run.register_adapter("nope", lambda st, ctx: ("verified", None))

    def test_doctor_rows_never_fail(self):
        self.assertEqual(first_run.doctor_row(self.st)[0], "ok")
        with fake_probes({"seat": ("waiting_local", "not_bound")}):
            first_run.resume(self.st)
        self.assertEqual(first_run.doctor_row(self.st)[0], "warn")

    def test_onboarding_offers_the_checklist_once_after_a_fresh_install(self):
        rec = onboarding._blank()
        now = int(time.time())
        rec.update(welcomed={"at": now, "device": "d", "kind": "computer"}, computer={"at": now, "device": "d"},
                   phone={"at": now, "device": "p"})
        onboarding._write(self.st, rec)
        with mock.patch.object(onboarding.cloud, "read_cloud", return_value={"tenant": {}}):
            note = onboarding.turn_note(self.st, "zh")
            self.assertIn("agentj setup checklist --resume --json", note)
            self.assertEqual(onboarding.turn_note(self.st, "zh"), "", "offered once")
        legacy = _state(tempfile.mkdtemp(dir=self.tmp.name))
        rec["welcomed"]["kind"] = "legacy"
        onboarding._write(legacy, rec)
        with mock.patch.object(onboarding.cloud, "read_cloud", return_value={"tenant": {}}):
            self.assertEqual(onboarding.turn_note(legacy, "en"), "", "existing hosts: only on request")
        self.assertIn("agentj setup checklist --resume --json", onboarding.welcome_prompt("phone", "x", {"computer": True, "phone": True, "seat": True}, "en"))

    def test_identity_line_rules(self):
        for lang in ("zh", "en"):
            line = main_identity.FIRST_RUN_LINE[lang]
            self.assertIn("agentj setup checklist", line)
            self.assertIn("agentj google status", line)
            self.assertIn("0.18.1", line)
            self.assertIn("Submit for verification", line)
            self.assertIn(line, main_identity.prompt({"language": lang}))
        main_identity.verify_core()       # the hashed core is unchanged


# ------------------------------------------------------------------ signatures and serve
def sign(ph: Phone, target: dict, extra: dict, *, key=None, n=None) -> dict:
    n = n or os.urandom(16).hex()
    ts = int(time.time() * 1000)
    msg = controls.signed_message(ph.channel, ph.did, "setup_mark", n, ts, controls.object_digest("setup_mark", target))
    return {**target, **extra, "n": n, "ts": ts, "sig": wire.b64u((key or ph.sk).sign(msg))}


class ServeSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def got(self, t, r=None):
        return [o for _, o in self.sent if o["t"] == t and (r is None or o.get("r") == r)]

    def test_js_and_python_sign_the_same_bytes(self):
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node not installed")
        o = {"item": "tg", "choice": "unused", "rev": 7}
        wire_js = (HERE.parents[1] / "protocol" / "wire.js").as_uri()
        js = (f"import {{ controlMessage }} from {json.dumps(wire_js)};\nconsole.log(Buffer.from(await controlMessage('CH','DEV',"
              f"'setup_mark','{'a' * 32}',1790000000123,{json.dumps(o)})).toString('hex'));")
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), controls.signed_message("CH", "DEV", "setup_mark", "a" * 32, 1790000000123,
                                                                   controls.object_digest("setup_mark", o)).hex())

    def test_get_mark_push_and_round_trip_over_the_session(self):
        ph = Phone(self.st, "网页 · iOS Safari")
        other = Phone(self.st, "别的")
        host = _host(self.st, self.sent)
        s = serve.Session(cid=11, state="ready", device=ph.did, name="网页 · iOS Safari", pub=ph.pub)
        host.sessions[11] = s
        real = first_run.probe

        def probe(st, i, ctx):
            return real(st, i, ctx) if i == "exit" else ("pending", "not_configured") if i == "tg" else ("verified", None)

        async def go():
            with mock.patch.object(first_run, "probe", side_effect=probe):
                await host._app(s, {"t": "setup_get", "r": "g0"})
                self.assertEqual(self.got("setup_card", "g0")[0]["result"], "needs_setup")
                self.assertFalse(first_run.path(self.st).exists())
                snap = await asyncio.to_thread(first_run.resume, self.st, host.setup_serve())
                rev = snap["revision"]
                tgt = {"item": "tg", "choice": "unused", "rev": rev}
                await host._app(s, {"t": "setup_mark", "r": "x1", **tgt})                       # unsigned
                await host._app(s, sign(ph, tgt, {"t": "setup_mark", "r": "x2"}, key=other.sk))  # wrong key
                await host._app(s, sign(ph, {**tgt, "item": "botkey"}, {"t": "setup_mark", "r": "x3", **tgt}))  # other item
                await host._app(s, sign(ph, {**tgt, "item": "awake"}, {"t": "setup_mark", "r": "x4"}))         # required
                self.assertEqual([(o["ok"], o["why"]) for o in self.got("ctl_res")],
                                 [(False, "shape"), (False, "bad_signature"), (False, "bad_signature"), (False, "invalid")])
                self.assertEqual(first_run.read(self.st)["items"]["tg"]["choice"], "undecided")
                await host._app(s, sign(ph, tgt, {"t": "setup_mark", "r": "ok"}))
                await asyncio.sleep(0.05)
                self.assertTrue(self.got("ctl_res", "ok")[0]["ok"])
                self.assertEqual(first_run.read(self.st)["items"]["tg"]["choice"], "unused")
                pushed = [o for o in self.got("setup_card") if o.get("show")]
                self.assertTrue(pushed and pushed[-1]["revision"] == rev + 1, "the new snapshot goes to the remotes")
                again = sign(ph, {**tgt, "rev": rev + 1, "choice": "selected"}, {"t": "setup_mark", "r": "stale"})
                again["rev"] = rev                                                  # signed for another revision
                await host._app(s, again)
                self.assertEqual(self.got("ctl_res", "stale")[0]["why"], "bad_signature")
                stale = sign(ph, {**tgt, "choice": "selected"}, {"t": "setup_mark", "r": "old"})
                await host._app(s, stale)
                self.assertEqual(self.got("ctl_res", "old")[0]["why"], "changed")
                # the leaving check: armed on the card, then any phone message is the evidence
                cur = first_run.read(self.st)["revision"]
                await host._app(s, sign(ph, {"item": "exit", "choice": "start_exit", "rev": cur}, {"t": "setup_mark", "r": "ex"}))
                self.assertTrue(self.got("ctl_res", "ex")[0]["ok"])
                await host._app(s, {"t": "setup_get", "r": "g1", "net": "cellular"})
                await host._setup_round_trip(s)
                await asyncio.sleep(0.05)
                self.assertTrue(self.got("setup_card")[-1]["remote_ready"])
                self.assertEqual(first_run.read(self.st)["exit"]["evidence"]["net"], "cellular")
        asyncio.run(go())
        cl = [json.loads(x) for x in self.st.controls_path.read_text().splitlines()]
        self.assertEqual([c["result"] for c in cl if c["action"] == "setup_mark"][:5],
                         ["refused:shape", "refused:bad_signature", "refused:bad_signature", "invalid", "ok"])

    def test_elevate_socket_ops(self):
        host = _host(self.st, self.sent)

        async def go():
            res = await host.elevate._p115({"t": "setup", "op": "status"})
            self.assertEqual(res["result"], "needs_setup")
            self.assertEqual((await host.elevate._p115({"t": "setup", "op": "mark"}))["why"], "shape",
                             "the Agent's socket cannot mark choices")
            res = await host.elevate._p115({"t": "google", "op": "status"})
            self.assertEqual(res["stage"], "0.18.0")
            self.assertFalse((await host.elevate._p115({"t": "google", "op": "rm -rf"}))["ok"])
            with mock.patch.object(first_run, "probe", return_value=("verified", None)):
                res = await host.elevate._p115({"t": "setup", "op": "resume"})
            self.assertEqual(res["card"], "sent")
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
