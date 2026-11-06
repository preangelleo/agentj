"""#427: several accounts per harness (Claude Code tokens, Codex CODEX_HOME slots), one tap to switch.

Storage (0600 / 0700, no-follow, migration of the login in use), switch semantics (service env file + this process's env;
Codex auth.json write-back of the rotated copy), remove / rename, new-account sign-in through the P258 flows into a NEW slot
(the login in use untouched), quota per account, the phone wire, heartbeat.sock and the CLI. Only stand-in harnesses
(tests/fakep258.py) and obviously fake credentials; never a real login, never the real ~/.claude, ~/.codex or service env.
"""
import _hermetic  # noqa: F401
import asyncio
import base64
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from agentj import accounts, auth_rescue as ar, quota
from agentj.state import State

HERE = pathlib.Path(__file__).resolve().parent
GOOD = "sk-ant-oat01-" + "A" * 95 + "Q"          # fakep258's live-check token
OTHER = "sk-ant-oat01-" + "B" * 95 + "Z"
THIRD = "sk-ant-oat01-" + "C" * 95 + "Y"


def jwt(sub):
    seg = base64.urlsafe_b64encode(json.dumps({"sub": sub, "email": f"{sub}@example.com"}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{seg}.sig"


def codex_auth(who, refresh="r1"):
    return json.dumps({"auth_mode": "chatgpt", "tokens": {"id_token": jwt(who), "access_token": "fake-access-" + who,
                                                          "refresh_token": f"fake-refresh-{who}-{refresh}",
                                                          "account_id": "acct-" + who}}).encode()


def mode(p):
    return stat.S_IMODE(os.stat(p, follow_symlinks=False).st_mode)


class Base(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("TEST_TMP") or None
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-427-", dir=base))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "work").mkdir()
        (self.tmp / "cc").mkdir()
        (self.tmp / "cx").mkdir()
        self.log = self.tmp / "fake.log"
        bins = {}
        for kind in ("claude", "codex"):
            w = self.tmp / kind
            w.write_text(f"#!/bin/sh\nFAKE258_KIND={kind} exec {sys.executable} {HERE / 'fakep258.py'} \"$@\"\n")
            w.chmod(0o700)
            bins[kind] = str(w)
        env = {"AGENTJ_CLAUDE_BIN": bins["claude"], "AGENTJ_CODEX_BIN": bins["codex"], "FAKE258_LOG": str(self.log),
               "HOME": str(self.tmp), "XDG_CONFIG_HOME": str(self.tmp / "config"), "CLAUDE_CONFIG_DIR": str(self.tmp / "cc"),
               "CODEX_HOME": str(self.tmp / "cx"), "AGENTJ_STATE_DIR": str(self.tmp / "st"), "FAKE258_GOOD_TOKEN": GOOD}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FAKE258_SCREENS", "FAKE258_DEVICE",
                  "FAKE258_PROBE", "FAKE258_NOURL", "FAKE258_WIPE", "FAKE258_CODEX_PROBE"):
            os.environ.pop(k, None)
        # the user manager and environment.d are per uid, not per HOME: never read the real ones from a test
        for target in ("agentj.keys_found.manager_env",):
            m = mock.patch(target, return_value={})
            m.start()
            self.addCleanup(m.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        self.root = self.st.root

    def env_file(self) -> pathlib.Path:
        return pathlib.Path(ar.service_env_path())

    def env_text(self):
        p = self.env_file()
        return p.read_text() if p.exists() else ""

    def set_service_token(self, tok):
        p = self.env_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"CLAUDE_CODE_OAUTH_TOKEN={tok}\n")
        p.chmod(0o600)
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = tok

    def slot_dir(self, h):
        return pathlib.Path(self.root) / "accounts" / h

    def everything_text(self):
        out = []
        for p in pathlib.Path(self.root).rglob("*"):
            if p.is_file() and not p.is_symlink() and "accounts" not in p.relative_to(self.root).parts[:1]:
                out.append(p.read_text(errors="replace"))
        return "\n".join(out)


# ------------------------------------------------------------------ ① storage + migration
class Storage(Base):
    def test_claude_token_migration_and_new_slot(self):
        self.set_service_token(GOOD)
        item = accounts.add_claude_token(self.root, "备用", bytearray(OTHER.encode()))
        d = accounts.load(self.root)["harness"]["claude"]
        self.assertEqual([i["name"] for i in d["items"]], ["主号", "备用"])
        main = d["items"][0]
        self.assertEqual((main["method"], d["active"]), ("token", main["id"]))   # what was in use = the first account
        self.assertEqual((self.slot_dir("claude") / f"{main['id']}.token").read_bytes(), GOOD.encode())
        self.assertEqual((self.slot_dir("claude") / f"{item['id']}.token").read_bytes(), OTHER.encode())
        for p in (self.slot_dir("claude") / f"{main['id']}.token", self.slot_dir("claude") / f"{item['id']}.token",
                  pathlib.Path(self.root) / accounts.FILE):
            self.assertEqual(mode(p), 0o600, p)
        self.assertEqual(mode(self.slot_dir("claude")), 0o700)
        self.assertEqual(mode(pathlib.Path(self.root) / "accounts"), 0o700)
        self.assertIn(f"CLAUDE_CODE_OAUTH_TOKEN={GOOD}", self.env_text())       # the account in use is untouched
        self.assertEqual(os.environ["CLAUDE_CODE_OAUTH_TOKEN"], GOOD)
        v = json.dumps(accounts.view(self.root))
        for secret in (GOOD, OTHER, "token\"", str(self.root)):
            if secret != "token\"":
                self.assertNotIn(secret, v)
        self.assertNotIn(GOOD, (pathlib.Path(self.root) / accounts.FILE).read_text())
        self.assertNotIn(OTHER, (pathlib.Path(self.root) / accounts.FILE).read_text())
        self.assertEqual(set(accounts.view(self.root)["claude"]["items"][0]), {"id", "name", "method", "added", "quota"})

    def test_claude_login_migration(self):
        accounts.add_claude_token(self.root, "work", bytearray(OTHER.encode()), lang="en")
        d = accounts.load(self.root)["harness"]["claude"]
        self.assertEqual([(i["name"], i["method"]) for i in d["items"]], [("Main", "login"), ("work", "token")])
        self.assertEqual(d["active"], d["items"][0]["id"])

    def test_no_accounts_is_the_old_behaviour(self):
        self.assertEqual(accounts.view(self.root)["claude"], {"active": None, "items": []})
        self.assertFalse((pathlib.Path(self.root) / accounts.FILE).exists())

    def test_names(self):
        accounts.add_claude_token(self.root, "备用", bytearray(OTHER.encode()))
        for bad, why in (("", "bad_name"), ("x" * 25, "bad_name"), ("a\nb", "bad_name"), ("a\u200bb", "bad_name"),
                         ("\u202eevil", "bad_name"), ("a\u2028b", "bad_name"), ("备用", "name_taken"),
                         ("主号", "name_taken")):
            with self.assertRaises(accounts.AccountError) as c:
                accounts.check_new(self.root, "claude", bad)
            self.assertEqual(c.exception.args[0], why, bad)
        with self.assertRaises(accounts.AccountError) as c:     # the first new one cannot take the migrated name
            accounts.check_new(self.root, "codex", "主号")
        self.assertEqual(c.exception.args[0], "name_taken")

    def test_at_most_eight(self):
        for i in range(7):
            accounts.add_claude_token(self.root, f"a{i}", bytearray(OTHER.encode()))
        self.assertEqual(len(accounts.load(self.root)["harness"]["claude"]["items"]), 8)
        with self.assertRaises(accounts.AccountError) as c:
            accounts.add_claude_token(self.root, "ninth", bytearray(OTHER.encode()))
        self.assertEqual(c.exception.args[0], "full")

    def test_symlinked_slot_folder_refused(self):
        (pathlib.Path(self.root) / "accounts").mkdir(mode=0o700)
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (pathlib.Path(self.root) / "accounts" / "claude").symlink_to(elsewhere)
        with self.assertRaises(accounts.AccountError) as c:
            accounts.add_claude_token(self.root, "备用", bytearray(OTHER.encode()))
        self.assertEqual(c.exception.args[0], "unsafe")
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_broken_registry_reads_as_none(self):
        (pathlib.Path(self.root) / accounts.FILE).write_text("{not json")
        self.assertEqual(accounts.load(self.root)["harness"]["codex"]["items"], [])


# ------------------------------------------------------------------ ② switch / rename / remove
class Switch(Base):
    def setUp(self):
        super().setUp()
        self.set_service_token(GOOD)
        self.backup = accounts.add_claude_token(self.root, "备用", bytearray(OTHER.encode()))
        self.main = accounts.load(self.root)["harness"]["claude"]["items"][0]

    def test_token_to_token_and_back(self):
        res = accounts.switch_sync(self.root, "claude", "备用")
        self.assertTrue(res["ok"], res)
        self.assertIn(f"CLAUDE_CODE_OAUTH_TOKEN={OTHER}", self.env_text())
        self.assertNotIn(GOOD, self.env_text())
        self.assertEqual(mode(self.env_file()), 0o600)
        self.assertEqual(os.environ["CLAUDE_CODE_OAUTH_TOKEN"], OTHER)
        self.assertEqual(accounts.load(self.root)["harness"]["claude"]["active"], self.backup["id"])
        self.assertTrue(accounts.switch_sync(self.root, "claude", self.main["id"])["ok"])   # by id
        self.assertEqual(os.environ["CLAUDE_CODE_OAUTH_TOKEN"], GOOD)
        self.assertTrue(accounts.switch_sync(self.root, "claude", "主号")["same"])
        self.assertNotIn(OTHER, json.dumps(res))

    def test_login_method_removes_the_key(self):
        accounts.rename_sync(self.root, "claude", "主号", "work")
        d = accounts.load(self.root)
        d["harness"]["claude"]["items"].append({"id": "0000beef", "name": "家里", "method": "login", "added": 1, "last_quota": None})
        accounts._save(self.root, d)
        self.assertTrue(accounts.switch_sync(self.root, "claude", "家里")["ok"])
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", self.env_text())
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", os.environ)
        # a token from a source Agent J does not write (environment.d / the user manager) would win over /login
        self.assertTrue(accounts.switch_sync(self.root, "claude", "work")["ok"])
        with mock.patch("agentj.keys_found.manager_env", return_value={"CLAUDE_CODE_OAUTH_TOKEN": THIRD}):
            res = accounts.switch_sync(self.root, "claude", "家里")
        self.assertEqual((res["ok"], res["why"], res["conflict"]), (False, "conflict", ["CLAUDE_CODE_OAUTH_TOKEN"]))
        self.assertIn(f"CLAUDE_CODE_OAUTH_TOKEN={GOOD}", self.env_text())        # nothing changed

    def test_outranking_key_refuses_by_name(self):
        os.environ["ANTHROPIC_API_KEY"] = "fake-key-for-test"
        res = accounts.switch_sync(self.root, "claude", "备用")
        self.assertEqual((res["ok"], res["why"], res["conflict"]), (False, "conflict", ["ANTHROPIC_API_KEY"]))
        self.assertNotIn("fake-key-for-test", json.dumps(res))
        self.assertIn(f"CLAUDE_CODE_OAUTH_TOKEN={GOOD}", self.env_text())
        self.assertEqual(accounts.view(self.root)["conflict"], ["ANTHROPIC_API_KEY"])

    def test_p288_expiry_follows_the_account(self):
        accounts.switch_sync(self.root, "claude", "备用")
        self.assertEqual(ar.read_meta(self.st)["claude"]["minted"], time.strftime("%Y-%m-%d"))

    def test_remove_and_rename(self):
        self.assertEqual(accounts.remove_sync(self.root, "claude", "主号")["why"], "active")
        self.assertEqual(accounts.rename_sync(self.root, "claude", "备用", "主号")["why"], "name_taken")
        self.assertEqual(accounts.rename_sync(self.root, "claude", "备用", "旧卡")["name"], "旧卡")
        slot = self.slot_dir("claude") / f"{self.backup['id']}.token"
        self.assertTrue(slot.exists())
        self.assertTrue(accounts.remove_sync(self.root, "claude", "旧卡")["ok"])
        self.assertFalse(slot.exists())                                          # the credential goes with it
        self.assertEqual([i["name"] for i in accounts.load(self.root)["harness"]["claude"]["items"]], ["主号"])
        self.assertEqual(accounts.remove_sync(self.root, "claude", "nope")["why"], "not_found")

    def test_rescue_renewal_updates_the_active_slot(self):
        accounts.on_rescue_token(self.root, bytearray(THIRD.encode()))
        self.assertEqual((self.slot_dir("claude") / f"{self.main['id']}.token").read_bytes(), THIRD.encode())

    def test_last_quota_saved_on_switch(self):
        (pathlib.Path(self.root) / "claude-statusline").mkdir()
        (pathlib.Path(self.root) / "claude-statusline" / "s1.json").write_text(json.dumps(
            {"session_id": "s1", "week": {"pct": 61, "reset": time.time() + 3 * 86400}}))
        with mock.patch("agentj.claude_statusline.stale", return_value=False):
            accounts.switch_sync(self.root, "claude", "备用")
            v = accounts.view(self.root)["claude"]
        main = next(i for i in v["items"] if i["name"] == "主号")
        self.assertEqual((main["quota"]["pct"], main["quota"]["window"]), (61.0, "week"))
        self.assertIsNone(next(i for i in v["items"] if i["name"] == "备用")["quota"])   # older than the switch → —


class CodexSwitch(Base):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "cx"
        (self.home / "auth.json").write_bytes(codex_auth("alice"))
        (self.home / "auth.json").chmod(0o600)
        iid, slot = accounts.new_codex_slot(self.root)
        (slot / "auth.json").write_bytes(codex_auth("bob"))
        self.bob = accounts.commit_codex(self.root, iid, "备用")
        self.alice = accounts.load(self.root)["harness"]["codex"]["items"][0]

    def test_migration_and_swap_with_write_back(self):
        self.assertEqual((self.alice["name"], self.alice["method"]), ("主号", "device"))
        self.assertEqual((self.slot_dir("codex") / self.alice["id"] / "auth.json").read_bytes(), codex_auth("alice"))
        (self.home / "auth.json").write_bytes(codex_auth("alice", "r2"))        # Codex rotated its refresh token meanwhile
        self.assertTrue(accounts.switch_sync(self.root, "codex", "备用")["ok"])
        self.assertEqual((self.home / "auth.json").read_bytes(), codex_auth("bob"))
        self.assertEqual(mode(self.home / "auth.json"), 0o600)
        self.assertEqual((self.slot_dir("codex") / self.alice["id"] / "auth.json").read_bytes(), codex_auth("alice", "r2"))
        self.assertEqual(mode(self.slot_dir("codex") / self.alice["id"]), 0o700)
        (self.home / "auth.json").write_bytes(codex_auth("bob", "r9"))
        self.assertTrue(accounts.switch_sync(self.root, "codex", "主号")["ok"])
        self.assertEqual((self.home / "auth.json").read_bytes(), codex_auth("alice", "r2"))
        self.assertEqual((self.slot_dir("codex") / self.bob["id"] / "auth.json").read_bytes(), codex_auth("bob", "r9"))

    def test_write_back_only_into_the_outgoing_slot(self):
        """F2: a live auth.json that claims ANOTHER registered account (unverified JWT claims) never lands in its slot."""
        accounts.switch_sync(self.root, "codex", "备用")                      # active = bob
        (self.home / "auth.json").write_bytes(codex_auth("alice", "forged"))
        self.assertEqual(accounts.switch_sync(self.root, "codex", "主号")["why"], "unregistered_login")
        self.assertEqual((self.slot_dir("codex") / self.alice["id"] / "auth.json").read_bytes(), codex_auth("alice"))
        self.assertEqual((self.home / "auth.json").read_bytes(), codex_auth("alice", "forged"))
        (self.home / "auth.json").write_bytes(b'{"no": "identity"}')          # fp None while bob's slot has one
        self.assertEqual(accounts.switch_sync(self.root, "codex", "主号")["why"], "unregistered_login")
        self.assertEqual((self.slot_dir("codex") / self.bob["id"] / "auth.json").read_bytes(), codex_auth("bob"))

    def test_fp_none_both_sides_writes_back(self):
        d = accounts.load(self.root)
        for i in d["harness"]["codex"]["items"]:
            i.pop("fp", None)
        accounts._save(self.root, d)
        (self.home / "auth.json").write_bytes(b'{"opaque": 1}')
        self.assertTrue(accounts.switch_sync(self.root, "codex", "备用")["ok"])
        self.assertEqual((self.slot_dir("codex") / self.alice["id"] / "auth.json").read_bytes(), b'{"opaque": 1}')

    def test_new_slot_is_exclusive_and_pins_the_file_store(self):
        ids = iter([self.bob["id"], self.alice["id"], "0badf00d", "0000cafe"])
        (self.slot_dir("codex") / "0badf00d").mkdir()                          # a stray folder: never reused
        with mock.patch("agentj.accounts.secrets.token_hex", side_effect=lambda n: next(ids, "feedf00d")):
            iid, slot = accounts.new_codex_slot(self.root)
        self.assertEqual(iid, "0000cafe")
        self.assertEqual((slot / "config.toml").read_text(), 'cli_auth_credentials_store = "file"\n')
        self.assertEqual(mode(slot / "config.toml"), 0o600)
        self.assertEqual(accounts.codex_store(slot), "file")

    def test_remove_keeps_the_row_when_the_slot_stays(self):
        with mock.patch("agentj.accounts.discard_slot"):
            self.assertEqual(accounts.remove_sync(self.root, "codex", "备用")["why"], "io")
        self.assertIn("备用", [i["name"] for i in accounts.load(self.root)["harness"]["codex"]["items"]])
        self.assertTrue(accounts.remove_sync(self.root, "codex", "备用")["ok"])

    def test_unregistered_login_never_overwritten(self):
        (self.home / "auth.json").write_bytes(codex_auth("carol"))
        self.assertEqual(accounts.switch_sync(self.root, "codex", "备用")["why"], "unregistered_login")
        self.assertEqual((self.home / "auth.json").read_bytes(), codex_auth("carol"))

    def test_same_account_twice_refused(self):
        iid, slot = accounts.new_codex_slot(self.root)
        (slot / "auth.json").write_bytes(codex_auth("bob", "r5"))
        with self.assertRaises(accounts.AccountError) as c:
            accounts.commit_codex(self.root, iid, "又一个")
        self.assertEqual(c.exception.args[0], "same_account")
        self.assertFalse(slot.exists())

    def test_keyring_store_unsupported(self):
        (self.home / "config.toml").write_text('cli_auth_credentials_store = "keyring"\n')
        self.assertEqual(accounts.switch_sync(self.root, "codex", "备用")["why"], "unsupported_store")
        with self.assertRaises(accounts.AccountError) as c:
            accounts.check_new(self.root, "codex", "x")
        self.assertEqual(c.exception.args[0], "unsupported_store")

    def test_symlinked_auth_refused(self):
        (self.home / "auth.json").unlink()
        (self.home / "auth.json").symlink_to(self.tmp / "work" / "x.json")
        with self.assertRaises(accounts.AccountError):
            accounts.switch_sync(self.root, "codex", "备用")
        self.assertFalse((self.tmp / "work" / "x.json").exists())

    def test_remove_deletes_the_slot_folder(self):
        (self.slot_dir("codex") / self.bob["id"] / "log").mkdir()
        (self.slot_dir("codex") / self.bob["id"] / "log" / "codex.log").write_text("x")
        self.assertTrue(accounts.remove_sync(self.root, "codex", "备用")["ok"])
        self.assertFalse((self.slot_dir("codex") / self.bob["id"]).exists())
        self.assertTrue((self.home / "auth.json").exists())


# ------------------------------------------------------------------ ③ quota per account
class QuotaPerAccount(Base):
    def test_codex_reading_before_switch_is_not_the_new_accounts(self):
        acc = {"codex": {"active": "bbbbbbbb", "switched": 2000,
                         "items": [{"id": "aaaaaaaa", "name": "主号", "last_quota": {"pct": 97.0, "window": "week", "at": 1999}},
                                   {"id": "bbbbbbbb", "name": "备用", "last_quota": None}]}}
        old = {"harness": "codex", "status": "ok", "used_pct": 97.0, "reset_at": 9e9, "source_at": 1500,
               "h5": {"used_pct": 50.0, "reset_at": 9e9, "source_at": 1500}}
        out = quota.since_switch(old, acc)
        self.assertEqual((out["status"], out["why"]), ("unknown", "since_switch"))
        self.assertNotIn("h5", out)
        fresh = dict(old, source_at=2500, used_pct=3.0)
        self.assertEqual(quota.since_switch(fresh, acc)["status"], "ok")
        rows = quota.account_rows(acc, "codex", {"status": "ok", "used_pct": 3.0, "source_at": 2500})
        self.assertEqual([(r["name"], r["active"], r["quota"] and r["quota"]["pct"]) for r in rows],
                         [("主号", False, 97.0), ("备用", True, 3.0)])

    def test_render_one_line_per_account_and_dash(self):
        self.set_service_token(GOOD)
        accounts.add_claude_token(self.root, "备用", bytearray(OTHER.encode()))
        d = accounts.load(self.root)
        d["harness"]["claude"]["items"][1]["last_quota"] = {"pct": 40, "window": "week", "at": int(time.time()) - 60}
        accounts._save(self.root, d)
        rep = quota.report(self.root, env={"HOME": str(self.tmp)})
        text = quota.render(rep, "zh")
        self.assertIn("· 主号（当前）: —", text)
        self.assertRegex(text, r"· 备用: 上次读到 40%（\d\d-\d\d \d\d:\d\d）")
        claude = next(a for a in rep["harnesses"] if a["harness"] == "claude")
        self.assertEqual([r["name"] for r in claude["accounts"]], ["主号", "备用"])
        self.assertNotIn("accounts", next(a for a in rep["harnesses"] if a["harness"] == "codex"))
        self.assertIn("· Main (in use): —", quota.render(rep, "en").replace("主号", "Main"))


# ------------------------------------------------------------------ ④ new accounts through the login rescue
class _Elev:
    def __init__(self, value):
        self.value, self.cards = value, []

    async def request(self, card):
        self.cards.append({k: v for k, v in card.items() if k != "sink"})
        return await asyncio.to_thread(card["sink"], bytearray(self.value))


class _Agent:
    kind = "claude"

    def __init__(self, kind="claude"):
        self.kind, self.restarts = kind, []

    def owns_harness(self):
        return True

    def request_restart(self, why):
        self.restarts.append(why)
        return "next_turn"


class _Host:
    def __init__(self, st, work, value=b"GOODCODE"):
        self.st, self.lang, self.agent_cfg = st, "zh", {"dir": str(work)}
        self.preferences = {}  # Real Host always has preferences; default mode keeps the external-turn refusal.
        self.agent = None
        self.notices, self.asks, self.allow = [], [], True
        self.elevate = _Elev(value)
        self.tasks, self.sent = [], []
        self.auth_rescue = ar.Rescue(self)

    def agent_notice(self, t):
        self.notices.append(t)

    async def ask(self, tool, tool_input, batch=True, require_passkey=False, **kw):
        self.asks.append((tool, tool_input.get("summary", "")))
        return {"behavior": "allow" if self.allow else "deny"}

    def _bg(self, coro, what="bg"):
        t = asyncio.ensure_future(coro)
        self.tasks.append(t)
        return t

    def _paired_live(self, s):
        return True


class Rescue(Base, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Base.setUp(self)
        self.host = _Host(self.st, self.tmp / "work")
        self.r = self.host.auth_rescue
        p = mock.patch("agentj.harness_setup.enabled", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    async def drain(self):
        while self.host.tasks:
            await self.host.tasks.pop(0)

    async def test_codex_new_account_into_its_own_slot(self):
        (self.tmp / "cx" / "auth.json").write_bytes(codex_auth("alice"))
        (self.tmp / "cx" / "auth.json").chmod(0o600)
        self.host.agent = _Agent("codex")
        self.assertEqual(await self.r.start("codex", "device", account={"name": "备用"}), {"ok": True})
        self.assertIn("登记新的 Codex 账号「备用」", self.host.asks[0][1])
        await self.drain()
        self.assertEqual(self.r.view()["flow"]["stage"], "done", self.r.view()["flow"])
        self.assertEqual(self.r.view()["flow"]["account"], "备用")
        self.assertEqual((self.tmp / "cx" / "auth.json").read_bytes(), codex_auth("alice"))   # never overwritten
        d = accounts.load(self.root)["harness"]["codex"]
        self.assertEqual([i["name"] for i in d["items"]], ["主号", "备用"])
        self.assertEqual((self.slot_dir("codex") / d["items"][1]["id"] / "auth.json").read_text(), '{"new": true}')
        self.assertEqual(self.host.agent.restarts, [])                         # nothing in use changed
        self.assertIn("已登记 Codex 账号「备用」", self.host.notices[-1])
        self.assertIn('"op": "add"', (pathlib.Path(self.root) / "host.log").read_text())

    async def test_codex_failed_login_leaves_no_slot(self):
        os.environ["FAKE258_DEVICE"] = "fail"
        await self.r.start("codex", "device", account={"name": "备用"})
        await self.drain()
        self.assertEqual(self.r.view()["flow"]["stage"], "failed")
        self.assertFalse(self.slot_dir("codex").exists() and any(self.slot_dir("codex").iterdir()))
        self.assertEqual(accounts.load(self.root)["harness"]["codex"]["items"], [])

    async def test_claude_setup_token_into_a_new_slot(self):
        self.set_service_token(OTHER)                       # the account in use (its token is not the fake's good one)
        self.host.agent = _Agent("claude")
        await self.r.start("claude", "setup_token", account={"name": "备用"})
        await self.drain()
        self.assertEqual(self.r.view()["flow"]["stage"], "done", self.r.view()["flow"])
        self.assertIn(f"CLAUDE_CODE_OAUTH_TOKEN={OTHER}", self.env_text())     # service env file untouched
        self.assertEqual(os.environ["CLAUDE_CODE_OAUTH_TOKEN"], OTHER)
        d = accounts.load(self.root)["harness"]["claude"]
        new = d["items"][1]
        self.assertEqual((self.slot_dir("claude") / f"{new['id']}.token").read_bytes(), GOOD.encode())
        self.assertEqual(self.host.agent.restarts, [])
        self.assertIn("新账号「备用」的凭据槽", self.host.elevate.cards[0]["dest"])
        logs = (pathlib.Path(self.root) / "host.log").read_text()
        for secret in (GOOD, OTHER, "GOODCODE"):
            self.assertNotIn(secret, logs)
            self.assertNotIn(secret, json.dumps(self.r.view()))
            for n in self.host.notices:
                self.assertNotIn(secret, n)

    async def test_claude_paste_into_a_new_slot(self):
        self.host.elevate = _Elev(GOOD.encode())
        await self.r.start("claude", "paste", account={"name": "备用"})
        await self.drain()
        self.assertEqual([i["name"] for i in accounts.load(self.root)["harness"]["claude"]["items"]], ["主号", "备用"])
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", self.env_text())

    async def test_refusals_before_any_card(self):
        self.assertEqual((await self.r.start("claude", "login", account={"name": "x"}))["why"], "unsupported")
        self.assertEqual((await self.r.start("kimi", "device", account={"name": "x"}))["why"], "unsupported")
        self.assertEqual((await self.r.start("claude", "setup_token", account={"name": ""}))["why"], "bad_name")
        self.assertEqual((await self.r.start("claude", "setup_token", account={"name": "主号"}))["why"], "name_taken")
        self.assertEqual(self.host.asks, [])

    async def test_plain_rescue_unchanged(self):
        """No account → P258 exactly as before: the renewal lands in the service env file (and, with accounts, the active slot)."""
        out = {}
        res = await asyncio.to_thread(self.r._store_token, bytearray(GOOD.encode()), out)
        self.assertEqual(res["result"], "saved")
        self.assertIn(f"CLAUDE_CODE_OAUTH_TOKEN={GOOD}", self.env_text())
        self.assertFalse((pathlib.Path(self.root) / accounts.FILE).exists())


# ------------------------------------------------------------------ ⑤ serve: phone wire, heartbeat.sock, restart
class _Sess:
    device, cid, state, pub = "dev1", "c1", "ready", "pk"


class Wire(Base, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Base.setUp(self)
        self.host = _Host(self.st, self.tmp / "work")
        self.set_service_token(GOOD)
        self.backup = accounts.add_claude_token(self.root, "备用", bytearray(OTHER.encode()))

    def test_types_registered(self):
        from agentj import serve
        for t in ("acct_get", "acct_use", "acct_rename", "acct_del"):
            self.assertIn(t, serve.P33_ONLY)
        self.assertTrue(hasattr(serve.Host, "on_acct"))
        src = (pathlib.Path(serve.__file__)).read_text()
        self.assertIn('if t.startswith("acct_"):', src)
        self.assertIn('{"t": "acct_res", "r": r, **res}', src)

    async def test_real_on_acct_answers_the_asking_session_only(self):
        from agentj import serve
        host = self.host
        s = _Sess()
        host.sessions = {s.cid: s}
        host.st.is_allowed = lambda pub: pub == "pk"
        sent = []

        async def send_app(sess, msg):
            sent.append((sess, msg))
        host.send_app = send_app
        serve.Host.on_acct(host, s, "acct_get", {"r": "r1"})
        serve.Host.on_acct(host, s, "acct_nope", {"r": "r2"})          # unknown type: nothing
        serve.Host.on_acct(host, s, "acct_get", {"r": 5})              # bad rid: nothing
        while host.tasks:
            await host.tasks.pop(0)
        self.assertEqual(len(sent), 1)
        self.assertIs(sent[0][0], s)
        self.assertEqual((sent[0][1]["t"], sent[0][1]["r"], sent[0][1]["ok"]), ("acct_res", "r1", True))
        s.state = "closing"                                            # a session that went away gets nothing
        serve.Host.on_acct(host, s, "acct_get", {"r": "r3"})
        while host.tasks:
            await host.tasks.pop(0)
        self.assertEqual(len(sent), 1)

    async def test_get_use_restarts_the_main_agent(self):
        self.host.agent = _Agent("claude")
        s = _Sess()
        got = await accounts.phone(self.host, s, "acct_get", {})
        self.assertEqual(set(got), {"ok", "view"})
        self.assertEqual(set(got["view"]), {"claude", "codex", "conflict", "primary"})
        self.assertEqual(set(got["view"]["primary"]), {"claude", "codex"})
        res = await accounts.phone(self.host, s, "acct_use", {"harness": "claude", "id": self.backup["id"]})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["restart"], "next_turn")
        self.assertEqual(self.host.agent.restarts, ["auth_rescue"])               # P320: before the next message
        self.assertEqual(res["view"]["claude"]["active"], self.backup["id"])
        self.assertEqual(self.host.asks, [])                                       # switching needs no card
        line = [json.loads(x) for x in (pathlib.Path(self.root) / "host.log").read_text().splitlines()
                if '"account"' in x][-1]
        self.assertEqual((line["harness"], line["op"], line["device"]), ("claude", "use", "dev1"))
        self.assertEqual(line["name_hash"], accounts.name_hash("备用"))
        self.assertNotIn(OTHER, json.dumps(res))

    async def test_delete_asks_the_owner(self):
        s = _Sess()
        self.host.allow = False
        res = await accounts.phone(self.host, s, "acct_del", {"harness": "claude", "id": self.backup["id"]})
        self.assertEqual((res["ok"], res["why"]), (False, "denied"))
        self.assertIn("删除 Claude Code 账号「备用」", self.host.asks[0][1])
        self.assertTrue((self.slot_dir("claude") / f"{self.backup['id']}.token").exists())
        self.host.allow = True
        res = await accounts.phone(self.host, s, "acct_del", {"harness": "claude", "id": self.backup["id"]})
        self.assertTrue(res["ok"])
        self.assertFalse((self.slot_dir("claude") / f"{self.backup['id']}.token").exists())

    async def test_rename_and_bad_input(self):
        s = _Sess()
        res = await accounts.phone(self.host, s, "acct_rename", {"harness": "claude", "id": self.backup["id"], "name": "旧卡"})
        self.assertEqual(next(i for i in res["view"]["claude"]["items"] if i["id"] == self.backup["id"])["name"], "旧卡")
        for obj in ({"harness": "kimi", "id": self.backup["id"]}, {"harness": "claude", "id": "../x"}, {"harness": "claude"}):
            self.assertEqual((await accounts.phone(self.host, s, "acct_use", obj))["why"], "unsupported")

    async def test_heartbeat_sock_use_in_untrusted_turn_remove_refused(self):
        from agentj import heartbeat
        hb = heartbeat.Heartbeat.__new__(heartbeat.Heartbeat)
        hb.host = self.host
        with mock.patch("agentj.provenance.untrusted_turn", return_value=True), \
                mock.patch("agentj.provenance.delegated_turn", return_value=False):
            res = await hb.handle({"op": "account_use", "harness": "claude", "ref": "备用"})
            self.assertTrue(res["ok"], res)
            res = await hb.handle({"op": "account_remove", "harness": "claude", "ref": "主号"})
            self.assertEqual(res["why"], "untrusted_turn")
            lst = await hb.handle({"op": "account_list"})
            again = await hb.handle({"op": "account_use", "harness": "claude", "ref": "主号"})
            await hb.handle({"op": "rm -rf ~ secret-ish op", "harness": "claude"})
        self.assertEqual(lst["view"]["claude"]["active"], self.backup["id"])
        self.assertEqual((again["ok"], again["why"]), (False, "rate"))                       # F5: once per minute
        logs = [json.loads(x) for x in (pathlib.Path(self.root) / "host.log").read_text().splitlines()]
        refused = [x["op"] for x in logs if x["ev"] == "heartbeat_refused"]
        self.assertEqual(refused, ["account_remove", "other"])                                # F7: names only
        use = [x for x in logs if x["ev"] == "account" and x.get("op") == "use"][-1]
        self.assertEqual(use.get("status"), "untrusted")


# ------------------------------------------------------------------ ⑥ CLI
class Cli(Base):
    def run_cli(self, *args, stdin=subprocess.DEVNULL):
        env = {k: v for k, v in os.environ.items()}
        env["AGENTJ_HEARTBEAT_SOCK"] = str(self.tmp / "no.sock")
        return subprocess.run([sys.executable, "-c", "import sys; from agentj.cli import main; main(sys.argv[1:])", *args],
                              cwd=str(HERE.parent), env=env, capture_output=True, text=True, timeout=120, stdin=stdin)

    def test_offline_list_use_remove(self):
        self.set_service_token(GOOD)
        accounts.add_claude_token(self.root, "备用", bytearray(OTHER.encode()))
        r = self.run_cli("account", "list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        v = json.loads(r.stdout)
        self.assertEqual([i["name"] for i in v["claude"]["items"]], ["主号", "备用"])
        r = self.run_cli("account", "use", "claude", "备用")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Claude Code → 「备用」", r.stdout)
        self.assertIn(f"CLAUDE_CODE_OAUTH_TOKEN={OTHER}", self.env_text())
        r = self.run_cli("account", "remove", "claude", "备用")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("正在用", r.stderr)
        for out in (r.stdout, r.stderr):
            self.assertNotIn(OTHER, out)
            self.assertNotIn(GOOD, out)

    def test_unanswered_card_never_falls_back_offline(self):
        """F1: serve took the remove but the owner never answered → serve_timeout, non-zero exit, the slot stays."""
        import socket
        import threading
        self.set_service_token(GOOD)
        item = accounts.add_claude_token(self.root, "备用", bytearray(OTHER.encode()))
        path = str(self.tmp / "hb.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        held = []
        threading.Thread(target=lambda: held.append(srv.accept()), daemon=True).start()
        self.addCleanup(srv.close)
        with mock.patch.dict(os.environ, {"AGENTJ_HEARTBEAT_SOCK": path}), mock.patch.object(accounts, "REMOVE_WAIT", 1.0):
            res = accounts._call({"op": "account_remove", "harness": "claude", "ref": "备用"})
        self.assertEqual(res, {"ok": False, "why": "serve_timeout"})
        self.assertTrue((self.slot_dir("claude") / f"{item['id']}.token").exists())
        dead = str(self.tmp / "dead.sock")                                    # a socket file nobody listens on → offline
        d = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        d.bind(dead)
        d.close()
        with mock.patch.dict(os.environ, {"AGENTJ_HEARTBEAT_SOCK": dead}), \
                mock.patch("agentj.heartbeat.sock_paths", return_value=[dead]):
            self.assertEqual(accounts._call({"op": "account_list"})["serve"], False)
        self.assertGreater(accounts.REMOVE_WAIT, 120 + 30)

    def test_add_refused_below_an_agent_or_serve(self):
        with mock.patch("sys.stdin") as stdin, mock.patch("agentj.ctl_auth.refusal", return_value=None), \
                mock.patch("agentj.ctl_auth.ancestors", return_value=[os.getpid(), 4242]), \
                mock.patch("agentj.ctl_auth._environ", side_effect=lambda pid: {"CLAUDECODE"} if pid == 4242 else set()):
            stdin.isatty.return_value = True
            self.assertEqual(accounts._terminal_only(), "agent")                # F6: `env -i` cannot hide the parent

    def test_add_needs_the_owner_terminal(self):
        r = self.run_cli("account", "add", "claude", "备用")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no_tty", r.stderr)
        self.assertFalse((pathlib.Path(self.root) / accounts.FILE).exists())

    def test_root_parser_has_one_account_command(self):
        src = (HERE.parent / "agentj").glob("*.py")
        hits = [p.name for p in src if 'add_parser("account"' in p.read_text()]
        self.assertEqual(hits, ["accounts.py"])
        r = self.run_cli("account", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        for w in ("list", "add", "use", "rename", "remove"):
            self.assertIn(w, r.stdout)


class Skill(unittest.TestCase):
    def test_switch_harness_skill_names_the_command(self):
        skill = (HERE.parent / "agentj/skills/agentj-switch-harness/SKILL.md").read_text()
        self.assertIn("agentj account use claude 备用", skill)
        self.assertIn("换 Claude 备用账号", skill.split("---")[1])               # the trigger words


if __name__ == "__main__":
    unittest.main()
