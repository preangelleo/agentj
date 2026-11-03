"""#436 (0.26.8): every defined Google purpose is connectable through the same guided flow.

gog purposes (gmail-read, gmail-send, drive-file) run Agent J's pinned gog in their OWN token bucket (`--client agentj-<purpose>`);
youtube-publish and search-console-read run the local standard-library adapter (google_oauth: PKCE S256, one loopback
callback). A fake gog script (token buckets, records argv/stdin) and a fake Google HTTP server on 127.0.0.1 stand in for
Google: nothing here reaches the network or uses a real credential. Covered: each purpose reaches `ready`; minimum gog flags;
token files 0600 in 0700 dirs; no token / code / e-mail in results, state or the host log (only the sha256); PKCE state
mismatch and a repeated callback refused; YouTube resumable upload + read-back; Search Console read-only calls; uploads /
sends never run without the owner's signed allow; disconnect removes only that purpose's grant; skill / status wording.
"""
import _hermetic  # noqa: F401,I001  isolated test HOME/state before importing the host
import asyncio
import base64
import hashlib
import http.server
import json
import os
import pathlib
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import google, google_connect as gc  # noqa: E402

from test_l1 import _state  # noqa: E402

AJ = pathlib.Path(__file__).resolve().parents[1] / "agentj"
EMAIL = "owner.p436@example.com"
CLIENT = (b'{"installed": {"client_id": "p436.apps.googleusercontent.com", "client_secret": "P436-CLIENT-SECRET",'
          b' "note": "P436-CLIENT-BYTES"}}')
REFRESH, ACCESS, CODE = "P436-REFRESH-TOKEN", "P436-ACCESS-TOKEN", "P436-AUTH-CODE"
SECRETS = (EMAIL, "example.com", "P436-CLIENT-BYTES", "P436-CLIENT-SECRET", REFRESH, ACCESS, CODE)

FAKE = r'''#!{py} -IS
import json, os, pathlib, sys
d = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
client = args[args.index("--client") + 1] if "--client" in args else "default"
j = " ".join(args)
data = sys.stdin.buffer.read() if ("credentials set -" in j or "--body-file=-" in j or "/dev/stdin" in j) else b""
with open(d / "calls.jsonl", "a") as f:
    f.write(json.dumps({{"argv": args, "stdin": data.decode("utf-8", "replace"), "n": len(data)}}) + "\n")
mode = json.loads((d / "mode.json").read_text()) if (d / "mode.json").exists() else {{}}
accf = d / "accounts.json"
accs = json.loads(accf.read_text()) if accf.exists() else []
def out(code, obj=None):
    if obj is not None:
        print(json.dumps(obj))
    sys.exit(code)
if "credentials set" in j:
    out(0, {{"saved": True, "client": client}})
if "auth add" in j:
    email = args[args.index("add") + 1]
    accf.write_text(json.dumps([a for a in accs if (a["email"], a["client"]) != (email, client)] + [{{"email": email, "client": client}}]))
    out(0)
if "auth list" in j:
    mine = [a for a in accs if "--client" not in args or a["client"] == client]
    out(0, {{"accounts": [{{**a, **({{"valid": not mode.get("invalid")}} if "--check" in args else {{}})}} for a in mine]}})
if "auth remove" in j:
    email = args[args.index("remove") + 1]
    accf.write_text(json.dumps([a for a in accs if (a["email"], a["client"]) != (email, client)]))
    out(0, {{}})
if mode.get("probe"):
    out(mode["probe"], {{"error": {{"message": mode.get("probe_msg", "boom")}}}})
if "gmail search" in j:
    out(0, {{"threads": [{{"id": "t1", "snippet": "hello"}}]}})
if "gmail get" in j:
    out(0, {{"id": args[-1], "snippet": "hello"}})
if "gmail send" in j:
    out(0, {{"id": "m1", "threadId": "t1"}})
if "drive ls" in j:
    out(0, {{"files": []}})
if "drive upload" in j:
    out(0, {{"id": "f1", "name": "x"}})
if "calendar" in j:
    out(0, {{"calendars": [{{"id": "primary"}}]}})
out(0, {{}})
'''


class FakeGoogle(http.server.ThreadingHTTPServer):
    """accounts.google.com + oauth2.googleapis.com + www.googleapis.com on one loopback port."""
    daemon_threads = True

    def __init__(self):
        self.calls, self.challenge, self.uploads, self.mode = [], None, {}, {}
        super().__init__(("127.0.0.1", 0), FakeHandler)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class FakeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def _send(self, code, obj=None, headers=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _authed(self):
        return self.headers.get("Authorization") == "Bearer " + ACCESS

    def do_POST(self):
        s, u = self.server, urllib.parse.urlsplit(self.path)
        body = self._body()
        s.calls.append(("POST", u.path, u.query))
        if u.path == "/token":
            f = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
            if f.get("grant_type") == "authorization_code":
                ver = f.get("code_verifier", "")
                calc = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode()
                if f.get("code") != CODE or calc != s.challenge or f.get("client_secret") != "P436-CLIENT-SECRET":
                    return self._send(400, {"error": "invalid_grant"})
                scope = s.mode.get("scope") or s.want_scope
                return self._send(200, {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600, "scope": scope})
            if f.get("grant_type") == "refresh_token":
                if s.mode.get("revoked") or f.get("refresh_token") != REFRESH:
                    return self._send(400, {"error": "invalid_grant"})
                return self._send(200, {"access_token": ACCESS, "expires_in": 3600})
            return self._send(400, {"error": "unsupported_grant_type"})
        if u.path == "/revoke":
            return self._send(200, {})
        if not self._authed():
            return self._send(401, {"error": {"message": "no auth"}})
        if u.path == "/upload/youtube/v3/videos":
            q = urllib.parse.parse_qs(u.query)
            if q.get("uploadType") != ["resumable"] or q.get("part") != ["snippet,status"]:
                return self._send(400, {})
            meta = json.loads(body)
            s.uploads["meta"], s.uploads["len"] = meta, int(self.headers["X-Upload-Content-Length"])
            return self._send(200, None, {"Location": s.base + "/upload/youtube/v3/videos?upload_id=U1"})
        if u.path.startswith("/webmasters/v3/sites/") and u.path.endswith("/searchAnalytics/query"):
            s.calls.append(("SCQ", urllib.parse.unquote(u.path.split("/")[4]), json.loads(body)))
            return self._send(200, {"rows": [{"keys": ["agent j"], "clicks": 3, "impressions": 9}]})
        return self._send(404, {})

    def do_PUT(self):
        s, u = self.server, urllib.parse.urlsplit(self.path)
        if not self._authed() or "upload_id=U1" not in u.query:
            return self._send(401, {})
        data = self._body()
        s.calls.append(("PUT", u.path, len(data)))
        s.uploads["bytes"] = data
        meta = s.uploads["meta"]
        return self._send(200, {"id": "VID436abc", "snippet": {"title": meta["snippet"]["title"]},
                                "status": {"privacyStatus": meta["status"]["privacyStatus"], "uploadStatus": "uploaded"}})

    def do_GET(self):
        s, u = self.server, urllib.parse.urlsplit(self.path)
        s.calls.append(("GET", u.path, u.query))
        if not self._authed():
            return self._send(401, {})
        if s.mode.get("disabled"):
            return self._send(403, {"error": {"errors": [{"reason": "accessNotConfigured"}], "message": "SERVICE_DISABLED"}})
        if u.path == "/youtube/v3/channels":
            return self._send(200, {"items": [{"id": "UC436"}]})
        if u.path == "/youtube/v3/videos":
            q = urllib.parse.parse_qs(u.query)
            return self._send(200, {"items": [{"id": q["id"][0], "snippet": {"title": "t"},
                                               "status": {"privacyStatus": "private", "uploadStatus": "processed"},
                                               "processingDetails": {"processingStatus": "succeeded"}}]})
        if u.path == "/webmasters/v3/sites":
            return self._send(200, {"siteEntry": [{"siteUrl": "sc-domain:agentj.example", "permissionLevel": "siteOwner"}]})
        return self._send(404, {})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p436-")
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
        self.client = self.d / "client_secret_desktop.json"
        self.client.write_bytes(CLIENT)
        self.work = self.d / "work"
        self.work.mkdir()
        p = mock.patch.dict(os.environ)          # the fake Google is on loopback: no proxy in between
        p.start()
        self.addCleanup(p.stop)
        for k in [k for k in os.environ if k.lower().endswith("_proxy")]:
            del os.environ[k]

    def mode(self, **kw):
        (self.fake / "mode.json").write_text(json.dumps(kw))

    def calls(self):
        f = self.fake / "calls.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def connect(self, purpose, **kw):
        return google.connect(self.st, purpose, wait=True, **kw)

    def full(self, purpose):
        r1 = self.connect(purpose, client=gc.read_client(str(self.client)))
        self.assertEqual((r1["ok"], r1["step"]), (True, "client_saved"), r1)
        r2 = self.connect(purpose, account=EMAIL)
        return r1, r2

    def clean_everywhere(self, *results):
        blob = json.dumps(results) + google._state_path(self.st).read_text()
        if self.st.log_path.exists():
            blob += self.st.log_path.read_text()
        for s in SECRETS:
            self.assertNotIn(s, blob)


class Adapter(Base):
    """A fake Google for youtube-publish / search-console-read; the "browser" follows the auth URL to the loopback."""

    def setUp(self):
        super().setUp()
        from agentj import google_oauth as go
        self.go = go
        self.srv = FakeGoogle()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        b = self.srv.base
        for p in (mock.patch.object(go, "AUTH_URL", b + "/auth"), mock.patch.object(go, "TOKEN_URL", b + "/token"),
                  mock.patch.object(go, "REVOKE_URL", b + "/revoke"), mock.patch.object(go, "API", b),
                  mock.patch.object(go, "_open", side_effect=self.browser)):
            p.start()
            self.addCleanup(p.stop)
        go._TOKENS.clear()
        self.addCleanup(go._TOKENS.clear)
        self.seen, self.before = [], None

    def browser(self, st, url):
        """What the owner's browser does after they approve: Google redirects to the loopback with code + state."""
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        self.seen.append(q)
        self.srv.challenge, self.srv.want_scope = q["code_challenge"], q["scope"]

        def go():
            if self.before:
                self.before(q)
            cb = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": CODE, "state": q["state"], "scope": q["scope"]})
            self.cb_status = urllib.request.urlopen(cb, timeout=10).status
        threading.Thread(target=go, daemon=True).start()


class GogPurposes(Base):
    def test_each_gog_purpose_reaches_ready_with_its_minimum_flags(self):
        want = {"gmail-read": ["--services", "gmail", "--gmail-scope", "readonly"],
                "gmail-send": ["--services", "gmail", "--gmail-scope", "send"],
                "drive-file": ["--services", "drive", "--drive-scope", "file"]}
        for pid, flags in want.items():
            r0 = self.connect(pid)
            self.assertEqual((r0["ok"], r0["step"]), (True, "client_needed"), pid)
            api = google.PURPOSES[pid]["api"]
            self.assertIn("https://console.cloud.google.com/apis/library/" + api, [o["url"] for o in r0["owner"]])
            r1, r2 = self.full(pid)
            self.assertEqual((r2["ok"], r2["step"]), (True, "ready"), (pid, r2))
            cid = ["--client", "agentj-" + pid]
            cred = [c for c in self.calls() if "credentials" in c["argv"]][-1]
            self.assertEqual(cred["argv"], cid + ["--no-input", "--json", "auth", "credentials", "set", "-"])
            self.assertEqual(cred["stdin"], CLIENT.decode())
            add = [c["argv"] for c in self.calls() if "add" in c["argv"]][-1]
            self.assertEqual(add, cid + ["auth", "add", EMAIL, *flags, "--force-consent", "--timeout", "5m"])
            self.clean_everywhere(r0, r1, r2, google.status(self.st), gc.connect_status(self.st))
        probes = [c["argv"] for c in self.calls() if "auth" not in c["argv"] or "--check" in c["argv"]]
        self.assertIn(["--client", "agentj-gmail-read", "-a", "auto", "--json", "--no-input", "--wrap-untrusted",
                       "--gmail-no-send", "--enable-commands-exact", "gmail.search", "gmail", "search", "--max=1", "--",
                       "in:inbox"], probes)
        self.assertIn(["--client", "agentj-gmail-send", "--json", "--no-input", "auth", "list", "--check"], probes)
        self.assertIn(["--client", "agentj-drive-file", "-a", "auto", "--json", "--no-input", "--gmail-no-send",
                       "--enable-commands-exact", "drive.ls", "drive", "ls", "--max=1"], probes)
        s = google.status(self.st)
        self.assertTrue(all(r["connectable"] for r in s["purposes"]))
        self.assertNotIn("not_available_yet", json.dumps(s))
        ready = {e["id"] for e in google.registry_entries(self.st) if e["status"] == "ready"}
        self.assertLessEqual({"google-gmail-read", "google-gmail-send", "google-drive-file"}, ready)

    def test_gmail_send_probe_needs_a_valid_token_of_ours(self):
        self.mode(invalid=True)
        _, r = self.full("gmail-send")
        self.assertEqual((r["ok"], r["step"], r["why"]), (False, "oauth_consent_required", "probe_failed"))

    def test_disconnect_removes_only_its_own_bucket(self):
        for pid in ("calendar-write", "gmail-read", "drive-file"):
            self.full(pid)
        accs = json.loads((self.fake / "accounts.json").read_text())
        self.assertEqual(sorted(a["client"] for a in accs), ["agentj-drive-file", "agentj-gmail-read", "default"])
        r = google.disconnect(self.st, "gmail-read")
        self.assertEqual((r["ok"], r["removed"]), (True, 1))
        accs = json.loads((self.fake / "accounts.json").read_text())
        self.assertEqual(sorted(a["client"] for a in accs), ["agentj-drive-file", "default"])
        conns = google.read(self.st)["connections"]
        self.assertNotIn("gmail-read", conns)
        self.assertEqual((conns["calendar-write"]["step"], conns["drive-file"]["step"]), ("ready", "ready"))
        self.assertIn(["--client", "agentj-gmail-read", "--no-input", "auth", "remove", EMAIL, "-y"],
                      [c["argv"] for c in self.calls()])
        r = google.disconnect(self.st, "calendar-write")
        accs = json.loads((self.fake / "accounts.json").read_text())
        self.assertEqual([a["client"] for a in accs], ["agentj-drive-file"], "Calendar's disconnect leaves Drive's bucket")
        self.assertEqual(google.read(self.st)["connections"]["drive-file"]["step"], "ready")

    def test_calendar_consent_keeps_other_buckets(self):
        self.full("gmail-read")
        self.full("calendar-write")
        accs = json.loads((self.fake / "accounts.json").read_text())
        self.assertEqual(sorted(a["client"] for a in accs), ["agentj-gmail-read", "default"])
        self.assertFalse([c for c in self.calls() if "remove" in c["argv"]])


class AdapterConnect(Adapter):
    def test_youtube_and_search_console_reach_ready_with_pkce(self):
        for pid, scopes in (("youtube-publish", ["https://www.googleapis.com/auth/youtube.upload",
                                                 "https://www.googleapis.com/auth/youtube.readonly"]),
                            ("search-console-read", ["https://www.googleapis.com/auth/webmasters.readonly"])):
            r0 = self.connect(pid)
            self.assertEqual(r0["step"], "client_needed")
            self.assertIn("https://console.cloud.google.com/apis/library/" + google.PURPOSES[pid]["api"],
                          [o["url"] for o in r0["owner"]])
            r1, r2 = self.full(pid)
            self.assertEqual((r2["ok"], r2["step"]), (True, "ready"), (pid, r2))
            q = self.seen[-1]
            self.assertEqual(q["scope"].split(), scopes)
            self.assertEqual((q["code_challenge_method"], q["response_type"], q["access_type"]), ("S256", "code", "offline"))
            self.assertTrue(q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/"))
            self.assertGreaterEqual(len(q["state"]), 32)
            d = self.go.pdir(self.st, pid)
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(d.parent).st_mode), 0o700)
            for f in ("token.json", "client.json"):
                self.assertEqual(stat.S_IMODE(os.stat(d / f).st_mode), 0o600, f)
            self.assertEqual(json.loads((d / "token.json").read_text())["refresh_token"], REFRESH)
            self.clean_everywhere(r0, r1, r2, google.status(self.st), gc.connect_status(self.st))
        self.assertFalse([c for c in self.calls()], "the adapter purposes never run gog")
        self.assertEqual(google.read(self.st)["connections"]["youtube-publish"]["acct"],
                         hashlib.sha256(EMAIL.casefold().encode()).hexdigest())

    def test_state_mismatch_and_repeat_callback_refused(self):
        bad = []

        def forged(q):
            u = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": "FORGED", "state": "x" * 43})
            try:
                urllib.request.urlopen(u, timeout=10)
            except urllib.error.HTTPError as e:
                bad.append(e.code)
        self.before = forged
        _, r = self.full("youtube-publish")
        self.assertEqual(bad, [400], "a callback with another state is refused")
        self.assertEqual(r["step"], "ready", "the forged code was never exchanged")
        q = self.seen[-1]
        again = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": CODE, "state": q["state"]})
        with self.assertRaises(OSError):
            urllib.request.urlopen(again, timeout=5)
        self.assertEqual(sum(1 for c in self.srv.calls if c[1] == "/token"), 1,
                         "exactly one code exchange (the forged code was never sent to Google)")

    def test_consent_timeout_and_denied_scope(self):
        with mock.patch.object(self.go, "_open"), mock.patch.object(gc, "CONSENT_SECS", 1):
            _, r = self.full("search-console-read")
        self.assertEqual((r["step"], r["why"]), ("oauth_consent_required", "consent_timeout"))
        self.srv.mode["scope"] = "https://www.googleapis.com/auth/youtube.readonly"
        r1 = self.connect("youtube-publish", client=gc.read_client(str(self.client)))
        r = self.connect("youtube-publish", account=EMAIL)
        self.assertEqual((r["step"], r["why"]), ("oauth_consent_required", "scope_denied"))
        self.assertFalse((self.go.pdir(self.st, "youtube-publish") / "token.json").exists())

    def test_api_disabled_word(self):
        self.srv.mode["disabled"] = True
        _, r = self.full("youtube-publish")
        self.assertEqual((r["step"], r["why"]), ("oauth_consent_required", "api_disabled"))
        self.srv.mode.clear()
        self.assertEqual(self.connect("youtube-publish")["step"], "ready", "connect again re-probes after enabling the API")

    def test_disconnect_revokes_and_deletes_only_its_files(self):
        self.full("youtube-publish")
        self.full("search-console-read")
        r = google.disconnect(self.st, "youtube-publish")
        self.assertEqual((r["ok"], r["removed"]), (True, 1))
        self.assertIn(("POST", "/revoke", ""), self.srv.calls)
        self.assertFalse(self.go.pdir(self.st, "youtube-publish").exists())
        self.assertTrue((self.go.pdir(self.st, "search-console-read") / "token.json").exists())
        conns = google.read(self.st)["connections"]
        self.assertNotIn("youtube-publish", conns)
        self.assertEqual(conns["search-console-read"]["step"], "ready")


class FakeHost:
    def __init__(self, st, work, answer):
        from agentj import provenance           # security round 1 (H1): Google runs only in the owner's (or a delegated) turn
        self.st, self.lang, self.answer, self.asked = st, "zh", answer, []
        self.agent = SimpleNamespace(cur_send=None, native_source=provenance.phone())
        self.agent_cfg = {"dir": str(work)}

    async def ask(self, tool, tool_input, **kw):
        self.asked.append((tool, tool_input, kw))
        return self.answer


ALLOW = {"behavior": "allow", "grant_info": {"rid": "r1", "device": "d1"}}


class Use(Adapter):
    def api(self, host, **req):
        from agentj import google_api
        return asyncio.run(google_api.serve(host, self.st, {"t": "google_api", **req}))

    def video(self, name="clip.mp4", n=300_000):
        p = self.work / name
        p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + os.urandom(n))
        return p

    def test_youtube_upload_needs_the_owner_then_resumable_and_read_back(self):
        self.full("youtube-publish")
        v = self.video()
        no = FakeHost(self.st, self.work, {"behavior": "deny", "reason": "device"})
        r = self.api(no, op="youtube_upload", file=str(v), title="P436 clip", description="d")
        self.assertEqual((r["ok"], r["why"]), (False, "denied"))
        self.assertNotIn("meta", self.srv.uploads, "nothing reaches YouTube without the owner's allow")
        tool, inp, kw = no.asked[0]
        self.assertEqual(tool, "agentj google youtube upload")
        self.assertEqual((kw["always_ask"], kw["grant_info"], kw["batch"]), (True, True, False))
        self.assertIn("private", inp["summary"])
        self.assertIn("clip.mp4", inp["summary"])
        unsigned = FakeHost(self.st, self.work, {"behavior": "allow"})
        self.assertFalse(self.api(unsigned, op="youtube_upload", file=str(v), title="P436 clip")["ok"],
                         "an allow without a paired device's signature does not count")
        yes = FakeHost(self.st, self.work, ALLOW)
        r = self.api(yes, op="youtube_upload", file=str(v), title="P436 clip")
        self.assertEqual((r["ok"], r["id"], r["privacy"]), (True, "VID436abc", "private"), r)
        self.assertEqual(self.srv.uploads["meta"]["status"]["privacyStatus"], "private", "default privacy = private")
        self.assertEqual(self.srv.uploads["bytes"], v.read_bytes())
        self.assertEqual(self.srv.uploads["len"], v.stat().st_size)
        g = self.api(yes, op="youtube_get", id="VID436abc")
        self.assertEqual((g["ok"], g["video"]["privacy"], g["video"]["processing"]), (True, "private", "succeeded"))
        self.assertIn(("GET", "/youtube/v3/videos", "id=VID436abc&part=snippet%2Cstatus%2CprocessingDetails"), self.srv.calls)
        self.assertEqual(len(yes.asked), 1, "the read-back needs no card")
        self.clean_everywhere(r, g)

    def test_public_upload_says_so_on_the_card(self):
        self.full("youtube-publish")
        h = FakeHost(self.st, self.work, ALLOW)
        r = self.api(h, op="youtube_upload", file=str(self.video()), title="t", privacy="public")
        self.assertTrue(r["ok"])
        self.assertIn("public", h.asked[0][1]["summary"])
        self.assertEqual(self.srv.uploads["meta"]["status"]["privacyStatus"], "public")

    def test_upload_file_checks(self):
        self.full("youtube-publish")
        h = FakeHost(self.st, self.work, ALLOW)
        outside = self.d / "outside.mp4"
        outside.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 100)
        ln = self.work / "ln.mp4"
        ln.symlink_to(outside)
        (self.work / ".ssh").mkdir()
        key = self.work / ".ssh" / "v.mp4"
        key.write_bytes(outside.read_bytes())
        fake = self.work / "notvideo.mp4"
        fake.write_bytes(b"#!/bin/sh\necho hi\n")
        for p, why in ((outside, "outside"), (ln, "outside"), (key, "secret"), (fake, "type"),
                       (self.work / "gone.mp4", "gone"), ("rel.mp4", "shape")):
            r = self.api(h, op="youtube_upload", file=str(p), title="t")
            self.assertEqual((r["ok"], r["why"]), (False, why), p)
        self.assertEqual(h.asked, [], "a refused file never opens a card")
        r = self.api(h, op="youtube_upload", file=str(self.video()), title="key ghp_" + "a1B2" * 9)
        self.assertEqual(r["why"], "secret")

    def test_shapes_and_not_connected(self):
        h = FakeHost(self.st, self.work, ALLOW)
        r = self.api(h, op="youtube_upload", file=str(self.video()), title="t")
        self.assertEqual((r["ok"], r["why"], r["purpose"]), (False, "not_connected", "youtube-publish"))
        for bad in ({"op": "nope"}, {"op": "youtube_upload", "file": "/x.mp4"},
                    {"op": "youtube_upload", "file": "/x.mp4", "title": "t", "privacy": "everyone"},
                    {"op": "youtube_upload", "file": "/x.mp4", "title": "t", "token": "x"},
                    {"op": "youtube_upload", "file": "/x.mp4", "title": "t", "privacy": "public",
                     "publish_at": "2027-01-01T00:00:00Z"},
                    {"op": "youtube_get", "id": "../x"}, {"op": "sc_query", "site": "x", "start": "1", "end": "2"},
                    {"op": "gmail_send", "to": ["not-an-address"], "subject": "s", "body": "b"},
                    {"op": "gmail_send", "to": [EMAIL], "subject": "a\nBcc: x@example.org", "body": "b"}):
            self.assertEqual(self.api(h, **bad)["why"], "shape", bad)
        self.assertEqual(h.asked, [])

    def test_search_console_read_only(self):
        self.full("search-console-read")
        h = FakeHost(self.st, self.work, ALLOW)
        r = self.api(h, op="sc_sites")
        self.assertEqual(r["sites"], [{"site": "sc-domain:agentj.example", "permission": "siteOwner"}])
        r = self.api(h, op="sc_query", site="sc-domain:agentj.example", start="2026-10-01", end="2026-10-31",
                     dimensions=["query"], rows=10)
        self.assertEqual(r["rows"][0]["clicks"], 3)
        q = [c for c in self.srv.calls if c[0] == "SCQ"][0]
        self.assertEqual(q[1:], ("sc-domain:agentj.example", {"startDate": "2026-10-01", "endDate": "2026-10-31",
                                                                "dimensions": ["query"], "rowLimit": 10}))
        self.assertEqual(h.asked, [], "read-only calls need no card")
        self.assertFalse([c for c in self.srv.calls if c[0] in ("PUT",) or c[1].startswith("/upload")])

    def test_revoked_grant_pauses_only_that_purpose(self):
        self.full("search-console-read")
        self.go._TOKENS.clear()
        self.srv.mode["revoked"] = True
        r = self.api(FakeHost(self.st, self.work, ALLOW), op="sc_sites")
        self.assertEqual((r["ok"], r["why"]), (False, "auth_expired"))
        self.assertEqual(google.read(self.st)["connections"]["search-console-read"]["reason"], "auth_expired")


class GogUse(Base):
    def api(self, host, **req):
        from agentj import google_api
        return asyncio.run(google_api.serve(host, self.st, {"t": "google_api", **req}))

    def test_gmail_send_needs_the_owner_and_only_gmail_send(self):
        self.full("gmail-send")
        no = FakeHost(self.st, self.work, {"behavior": "deny"})
        r = self.api(no, op="gmail_send", to=["friend.p436@example.org"], subject="Hi", body="Hello there")
        self.assertEqual(r["why"], "denied")
        self.assertFalse([c for c in self.calls() if c["argv"][-6:-4] == ["gmail", "send"] or "--body-file=-" in c["argv"]])
        self.assertEqual(no.asked[0][0], "agentj google gmail send")
        self.assertIn("friend.p436@example.org", no.asked[0][1]["summary"])
        self.assertIn("Hello there", no.asked[0][1]["summary"])
        r = self.api(FakeHost(self.st, self.work, ALLOW), op="gmail_send", to=["friend.p436@example.org"], subject="Hi",
                     body="Hello there")
        self.assertEqual((r["ok"], r["id"]), (True, "m1"))
        send = [c for c in self.calls() if c["argv"][-6:-4] == ["gmail", "send"] or "--body-file=-" in c["argv"]][-1]
        self.assertEqual(send["argv"], ["--client", "agentj-gmail-send", "-a", "auto", "--json", "--no-input",
                                        "--enable-commands-exact", "gmail.send", "gmail", "send",
                                        "--to=friend.p436@example.org", "--subject=Hi", "--body-file=-"])
        self.assertEqual(send["stdin"], "Hello there")
        r = self.api(FakeHost(self.st, self.work, ALLOW), op="gmail_send", to=["friend.p436@example.org"], subject="k",
                     body="key sk-ant-api03-" + "B" * 90)
        self.assertEqual(r["why"], "secret", "a credential never leaves by mail")

    def test_gmail_read_and_drive(self):
        self.full("gmail-read")
        self.full("drive-file")
        h = FakeHost(self.st, self.work, ALLOW)
        r = self.api(h, op="gmail_search", query="-in:spam from:boss", max=5)
        self.assertEqual(r["result"]["threads"][0]["id"], "t1")
        argv = [c["argv"] for c in self.calls() if "search" in c["argv"]][-1]
        self.assertEqual(argv[-3:], ["--max=5", "--", "-in:spam from:boss"])
        self.assertIn("--gmail-no-send", argv)
        self.assertEqual(argv[argv.index("--enable-commands-exact") + 1], "gmail.search", "one exact command per call")
        self.assertEqual(self.api(h, op="gmail_get", id="18c0ffee")["result"]["id"], "18c0ffee")
        self.assertEqual(self.api(h, op="drive_ls", max=3)["result"], {"files": []})
        self.assertEqual(h.asked, [])
        doc = self.work / "notes.txt"
        doc.write_text("plain notes\n")
        no = FakeHost(self.st, self.work, {"behavior": "deny"})
        self.assertEqual(self.api(no, op="drive_upload", file=str(doc))["why"], "denied")
        self.assertFalse([c for c in self.calls() if "upload" in c["argv"]])
        r = self.api(h, op="drive_upload", file=str(doc))
        self.assertTrue(r["ok"], r)
        up = [c for c in self.calls() if "upload" in c["argv"]][-1]
        self.assertEqual(up["argv"][-4:], ["drive", "upload", "/dev/stdin", "--name=notes.txt"])
        self.assertEqual(up["argv"][up["argv"].index("--enable-commands-exact") + 1], "drive.upload")
        self.assertEqual(up["n"], len(b"plain notes\n"))
        env = self.work / ".env"
        env.write_text("A=1\n")
        self.assertEqual(self.api(h, op="drive_upload", file=str(env))["why"], "secret")
        value = "".join(("Qm7x", "Lp2v", "Rt9k", "Wz4n", "Hy6b"))          # synthetic; never printed
        env.write_text("DEMO_TOKEN=" + value + "\n")
        named = self.work / ("notes-" + value + ".txt")                   # N-L1: the default Drive name is the basename
        named.write_text("plain notes\n")
        n = len(self.calls())
        self.assertEqual(self.api(h, op="drive_upload", file=str(named))["why"], "secret")
        self.assertTrue(self.api(h, op="drive_upload", file=str(named), name="notes.txt")["ok"])
        self.assertEqual(len([c for c in self.calls()[n:] if "upload" in c["argv"]]), 1)

    def test_cli_parses_and_reads_nothing(self):
        from agentj import google_api
        import argparse
        root = argparse.ArgumentParser()
        sub = root.add_subparsers()
        google.add_parser(sub)
        a = root.parse_args(["google", "youtube", "upload", "clip.mp4", "--title", "T", "--privacy", "unlisted"])
        cwd = os.getcwd()
        os.chdir(self.work)
        try:
            res, req = google_api.cli_request(a)
        finally:
            os.chdir(cwd)
        self.assertIsNone(res)
        self.assertEqual(req, {"t": "google_api", "op": "youtube_upload", "file": str(self.work / "clip.mp4"), "title": "T",
                               "privacy": "unlisted"})
        a = root.parse_args(["google", "search-console", "query", "sc-domain:x.example", "--start", "2026-10-01",
                             "--end", "2026-10-02", "--dimension", "page"])
        self.assertEqual(google_api.cli_request(a)[1]["dimensions"], ["page"])
        a = root.parse_args(["google", "status"])
        self.assertEqual(a.action, "status")


class Wording(unittest.TestCase):
    def test_skill_and_status_no_longer_say_not_open(self):
        skill = (AJ / "skills" / "agentj-google" / "SKILL.md").read_text()
        for w in ("仍未开放", "not authorizable yet", "未开放"):
            self.assertNotIn(w, skill)
        for w in ("youtube upload", "search-console", "gmail send", "--client-file", "--account"):
            self.assertIn(w, skill)
        src = (AJ / "google.py").read_text() + (AJ / "google_connect.py").read_text()
        for w in ("not_available_yet", "calendar_connectable", "only Calendar purposes"):
            self.assertNotIn(w, src)
        self.assertEqual(set(gc.CONNECTABLE), set(google.PURPOSES))


if __name__ == "__main__":
    unittest.main()
