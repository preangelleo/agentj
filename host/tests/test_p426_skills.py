"""P426 #181-3 (PROTOCOL §39.1): skill health — the usage ledger counts exact events only (phone `/skill`, Claude's Skill tool,
a desktop `/skill`), adds up across threads and processes, folds a double report into one use, shows 「—」 (no `use`) when
nothing was seen and never calls a skill long unused on a harness that reports no skill runs; stale = seen ≥ N days and no
use within N; archive moves the folder (restorable), refuses Agent J's own / official skills, restore refuses a taken place;
the 技能 page tail carries it. Security review B1: a link at any level (the harness folder, the skills root, the archive, the
skill) or a place inside / around the state directory refuses, also when the link is swapped in after the checks (TOCTOU), and
the index the Agent can write is ignored (serve's own copy in the state directory counts); S6: the phone's 「归档」/「恢复」 are
signed controls (unsigned / wrongly signed → nothing moves, controls.log); N1: elevate.sock always records by=agent.
#181-4 (suggestions) is test_p426_suggest.py."""
import _hermetic  # noqa: F401
import asyncio
import json
import multiprocessing
import os
import pathlib
import shutil
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agentj import skill_usage, skills_cli, workflows
from agentj.state import State

DAY = 86400
NOW = 1_800_000_000


def _skill(root: pathlib.Path, name: str, desc: str = "test skill") -> pathlib.Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nDo the thing.\n", encoding="utf-8")
    return d


def _bump(state_root: str, n: int, at: int) -> None:     # a separate process adding n uses
    st = State(pathlib.Path(state_root))
    for i in range(n):
        skill_usage.note(st, "weekly", "phone", now=at + i * 60)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p426-")
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name)
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")}
        p = patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "DSH_HOME"):
            os.environ.pop(k, None)
        (self.home / "state").mkdir(mode=0o700)
        self.st = State(self.home / "state")
        self.skills = self.home / ".claude" / "skills"
        skill_usage._NAMES.clear()


class UsageLedger(Base):
    def test_counts_add_up_and_a_double_report_is_one_use(self):
        self.assertTrue(skill_usage.note(self.st, "weekly", "phone", now=NOW))
        self.assertFalse(skill_usage.note(self.st, "weekly", "claude", now=NOW + 5), "the session's own echo of a phone /x")
        self.assertTrue(skill_usage.note(self.st, "weekly", "claude", now=NOW + 120))
        r = skill_usage.load(self.st)["skills"]["weekly"]
        self.assertEqual((r["n"], r["last"], r["src"]), (2, NOW + 120, {"phone": 1, "claude": 1}))
        self.assertFalse(skill_usage.note(self.st, "../etc", "phone"), "names only")
        self.assertFalse(skill_usage.note(self.st, "weekly", "codex"), "sources Agent J sees exactly")
        raw = (self.home / "state" / "skill-usage.json").read_text()
        self.assertEqual(set(json.loads(raw)), {"v", "skills", "seen"}, "names, counts and times — nothing else")
        self.assertEqual(oct((self.home / "state" / "skill-usage.json").stat().st_mode & 0o777), "0o600")

    def test_concurrent_writers_lose_nothing(self):
        def run(i):
            for j in range(10):
                skill_usage.note(self.st, f"s{i}", "claude", now=NOW + j * 60)
        ts = [threading.Thread(target=run, args=(i,)) for i in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        use = skill_usage.load(self.st)["skills"]
        self.assertEqual({k: v["n"] for k, v in use.items()}, {f"s{i}": 10 for i in range(6)})
        ctx = multiprocessing.get_context("fork")
        ps = [ctx.Process(target=_bump, args=(str(self.st.root), 5, NOW + k * DAY)) for k in range(3)]
        [p.start() for p in ps]
        [p.join(30) for p in ps]
        self.assertEqual(skill_usage.load(self.st)["skills"]["weekly"]["n"], 15, "three processes, nothing lost")
        json.loads((self.home / "state" / "skill-usage.json").read_text())

    def test_no_data_shows_dash_and_stale_needs_a_tracked_harness(self):
        skills = [{"name": "weekly", "scope": "global"}, {"name": "fresh", "scope": "global"},
                  {"name": "agentj-commands", "scope": "bundled"}, {"name": "mine", "scope": "local", "folder": "w"}]
        info = skill_usage.annotate(self.st, [dict(s) for s in skills], "claude", {}, NOW)    # first listing: seen = NOW
        self.assertEqual(info, {"tracked": True, "days": 30})
        skill_usage.note(self.st, "fresh", "claude", now=NOW + 29 * DAY)
        out = [dict(s) for s in skills]
        skill_usage.annotate(self.st, out, "claude", {}, NOW + 31 * DAY)
        by = {s["name"]: s for s in out}
        self.assertNotIn("use", by["weekly"], "never seen used → no number (the page shows —)")
        self.assertTrue(by["weekly"]["stale"] and by["weekly"]["arch"])
        self.assertEqual(by["fresh"]["use"], {"n": 1, "last": NOW + 29 * DAY})
        self.assertNotIn("stale", by["fresh"])
        self.assertNotIn("stale", by["agentj-commands"], "Agent J's own: never suggested")
        self.assertNotIn("arch", by["agentj-commands"])
        self.assertTrue(by["mine"]["stale"])
        self.assertNotIn("arch", by["mine"], "a workflow's own skill is managed in that project")
        # installed < N days: not yet
        young = [{"name": "brand-new", "scope": "global"}]
        skill_usage.annotate(self.st, young, "claude", {}, NOW + 31 * DAY)
        self.assertNotIn("stale", young[0])
        # Codex / OpenCode report no skill run: never "long unused" (it would be a guess)
        cx = [dict(s) for s in skills]
        self.assertFalse(skill_usage.annotate(self.st, cx, "codex", {}, NOW + 90 * DAY)["tracked"])
        self.assertFalse(any(s.get("stale") for s in cx))
        # the threshold is the owner's: skills.stale_days
        mine = [{"name": "fresh", "scope": "global"}]
        skill_usage.annotate(self.st, mine, "claude", {"skills": {"stale_days": 7}}, NOW + 37 * DAY)
        self.assertTrue(mine[0]["stale"])
        self.assertEqual(skill_usage.stale_days({"skills": {"stale_days": 3}}), 30, "outside 7–365 → the default")
        self.assertEqual(skill_usage.ago(None), "—")
        self.assertEqual(skill_usage.ago(NOW - 3 * DAY, NOW), "3 天前")
        self.assertEqual(skill_usage.ago(NOW - 3 * DAY, NOW, "en"), "3 days ago")


class Archive(Base):
    def test_round_trip_never_deletes(self):
        _skill(self.skills, "old-report")
        (self.skills / "old-report" / "run.sh").write_text("echo hi\n")
        res = skill_usage.archive(self.st, "old-report", "claude", now=NOW)
        arch = self.home / ".claude" / "skills-archive" / "old-report"
        self.assertEqual(res["to"], str(arch))
        self.assertFalse((self.skills / "old-report").exists())
        self.assertEqual((arch / "run.sh").read_text(), "echo hi\n")
        self.assertEqual(skill_usage.archived(self.st, "claude"), [{"name": "old-report", "at": NOW, "dir": "old-report",
                                                          "from": str(self.skills / "old-report")}])
        self.assertEqual(skill_usage.restore(self.st, "old-report", "claude")["to"], str(self.skills / "old-report"))
        self.assertTrue((self.skills / "old-report" / "SKILL.md").is_file())
        self.assertEqual(skill_usage.archived(self.st, "claude"), [])
        # archived twice: the second gets its own folder; restore refuses a taken place
        skill_usage.archive(self.st, "old-report", "claude")
        _skill(self.skills, "old-report")
        skill_usage.archive(self.st, "old-report", "claude")
        self.assertTrue((self.home / ".claude" / "skills-archive" / "old-report-2").is_dir())
        _skill(self.skills, "old-report")
        with self.assertRaises(skill_usage.ArchiveError) as e:
            skill_usage.restore(self.st, "old-report", "claude")
        self.assertEqual(e.exception.why, "exists")

    def test_official_and_unknown_refused(self):
        _skill(self.skills, "agentj-mine")
        for name, why in (("agentj-mine", "official"), ("agentj-commands", "official"), ("plaza-x", "official"),
                          ("nope", "not_found"), ("../x", "shape")):
            with self.subTest(name=name), self.assertRaises(skill_usage.ArchiveError) as e:
                skill_usage.archive(self.st, name, "claude")
            self.assertEqual(e.exception.why, why)
        self.assertTrue((self.skills / "agentj-mine").is_dir())

    def test_cli_execute_and_page_tail(self):
        _skill(self.skills, "weekly")
        cfg = {"kind": "claude", "dir": str(self.home)}
        h = skills_cli.execute(self.st, {"op": "health"}, cfg)
        row = next(r for r in h["skills"] if r["name"] == "weekly")
        self.assertEqual((row["uses"], row["last"], row["archivable"], row["official"]), (None, None, True, False))
        self.assertTrue(all(r["official"] for r in h["skills"] if r["scope"] == "bundled"))
        self.assertEqual(skills_cli.execute(self.st, {"op": "archive", "name": "agentj-commands"}, cfg),
                         {"ok": False, "why": "official"})
        self.assertTrue(skills_cli.execute(self.st, {"op": "archive", "name": "weekly"}, cfg)["ok"])
        pic = workflows.Scanner(self.st).scan(self.home, "claude", "zh")
        self.assertEqual([a["name"] for a in pic["tail"]["skills_archived"]], ["weekly"])
        self.assertEqual(pic["tail"]["skill_use"], {"tracked": True, "days": 30})
        self.assertNotIn("weekly", [s["name"] for s in pic["tail"]["skills"]])
        self.assertTrue(skills_cli.execute(self.st, {"op": "restore", "name": "weekly"}, cfg)["ok"])
        skill_usage.note(self.st, "weekly", "phone", now=NOW)
        pic = workflows.Scanner(self.st).scan(self.home, "claude", "zh")
        s = next(s for s in pic["tail"]["skills"] if s["name"] == "weekly")
        self.assertEqual((s["use"]["n"], s["arch"]), (1, True))
        self.assertEqual(skills_cli.execute(self.st, {"op": "rm"}, cfg), {"ok": False, "why": "shape"})


class ArchiveRedTeam(Base):
    """B1: serve runs outside the Agent fence and moves folders the Agent can rearrange — never through a link."""
    def assertRefused(self, why, fn, *a):
        with self.assertRaises(skill_usage.ArchiveError) as e:
            fn(*a)
        self.assertEqual(e.exception.why, why)

    def _hidden_state(self, name="aj"):
        """A state directory the Agent cannot see, with a private file in it; its folder name doubles as a skill name."""
        root = self.home / "hidden" / name
        root.mkdir(parents=True, mode=0o700)
        (root / "keys.json").write_text("private\n")
        return State(root)

    def test_archive_cannot_plant_into_the_state_dir(self):
        """skills-archive → <state>: archiving a skill named wfruns would plant <state>/wfruns (forged checkpoints)."""
        _skill(self.skills, "wfruns")
        (self.home / ".claude" / "skills-archive").symlink_to(self.st.root)
        self.assertRefused("link", skill_usage.archive, self.st, "wfruns", "claude")
        self.assertFalse((self.st.root / "wfruns").exists())
        self.assertTrue((self.skills / "wfruns" / "SKILL.md").is_file())

    def test_restore_cannot_move_the_state_dir_out(self):
        """skills-archive → the state's parent + a record naming the state's folder: restore would move the whole state
        directory (keys, devices) into ~/.claude/skills where the Agent reads it."""
        st = self._hidden_state()
        _skill(self.skills, "aj")
        skill_usage.archive(st, "aj", "claude", now=NOW)
        arch = self.home / ".claude" / "skills-archive"
        arch.rename(self.home / ".claude" / "skills-archive.real")
        arch.symlink_to(self.home / "hidden")
        self.assertRefused("link", skill_usage.restore, st, "aj", "claude")
        self.assertEqual((st.root / "keys.json").read_text(), "private\n")
        self.assertFalse((self.skills / "aj").exists())

    def test_forged_index_next_to_the_archive_is_ignored(self):
        arch = self.home / ".claude" / "skills-archive"
        _skill(arch, "stolen")
        (arch / ".agentj-archive.json").write_text(json.dumps({"stolen": {"name": "stolen", "from": str(self.skills / "stolen"),
                                                                           "at": NOW}}))
        self.assertEqual(skill_usage.archived(self.st, "claude"), [])
        self.assertRefused("not_found", skill_usage.restore, self.st, "stolen", "claude")
        self.assertFalse((self.skills / "stolen").exists())
        # the state copy is the index: a legit archive is listed and restorable, the archive folder gets no index file
        _skill(self.skills, "weekly")
        skill_usage.archive(self.st, "weekly", "claude", now=NOW)
        self.assertEqual([a["name"] for a in skill_usage.archived(self.st, "claude")], ["weekly"])
        self.assertTrue((self.st.root / "skill-archive.json").is_file())
        self.assertEqual(oct((self.st.root / "skill-archive.json").stat().st_mode & 0o777), "0o600")

    def test_a_link_at_any_level_refuses(self):
        elsewhere = self.home / "elsewhere"
        _skill(elsewhere / "skills", "weekly")
        # the skills root is a link
        self.skills.parent.mkdir(parents=True)
        self.skills.symlink_to(elsewhere / "skills")
        self.assertRefused("link", skill_usage.archive, self.st, "weekly", "claude")
        self.assertTrue((elsewhere / "skills" / "weekly").is_dir())
        self.skills.unlink()
        # the skill folder itself is a link
        self.skills.mkdir()
        (self.skills / "weekly").symlink_to(elsewhere / "skills" / "weekly")
        self.assertRefused("link", skill_usage.archive, self.st, "weekly", "claude")
        self.assertTrue((self.skills / "weekly").is_symlink())
        (self.skills / "weekly").unlink()
        # the harness folder (~/.claude) is a link
        shutil.rmtree(self.skills.parent)
        (self.home / ".claude").symlink_to(elsewhere)
        self.assertRefused("link", skill_usage.archive, self.st, "weekly", "claude")
        self.assertFalse((elsewhere / "skills-archive").exists())

    def test_a_linked_skill_is_marked_link_not_archivable(self):
        """Jarvis ruling 4: the page hides 「归档」 for what archive() would refuse as a link, and says why (`link`)."""
        elsewhere = self.home / "elsewhere"
        _skill(elsewhere / "skills", "bible")
        _skill(self.skills, "weekly")
        (self.skills / "bible").symlink_to(elsewhere / "skills" / "bible")
        out = [{"name": "weekly", "scope": "global"}, {"name": "bible", "scope": "global"}]
        skill_usage.annotate(self.st, out, "claude", {}, NOW)
        by = {s["name"]: s for s in out}
        self.assertEqual((by["weekly"].get("arch"), by["weekly"].get("link")), (True, None))
        self.assertEqual((by["bible"].get("arch"), by["bible"].get("link")), (None, True))
        self.assertRefused("link", skill_usage.archive, self.st, "bible", "claude")
        rows = {r["name"]: r for r in skill_usage.health(self.st, "claude", now=NOW)["skills"]}
        if "bible" in rows:
            self.assertEqual((rows["bible"]["archivable"], rows["bible"].get("link")), (False, True))
        self.assertIn("链接，不能归档", skills_cli._health_text({"harness": "claude", "days": 30, "tracked": True, "archived": [],
                      "skills": [{"name": "bible", "official": False, "uses": None, "last": None, "stale": False, "link": True}]}))

    def test_inside_or_around_a_protected_dir_refuses(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.st.root / "cc")
        _skill(self.st.root / "cc" / "skills", "weekly")
        self.assertRefused("protected", skill_usage.archive, self.st, "weekly", "claude")
        self.assertTrue((self.st.root / "cc" / "skills" / "weekly").is_dir())

    def _swap_on_rename(self, link: pathlib.Path, to: pathlib.Path):
        """Patch os.rename so the folder `link` is replaced by a symlink to `to` after every check, just before the move."""
        real = os.rename

        def swap(*a, **kw):
            real(link, link.with_name(link.name + ".real"))
            link.symlink_to(to)
            return real(*a, **kw)
        return patch.object(skill_usage.os, "rename", side_effect=swap)

    def test_toctou_archive_link_swapped_in_after_the_checks(self):
        _skill(self.skills, "wfruns")
        (self.home / ".claude" / "skills-archive").mkdir(mode=0o700)
        with self._swap_on_rename(self.home / ".claude" / "skills-archive", self.st.root):
            skill_usage.archive(self.st, "wfruns", "claude", now=NOW)
        self.assertFalse((self.st.root / "wfruns").exists(), "the move used the checked directory, not the new link")
        self.assertTrue((self.home / ".claude" / "skills-archive.real" / "wfruns" / "SKILL.md").is_file())

    def test_toctou_restore_link_swapped_in_after_the_checks(self):
        st = self._hidden_state()
        _skill(self.skills, "aj")
        skill_usage.archive(st, "aj", "claude", now=NOW)
        with self._swap_on_rename(self.home / ".claude" / "skills-archive", self.home / "hidden"):
            skill_usage.restore(st, "aj", "claude")
        self.assertEqual((st.root / "keys.json").read_text(), "private\n", "the state directory stayed where it was")
        self.assertIn("Do the thing", (self.skills / "aj" / "SKILL.md").read_text(), "the archived skill came back")
        self.assertFalse((self.skills / "aj" / "keys.json").exists())


class PhoneSigned(unittest.TestCase):
    """S6: 「归档」/「恢复」 from the phone are signed controls (object = op \\n name); N1: elevate.sock forces by=agent."""
    def setUp(self):
        from test_l1 import Phone, _owner_host as _host, _ready, _state
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p426s-")
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name)
        p = patch.dict(os.environ, {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")})
        p.start()
        self.addCleanup(p.stop)
        for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "DSH_HOME"):
            os.environ.pop(k, None)
        self.st = _state(self.tmp.name)
        (self.home / "work").mkdir()
        self.st.set_agent_config("claude", str(self.home / "work"))
        self.sent = []
        self.host = _host(self.st, self.sent)
        self.phone = Phone(self.st)
        self.s = _ready(self.host, self.phone)
        self.skills = self.home / ".claude" / "skills"
        _skill(self.skills, "weekly")

    def sign(self, t, name, *, key=None, n=None, op=None):
        from agentj import controls, wire
        n = n or os.urandom(16).hex()
        ts = int(time.time() * 1000)
        target = {"op": op or ("archive" if t == "skill_archive" else "restore"), "name": name}
        msg = controls.signed_message(self.phone.channel, self.phone.did, t, n, ts, controls.object_digest(t, target))
        return {"t": t, "r": "0123456789abcdef", "name": name, "n": n, "ts": ts,
                "sig": wire.b64u((key or self.phone.sk).sign(msg))}

    def tap(self, obj):
        async def go():
            await self.host.on_skill_act(self.s, obj["t"], obj)
        asyncio.run(go())
        return [o for _, o in self.sent if o["t"] == "ctl_res"][-1]

    def controls_log(self):
        return [json.loads(x) for x in self.st.controls_path.read_text().splitlines()] if self.st.controls_path.exists() else []

    def test_object_text_and_actions(self):
        from agentj import controls
        self.assertIn("skill_archive", controls.ACTIONS)
        self.assertIn("skill_restore", controls.ACTIONS)
        self.assertEqual(controls.object_text("skill_archive", {"op": "archive", "name": "weekly"}), "archive\nweekly")
        self.assertEqual(controls.object_text("skill_restore", {"op": "restore", "name": "weekly"}), "restore\nweekly")

    def test_unsigned_or_wrong_signature_moves_nothing(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        other = Ed25519PrivateKey.generate()
        for obj, why in (({"t": "skill_archive", "r": "0123456789abcdef", "name": "weekly"}, "shape"),
                         (self.sign("skill_archive", "weekly", key=other), "bad_signature"),
                         ({**self.sign("skill_archive", "other-skill"), "name": "weekly"}, "bad_signature"),
                         (self.sign("skill_archive", "weekly", op="restore"), "bad_signature")):
            res = self.tap(obj)
            self.assertEqual((res["action"], res["ok"], res["why"]), ("skill_archive", False, why), obj)
            self.assertTrue((self.skills / "weekly").is_dir())
        self.assertEqual([r["result"] for r in self.controls_log()],
                         ["refused:shape", "refused:bad_signature", "refused:bad_signature", "refused:bad_signature"])

    def test_signed_archive_and_restore_and_replay(self):
        obj = self.sign("skill_archive", "weekly")
        res = self.tap(obj)
        self.assertEqual((res["ok"], res["name"]), (True, "weekly"))
        self.assertFalse((self.skills / "weekly").exists())
        self.assertFalse(self.tap(obj)["ok"], "a nonce is taken once")
        self.assertTrue(self.tap(self.sign("skill_restore", "weekly"))["ok"])
        self.assertTrue((self.skills / "weekly" / "SKILL.md").is_file())
        log = self.controls_log()
        self.assertEqual([(r["action"], r["result"]) for r in log],
                         [("skill_archive", "ok"), ("skill_archive", "refused:replay"), ("skill_restore", "ok")])
        self.assertTrue(all(r["device"] == self.phone.did for r in log))
        self.assertIn("object_sha256", log[0])

    def test_elevate_socket_records_by_agent(self):
        from agentj import elevate
        e = elevate.Elevator.__new__(elevate.Elevator)
        e.host = types.SimpleNamespace(agent_cfg={"kind": "claude"}, lang="zh", preferences={})
        e.st = self.st
        out, seen = [], []

        def fake(st, req, *a, **kw):
            seen.append(req)
            return {"ok": True}

        async def go():
            r = asyncio.StreamReader()
            r.feed_data(b'{"t": "skills", "op": "archive", "name": "weekly", "by": "phone"}\n')
            r.feed_eof()
            w = types.SimpleNamespace(get_extra_info=lambda k: None, write=out.append, drain=AsyncMock(), close=Mock())
            await e.on_client(r, w)
        with patch.object(skills_cli, "execute", side_effect=fake):
            asyncio.run(go())
        self.assertEqual(seen[0]["by"], "agent", "the Agent cannot pass itself off as the phone")
        self.assertEqual(json.loads(b"".join(out)), {"ok": True})


class Events(Base):
    def test_phone_slash_desktop_slash_and_skill_tool(self):
        _skill(self.skills, "weekly")
        host = types.SimpleNamespace(st=self.st, lang="zh", preferences={})
        from agentj.shared import SharedClaudeAgent
        fake = types.SimpleNamespace(host=host, kind="claude", cfg={"dir": str(self.home)}, phone_turn=False)
        SharedClaudeAgent._note_skill(fake, "PostToolUse", {"tool_name": "Skill", "tool_input": {"skill": "weekly"}})
        SharedClaudeAgent._note_skill(fake, "UserPromptSubmit", {"prompt": "/compact"})        # not a skill: not counted
        SharedClaudeAgent._note_skill(fake, "UserPromptSubmit", {"prompt": "/nothing-here x"})
        use = skill_usage.load(self.st)["skills"]
        self.assertEqual(list(use), ["weekly"])
        with patch("time.time", return_value=NOW + 3600):
            SharedClaudeAgent._note_skill(fake, "UserPromptSubmit", {"prompt": "/weekly please"})
        self.assertEqual(skill_usage.load(self.st)["skills"]["weekly"]["n"], 2)
        from agentj.agent import ClaudeAgent
        cl = types.SimpleNamespace(host=host, kind="claude", cfg={"dir": str(self.home)})
        with patch("time.time", return_value=NOW + 7200):
            ClaudeAgent._note_tools(cl, [{"type": "tool_use", "name": "Skill", "input": {"skill": "weekly"}}], None)
        self.assertEqual(skill_usage.load(self.st)["skills"]["weekly"]["src"], {"claude": 3})

    def test_serve_turn_note_counts_the_owner_only(self):
        _skill(self.skills, "weekly")
        from agentj import serve
        fake = types.SimpleNamespace(st=self.st, agent_cfg={"kind": "claude", "dir": str(self.home)}, preferences={},
                                     onboarding_lang=lambda: "zh")
        owner = types.SimpleNamespace(provenance={"kind":"phone","role":"owner"})
        group = types.SimpleNamespace(provenance={"kind": "telegram", "role": "group", "sender": 1, "chat": 2})
        self.assertEqual(serve.Host.skill_turn_note(fake, owner, "/weekly 本周"), "")
        with patch("time.time", return_value=NOW):
            serve.Host.skill_turn_note(fake, group, "/weekly 群里有人发的")
        self.assertEqual(skill_usage.load(self.st)["skills"]["weekly"]["src"], {"phone": 1}, "a group member is not the owner")


class Wiring(unittest.TestCase):
    def test_parser_identity_hook_and_skill_text(self):
        import argparse
        from agentj import cli
        src = pathlib.Path(cli.__file__).read_text()
        self.assertEqual(src.count("skills_cli.add_parser(sub)"), 1, "one `agentj skills`")
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="cmd")
        skills_cli.add_parser(sub)
        self.assertEqual(p.parse_args(["skills", "archive", "old-report"]).name, "old-report")
        self.assertTrue(p.parse_args(["skills", "health", "--json"]).json)
        ident = pathlib.Path(workflows.__file__).with_name("identity")
        for f in ident.glob("*.md"):
            self.assertNotIn("agentj skills", f.read_text(encoding="utf-8"), "no new words in the identity hook")
        skill = (workflows.SKILLS / "agentj-commands" / "SKILL.md").read_text(encoding="utf-8")
        for needle in ("agentj skills health", "agentj skills archive", "agentj skills restore"):
            self.assertIn(needle, skill)


if __name__ == "__main__":
    unittest.main()
