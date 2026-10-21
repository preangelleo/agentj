"""P432 (#66 Cards phase 2): a card's own server on this computer (jcards_server), the Files card, the Obsidian card and the
plaza card package. Throw-away state dirs and real loopback HTTP servers only; the fake key is assembled, never a literal."""
import _hermetic  # noqa: F401  (first: a throw-away AGENTJ_STATE_DIR for this process)
import asyncio
import copy
import os
import http.server
import json
import pathlib
import tempfile
import threading
import time
import unittest
import unittest.mock
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from agentj import jcards, jcards_server

AJ = pathlib.Path(__file__).resolve().parents[2]
FAKE_KEY = "AKIA" + "Q3EGRKX7" + "PLM2VN4B"


class CardServer:
    """A real HTTP server on 127.0.0.1:<free port>; `routes` maps a path to (status, headers, body, delay)."""

    def __init__(self):
        self.routes, self.seen = {}, []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.seen.append((self.command, self.path, dict(self.headers), self.rfile.read(n) if n else b""))
                status, hdr, body, delay = outer.routes.get(self.path, (404, {}, b"", 0))
                time.sleep(delay)
                self.send_response(status)
                for k, v in hdr.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_OPTIONS = _answer

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def server_card(port, **over):
    doc = {"schemaVersion": 1, "id": "board", "version": "1", "name": {"zh": "看板", "en": "Board"},
           "description": {"zh": "本机服务的数据", "en": "Data from a local server"}, "icon": "board", "accent": "windstorm",
           "template": "checklist", "data": {"get": "/cards/board"},
           "server": {"port": port, "routes": [{"id": "refresh", "path": "/refresh"}, {"id": "done", "path": "/done"}]},
           "actions": [{"id": "refresh", "label": {"zh": "刷新", "en": "Refresh"}, "call": "refresh"},
                       {"id": "ask", "label": {"zh": "问问", "en": "Ask"}, "prompt": "看看 {{card.name}}：{{data.path}}"}],
           "itemActions": [{"id": "done", "label": {"zh": "完成", "en": "Done"}, "call": "done"}]}
    doc.update(over)
    if doc["template"] != "checklist":
        doc.pop("itemActions", None)
    return doc


class Base(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p432-")
        self.addCleanup(t.cleanup)
        self.tmp = pathlib.Path(t.name)
        self.root = self.tmp / "state"
        self.root.mkdir()
        jcards_server._last_get.clear()
        jcards_server._last_call.clear()

    def apply(self, doc):
        d = self.tmp / ("card-" + doc["id"])
        d.mkdir(exist_ok=True)
        (d / "card.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        r = jcards.apply(self.root, d)
        self.assertTrue(r["ok"], r)
        return r


class ServerManifest(Base):
    def codes(self, doc):
        return {e["code"] for e in jcards.check(doc)}

    def test_a_server_card_passes(self):
        self.assertEqual(jcards.check(server_card(8790)), [])
        self.assertEqual(jcards.check(server_card(8790, template="notes")), [])
        ok = server_card(8790)
        ok["server"].update({"repo": "https://github.com/someone/board-server", "ref": "a" * 40})
        self.assertEqual(jcards.check(ok), [])

    def test_refusals(self):
        bad = []
        for port in (80, 9222, 6379, 70000, "8790", True):
            bad.append(server_card(port))
        for path in ("cards", "/a/../b", "/a?x=1", "/a b", "http://evil/x", "/" + "a/" * 40, "/a%2e%2e"):
            bad.append(server_card(8790, data={"get": path}))
        d = server_card(8790)
        d["server"]["host"] = "0.0.0.0"
        bad.append(d)
        d = server_card(8790)
        d["server"]["routes"].append({"id": "x", "path": "/x", "method": "DELETE"})
        bad.append(d)
        d = server_card(8790)
        d["server"].update({"repo": "https://github.com/a/b", "ref": "main"})
        bad.append(d)
        d = server_card(8790)
        d["server"]["repo"] = "https://gitlab.com/a/b"
        bad.append(d)
        for doc in bad:
            self.assertTrue(self.codes(doc) & {"server", "data"}, doc)

    def test_buttons_name_a_declared_route(self):
        d = server_card(8790)
        d["actions"][0]["call"] = "nope"
        self.assertIn("call", self.codes(d))
        d = server_card(8790)
        d["actions"][0]["draft"] = True
        self.assertIn("call", self.codes(d))
        d = server_card(8790)
        d["actions"][0]["prompt"] = "x"
        self.assertIn("actions", self.codes(d))                  # prompt and call: one of the two
        d = server_card(8790)
        del d["server"]
        self.assertTrue({"data", "call"} <= self.codes(d))
        both = server_card(8790, data={"get": "/x", "file": "x.json"})
        self.assertIn("data", self.codes(both))

    def test_schema_knows_the_server(self):
        schema = json.loads(jcards.SCHEMA_PATH.read_text(encoding="utf-8"))
        srv = schema["properties"]["server"]
        self.assertEqual(set(srv["properties"]), jcards_server.SERVER_KEYS)
        self.assertEqual(srv["properties"]["routes"]["maxItems"], jcards_server.ROUTES_MAX)
        self.assertEqual(set(srv["properties"]["port"]["not"]["enum"]), set(jcards_server.DENY_PORTS))
        self.assertEqual(srv["properties"]["ref"]["pattern"], "^" + jcards_server.REF_RE.pattern + "$")
        self.assertIn("call", schema["$defs"]["action"]["properties"])
        self.assertIn("get", schema["properties"]["data"]["properties"])


class ServerData(Base):
    def setUp(self):
        super().setUp()
        self.srv = CardServer()
        self.addCleanup(self.srv.close)
        self.apply(server_card(self.srv.port))

    def ok(self, body, ctype="application/json", delay=0):
        return (200, {"X-AgentJ-Card": "board", "Content-Type": ctype}, body, delay)

    def test_data_comes_from_the_server_and_is_redacted(self):
        self.srv.routes["/cards/board"] = self.ok(json.dumps({"items": [{"id": "a1", "text": "deploy " + FAKE_KEY}, "two"]}).encode())
        m = jcards.phone_get(self.root, "board", "en")
        self.assertTrue(m["ok"], m)
        self.assertNotIn("problem", m)
        self.assertEqual([i["id"] for i in m["items"]], ["a1", "i2"])
        self.assertNotIn(FAKE_KEY, json.dumps(m))
        self.assertEqual(m["path"], f"http://127.0.0.1:{self.srv.port}/cards/board")
        method, path, hdr, _ = self.srv.seen[-1]
        self.assertEqual((method, path, hdr.get("X-AgentJ-Card")), ("GET", "/cards/board", "board"))
        self.assertEqual(m["actions"][0], {"id": "refresh", "label": "Refresh", "call": True,
                                           "where": f"POST http://127.0.0.1:{self.srv.port}/refresh"})
        self.assertIn(f"http://127.0.0.1:{self.srv.port}/cards/board", m["actions"][1]["prompt"])
        self.assertEqual(m["items"][0]["acts"][0]["call"], True)
        self.assertNotIn("prompt", m["items"][0]["acts"][0])

    def test_only_this_cards_server_is_shown(self):
        self.srv.routes["/cards/board"] = (200, {"Content-Type": "application/json"}, b'{"items":["secret page"]}', 0)
        m = jcards.phone_get(self.root, "board")
        self.assertEqual((m["problem"], m["items"]), ("not_card", []))
        jcards_server._last_get.clear()
        self.srv.routes["/cards/board"] = (200, {"X-AgentJ-Card": "other"}, b'{"items":["x"]}', 0)
        self.assertEqual(jcards.phone_get(self.root, "board")["problem"], "not_card")

    def test_redirects_errors_size_and_timeout(self):
        self.srv.routes["/cards/board"] = (302, {"Location": "http://example.com/", "X-AgentJ-Card": "board"}, b"", 0)
        self.assertEqual(jcards.phone_get(self.root, "board")["problem"], "server_status")
        self.assertEqual(len(self.srv.seen), 1)                    # never followed
        jcards_server._last_get.clear()
        self.srv.routes["/cards/board"] = self.ok(b'{"items":["' + b"x" * 70000 + b'"]}')
        self.assertEqual(jcards.phone_get(self.root, "board")["problem"], "too_big")
        jcards_server._last_get.clear()
        self.srv.routes["/cards/board"] = self.ok(b'{"items":[]}', delay=0.6)
        with unittest.mock.patch.object(jcards_server, "TIMEOUT", 0.2):
            self.assertEqual(jcards.phone_get(self.root, "board")["problem"], "server_timeout")
        jcards_server._last_get.clear()
        port = self.srv.port
        self.srv.close()
        self.addCleanup(lambda: None)
        self.srv = SimpleNamespace(close=lambda: None)
        m = jcards.phone_get(self.root, "board")
        self.assertEqual((m["problem"], m["path"]), ("server_down", f"http://127.0.0.1:{port}/cards/board"))

    def test_one_read_per_second(self):
        self.srv.routes["/cards/board"] = self.ok(b'{"items":["a"]}')
        for _ in range(5):
            jcards.phone_get(self.root, "board")
        self.assertEqual(len(self.srv.seen), 1)

    def test_notes_from_a_server_are_not_editable(self):
        doc = server_card(self.srv.port, id="diary", template="notes", data={"get": "/diary"})
        self.apply(doc)
        self.srv.routes["/diary"] = (200, {"X-AgentJ-Card": "diary", "Content-Type": "text/markdown"}, "- one\n- two\n".encode(), 0)
        m = jcards.phone_get(self.root, "diary")
        self.assertEqual([e["editable"] for e in m["entries"]], [False, False])
        from agentj import jcards_data
        r = jcards_data.note_change(self.root, "diary", "edit", m["entries"][0]["id"], m["revision"], "x")
        self.assertFalse(r["ok"])

    def test_call_posts_a_fixed_body_and_answers_one_line(self):
        self.srv.routes["/refresh"] = self.ok(json.dumps({"ok": True, "message": "刷新好了\n" + FAKE_KEY}).encode())
        r = jcards_server.call(self.root, "board", "refresh", None, "zh")
        self.assertTrue(r["ok"], r)
        self.assertNotIn(FAKE_KEY, r["message"])
        self.assertNotIn("\n", r["message"])
        method, path, hdr, body = self.srv.seen[-1]
        self.assertEqual((method, path, hdr["X-AgentJ-Card"]), ("POST", "/refresh", "board"))
        self.assertEqual(json.loads(body), {"card": "board", "action": "refresh", "lang": "zh"})
        self.assertEqual(jcards_server.call(self.root, "board", "refresh")["why"], "too_soon")   # one per CALL_GAP
        jcards_server._last_call.clear()
        self.srv.routes["/done"] = self.ok(b'{"ok": false, "message": "no"}')
        r = jcards_server.call(self.root, "board", "done", "a1")
        self.assertEqual((r["ok"], r["message"]), (False, "no"))
        self.assertEqual(json.loads(self.srv.seen[-1][3])["item"], "a1")

    def test_call_refusals(self):
        n = len(self.srv.seen)
        for args, why in ((("nope", "refresh"), "not_found"), (("board", "ask"), "not_found"),       # a prompt button never calls
                          (("board", "done"), "not_found"), (("board", "refresh", "x y"), "bad_item"),
                          (("board", "refresh", "a1"), "not_found"), (("board", "x" * 500), "not_found")):
            r = jcards_server.call(self.root, *args)
            self.assertEqual(r["why"], why, args)
        self.assertIsNone(jcards_server.call(self.root, "board", "x" * 500)["act"])
        jcards.set_on(self.root, "board", False)
        self.assertEqual(jcards_server.call(self.root, "board", "refresh")["why"], "off")
        self.assertEqual(len(self.srv.seen), n)

    def test_serve_handler_answers_and_logs(self):
        from agentj.serve import Host
        h = Host.__new__(Host)
        h.st = SimpleNamespace(root=self.root, log=Mock())
        h.lang, h.agent_cfg, h.estop = "zh", None, {}
        h.send_app = AsyncMock(return_value=True)
        self.srv.routes["/refresh"] = self.ok(b'{"message":"ok"}')
        asyncio.run(h.on_jcard(SimpleNamespace(device="d1"), "jcard_call", {"r": "r1", "id": "board", "act": "refresh"}))
        m = h.send_app.call_args.args[1]
        self.assertEqual((m["t"], m["r"], m["ok"], m["message"]), ("jcard_call", "r1", True, "ok"))
        h.st.log.assert_called_with("jcard_call", device="d1", status="board:refresh:ok")
        with unittest.mock.patch.object(jcards_server, "call", side_effect=RuntimeError("x")):
            asyncio.run(h.on_jcard(SimpleNamespace(device="d1"), "jcard_call", {"r": "r2", "id": "board", "act": "refresh"}))
        self.assertEqual(h.send_app.call_args.args[1], {"t": "jcard_call", "ok": False, "id": None, "on": None, "r": "r2"})
        self.assertIn("jcard_call", jcards.PHONE_TYPES)


class FilesCard(Base):
    """P432: the official Files card — roots, the no-follow listing, downloads through §13 media."""

    def setUp(self):
        super().setUp()
        from agentj import jcards_files, media
        self.jf, self.media_mod = jcards_files, media
        self.work = self.tmp / "work"
        (self.work / "docs").mkdir(parents=True)
        (self.work / "docs" / "plan.md").write_text("# plan\n", encoding="utf-8")
        (self.work / "report.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 100)
        (self.work / "notes.txt").write_text("hello", encoding="utf-8")
        (self.work / ".env").write_text("K=v", encoding="utf-8")
        (self.work / ".git").mkdir()
        (self.work / "credentials").mkdir()
        (self.work / "server.pem").write_text("x", encoding="utf-8")
        (self.work / "my-token.txt").write_text("x", encoding="utf-8")
        (self.work / "keys.txt").write_text("deploy " + FAKE_KEY, encoding="utf-8")
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("x", encoding="utf-8")
        (self.work / "link-dir").symlink_to(outside)
        (self.work / "link.md").symlink_to(outside / "secret.md")
        self.cfg = {"dir": str(self.work)}

    def names(self, r):
        return [(e["name"], e["kind"]) for e in r["entries"]]

    def test_default_off_and_routing(self):
        c = jcards.find(self.root, "files")
        self.assertTrue(c["official"])
        self.assertFalse(c["on"])
        self.assertIn("files", jcards.OFFICIAL)
        self.assertIn("reserved", {e["code"] for e in jcards.check({**jcards.find(self.root, "notes")["doc"], "id": "files"})})
        self.assertTrue(jcards.set_on(self.root, "files", True))
        m = jcards.phone_get(self.root, "files")
        self.assertEqual((m["ok"], m["template"]), (True, "files"))

    def test_listing_hides_links_hidden_and_credential_names(self):
        r = self.jf.listing(self.root, self.cfg)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.names(r), [("docs", "dir"), ("keys.txt", "file"), ("notes.txt", "file"), ("report.pdf", "file")])
        self.assertEqual(r["roots"], [{"id": "r0", "label": self.jf._short(os.path.realpath(self.work))}])
        pdf = next(e for e in r["entries"] if e["name"] == "report.pdf")
        self.assertTrue(pdf["dl"])
        r = self.jf.listing(self.root, self.cfg, "r0", "docs")
        self.assertEqual((r["path"], self.names(r)), ("docs", [("plan.md", "file")]))
        for bad in ("link-dir", "../outside", ".git", "credentials", "docs/../..", "/etc", "a\x00b"):
            r = self.jf.listing(self.root, self.cfg, "r0", bad)
            self.assertFalse(r["ok"], bad)
        self.assertEqual(self.jf.listing(self.root, self.cfg, "r0", "link-dir")["why"], "refused")
        self.assertEqual(self.jf.listing(self.root, self.cfg, "r0", "nope")["why"], "gone")
        self.assertEqual(self.jf.listing(self.root, self.cfg, "r9", "")["why"], "not_found")
        self.assertEqual(self.jf.listing(self.root, None)["why"], "no_root")

    def test_roots_the_owner_adds_and_refusals(self):
        extra = self.tmp / "extra"
        extra.mkdir()
        self.assertTrue(self.jf.change_roots(self.root, "add", str(extra))["ok"])
        rs = self.jf.roots(self.root, self.cfg)
        self.assertEqual([r["id"] for r in rs], ["r0", "r1"])
        self.assertFalse(str(self.jf._roots_file(self.root)).startswith(str(self.root / "agentperm")))   # outside the fence's view
        for bad in ("/", str(self.root), str(self.root / "agentperm"), str(self.work / ".git"), str(self.tmp / "nope"), "relative/dir"):
            self.assertFalse(self.jf.change_roots(self.root, "add", bad)["ok"], bad)
        self.assertTrue(self.jf.change_roots(self.root, "remove", str(extra))["ok"])
        self.assertEqual(self.jf.change_roots(self.root, "remove", str(extra))["why"], "not_found")
        # a working folder that holds the state dir is never a root
        self.assertEqual(self.jf.roots(self.root, {"dir": str(self.tmp)}), [])

    def test_download_is_a_media_item_with_the_same_checks(self):
        from agentj.state import State
        med = self.media_mod.Media(State(self.root), None)
        r = self.jf.fetch(self.root, self.cfg, med, "r0", "report.pdf")
        self.assertTrue(r["ok"], r)
        item = r["item"]
        self.assertEqual((item["name"], item["mime"], item["kind"]), ("report.pdf", "application/pdf", "pdf"))
        chunks = med.window(item["mid"], 0)
        self.assertEqual(b"".join(c[1] for c in chunks), (self.work / "report.pdf").read_bytes())
        for rel, why in (("keys.txt", "secret"), ("link.md", "refused"), ("link-dir/secret.md", "refused"), (".env", "refused"),
                         ("server.pem", "refused"), ("credentials/x", "refused"), ("nope.pdf", "gone"), ("docs", "refused"), ("", "not_found")):
            got = self.jf.fetch(self.root, self.cfg, med, "r0", rel)
            self.assertEqual((got["ok"], got["why"]), (False, why), rel)
        with unittest.mock.patch.dict(self.media_mod.CAPS, {"pdf": 10}):
            got = self.jf.fetch(self.root, self.cfg, med, "r0", "report.pdf")
        self.assertEqual((got["why"], got["bytes"]), ("too_big", 109))

    def test_serve_answers_only_for_the_switched_on_card(self):
        from agentj.serve import Host
        from agentj.state import State
        h = Host.__new__(Host)
        h.st = SimpleNamespace(root=self.root, log=Mock())
        h.lang, h.agent_cfg, h.estop = "zh", self.cfg, {}
        h.media = self.media_mod.Media(State(self.root), None)
        h.send_app = AsyncMock(return_value=True)
        s = SimpleNamespace(device="d1")
        asyncio.run(h.on_jcard(s, "jcard_files", {"r": "r1", "id": "files"}))
        self.assertEqual(h.send_app.call_args.args[1]["why"], "off")
        jcards.set_on(self.root, "files", True)
        asyncio.run(h.on_jcard(s, "jcard_files", {"r": "r2", "id": "files", "path": "docs"}))
        m = h.send_app.call_args.args[1]
        self.assertEqual((m["t"], m["r"], m["ok"], [e["name"] for e in m["entries"]]), ("jcard_files", "r2", True, ["plan.md"]))
        asyncio.run(h.on_jcard(s, "jcard_file", {"r": "r3", "id": "files", "root": "r0", "path": "report.pdf"}))
        m = h.send_app.call_args.args[1]
        self.assertTrue(m["ok"])
        h.st.log.assert_called_with("jcard_file", device="d1", status="ok")
        asyncio.run(h.on_jcard(s, "jcard_files", {"r": "r4", "id": "notes"}))
        self.assertFalse(h.send_app.call_args.args[1]["ok"])

    def test_cli(self):
        import contextlib
        import io
        old = os.environ.get("AGENTJ_STATE_DIR")
        self.addCleanup(lambda: os.environ.__setitem__("AGENTJ_STATE_DIR", old) if old else os.environ.pop("AGENTJ_STATE_DIR", None))
        os.environ["AGENTJ_STATE_DIR"] = str(self.root)
        extra = self.tmp / "extra2"
        extra.mkdir()
        outs = []
        for args, code in ((["files", "add", str(extra), "--json"], 0), (["files", "list", "--json"], 0), (["files", "add", "/", "--json"], 1)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(jcards.command(args), code, args)
            outs.append(json.loads(out.getvalue()))
        self.assertIn(os.path.realpath(extra), outs[1]["roots"])
        self.assertEqual(outs[2]["why"], "refused")


class ObsidianCard(Base):
    """P432: the official Obsidian card — vault + picked notes, read without links, redacted; set up by the Agent's CLI."""

    def setUp(self):
        super().setUp()
        from agentj import jcards_obsidian
        self.jo = jcards_obsidian
        self.vault = self.tmp / "Vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        (self.vault / "Projects").mkdir()
        (self.vault / "Inbox.md").write_text("# Inbox\n\n- call the bank\n- key " + FAKE_KEY + "\n", encoding="utf-8")
        (self.vault / "Projects" / "Plan.md").write_text("Ship **cards**.\n", encoding="utf-8")
        (self.vault / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
        (self.vault / "big.md").write_text("word " * 20000, encoding="utf-8")
        outside = self.tmp / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        (self.vault / "link.md").symlink_to(outside)
        (self.vault / "linkdir").symlink_to(self.vault / "Projects", target_is_directory=True)

    def test_set_up_pick_and_show(self):
        c = jcards.find(self.root, "obsidian")
        self.assertEqual((c["official"], c["on"]), (True, False))           # starts off: it needs a vault first
        self.assertEqual(self.jo.change(self.root, "pick", ["Inbox.md"])["why"], "not_set")
        self.assertTrue(self.jo.change(self.root, "vault", [str(self.vault)])["ok"])
        r = self.jo.change(self.root, "pick", ["Inbox.md", str(self.vault / "Projects" / "Plan.md"), "big.md"])
        self.assertEqual(r["notes"], ["Inbox.md", "Projects/Plan.md", "big.md"])
        self.assertTrue(str(jcards.data_dir(self.root, "obsidian") / "selection.json").startswith(str(self.root / "agentperm")))
        jcards.set_on(self.root, "obsidian", True)
        m = jcards.phone_get(self.root, "obsidian", "en")
        self.assertEqual((m["ok"], m["template"], m["plus"]), (True, "obsidian", "agentj-obsidian"))
        self.assertEqual([e["title"] for e in m["entries"]], ["Inbox", "Plan", "big"])
        self.assertNotIn(FAKE_KEY, json.dumps(m))
        self.assertIn("call the bank", m["entries"][0]["text"])
        self.assertTrue(m["entries"][2]["cut"])
        self.assertLessEqual(len(m["entries"][2]["text"].encode()), self.jo.NOTE_MAX)
        self.assertEqual(m["actions"][0], {"id": "pick", "label": "Pick notes", "prompt": "/agentj-obsidian pick", "draft": True})
        self.assertEqual(self.jo.change(self.root, "unpick", ["big.md"])["notes"], ["Inbox.md", "Projects/Plan.md"])

    def test_refusals(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        for folder, why in ((str(plain), "no_vault"), ("/", "refused"), (str(self.root), "refused"), (str(self.tmp / "nope"), "not_a_folder")):
            self.assertEqual(self.jo.change(self.root, "vault", [folder])["why"], why, folder)
        self.assertTrue(self.jo.change(self.root, "vault", [str(self.vault)])["ok"])
        for note, why in (("link.md", "refused"), ("linkdir/Plan.md", "refused"), (".obsidian/app.json", "refused"),
                          ("../outside.md", "refused"), ("Projects", "refused"), ("missing.md", "gone"), ("/etc/passwd", "refused")):
            self.assertEqual(self.jo.change(self.root, "pick", [note])["why"], why, note)
        # a selection written by hand (an Agent inside the fence can) is checked again on every read
        jcards._write_json(self.root, ("cards-data", "obsidian"), "selection.json", {"vault": str(self.vault), "notes": ["link.md", "../outside.md"]})
        jcards.set_on(self.root, "obsidian", True)
        m = jcards.phone_get(self.root, "obsidian")
        self.assertEqual([e.get("problem") for e in m["entries"]], ["refused", "refused"])
        self.assertNotIn("outside", json.dumps([e["text"] for e in m["entries"]]))
        jcards._write_json(self.root, ("cards-data", "obsidian"), "selection.json", {"vault": str(plain), "notes": ["x.md"]})
        self.assertEqual(jcards.phone_get(self.root, "obsidian")["problem"], "no_vault")

    def test_known_vaults_from_obsidians_own_list(self):
        home = self.tmp / "home"
        (home / ".config/obsidian").mkdir(parents=True)
        (home / ".config/obsidian/obsidian.json").write_text(json.dumps({"vaults": {"a": {"path": str(self.vault)}, "b": {"path": str(self.tmp / "gone")}}}))
        with unittest.mock.patch("pathlib.Path.home", return_value=home):
            self.assertEqual(self.jo.known_vaults(), [str(self.vault)])

    def test_cli_and_skill(self):
        import contextlib
        import io
        old = os.environ.get("AGENTJ_STATE_DIR")
        self.addCleanup(lambda: os.environ.__setitem__("AGENTJ_STATE_DIR", old) if old else os.environ.pop("AGENTJ_STATE_DIR", None))
        os.environ["AGENTJ_STATE_DIR"] = str(self.root)
        for args, code in ((["obsidian", "vault", str(self.vault)], 0), (["obsidian", "pick", "Inbox.md"], 0), (["obsidian", "list"], 0),
                           (["obsidian", "pick", "link.md"], 1)):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(jcards.command(args), code, args)
        self.assertEqual(self.jo.config(self.root)["notes"], ["Inbox.md"])
        from agentj import personalize
        self.assertIn("agentj-obsidian", personalize.bundled())
        text = (AJ / "host/agentj/skills/agentj-obsidian/SKILL.md").read_text(encoding="utf-8")
        for word in ("agentj card obsidian find", "agentj card obsidian vault", "agentj card obsidian pick", "agentj card open obsidian"):
            self.assertIn(word, text)


class CardPackage(Base):
    """P432: a card as a plaza package — pack, the 上线关键点 preflight, install switched off, the phone's permission summary
    with its digest, update and rollback."""

    def setUp(self):
        super().setUp()
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import market_fixtures as fx
        from agentj import bundle, jcards_pkg, market
        self.fx, self.bundle, self.pkg, self.market = fx, bundle, jcards_pkg, market
        self.e = fx.Env()
        self.addCleanup(self.e.close)
        self.root = self.e.st.root
        self.srv = fx.PkgServer()

    def card(self, **over):
        doc = server_card(8790, id="release-board", version="1.2")
        doc["server"].update({"repo": "https://github.com/someone/release-board", "ref": "a" * 40})
        doc.update(over)
        return doc

    def packed(self, doc, sample=b'{"items":[{"id":"a","text":"ok","done":false}]}', name="src"):
        d = self.tmp / name
        d.mkdir()
        (d / "card.json").write_text(json.dumps(doc, ensure_ascii=False))
        r = jcards.apply(self.root, d)
        self.assertTrue(r["ok"], r)
        out = self.tmp / (name + "-pkg")
        return self.pkg.pack(self.root, doc["id"], out), out

    def test_pack_builds_a_package_that_passes_and_carries_no_data(self):
        (jcards.data_dir(self.root, "release-board")).mkdir(parents=True)
        (jcards.data_dir(self.root, "release-board") / "private.json").write_text("{}")
        r, out = self.packed(self.card())
        self.assertTrue(r["ok"], r)
        self.assertEqual(sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()), ["README.md", "card/card.json", "manifest.json"])
        data, m = self.bundle.build(out)
        self.assertEqual((m["type"], m["name"], m["version"], m["entry"]), ("card", "release-board", "1.2.0", "card/card.json"))
        self.assertEqual(self.pkg.preflight(self.bundle.parse(data)[1]), [])
        readme = (out / "README.md").read_text()
        self.assertIn("https://github.com/someone/release-board", readme)
        self.assertIn("POST http://127.0.0.1:8790/refresh", readme)
        self.assertEqual(self.pkg.pack(self.root, "notes", self.tmp / "x")["why"], "official")

    def test_preflight_refusals(self):
        r, out = self.packed(self.card())
        base = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file() and p.name != "manifest.json"}
        cases = []
        cases.append(({**base, "card/run.sh": b"echo"}, "only README.md"))
        unpinned = self.card()
        del unpinned["server"]["repo"], unpinned["server"]["ref"]
        cases.append(({**base, "card/card.json": json.dumps(unpinned).encode()}, "server.repo"))
        cases.append(({**base, "card/card.json": json.dumps(self.card(id="notes")).encode()}, "reserved"))
        cases.append(({k: v for k, v in base.items() if k != "README.md"}, "README.md"))
        notes = self.card(template="notes", data={"file": "n.md"}, itemActions=[], actions=[])
        notes.pop("itemActions")
        cases.append(({**base, "card/card.json": json.dumps(notes).encode(), "card/sample/n.md": ("key " + FAKE_KEY).encode()}, "looks like a key"))
        for files, word in cases:
            probs = self.pkg.preflight(files)
            self.assertTrue(any(word in p for p in probs), (word, probs))
        with self.assertRaises(self.bundle.BundleError):
            m = json.loads((out / "manifest.json").read_text())
            m["install"] = {"post_install": ["rm -rf ~"]}
            (out / "manifest.json").write_text(json.dumps(m))
            self.bundle.build(out)

    def test_plaza_install_off_summary_digest_update_and_rollback(self):
        import io
        r, out = self.packed(self.card(), name="author")
        data, m = self.bundle.build(out)
        jcards.remove(self.root, "release-board")                       # this computer is someone else's: no such card yet
        jcards._write_json(self.root, ("cards",), "state.json", {"on": {}})
        self.srv.add(data)
        o = io.StringIO()
        self.assertEqual(self.market.run_install("release-board", st=self.e.st, post=self.srv, get=self.srv.get, out=o), 0)
        text = o.getvalue()
        for word in ("卡片 card", "POST http://127.0.0.1:8790/refresh", "https://github.com/someone/release-board", "NOT INSTALLED", "--accept-unverified"):
            self.assertIn(word, text)
        self.assertIsNone(jcards.find(self.root, "release-board"))       # the preview writes nothing
        dg = text.split("digest: ")[1].split()[0]
        o = io.StringIO()
        self.assertEqual(self.market.run_install("release-board", st=self.e.st, post=self.srv, get=self.srv.get, out=o, owner_confirmed=True,
                                                 digest=dg, accept_unverified=True), 0, o.getvalue())
        c = jcards.find(self.root, "release-board")
        self.assertFalse(c["on"])                                        # installed switched off
        self.assertEqual(self.market.read_installed(self.e.st)[-1]["type"], "card")
        row = next(x for x in jcards.phone_list(self.root)["cards"] if x["id"] == "release-board")
        self.assertEqual(row["src"], "plaza")
        # the phone: summary first, the switch only with its digest
        from agentj.serve import Host
        h = Host.__new__(Host)
        h.st = SimpleNamespace(root=self.root, log=Mock())
        h.lang, h.agent_cfg, h.estop = "zh", None, {}
        h.send_app = AsyncMock(return_value=True)
        s = SimpleNamespace(device="d1")
        asyncio.run(h.on_jcard(s, "jcard_set", {"r": "r1", "id": "release-board", "on": True}))
        self.assertFalse(h.send_app.call_args.args[1]["ok"])
        asyncio.run(h.on_jcard(s, "jcard_perm", {"r": "r2", "id": "release-board", "lang": "en"}))
        perm = h.send_app.call_args.args[1]
        self.assertIn("From the plaza: release-board v1.2.0 (community · pending review)", perm["text"])
        self.assertIn("calls POST http://127.0.0.1:8790/refresh", perm["text"])
        asyncio.run(h.on_jcard(s, "jcard_set", {"r": "r3", "id": "release-board", "on": True, "seen": "0" * 16}))
        self.assertFalse(h.send_app.call_args.args[1]["ok"])
        asyncio.run(h.on_jcard(s, "jcard_set", {"r": "r4", "id": "release-board", "on": True, "seen": perm["digest"]}))
        self.assertTrue(h.send_app.call_args.args[1]["ok"])
        self.assertTrue(jcards.find(self.root, "release-board")["on"])
        # a new version: --replace; the old one sleeps, the new one is off again
        d2 = self.tmp / "author-v2"
        d2.mkdir()
        new = self.card(version="1.3", description={"zh": "新版", "en": "New"})
        (d2 / "card.json").write_text(json.dumps(new))
        out2 = self.tmp / "author-v2-pkg"
        other = self.tmp / "other-state"
        other.mkdir()
        self.assertTrue(jcards.apply(other, d2)["ok"])
        self.assertTrue(self.pkg.pack(other, "release-board", out2)["ok"])
        data2, _m2 = self.bundle.build(out2)
        self.srv.add(data2)
        o = io.StringIO()
        self.market.run_install("release-board", st=self.e.st, post=self.srv, get=self.srv.get, out=o, replace=True)
        dg2 = o.getvalue().split("digest: ")[1].split()[0]
        o = io.StringIO()
        self.assertEqual(self.market.run_install("release-board", st=self.e.st, post=self.srv, get=self.srv.get, out=o, replace=True,
                                                 owner_confirmed=True, digest=dg2, accept_unverified=True), 0, o.getvalue())
        c = jcards.find(self.root, "release-board")
        self.assertEqual((c["doc"]["version"], c["on"]), ("1.3", False))
        r = self.pkg.rollback(self.root, "release-board")
        self.assertTrue(r["ok"], r)
        c = jcards.find(self.root, "release-board")
        self.assertEqual((c["doc"]["version"], c["on"]), ("1.2", False))
        self.assertEqual(self.pkg.source(self.root, "release-board", c["doc"])["version"], "1.2.0")
        self.assertEqual(self.pkg.rollback(self.root, "notes")["why"], "not_found")

    def test_an_owners_own_card_is_never_replaced_by_a_package(self):
        import io
        r, out = self.packed(self.card(), name="mine")                    # applied by the owner, not from the plaza
        data, _m = self.bundle.build(out)
        self.srv.add(data)
        with self.assertRaisesRegex(Exception, "never replaced"):
            self.market.run_install("release-board", st=self.e.st, post=self.srv, get=self.srv.get, out=io.StringIO())


class Hardening432(Base):
    """P432 security review (PASS-WITH-FIXES): every finding has its regression here."""

    def test_obsidian_vault_swapped_for_a_link_after_the_check_is_refused(self):
        from agentj import jcards_obsidian as jo
        a, b = self.tmp / "a", self.tmp / "b"
        for d, text in ((a, "mine"), (b, "someone else's")):
            (d / "v" / ".obsidian").mkdir(parents=True)
            (d / "v" / "n.md").write_text(text, encoding="utf-8")
        real = os.path.realpath(a / "v")
        self.assertEqual(jo.read_note(real, "n.md", self.root)[0], "mine")
        os.rename(a, self.tmp / "a-old")
        os.symlink(b, a)                                      # the parent swapped for a link after the vault was checked
        self.assertEqual(jo.read_note(real, "n.md", self.root)[:2], (None, "refused"))

    def test_files_root_swapped_for_a_link_is_refused(self):
        from agentj import jcards_files as jf
        a, b = self.tmp / "wa", self.tmp / "wb"
        (a / "w").mkdir(parents=True)
        (b / "w").mkdir(parents=True)
        (b / "w" / "other.txt").write_text("x")
        cfg = {"dir": str(a / "w")}
        self.assertTrue(jf.listing(self.root, cfg)["ok"])
        real = jf.roots(self.root, cfg)[0]["path"]
        os.rename(a, self.tmp / "wa-old")
        os.symlink(b, a)
        with unittest.mock.patch.object(jf, "roots", return_value=[{"id": "r0", "path": real, "label": "w"}]):
            self.assertEqual(jf.listing(self.root, cfg)["why"], "refused")

    def test_fence_hidden_paths_are_never_roots_entered_or_listed(self):
        from agentj import jcards_files as jf
        work = self.tmp / "work"
        (work / "seat2" / "x").mkdir(parents=True)
        (work / "mine").mkdir()
        seat2 = os.path.realpath(work / "seat2")
        with unittest.mock.patch("agentj.fence.protected_paths", return_value=[seat2]):
            r = jf.listing(self.root, {"dir": str(work)})
            self.assertEqual([e["name"] for e in r["entries"]], ["mine"])
            self.assertEqual(jf.listing(self.root, {"dir": str(work)}, "r0", "seat2")["why"], "refused")
            self.assertEqual(jf.roots(self.root, {"dir": seat2}), [])
            self.assertEqual(jf.change_roots(self.root, "add", str(work / "seat2" / "x"))["why"], "refused")
        for p in ("/proc", "/sys", "/dev"):
            if os.path.isdir(p):
                self.assertEqual(jf.change_roots(self.root, "add", p)["why"], "refused", p)

    def test_a_download_must_resolve_to_the_walked_path(self):
        from agentj import jcards_files as jf, media
        from agentj.state import State
        work = self.tmp / "w2"
        work.mkdir()
        (work / "a.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 50)
        med = media.Media(State(self.root), None)
        real = media.Media.check
        def moved(selfm, ref, owner=False, root=None):
            rec, why = real(selfm, ref, owner=owner, root=root)
            return ({**rec, "parts": ["elsewhere.pdf"]} if rec else rec), why
        with unittest.mock.patch.object(media.Media, "check", moved):
            self.assertEqual(jf.fetch(self.root, {"dir": str(work)}, med, "r0", "a.pdf")["why"], "refused")

    def test_a_trickling_server_cannot_outlive_the_deadline(self):
        import socket as so
        srv = so.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        stop = threading.Event()
        def trickle():
            c, _ = srv.accept()
            c.recv(4096)
            c.sendall(b"HTTP/1.1 200 OK\r\nX-AgentJ-Card: board\r\nContent-Length: 100\r\n\r\n")
            while not stop.is_set():
                try:
                    c.sendall(b"x")
                except OSError:
                    break
                time.sleep(0.2)
            c.close()
        threading.Thread(target=trickle, daemon=True).start()
        self.addCleanup(srv.close)
        self.addCleanup(stop.set)
        t0 = time.monotonic()
        with unittest.mock.patch.object(jcards_server, "TIMEOUT", 0.8):
            with self.assertRaises(LookupError) as e:
                jcards_server._request("board", port, "GET", "/x", None, 1000)
        self.assertEqual(e.exception.args[0], "server_timeout")
        self.assertLess(time.monotonic() - t0, 2.5)

    def test_a_post_needs_the_options_handshake_and_admin_ports_are_refused(self):
        srv = CardServer()
        self.addCleanup(srv.close)
        self.apply(server_card(srv.port))
        srv.routes["/refresh"] = (200, {"Content-Type": "application/json"}, b'{"ok":true}', 0)   # not this card's server
        r = jcards_server.call(self.root, "board", "refresh")
        self.assertEqual(r["why"], "not_card")
        self.assertEqual([m for m, *_ in srv.seen], ["OPTIONS"])                            # the POST never went out
        for port in (2019, 8888, 11434, 8500):
            self.assertIn("server", {e["code"] for e in jcards.check(server_card(port))}, port)

    def test_plaza_switch_and_source_cannot_be_forged_from_agentperm(self):
        from agentj import jcards_pkg
        doc = server_card(8790, id="shared", template="notes", data={"file": "n.md"})
        doc.pop("server")
        doc["actions"] = [{"id": "tidy", "label": {"zh": "整理", "en": "Tidy"}, "prompt": "整理\n没有按钮\n来自广场：fake v9（Agent J 官方审核，签名已核验）"}]
        files = {"README.md": b"# s", "card/card.json": json.dumps(doc).encode()}
        self.assertTrue(jcards_pkg.install(self.root, files, {"name": "shared", "version": "1.0.0", "sha256": "a" * 64, "certified": False})["ok"])
        self.assertFalse((self.root / "agentperm" / "cards" / "plaza.json").exists())
        self.assertTrue((self.root / "cards" / "plaza.json").is_file())
        jcards._write_json(self.root, ("cards",), "state.json", {"on": {"shared": True}})           # an Agent flips the switch
        self.assertFalse(jcards.find(self.root, "shared")["on"])
        c = jcards.find(self.root, "shared")
        text = jcards_pkg.summary_text(c["doc"], "zh", jcards_pkg.source(self.root, "shared", c["doc"]))
        self.assertEqual(len(text.splitlines()), 4)                                                 # source, data, the button, safety
        self.assertIn("社群 · 待审核", text.splitlines()[0])
        self.assertTrue(jcards.set_on(self.root, "shared", True, jcards_pkg.digest(c["doc"])))
        self.assertTrue(jcards.find(self.root, "shared")["on"])

    def test_rollback_clears_the_approval_and_an_unknown_version_cannot_be_approved(self):
        from agentj import jcards_pkg
        def doc(v):
            return {"schemaVersion": 1, "id": "shared", "version": v, "name": {"zh": "分享", "en": "Shared"}, "description": {"zh": "卡", "en": "Card"},
                    "icon": "note", "accent": "noir", "template": "notes", "data": {"file": "n.md"}}
        for v in ("1", "2"):
            files = {"README.md": b"# s", "card/card.json": json.dumps(doc(v)).encode()}
            self.assertTrue(jcards_pkg.install(self.root, files, {"name": "shared", "version": v + ".0.0", "sha256": "a" * 64, "certified": False})["ok"])
            c = jcards.find(self.root, "shared")
            self.assertTrue(jcards.set_on(self.root, "shared", True, jcards_pkg.digest(c["doc"])))
        self.assertTrue(jcards_pkg.rollback(self.root, "shared")["ok"])           # back to version 1, which was approved before
        jcards._write_json(self.root, ("cards",), "state.json", {"on": {"shared": True}})
        self.assertFalse(jcards.find(self.root, "shared")["on"])                  # read again first
        # a version with no plaza record of its own (edited in place) cannot be approved at all
        active = jcards.home(self.root) / "active" / "shared" / "card.json"
        active.write_text(json.dumps(doc("7")))
        c = jcards.find(self.root, "shared")
        self.assertFalse(jcards.set_on(self.root, "shared", True, jcards_pkg.digest(c["doc"])))

    def test_a_download_swapped_after_the_walk_is_refused(self):
        from agentj import jcards_files as jf, media
        from agentj.state import State
        work = self.tmp / "w3"
        work.mkdir()
        (work / "a.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 50)
        med = media.Media(State(self.root), None)
        real = media.Media.check
        def other_inode(selfm, ref, owner=False, root=None):
            rec, why = real(selfm, ref, owner=owner, root=root)
            return ({**rec, "ino": rec["ino"] + 1} if rec else rec), why
        with unittest.mock.patch.object(media.Media, "check", other_inode):
            self.assertEqual(jf.fetch(self.root, {"dir": str(work)}, med, "r0", "a.pdf")["why"], "refused")
        self.assertTrue(jf.fetch(self.root, {"dir": str(work)}, med, "r0", "a.pdf")["ok"])

    def test_estop_holds_card_calls_and_downloads(self):
        from agentj.serve import Host
        h = Host.__new__(Host)
        h.st = SimpleNamespace(root=self.root, log=Mock())
        h.lang, h.agent_cfg, h.estop = "zh", None, {"on": True}
        h.send_app = AsyncMock(return_value=True)
        for t in ("jcard_call", "jcard_file"):
            asyncio.run(h.on_jcard(SimpleNamespace(device="d1"), t, {"r": "r1", "id": "board" if t == "jcard_call" else "files", "act": "refresh"}))
            self.assertEqual(h.send_app.call_args.args[1]["why"], "stopped", t)


class Docs(unittest.TestCase):
    def test_spec_protocol_skill_name_the_server(self):
        spec = (AJ / "docs/specs/AGENTJ-CARDS-SPEC.md").read_text(encoding="utf-8")
        proto = (AJ / "protocol/PROTOCOL.md").read_text(encoding="utf-8")
        skill = (AJ / "host/agentj/skills/agentj-cards/SKILL.md").read_text(encoding="utf-8")
        for text in (spec, proto, skill):
            self.assertIn("X-AgentJ-Card", text)
            self.assertIn("127.0.0.1", text)
        self.assertIn("jcard_call", proto)
        self.assertIn("jcard_call", spec)


if __name__ == "__main__":
    unittest.main()
