"""P238: the phone's 「/」 list always has `/switch-harness` (ADR-P155) for a paired owner.

Root cause (0.18.6 – 0.20.2): serve's `menu_get` replaced the whole list with the owner commands set through `agentj menu add`
(preferences `menu.items`) — an owner with one own command (/good-morning) lost `/switch-harness` and `/goal`. menu.json and
the defaults always kept them. Here the REAL `menu_get` handler answers a paired p33 phone for every combination:
session mode (shared / independent) × harness (Claude Code / Codex / OpenCode) × own commands (none / menu.json /
`agentj menu add` / both) × language. `/switch-harness` is listed exactly once, in the main-Agent group, with its sentence;
`/goal` stays; the owner's own commands come first in their order; a removed (disabled) row is not shown.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import menu, preferences, serve, update  # noqa: E402
from agentj.state import State  # noqa: E402

MODES = ("shared", "independent")
KINDS = ("claude", "codex", "opencode")
OWN = ("none", "file", "config", "both")
GM = {"id": "good-morning", "cmd": "/good-morning", "desc": "早安例行"}


def _host(d):
    st = State(pathlib.Path(d) / "s")
    st.init(relay="ws://127.0.0.1:1")
    host = serve.Host(st, events="jsonl", read_stdin=False)
    host.test_pub = os.urandom(32)
    host.test_did = st.add_device(host.test_pub, "手机", None)       # a paired phone (the allowlist is checked first)
    sent = []

    async def send_app(s, obj):
        sent.append(obj)
        return True
    host.send_app = send_app
    return host, sent


def _ask(host, sent, mode, kind, own, lang, work):
    host.lang = lang
    host.agent_cfg = {"kind": kind, "dir": str(work), "session_mode": mode}
    host.agent = types.SimpleNamespace(kind=kind, init={"skills": ["review", "agentj-switch-harness"]} if kind == "claude" else {})
    mj = work / ".agentj" / "menu.json"
    if own in ("file", "both"):
        mj.parent.mkdir(exist_ok=True)
        mj.write_text(json.dumps({"version": 1, "items": [{"cmd": "/deploy", "desc": "上线"}]}))
    elif mj.exists():
        mj.unlink()
    prefs = preferences.defaults()
    if own in ("config", "both"):
        preferences.put(prefs, "menu.items", [GM, {"id": "old", "cmd": "/old", "desc": "旧", "disabled": True}])
    host.preferences = prefs
    s = serve.Session(cid=7, state="ready", device=host.test_did, name="手机", pub=host.test_pub)
    s.p33 = True
    host.sessions[7] = s
    sent.clear()
    asyncio.run(host._app(s, {"t": "menu_get", "r": "r1"}))
    out = [m for m in sent if m.get("t") == "menu"]
    assert len(out) == 1, sent
    return out[0]


class EveryCombination(unittest.TestCase):
    def test_switch_harness_always_listed(self):
        with tempfile.TemporaryDirectory() as d:
            host, sent = _host(d)
            work = pathlib.Path(d) / "work"
            work.mkdir()
            n = 0
            for mode in MODES:
                for kind in KINDS:
                    for own in OWN:
                        for lang in ("zh", "en"):
                            with self.subTest(mode=mode, kind=kind, own=own, lang=lang):
                                m = _ask(host, sent, mode, kind, own, lang, work)
                                cmds = [i["cmd"] for i in m["items"]]
                                self.assertEqual(cmds.count("/switch-harness"), 1, cmds)
                                sw = next(i for i in m["items"] if i["cmd"] == "/switch-harness")
                                self.assertEqual(sw["group"], menu.AGENT_GROUP[lang])
                                self.assertIn("Codex", sw["desc"])
                                self.assertIn("/goal", cmds)
                                self.assertNotIn("/old", cmds, "a removed command stays removed")
                                want_src = {"none": "default", "file": "file", "config": "config", "both": "config"}[own]
                                self.assertEqual(m["source"], want_src)
                                if own in ("config", "both"):
                                    self.assertEqual(cmds[0], "/good-morning", "the owner's own commands come first")
                                if own == "file":
                                    self.assertEqual(cmds, ["/deploy", "/goal", "/switch-harness", "/feedback-to-agentj-father"])
                                if own == "none":
                                    self.assertIn("/add-friend", cmds)
                                skills = [x["cmd"] for x in m["skills"]]
                                self.assertNotIn("/switch-harness", skills)
                                self.assertEqual(skills, ["/review", "/agentj-switch-harness"] if kind == "claude" else [])
                                self.assertIn("upgrade", m)
                                n += 1
            self.assertEqual(n, len(MODES) * len(KINDS) * len(OWN) * 2)

    def test_owner_list_with_its_own_switch_harness_keeps_the_owners_words(self):
        rows = [{"id": "sw", "cmd": "/switch-harness", "desc": "我自己的说明", "group": "常用"}, GM]
        out = menu.for_phone(None, [], "zh", rows)
        self.assertEqual([i["cmd"] for i in out["items"]], ["/switch-harness", "/good-morning", "/goal", "/feedback-to-agentj-father"])
        self.assertEqual(out["items"][0]["desc"], "我自己的说明")

    def test_all_rows_removed_falls_back_to_the_defaults(self):
        out = menu.for_phone(None, [], "en", [{"id": "x", "disabled": True}])
        self.assertEqual(out["source"], "default")
        self.assertIn("/switch-harness", [i["cmd"] for i in out["items"]])

    def test_a_full_owner_list_still_gets_switch_harness(self):
        rows = [{"id": f"c{i}", "cmd": f"/c{i}", "desc": "x"} for i in range(menu.MAX_ITEMS)]
        cmds = [i["cmd"] for i in menu.for_phone(None, [], "zh", rows)["items"]]
        self.assertEqual(cmds[-2:], ["/switch-harness", "/feedback-to-agentj-father"])
        self.assertNotIn("/goal", cmds)

    def test_serve_uses_the_single_answer(self):
        src = (pathlib.Path(serve.__file__)).read_text()
        self.assertIn("menu.for_phone", src)
        self.assertNotIn('res.update(source="config"', src)


class UpgradeRow(unittest.TestCase):
    """RESUME (Leo 23:31, IMG_2421): 「⬆ 升级 Agent J · 0.20.1a1 → 0.20.2a1 · 点击即授权升级」 while 0.20.2a1 was already installed.
    serve reported its own running __version__; between `apply` installing the new copy and the recovery worker restarting an
    idle serve, the phone offered the same upgrade again. The menu answer now also carries the version on disk."""

    def _menu(self, disk, latest):
        with tempfile.TemporaryDirectory() as d:
            host, sent = _host(d)
            work = pathlib.Path(d) / "work"
            work.mkdir()
            rec = host.st.root / "update.json"
            rec.write_text(json.dumps({"latest": latest}))
            orig = update.installed_version
            update.installed_version = lambda: disk
            try:
                return _ask(host, sent, "shared", "claude", "config", "zh", work)["upgrade"]
            finally:
                update.installed_version = orig

    def test_installed_but_not_restarted(self):
        up = self._menu("9.9.9a1", "9.9.9a1")
        self.assertEqual(up, {"current": update.__version__, "latest": "9.9.9a1", "installed": "9.9.9a1"})

    def test_running_is_what_is_on_disk(self):
        up = self._menu(update.__version__, "9.9.9a1")
        self.assertEqual(up, {"current": update.__version__, "latest": "9.9.9a1"})
        self.assertNotIn("installed", self._menu(None, None))

    def test_installed_version_reads_the_file_on_disk(self):
        self.assertEqual(update.installed_version(), update.__version__)


if __name__ == "__main__":
    unittest.main()
