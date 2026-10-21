"""P428 §1: guided Google Calendar authorization through Agent J's pinned gog (`agentj google connect|disconnect`).

A fake gog (an executable script in a temp dir) records every argv and answers with canned JSON; `binary()` / `installed()`
point at it. Covered: every step transition, client-file refusals on the CLI side (symlink / too big / missing / relative) and on the
host side (bytes that are not a Desktop client JSON), the host never taking a path (F4), the e-mail never reaching
state.json, results or the host log (only its sha256), consent failure / timeout words, the probe's API-disabled word,
disconnect removing only our own account (F3), gog's allowlisted environment and keyring namespace (F3 / F7), purposes that
cannot be connected, `--enable-commands-exact` on every Calendar call, the P86 projection (`ready` only for Calendar purposes
after `ready`) and elevate.sock shape refusals.
"""
import _hermetic  # noqa: F401,I001  isolated test HOME/state before importing the host
import asyncio
import base64
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import google, google_connect as gc  # noqa: E402

from test_l1 import _host, _state  # noqa: E402

EMAIL = "owner.p428@example.com"
CLIENT = b'{"installed": {"client_id": "p428.apps.googleusercontent.com", "note": "P428-CLIENT-BYTES"}}'
FAKE = r'''#!{py} -IS
import json, os, pathlib, sys, time
d = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
j = " ".join(args)
data = sys.stdin.read() if "credentials set -" in j else None
with open(d / "calls.jsonl", "a") as f:
    f.write(json.dumps({{"argv": args, "stdin": data, "ks": os.environ.get("GOG_KEYRING_SERVICE_NAME"),
                        "env": sorted(os.environ)}}) + "\n")
mode = json.loads((d / "mode.json").read_text()) if (d / "mode.json").exists() else {{}}
accf = d / "accounts.json"
accs = json.loads(accf.read_text()) if accf.exists() else []
def out(code, obj=None, err=""):
    if obj is not None:
        print(json.dumps(obj))
    if err:
        print(err, file=sys.stderr)
    sys.exit(code)
if "credentials set" in j:
    out(mode.get("cred", 0), {{"saved": True}} if not mode.get("cred") else None, "invalid credentials.json for x@y.z")
if "auth add" in j:
    time.sleep(mode.get("sleep", 0))
    if mode.get("add", 0) == 0:
        email = args[args.index("add") + 1]
        accf.write_text(json.dumps(sorted(set(accs) | {{email}})))
    out(mode.get("add", 0), None, mode.get("add_err", ""))
if "auth list" in j:
    out(0, {{"accounts": [{{"email": e, "services": ["calendar"]}} for e in accs]}})
if "auth remove" in j:
    email = args[args.index("remove") + 1]
    accf.write_text(json.dumps([a for a in accs if a != email]))
    out(0, {{}})
if "calendar calendars" in j:
    if mode.get("probe", 0):
        out(mode["probe"], {{"error": {{"message": mode.get("probe_msg", "boom " + "".join(accs))}}}})
    out(0, {{"calendars": [{{"id": "primary"}}]}})
if "calendar" in j:
    out(0, {{"events": []}})
out(0, {{}})
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p428-")
        self.addCleanup(self.tmp.cleanup)
        self.d = pathlib.Path(self.tmp.name)
        self.st = _state(self.d)
        self.fake = self.d / "fakegog"
        self.fake.mkdir()
        exe = self.fake / "gog"
        exe.write_text(FAKE.format(py=sys.executable))
        exe.chmod(0o755)
        for p in (mock.patch.object(google, "binary", return_value=exe),
                  mock.patch.object(google, "installed", return_value=True)):
            p.start()
            self.addCleanup(p.stop)
        dl = self.d / "downloads"
        dl.mkdir()
        self.client = dl / "client_secret_desktop.json"
        self.client.write_bytes(CLIENT)

    def mode(self, **kw):
        (self.fake / "mode.json").write_text(json.dumps(kw))

    def calls(self) -> list[dict]:
        f = self.fake / "calls.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def step(self, purpose="calendar-write"):
        return google.read(self.st)["connections"].get(purpose)

    def connect(self, client_file=None, **kw):
        """As the CLI does it (F4): the file is read here, only its bytes reach the host."""
        if client_file is not None:
            kw["client"] = gc.read_client(str(client_file))
            self.assertIsNotNone(kw["client"], client_file)
        return google.connect(self.st, "calendar-write", wait=True, **kw)

    def cli(self, **kw):
        a = dict(action="connect", connect_status=False, purpose=["calendar-write"], client_file=None, account=None)
        return gc.cli_request(SimpleNamespace(**{**a, **kw}))

    def no_email_anywhere(self, *results):
        blob = json.dumps(results) + google._state_path(self.st).read_text()
        if self.st.log_path.exists():
            blob += self.st.log_path.read_text()
        self.assertNotIn(EMAIL, blob)
        self.assertNotIn("example.com", blob)
        self.assertNotIn("P428-CLIENT-BYTES", blob)


class Steps(Base):
    def test_full_flow_step_transitions(self):
        r0 = self.connect()
        self.assertEqual((r0["ok"], r0["step"]), (True, "client_needed"))
        self.assertEqual([o["url"] for o in r0["owner"]][1],
                         "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com")
        self.assertEqual(r0["scopes"], ["https://www.googleapis.com/auth/calendar"])
        self.assertIn("events-only", r0["scope_note"])
        self.assertEqual(self.calls(), [], "client_needed runs nothing")
        r1 = self.connect(client_file=str(self.client))
        self.assertEqual((r1["ok"], r1["step"]), (True, "client_saved"))
        self.assertIn("delete", r1["delete_download"])
        cred = self.calls()[-1]
        self.assertEqual(cred["argv"], ["--no-input", "--json", "auth", "credentials", "set", "-"])
        self.assertEqual(cred["stdin"], CLIENT.decode(), "client bytes go to gog on stdin, never as a path it could re-open")
        r2 = self.connect(account=EMAIL)
        self.assertEqual((r2["ok"], r2["step"]), (True, "ready"))
        add = [c["argv"] for c in self.calls() if "add" in c["argv"]][0]
        self.assertEqual(add, ["auth", "add", EMAIL, "--services", "calendar", "--force-consent", "--timeout", "5m"])
        self.assertEqual(self.calls()[-1]["argv"], ["-a", "auto", "--json", "--no-input", "--enable-commands-exact",
                                                     "calendar.calendars", "calendar", "calendars", "--max", "1"])
        self.assertEqual(set(self.step()), {"step", "at", "acct"})
        self.assertEqual(self.step()["acct"], hashlib.sha256(EMAIL.casefold().encode()).hexdigest())
        self.assertNotIn("acct", google.connect_status(self.st)["connections"]["calendar-write"])
        s = google.connect_status(self.st)
        self.assertEqual(s["connections"]["calendar-write"]["step"], "ready")
        self.no_email_anywhere(r0, r1, r2, s, google.status(self.st))

    def test_background_consent_thread_and_status(self):
        self.connect(client_file=self.client)
        self.mode(sleep=1)
        r = google.connect(self.st, "calendar-write", account=EMAIL)
        self.assertEqual((r["step"], r.get("running")), ("oauth_consent_required", True))
        self.assertEqual(google.connect_status(self.st, "calendar-write")["connections"]["calendar-write"].get("running"), True)
        busy = google.connect(self.st, "calendar-write", account=EMAIL)
        self.assertEqual(busy["why"], "busy")
        self.assertEqual(google.disconnect(self.st, "calendar-write")["why"], "busy")
        gc._RUNNING[gc._key(self.st)].join(10)
        self.assertEqual(self.step()["step"], "ready")
        self.no_email_anywhere(r, busy)

    def test_calendar_read_maps_to_readonly(self):
        r = google.connect(self.st, "calendar-read", wait=True, client=gc.read_client(str(self.client)), account=EMAIL)
        self.assertEqual(r["step"], "ready")
        add = [c["argv"] for c in self.calls() if "add" in c["argv"]][0]
        self.assertEqual(add[3:6], ["--services", "calendar", "--readonly"])

    def test_purpose_not_connectable(self):
        for pid in ("gmail-read", "drive-file", "youtube-publish", "nope"):
            self.assertEqual(google.connect(self.st, pid)["why"], "purpose_not_connectable")
            self.assertEqual(google.disconnect(self.st, pid)["why"], "purpose_not_connectable")
        self.assertEqual(self.calls(), [])
        self.assertEqual(google.connect_status(self.st, "gmail-read")["connections"]["gmail-read"]["step"], "not_connectable")

    def test_not_installed(self):
        with mock.patch.object(google, "installed", return_value=False):
            r = self.connect(client_file=str(self.client))
        self.assertEqual((r["ok"], r["why"]), (False, "not_installed"))
        self.assertEqual(self.calls(), [])


class ClientFile(Base):
    def refused(self, path):
        """The CLI refuses with the caller's own permissions: nothing is sent, the host and gog never see it."""
        res, req = self.cli(client_file=str(path))
        self.assertEqual((res["ok"], res["why"], req), (False, "client_file_refused", {}), path)
        self.assertIsNone(gc.read_client(str(path)))
        self.assertEqual(self.calls(), [], "gog never sees a refused file")

    def host_refused(self, data):
        buf = bytearray(data)
        r = google.connect(self.st, "calendar-write", wait=True, client=buf)
        self.assertEqual((r["ok"], r["why"], r["step"]), (False, "client_file_refused", "client_needed"), data)
        self.assertEqual(self.step()["reason"], "client_file_refused")
        self.assertEqual(bytes(buf), b"\0" * len(data), "the host zeroes the bytes it was given")

    def test_symlink_refused(self):
        ln = self.d / "downloads" / "link.json"
        ln.symlink_to(self.client)
        self.refused(ln)

    def test_too_big_refused(self):
        big = self.d / "downloads" / "big.json"
        big.write_bytes(b" " * (gc.CLIENT_MAX + 1))
        self.refused(big)

    def test_missing_empty_relative_dir_refused(self):
        self.refused(self.d / "downloads" / "absent.json")
        empty = self.d / "downloads" / "empty.json"
        empty.write_bytes(b"")
        self.refused(empty)
        self.refused(self.d / "downloads")
        self.assertIsNone(gc.read_client("downloads/client.json"))

    def test_non_desktop_json_refused_by_the_host(self):
        for data in (b'{"web": {"client_id": "x"}}', b'{"installed": {"client_id": 5}}', b'{"installed": {}}', b"[1]",
                     b"not json", b"\xff\xfe", b" " * (gc.CLIENT_MAX + 1)):
            self.host_refused(data)
        self.assertEqual(self.calls(), [], "gog never sees bytes that are not a Desktop client")
        r = google.connect(self.st, "calendar-write", wait=True, client=b"")
        self.assertEqual(r["why"], "client_file_refused")

    def test_gog_rejects_the_file(self):
        self.mode(cred=1)
        r = self.connect(client_file=str(self.client))
        self.assertEqual((r["why"], r["step"]), ("client_file_refused", "client_needed"))
        self.assertNotIn("invalid credentials", json.dumps(r), "gog's stderr is never propagated")

    def test_bad_file_never_downgrades_a_ready_connection(self):
        self.connect(client_file=str(self.client), account=EMAIL)
        r = google.connect(self.st, "calendar-write", wait=True, client=bytearray(b'{"web": {}}'))
        self.assertEqual((r["why"], r["step"]), ("client_file_refused", "ready"))
        self.assertEqual(self.step()["step"], "ready")


class Failures(Base):
    def setUp(self):
        super().setUp()
        self.connect(client_file=str(self.client))

    def test_consent_failed(self):
        self.mode(add=4, add_err="oauth error for " + EMAIL)
        r = self.connect(account=EMAIL)
        self.assertEqual((r["ok"], r["step"], r["why"]), (False, "oauth_consent_required", "consent_failed"))
        self.no_email_anywhere(r)

    def test_consent_timeout_from_gog(self):
        self.mode(add=1, add_err="authorization timed out")
        self.assertEqual(self.connect(account=EMAIL)["why"], "consent_timeout")

    def test_consent_timeout_ours(self):
        self.mode(sleep=3)
        with mock.patch.object(gc, "CONSENT_SECS", 1):
            r = self.connect(account=EMAIL)
        self.assertEqual(r["why"], "consent_timeout")

    def test_api_disabled_then_reprobe(self):
        self.mode(probe=6, probe_msg="Google Calendar API has not been used in project 1 or it is disabled")
        r = self.connect(account=EMAIL)
        self.assertEqual((r["step"], r["why"]), ("oauth_consent_required", "api_disabled"))
        self.mode()
        r = self.connect()           # the owner enabled the API: connect again re-probes, no second consent
        self.assertEqual((r["ok"], r["step"]), (True, "ready"))
        self.assertEqual(sum("add" in c["argv"] for c in self.calls()), 1)

    def test_probe_failed_word_only(self):
        self.mode(probe=4)
        r = self.connect(account=EMAIL)
        self.assertEqual(r["why"], "probe_failed")
        self.assertNotIn("boom", json.dumps(r))
        self.no_email_anywhere(r)

    def test_bad_account_shape_never_reaches_gog(self):
        n = len(self.calls())
        for bad in ("-x@example.com", "not-an-email", "a@b", "x@example.com --services=all", 7):
            self.assertEqual(google.connect(self.st, "calendar-write", account=bad)["why"], "shape", bad)
        self.assertEqual(len(self.calls()), n)


class Disconnect(Base):
    def test_disconnect_removes_accounts_and_both_connections(self):
        self.connect(client_file=str(self.client), account=EMAIL)
        self.assertEqual(google.read(self.st)["connections"]["calendar-write"]["step"], "ready")
        r = google.disconnect(self.st, "calendar-write")
        self.assertEqual(r, {"ok": True, "purpose": "calendar-write", "step": "client_needed", "removed": 1})
        self.assertIn(["--no-input", "auth", "remove", EMAIL, "-y"], [c["argv"] for c in self.calls()])
        self.assertEqual(google.read(self.st)["connections"], {})
        self.assertEqual(json.loads((self.fake / "accounts.json").read_text()), [])
        self.no_email_anywhere(r)

    def test_disconnect_removes_only_our_account(self):
        """F3: the account list may hold someone else's account (a keyring shared before the namespace): never removed."""
        self.connect(client_file=str(self.client), account=EMAIL)
        other = "owner.own.gog@example.org"
        (self.fake / "accounts.json").write_text(json.dumps([EMAIL, other]))
        r = google.disconnect(self.st, "calendar-write")
        self.assertEqual((r["ok"], r["removed"]), (True, 1))
        self.assertEqual(json.loads((self.fake / "accounts.json").read_text()), [other])
        removes = [c["argv"] for c in self.calls() if "remove" in c["argv"]]
        self.assertEqual(removes, [["--no-input", "auth", "remove", EMAIL, "-y"]])
        self.no_email_anywhere(r)

    def test_disconnect_without_a_matching_hash_removes_nothing(self):
        (self.fake / "accounts.json").write_text(json.dumps([EMAIL, "owner.own.gog@example.org"]))
        gc._set(self.st, "calendar-write", "ready")                 # a record with no account hash (or another one)
        r = google.disconnect(self.st, "calendar-write")
        self.assertEqual(r, {"ok": True, "purpose": "calendar-write", "step": "client_needed", "removed": 0})
        self.assertFalse([c for c in self.calls() if "remove" in c["argv"]])
        self.assertEqual(len(json.loads((self.fake / "accounts.json").read_text())), 2)
        self.assertEqual(google.read(self.st)["connections"], {})

    def test_reconnect_keeps_one_account(self):
        (self.fake / "accounts.json").write_text(json.dumps(["old.p428@example.org"]))
        self.connect(client_file=str(self.client), account=EMAIL)
        self.assertEqual(json.loads((self.fake / "accounts.json").read_text()), [EMAIL], "-a auto needs exactly one token")


class Restricted(Base):
    def test_every_calendar_call_is_command_restricted(self):
        self.connect(client_file=str(self.client), account=EMAIL)
        code, body = google.gog_calendar(self.st, ["calendar", "events", "primary", "--max", "5"])
        self.assertEqual((code, body), (0, {"events": []}))
        cal = [c["argv"] for c in self.calls() if "calendar" in c["argv"] and "auth" not in c["argv"]]
        self.assertGreaterEqual(len(cal), 2)
        for argv in cal:
            self.assertEqual(argv[:5], ["-a", "auto", "--json", "--no-input", "--enable-commands-exact"], argv)
            self.assertTrue(set(argv[5].split(",")) <= set(gc.CAL_COMMANDS.split(",")), argv)
        self.assertEqual(cal[-1][5], gc.CAL_COMMANDS)
        self.assertEqual(gc.CAL_COMMANDS,
                         "calendar.events,calendar.create,calendar.update,calendar.delete,calendar.calendars")
        for argv in (c["argv"] for c in self.calls()):
            self.assertNotIn("tokens", argv)
            self.assertFalse(any(a.startswith("--services=") or a in ("all", "user") for a in argv), argv)

    def test_gog_env_is_an_allowlist_with_our_keyring_namespace(self):
        """F3 / F7: every gog run gets GOG_HOME + GOG_KEYRING_SERVICE_NAME=agentj-gog and no key-card value from serve's env."""
        with mock.patch.dict(os.environ, {"P428_LEAK_SECRET": "x", "AGENTJ_HOOK_SECRET": "y", "GOG_KEYRING_SERVICE_NAME": "gogcli",
                                          "GOG_KEYRING_BACKEND": "file", "GOOGLE_APPLICATION_CREDENTIALS": "/a",
                                          "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/p428/bus"}):
            e = google.env(self.st)
            self.connect(client_file=str(self.client), account=EMAIL)
            google.gog_calendar(self.st, ["calendar", "events", "primary"])
        self.assertEqual((e["GOG_HOME"], e["GOG_KEYRING_SERVICE_NAME"]), (str(google.gog_home(self.st)), "agentj-gog"))
        self.assertEqual(e["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/p428/bus")
        self.assertLessEqual(set(e), set(google._ENV_KEEP) | {"GOG_HOME", "GOG_KEYRING_SERVICE_NAME"})
        for c in self.calls():
            self.assertEqual(c["ks"], "agentj-gog", c["argv"])
            for k in ("P428_LEAK_SECRET", "AGENTJ_HOOK_SECRET", "GOG_KEYRING_BACKEND", "GOOGLE_APPLICATION_CREDENTIALS"):
                self.assertNotIn(k, c["env"])

    def test_gog_output_not_json(self):
        exe = google.binary(self.st)
        exe.write_text("#!/bin/sh\necho 'not json'; echo 'secret stderr' >&2; exit 3\n")
        self.assertEqual(google.gog_calendar(self.st, ["calendar", "calendars"]), (3, None))


class Projection(Base):
    def entries(self):
        return {e["id"]: e for e in google.registry_entries(self.st)}

    def test_registry_ready_only_for_calendar_after_ready(self):
        e = self.entries()
        self.assertFalse([k for k, v in e.items() if v["status"] == "ready" and k != "google-cli"])
        self.connect(client_file=str(self.client))
        self.assertEqual(self.entries()["google-calendar-write"]["status"], "unverified")
        self.assertEqual(self.entries()["google-calendar-write"]["check"]["status"], "auth_required")
        self.connect(account=EMAIL)
        e = self.entries()
        for pid in ("google-calendar-write", "google-calendar-read"):
            self.assertEqual(e[pid]["status"], "ready", pid)
            self.assertEqual(e[pid]["check"]["status"], "ok")
            self.assertTrue(e[pid]["check"]["last_verified_at"] and e[pid]["check"]["expires_at"])
        for pid, row in e.items():
            if pid.startswith("google-") and not pid.startswith("google-calendar") and row["kind"] == "credential":
                self.assertNotEqual(row["status"], "ready", pid)
                self.assertEqual(row["check"]["status"], "unsupported", pid)
        s = google.status(self.st)
        self.assertEqual(s["api_authorization"]["state"], "calendar_ready")
        self.assertEqual(s["api_authorization"]["others"], "not_available_yet")
        self.assertEqual(s["stage"], "0.18.0")
        from agentj import ltschema
        self.assertEqual(ltschema.problems("registry", {"schema_version": 1, "host_id": "h", "revision": 1,
                                                        "generated_at": "2026-10-21T00:00:00Z",
                                                        "entries": list(e.values())}), [])

    def test_v1_record_without_connections_and_junk_dropped(self):
        google._private_dir(google.root(self.st))
        google._state_path(self.st).write_text(json.dumps({"v": 1, "preinstall": "on"}))
        self.assertEqual(google.read(self.st)["connections"], {})
        google._state_path(self.st).write_text(json.dumps({"v": 1, "connections": {
            "calendar-write": {"step": "ready", "at": 5, "email": EMAIL, "reason": "free text"},
            "gmail-read": {"step": "hacked"}, "zzz": {"step": "ready"}}}))
        self.assertEqual(google.read(self.st)["connections"], {"calendar-write": {"step": "ready", "at": 5}})


class Elevate(Base):
    def test_socket_shapes(self):
        sent = []
        host = _host(self.st, sent)

        async def go():
            p = host.elevate._p115
            for bad in ({"t": "google", "op": "connect"},
                        {"t": "google", "op": "connect", "purpose": ["calendar-write"]},
                        {"t": "google", "op": "connect", "purpose": "calendar-write", "account": 5},
                        {"t": "google", "op": "connect", "purpose": "calendar-write", "token": "x"},
                        {"t": "google", "op": "disconnect", "purpose": "calendar-write", "account": EMAIL},
                        {"t": "google", "op": "status", "purpose": "calendar-write"},
                        {"t": "google", "op": "connect_status", "purpose": "everything"},
                        {"t": "google", "op": "connect", "purpose": "calendar-write", "client_file": str(self.client)},
                        {"t": "google", "op": "connect", "purpose": "calendar-write", "client_b64": "not base64!"},
                        {"t": "google", "op": "connect", "purpose": "calendar-write", "client_b64": "A" * (gc.B64_MAX + 4)},
                        {"t": "google", "op": "disconnect", "purpose": "calendar-write", "client_b64": "e30="}):
                r = await p(bad)
                self.assertEqual((r["ok"], r["why"]), (False, "shape"), bad)
            r = await p({"t": "google", "op": "connect_status", "purpose": None})
            self.assertEqual(r["connections"]["calendar-write"]["step"], "client_needed")
            self.assertEqual(self.calls(), [], "a path over the socket is refused before anything runs (F4)")
            r = await p({"t": "google", "op": "connect", "purpose": "calendar-write", "client_b64": base64.b64encode(CLIENT).decode()})
            self.assertEqual(r["step"], "client_saved")
            self.assertEqual(self.calls()[-1]["stdin"], CLIENT.decode())
            r = await p({"t": "google", "op": "connect", "purpose": "calendar-write",
                         "client_b64": base64.b64encode(b'{"web": {"client_id": "x"}}').decode()})
            self.assertEqual(r["why"], "client_file_refused")
            r = await p({"t": "google", "op": "connect", "purpose": "gmail-read"})
            self.assertEqual(r["why"], "purpose_not_connectable")
            self.assertEqual((await p({"t": "google", "op": "status"}))["stage"], "0.18.0")
        asyncio.run(go())

    def test_cli_reads_the_file_with_own_permissions_and_sends_bytes(self):
        a = SimpleNamespace(action="connect", connect_status=False, purpose=["calendar-write"], client_file=None, account=None)
        cwd = os.getcwd()
        os.chdir(self.client.parent)
        try:
            res, req = gc.cli_request(SimpleNamespace(**{**vars(a), "client_file": self.client.name}))
        finally:
            os.chdir(cwd)
        self.assertIsNone(res)
        self.assertEqual(req, {"t": "google", "op": "connect", "purpose": "calendar-write",
                               "client_b64": base64.b64encode(CLIENT).decode()})
        self.assertIsNone(google.request_shape(req))
        ln = self.client.parent / "ln.json"
        ln.symlink_to(self.client)
        res, _ = gc.cli_request(SimpleNamespace(**{**vars(a), "client_file": str(ln)}))
        self.assertEqual(res["why"], "client_file_refused")
        res, req = gc.cli_request(SimpleNamespace(**{**vars(a), "connect_status": True, "purpose": None}))
        self.assertEqual(req, {"t": "google", "op": "connect_status", "purpose": None})
        res, _ = gc.cli_request(SimpleNamespace(**{**vars(a), "purpose": None}))
        self.assertEqual(res["why"], "shape")
        self.assertIsNone(google.request_shape(req))


if __name__ == "__main__":
    unittest.main()
