"""#436 security round 1: every Google op passes the host's turn gate; cards show all that goes out and where it came from.

H1  gmail_search / drive_ls / youtube_get under a group request source → refused (group_source), zero gog / HTTP calls;
    the CLI has no in-process fallback when serve is not running.
L1  connect / disconnect over elevate.sock from a group turn → refused before google.execute.
M2  a body / description longer than the card shows → refused (too_long), no card; a value the local SecretIndex knows
    (also split with "/") in to / subject / body / title / description → refused (secret), no card.
L2  the card names a delegated source (plaza_perms.VIA).
L3  a loopback connection that sends nothing does not hold consent past its deadline.
L5  after gmail search in this turn, the send card says so; a new turn does not.
Info google_oauth.call does not follow a redirect (the bearer token stays with the URL we named).
Synthetic everything (fake gog, fake Google on 127.0.0.1, test HOME from _hermetic).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import http.server
import socket
import threading
import time
import unittest
import urllib.parse
from types import SimpleNamespace
from unittest import mock

import test_p436_google_all as T
from agentj import google, provenance

GROUP = {"kind": "group", "id": "g-synthetic"}
LOCAL = "p436-synthetic-local-0001"          # a value only this host's .env knows (not a known credential shape)


class Host(T.FakeHost):
    def __init__(self, st, work, answer, source=None):
        super().__init__(st, work, answer)
        self.agent = SimpleNamespace(cur_send=None, native_source=source or provenance.phone())
        self.cur_turn = 1


class Gate(T.Adapter):
    def api(self, host, **req):
        from agentj import google_api
        return asyncio.run(google_api.serve(host, self.st, {"t": "google_api", **req}))

    def gapi(self, host, src, **req):
        from agentj import google_api

        async def go():
            with provenance.request_source(src):
                return await google_api.serve(host, self.st, {"t": "google_api", **req})
        return asyncio.run(go())

    def test_group_reads_refused_before_any_call(self):
        self.full("youtube-publish")
        self.full("search-console-read")
        self.full("gmail-read")
        self.full("drive-file")
        gog, http_ = len(self.calls()), len(self.srv.calls)
        h = Host(self.st, self.work, T.ALLOW)
        for req in ({"op": "gmail_search", "query": "from:boss"}, {"op": "gmail_get", "id": "18c0ffee"},
                    {"op": "drive_ls"}, {"op": "youtube_get", "id": "VID436abc"}, {"op": "sc_sites"}):
            r = self.gapi(h, GROUP, **req)
            self.assertEqual((r["ok"], r["why"]), (False, "group_source"), req)
            self.assertIn("detail", r)
        self.assertEqual(len(self.calls()), gog, "no gog call from a group turn")
        self.assertEqual(len(self.srv.calls), http_, "no HTTP call from a group turn")
        self.assertEqual(h.asked, [])
        otg = Host(self.st, self.work, T.ALLOW, {"kind": "same_owner", "role": "task", "fid": "synthetic-peer",
                                                  "otg_required": True})
        self.assertEqual(len(self.calls()), gog)
        self.assertTrue(self.api(h, op="drive_ls")["ok"], "the owner's own turn still reads")
        # 0304 A2-②: a same-owner grant task needs its grant only for UV steps; a Google read follows the delegated rule.
        self.assertTrue(self.api(otg, op="drive_ls")["ok"])

    def test_cli_has_no_in_process_fallback(self):
        import argparse
        from agentj import elevate, google_api
        root = argparse.ArgumentParser()
        google.add_parser(root.add_subparsers())
        a = root.parse_args(["google", "gmail", "search", "from:boss", "--json"])
        with mock.patch.object(elevate, "client_request", return_value={"result": "unavailable"}), \
                mock.patch.object(google_api, "serve", side_effect=AssertionError("in-process serve")), \
                mock.patch("builtins.print") as out:
            self.assertEqual(google_api.cmd(a), 1)
        self.assertIn('"not_running"', out.call_args[0][0])

    def test_l1_connect_disconnect_from_group_turn_refused(self):
        from test_l1 import _owner_host
        host = _owner_host(self.st, [])
        seen = []

        async def go():
            with mock.patch.object(google, "execute", side_effect=lambda *a, **k: seen.append(a) or {"ok": True}):
                out = []
                with provenance.request_source(GROUP):
                    out.append(await host.elevate._p115({"t": "google", "op": "disconnect", "purpose": "youtube-publish"}))
                    out.append(await host.elevate._p115({"t": "google", "op": "connect", "purpose": "gmail-read",
                                                         "account": T.EMAIL}))
                out.append(await host.elevate._p115({"t": "google", "op": "connect_status", "purpose": None}))
                return out
        res = asyncio.run(go())
        self.assertEqual([r.get("why") for r in res[:2]], ["group_source", "group_source"])
        self.assertEqual([a[0] for a in seen], ["connect_status"], "only the owner's own turn reaches google.execute")

    def test_m2_too_long_refused_not_cut(self):
        self.full("gmail-send")
        self.full("youtube-publish")
        h = Host(self.st, self.work, T.ALLOW)
        r = self.api(h, op="gmail_send", to=["friend.p436@example.org"], subject="Hi", body="x" * 1501)
        self.assertEqual((r["ok"], r["why"], r["field"]), (False, "too_long", "body"))
        self.assertIn("分开发", r["detail"])
        r = self.api(h, op="youtube_upload", file=str(T.Use.video(self)), title="t", description="d" * 601)
        self.assertEqual((r["why"], r["field"]), ("too_long", "description"))
        self.assertEqual(h.asked, [], "nothing longer than the card shows reaches a card")
        r = self.api(h, op="gmail_send", to=["friend.p436@example.org"], subject="Hi", body="y" * 1500)
        self.assertTrue(r["ok"], r)
        self.assertIn("y" * 1500, h.asked[-1][1]["summary"], "the card shows the whole body")

    def test_m2_local_secret_index_refused(self):
        self.full("gmail-send")
        self.full("youtube-publish")
        (self.work / ".env").write_text(f"P436_SYNTH_TOKEN={LOCAL}\n")
        h = Host(self.st, self.work, T.ALLOW)
        for kw in ({"subject": "Hi", "body": "see " + LOCAL}, {"subject": "/".join(LOCAL[i:i + 4] for i in range(0, len(LOCAL), 4)), "body": "b"},
                   {"subject": "Hi", "body": "b", "cc": [LOCAL[:16] + "@example.org"]}):
            r = self.api(h, op="gmail_send", to=["friend.p436@example.org"], **kw)
            self.assertEqual((r["ok"], r["why"]), (False, "secret"), kw)
        r = self.api(h, op="youtube_upload", file=str(T.Use.video(self)), title="t", description="key " + LOCAL)
        self.assertEqual(r["why"], "secret")
        self.assertEqual(h.asked, [])

    def test_l2_card_names_a_delegated_source(self):
        self.full("gmail-send")
        h = Host(self.st, self.work, T.ALLOW, {"kind": "same_owner", "role": "task", "fid": "synthetic-peer"})
        r = self.api(h, op="gmail_send", to=["friend.p436@example.org"], subject="Hi", body="b")
        self.assertTrue(r["ok"], r)
        self.assertIn("来源：委托任务", h.asked[-1][1]["summary"])

    def test_l5_mail_read_marks_this_turn_only(self):
        self.full("gmail-read")
        self.full("gmail-send")
        h = Host(self.st, self.work, T.ALLOW)
        send = dict(op="gmail_send", to=["friend.p436@example.org"], subject="Hi", body="b")
        self.assertTrue(self.api(h, **send)["ok"])
        self.assertNotIn("读过外部邮件", h.asked[-1][1]["summary"])
        self.assertTrue(self.api(h, op="gmail_search", query="from:boss")["ok"])
        self.assertTrue(self.api(h, **send)["ok"])
        self.assertIn("这一轮读过外部邮件", h.asked[-1][1]["summary"])
        h.cur_turn = 2                               # the next turn
        self.assertTrue(self.api(h, **send)["ok"])
        self.assertNotIn("读过外部邮件", h.asked[-1][1]["summary"])
        from agentj import plaza_perms
        h.cur_turn = 3
        self.assertTrue(self.api(h, op="gmail_get", id="18c0ffee")["ok"])
        self.assertTrue(plaza_perms.turn_gate(h)["mail"], "plaza cards in this turn get the line too")



class Loopback(unittest.TestCase):
    def test_l3_silent_connection_does_not_hold_consent(self):
        import tempfile
        from agentj import google_oauth as go
        from test_l1 import _state
        tmp = tempfile.TemporaryDirectory(prefix="p436s-")
        self.addCleanup(tmp.cleanup)
        st = _state(__import__("pathlib").Path(tmp.name))
        socks = []

        def silent(_st, url):
            port = urllib.parse.urlsplit(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["redirect_uri"]).port
            socks.append(socket.create_connection(("127.0.0.1", port)))          # connects, never sends a byte
        res = {}
        with mock.patch.object(go, "_load", return_value={"client_id": "p436.apps.googleusercontent.com"}), \
                mock.patch.object(go, "_open", side_effect=silent):
            t0 = time.monotonic()
            th = threading.Thread(target=lambda: res.update(r=go.consent(st, "youtube-publish", T.EMAIL, 1.5)), daemon=True)
            th.start()
            th.join(8)
            alive, took = th.is_alive(), time.monotonic() - t0
        for s in socks:
            s.close()
        th.join(5)
        self.assertFalse(alive, "a silent local connection held the consent window")
        self.assertLess(took, 3.5, "the consent deadline is wall-clock")
        self.assertEqual(res["r"], ("consent_timeout", []))


class NoRedirect(unittest.TestCase):
    def test_info_call_does_not_follow_redirects(self):
        from agentj import google_oauth as go
        hits = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_GET(self):
                hits.append((self.path, self.headers.get("Authorization")))
                self.send_response(302 if self.path == "/a" else 200)
                self.send_header("Location", "/b")
                self.send_header("Content-Length", "0")
                self.end_headers()
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with mock.patch.dict("os.environ", {k: "" for k in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY")}):
            s, _h, _b = go.call("GET", f"http://127.0.0.1:{srv.server_address[1]}/a", headers={"Authorization": "Bearer P436-X"})
        self.assertEqual(s, 302)
        self.assertEqual([p for p, _ in hits], ["/a"], "the bearer token never follows a redirect")


if __name__ == "__main__":
    unittest.main()
