"""0.30.8 ADR-0308-fingerprint-scope (chairman 2026-11-11).

A. The fingerprint card is for owner turns only: CHARGE_COMMANDS and the closed
   IRREVERSIBLE list. B. 董事长 11-11 定: all host-held scheduled tasks / background jobs, regardless of origin,
   and heartbeat run with NO runtime fingerprint card or refusal (they follow their harness; logged as
   fingerprint_headless); host_read / external / unknown turns keep the
   ordinary off-list refusal of any write (nothing fingerprint-specific). C. Main-thread / dispatched native rm / rmdir / unlink → deny with
   the `agentj delete` hint (no card); a host-launched user automation (scheduled task) keeps real deletion.
   D. IRREVERSIBLE positives / negatives; ordinary commands unchanged.
Every path lives under a throw-away HOME (never the real ~/coding/.env); no systemctl, no real harness.
"""
import _hermetic  # noqa: F401,I001
import ast
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import approval_offlist, delete_redirect, uv_list  # noqa: E402

import test_p552_approval_signer as p552  # noqa: E402

AJ = pathlib.Path(__file__).resolve().parents[1]
OWNER = approval_offlist._make("owner_remote", "iPhone", "owner")
TERMINAL = approval_offlist._make("owner_terminal", None, "owner")
HEARTBEAT = approval_offlist._make("host_heartbeat", None, "host")
TASK = dict(approval_offlist._make("scheduled_task", "nightly-backup", "host"), owner_dispatch=True)
ORDER = dict(approval_offlist._make("host_system", ("后台活", "background job"), "host"), owner_dispatch=True)
# security 0310 H1: the same work started by a heartbeat / system notice / another local session, or a schedule enabled
# outside the paired phone / account
TASK_UNOWNED = approval_offlist._make("scheduled_task", "nightly-backup", "host")
ORDER_UNOWNED = approval_offlist._make("host_system", ("后台活", "background job"), "host")
COMPACT = approval_offlist._make("host_system", ("压缩恢复", "compaction recovery"), "host_read")
AUTOMATION = approval_offlist._make("automation", "a1", "host_read")
SETTINGS = dict(approval_offlist._make("settings", ("通用", "general"), "host"), owner_dispatch=True)
FRIEND = approval_offlist._make("friend", "f1", "external")
UNKNOWN = approval_offlist._make("unknown", None, "unknown")

IRREVERSIBLE_HITS = (
    ("rm -rf ~/", "rm -r <home|/|/home>"), ("rm -rf $HOME", "rm -r <home|/|/home>"),
    ("sudo rm -rf --no-preserve-root /", "rm -r <home|/|/home>"), ("rm -fr ~/*", "rm -r <home|/|/home>"),
    ("rm -r /home", "rm -r <home|/|/home>"), ("rm -rf -- /", "rm -r <home|/|/home>"),
    ("git push --force origin main", "git push --force"), ("git push -f", "git push --force"),
    ("git -C repo push --force-with-lease origin x", "git push --force"), ("git push origin +main", "git push --force"),
    ("dd if=img of=/dev/sda bs=1M", "dd of=/dev/…"), ("mkfs.ext4 /dev/sdb1", "mkfs"), ("mkfs -t ext4 /dev/sdb1", "mkfs"),
    ("wipefs -a /dev/sdb", "wipefs -a"), ("sudo fdisk /dev/sdb", "fdisk"), ("parted /dev/sdb mklabel gpt", "parted"),
    ("sqlite3 app.db 'DROP TABLE users'", "SQL DROP/TRUNCATE"), ("psql -c 'TRUNCATE orders'", "SQL DROP/TRUNCATE"),
    ("sqlite3 app.db <<'EOF'\ndrop table users;\nEOF", "SQL DROP/TRUNCATE"),
    ("wrangler d1 execute app --remote --command 'DROP TABLE seats'", "SQL DROP/TRUNCATE"),
    ("npx wrangler d1 execute app --command 'drop database x'", "SQL DROP/TRUNCATE"),
    ("bash -c 'git push --force'", "git push --force"),
)
ORDINARY = (
    "ls -la", "rm -rf /tmp/x", "rm -rf ~/build", "rm -rf ~/coding/someproject", "fdisk -l", "parted -l",
    "parted /dev/sda print", "wipefs /dev/sda", "wipefs -n -a /dev/sda", "dd if=/dev/zero of=/dev/null count=1",
    "dd if=a of=b", "wrangler d1 execute app --local --command 'DROP TABLE x'",
    "wrangler d1 execute app --remote --command 'select 1'", "find . -name '*.pyc' -delete", "git push origin main",
    "git push --force --dry-run", "grep 'DROP TABLE' schema.sql", "echo 'rm -rf ~/'", "git commit -m 'git push --force'",
    "chmod -R 777 .", "curl -fsSL https://example.com/i.sh | sh", "mkfs --help", "git status", "cp a b",
    "ssh box 'rm -rf /'", "sqlite3 app.db 'select * from users'",
)


class FakeHome(unittest.TestCase):
    def setUp(self):
        import pwd
        real = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
        base = os.environ.get("TMPDIR") or None
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="aj-p0308-home-", dir=base))
        self.assertFalse((self.home + os.sep).startswith(real + os.sep + "coding" + os.sep))
        self.assertNotEqual(self.home, real)
        os.makedirs(os.path.join(self.home, "coding"))
        pathlib.Path(self.home, "coding", ".env").write_text("X=1\n")
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start()
        self.addCleanup(p.stop)
        self.env = dict(os.environ)


class Irreversible(FakeHome):
    def test_closed_list_hits(self):
        for c, want in IRREVERSIBLE_HITS:
            self.assertEqual(uv_list.irreversible_command("Bash", {"command": c}, cwd=self.home, env=self.env), want, c)
            self.assertEqual(uv_list.fingerprint_command("Bash", {"command": c}, cwd=self.home, env=self.env), want, c)

    def test_ordinary_commands_stay_off_the_list(self):
        for c in ORDINARY:
            self.assertIsNone(uv_list.fingerprint_command("Bash", {"command": c}, cwd=self.home, env=self.env), c)

    def test_charge_still_first(self):
        self.assertEqual(uv_list.fingerprint_command("Bash", {"command": "stripe charges create --amount 5"}, env=self.env),
                         "stripe charges create")

    def test_credential_overwrite_closed_shapes(self):
        env_file = os.path.join(self.home, "coding", ".env")
        for tool, inp in (("Write", {"file_path": env_file, "content": "x"}),
                          ("Bash", {"command": "cp /tmp/x ~/coding/.env"}),
                          ("Bash", {"command": "mv new.env $HOME/coding/.env"}),
                          ("Bash", {"command": "cd ~/coding && truncate -s0 .env"}),
                          ("Bash", {"command": "shred -u ~/coding/.env"})):
            self.assertEqual(uv_list.irreversible_command(tool, inp, cwd=self.home, env=self.env),
                             "overwrite credential file", inp)
        for tool, inp in (("Edit", {"file_path": env_file, "old_string": "a", "new_string": "b"}),
                          ("Bash", {"command": "cp ~/coding/.env /tmp/b"}),
                          ("Bash", {"command": "echo Y=2 >> ~/coding/.env"}),
                          ("Bash", {"command": "chmod 600 ~/coding/.env"}),
                          ("Write", {"file_path": os.path.join(self.home, "coding", "notes.md"), "content": "x"})):
            self.assertIsNone(uv_list.irreversible_command(tool, inp, cwd=self.home, env=self.env), inp)

    def test_no_file_deletion_shape_on_the_list(self):
        # chairman 11-11: deletion is covered by the trashbin; only the root / home / /home shape stays (no trash holds it)
        self.assertEqual([k for k in uv_list.IRREVERSIBLE if k.startswith("rm")], ["rm -r <home|/|/home>"])
        self.assertEqual(uv_list.FINGERPRINT_SOURCES,
                         frozenset({"owner_remote", "owner_terminal", "owner_channel"}))


class NativeDelete(unittest.TestCase):
    def test_parsed_top_level_deletions(self):
        for c, want in (("rm x", "rm"), ("sudo rm -rf build", "rm"), ("/bin/rm -f a", "rm"), ("bash -c 'rm a'", "rm"),
                        ("ls | xargs rm", "rm"), ("find . -name x -exec rm {} \\;", "rm"), ("rmdir d", "rmdir"),
                        ("unlink f", "unlink"), ("cd /tmp && rm -rf x", "rm"), ("timeout 5 rm x", "rm")):
            self.assertEqual(delete_redirect.native_delete("Bash", {"command": c}), want, c)
            self.assertEqual(delete_redirect.native_delete("exec_command", {"cmd": c}), want, c)
        for c in ("echo rm x", "agentj delete x", "git rm x", "grep rm notes", "ssh box rm x", "ls", "trash-put x"):
            self.assertIsNone(delete_redirect.native_delete("Bash", {"command": c}), c)
        self.assertIsNone(delete_redirect.native_delete("Write", {"file_path": "rm", "content": "rm x"}))

    def test_mode_is_host_marked_only(self):
        self.assertEqual(delete_redirect.mode({}), "trash")
        self.assertEqual(delete_redirect.mode(None), "trash")
        self.assertEqual(delete_redirect.mode({"_delete_mode": "REAL"}), "trash")
        self.assertEqual(delete_redirect.mode({"_delete_mode": "real"}), "real")
        self.assertIsNone(delete_redirect.denial({"_delete_mode": "real"}, "Bash", {"command": "rm x"}))
        text = delete_redirect.denial({}, "Bash", {"command": "rm x"})
        self.assertIn("agentj delete", text)
        self.assertIn("24", text)

    def test_config_cannot_carry_the_mark(self):
        # state.agent_config builds a closed dict: an owner / model config.json key never reaches the adapter cfg
        src = (AJ / "agentj" / "state.py").read_text()
        self.assertNotIn("_delete_mode", src)

    def test_host_marks_only_user_automations(self):
        def marks(rel):
            tree = ast.parse((AJ / "agentj" / rel).read_text())
            return [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "_delete_mode"]
        self.assertEqual(len(marks("tasks.py")), 1)                    # scheduled tasks (+ their workflow steps)
        for rel in ("core/native_order.py", "busy_native.py", "serve.py", "agent.py", "agent_codex.py",
                    "owner_avatar.py"):
            self.assertEqual(marks(rel), [], rel)                      # orders, main thread, avatar, busy jobs: trash


class OrderDeleteMode(unittest.TestCase):
    """Security r1 M1: parent.kind 'task' is a main-session system / same-host turn (heartbeat, background_done,
    approval_review), not a scheduled task — an order it dispatches still has rm denied (trashbin)."""
    def test_order_from_a_system_turn_gets_rm_denied(self):
        from agentj.core import native_order, orders
        tmp = tempfile.mkdtemp(prefix="aj-p0308-order-", dir=os.environ.get("TMPDIR") or None)
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        base = pathlib.Path(tmp)
        master = base / ("m" * max(1, 79 - len(os.fsencode(base))))
        master.mkdir()
        project = base / "project"
        project.mkdir()
        brief = {"goal": "offline", "scope": {"write": ["result.txt"]}, "verify": ["offline"], "timebox": 1,
                 "report": "head"}
        for parent in ({"kind": "task", "session": "s1", "harness": "claude", "source_class": "host_heartbeat"},
                       {"kind": "task", "session": "s1", "harness": "claude", "source_class": "host_system"},
                       {"kind": "owner_turn", "session": "s1", "harness": "claude"}):
            with self.subTest(parent=parent):
                o = orders.create(project=str(project), prompt_text="offline", title="t", brief=brief, parent=parent,
                                  harness="claude", model="synthetic", fence=True, st_root=master,
                                  origin=parent["kind"])
                h = native_order.Host(o, None, master)
                self.assertEqual(delete_redirect.mode(h.cfg), "trash")
                self.assertIn("agentj delete", delete_redirect.denial(h.cfg, "Bash", {"command": "rm -rf build"}))


class OffList(FakeHome, p552.Fixture):
    """Real Host.ask → list_approval → approval_offlist with the source pinned."""
    def setUp(self):
        p552.Fixture.setUp(self)
        FakeHome.setUp(self)

    def asked(self, src, inp, tool="Bash"):
        n = len(self.cards())
        with mock.patch.object(approval_offlist, "source", return_value=src):
            res = self.ask(inp, tool=tool, _harness_permission=True)
        return res, self.cards()[n:]

    def test_main_thread_gets_the_card(self):
        p552.passkey_on(self.st, self.s.pub)
        for c in ("stripe charges create --amount 500", "git push --force origin main", "dd if=x of=/dev/sdb"):
            for src in (OWNER, TERMINAL):
                res, cards = self.asked(src, c)
                self.assertEqual(len(cards), 1, (src["type"], c))
                self.assertTrue(cards[0].get("passkey_required"), c)
                self.assertNotEqual(res["behavior"], "allow", c)          # nobody answered the card

    def test_same_charge_main_thread_cards_headless_runs(self):
        # 董事长 11-11 定: the same charge command — main thread: fingerprint card; headless / automation: no card, no
        # refusal (follows the harness), one host.log line without the command text.
        p552.passkey_on(self.st, self.s.pub)
        for c in ("stripe charges create --amount 500", "git push --force origin main", "mkfs.ext4 /dev/sdb1"):
            res, cards = self.asked(OWNER, c)
            self.assertEqual(len(cards), 1, c)
            self.assertTrue(cards[0].get("passkey_required"), c)
            for src in (TASK, ORDER, SETTINGS, TASK_UNOWNED, ORDER_UNOWNED, HEARTBEAT):
                res, cards = self.asked(src, c)
                self.assertEqual((res["behavior"], cards), ("allow", []), (src["type"], c))
                self.assertEqual(res.get("updatedInput"), {"command": c})
            # host_read (data-carrying notices / automation data) and external / unknown: the same refusal an ordinary
            # write gets from that source — nothing fingerprint-specific, still no card
            for src in (COMPACT, AUTOMATION, FRIEND, UNKNOWN):
                res, cards = self.asked(src, c)
                plain, plain_cards = self.asked(src, "touch ./notes.txt")
                self.assertEqual((res["behavior"], cards), ("deny", []), (src["type"], c))
                self.assertEqual((res["reason"], plain_cards), (plain["reason"], []), (src["type"], c))
                self.assertTrue(res["reason"].startswith("off_list_write_"), res)
        log = (self.st.root / "host.log").read_text() if (self.st.root / "host.log").exists() else ""
        self.assertIn("fingerprint_headless", log)
        self.assertNotIn("stripe charges create --amount 500", log)
        self.assertNotIn("fingerprint_unattended", log)

    def test_existing_cli_tasks_manual_dispatch_resume_and_workflows_never_get_fingerprint_cards(self):
        # Existing jobs have no owner_started / owner_dispatch fact. The host must not retroactively review them.
        for src in (TASK_UNOWNED, ORDER_UNOWNED, HEARTBEAT):
            for command in ("stripe charges create --amount 500", "git push --force origin main",
                            "mkfs.ext4 /dev/sdb1"):
                with self.subTest(source=src['type'], command=command):
                    result, cards = self.asked(src, command)
                    self.assertEqual((result['behavior'], cards), ('allow', []))
                    self.assertEqual(result['updatedInput'], {'command': command})
        # No journal migration or added persisted approvals are needed for old schedules.

    def test_headless_allow_respects_emergency_stop(self):
        # the card-free headless path still runs behind serve stop / Stop everything (never an early allow)
        with mock.patch.object(self.host, "stopped", return_value=True):
            for src in (TASK, ORDER):
                res, cards = self.asked(src, "stripe charges create --amount 500")
                self.assertEqual((res["behavior"], cards, res["reason"]), ("deny", [], "estop"), src["type"])

    def test_log_shape_is_fixed_label(self):
        self.assertEqual(approval_offlist.log_shape("git push --force"), "git push --force")
        self.assertEqual(approval_offlist.log_shape("rm -r <home|/|/home>"), "rm -r <home|/|/home>")
        self.assertEqual(approval_offlist.log_shape("stripe post /v1/charges/ch_secretish/capture"), "stripe post")

    def test_headless_credential_read_still_refused(self):
        env_file = os.path.join(self.home, "coding", ".env")
        for src in (TASK, ORDER):
            res, cards = self.asked(src, f"stripe charges create --api-key \"$(cat {env_file})\"")
            self.assertEqual((res["behavior"], cards, res["reason"]), ("deny", [], "off_list_credential"), src["type"])

    def test_ordinary_commands_unchanged(self):
        p552.passkey_on(self.st, self.s.pub)
        for c in ("ls -la", "rm -rf ~/build", "git push origin main", "fdisk -l",
                  "wrangler d1 execute app --local --command 'DROP TABLE x'"):
            for src in (OWNER, HEARTBEAT, TASK, ORDER):
                res, cards = self.asked(src, c)
                self.assertEqual((res["behavior"], cards), ("allow", []), (src["type"], c))

    def test_credential_read_still_wins_on_the_main_thread(self):
        env_file = os.path.join(self.home, "coding", ".env")
        res, cards = self.asked(OWNER, f"stripe charges create --api-key \"$(cat {env_file})\"")
        self.assertEqual((res["behavior"], cards), ("deny", []))
        self.assertEqual(res["reason"], "off_list_credential")


class OwnerDispatchTyping(unittest.TestCase):
    """security 0310 H1: who marks background work as owner-made — host-held facts only."""
    def test_order_typing_follows_the_host_held_parent(self):
        host = mock.Mock(agent=None)
        self.assertIs(approval_offlist.source(host, background="order", order_owner=True)["owner_dispatch"], True)
        self.assertIs(approval_offlist.source(host, background="order")["owner_dispatch"], False)
        self.assertIs(approval_offlist.source(host, background="order", order_owner="yes")["owner_dispatch"], False)

    def test_askbridge_owner_mark_comes_from_the_creation_record(self):
        # security 0310 H1-o: only the FIRST origin event written by order_host, with an owner_turn parent
        from agentj.core import askbridge
        src = (AJ / "agentj" / "core" / "askbridge.py").read_text()
        self.assertIn('_order_owner=meta.get("_owner_dispatch") is True', src)
        self.assertIn("meta['_owner_dispatch'] = owner_dispatched(o)", src)

        def order(parent, events):
            return mock.Mock(meta={"parent": parent}, events=lambda: events)
        owner = {"kind": "owner_turn", "session": "s1", "harness": "claude"}
        task = {"kind": "task", "session": "s1", "harness": "claude", "source_class": "system"}
        self.assertTrue(askbridge.owner_dispatched(order(owner, [{"ev": "origin", "owner_dispatch": True}])))
        self.assertFalse(askbridge.owner_dispatched(order(owner, [{"ev": "origin", "owner_dispatch": False}])))
        self.assertFalse(askbridge.owner_dispatched(order(owner, [{"ev": "origin"}])))
        self.assertFalse(askbridge.owner_dispatched(order(task, [{"ev": "origin", "owner_dispatch": True}])))
        self.assertFalse(askbridge.owner_dispatched(order(owner, [{"ev": "origin", "owner_dispatch": False},
                                                                  {"ev": "origin", "owner_dispatch": True}])))
        self.assertFalse(askbridge.owner_dispatched(order(owner, [{"ev": "origin", "owner_dispatch": "yes"}])))
        broken = mock.Mock(meta={"parent": owner}, events=mock.Mock(side_effect=OSError))
        self.assertFalse(askbridge.owner_dispatched(broken))

    def test_order_host_binds_the_mark_to_the_current_turn(self):
        # security 0310 H1-o / H1-o2: only the in-process core tool or a peer born entirely within the current turn
        from agentj import approval_material
        from agentj.core import order_host
        agent = object()
        host = mock.Mock(agent=agent)
        self.assertTrue(order_host.turn_bound(host, None, agent))
        self.assertFalse(order_host.turn_bound(host, None, object()))
        self.assertFalse(order_host.turn_bound(host, None, None))
        for scope, want in (({"kind": "agent"}, True), ({"kind": "desktop"}, True), ({"kind": "unknown_agent"}, False),
                            ({"kind": "unit"}, False), (None, False), ("agent", False)):
            with mock.patch.object(approval_material, "_direct_scope", return_value=scope):
                self.assertIs(order_host.turn_bound(host, 4242, None), want, scope)
        with mock.patch.object(approval_material, "_direct_scope", side_effect=OSError):
            self.assertFalse(order_host.turn_bound(host, 4242, None))
        src = (AJ / "agentj" / "core" / "order_host.py").read_text()
        self.assertIn("owner_dispatch = parent.get('kind') == 'owner_turn' and turn_bound(host, peer, internal)", src)
        self.assertIn("o.event('origin', parent=parent, source=source, available_root=root, owner_dispatch=owner_dispatch)", src)

    def test_unbound_caller_cannot_steer_an_owner_dispatched_order(self):
        # security 0310 H1-o3: send / resume / gate answers on an owner-dispatched order need the same binding
        src = (AJ / "agentj" / "core" / "order_host.py").read_text()
        guard = "if act in ('send', 'resume') and owner_dispatched(o) and not turn_bound(host, peer, internal):"
        self.assertIn(guard, src)
        self.assertLess(src.index(guard), src.index("if act == 'send' and ('gate' in args or 'answer' in args):"))
        self.assertLess(src.index(guard), src.index("o.event('resume_source', source=source)"))

    def test_task_scope_typing(self):
        from agentj import provenance
        host = mock.Mock(agent=None)
        for owner, want in ((True, True), (False, False), ("1", False)):
            with provenance.task_run(owner=owner):
                with mock.patch.object(provenance, "_paired_source", return_value=provenance._UNSET):
                    t = approval_offlist.source(host)
            self.assertEqual((t["type"], t["owner_dispatch"]), ("scheduled_task", want), owner)

    def test_scheduler_marks_owner_started(self):
        src = (AJ / "agentj" / "tasks.py").read_text()
        # security 0310 H1-r: only the host-held phone / account enable marks a run (never the main thread's turn state)
        self.assertIn('rec["owner_started"] = owner_schedule\n', src)
        self.assertIn('provenance.task_run(owner=rec.get("owner_started") is True)', src)

    def test_scheduler_request_marks_only_phone_enabled_schedules(self):
        from agentj import tasks
        tmp = tempfile.mkdtemp(prefix="aj-p0310-sched-", dir=os.environ.get("TMPDIR") or None)
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        entry = {"tsha": "t1", "relative_dir": "workflows/w1", "task": {}}
        for by, trigger, want in (("phone:abc", "schedule", True), ("account:x", "catch_up", True),
                                  ("cli", "schedule", False), ("phone:abc", "manual", False),
                                  ("phone:abc", "dispatch", False), ("phone:abc", "quota_resume", False)):
            journal = {"on": {"w1": {"tsha": "t1", "by": by}}, "runs": {}, "ran": {}}
            sched = tasks.Scheduler.__new__(tasks.Scheduler)
            sched.current_id, sched.waiting, sched.queued, sched.instance_id = None, [], {}, "i1"
            sched.host = mock.Mock(preferences=None)
            sched.workdir = lambda: tmp
            with mock.patch.object(tasks, "find", return_value=entry), \
                    mock.patch.object(tasks, "uncertain_run", return_value=False), \
                    mock.patch.object(tasks, "load", return_value=journal), \
                    mock.patch.object(tasks, "update", side_effect=lambda st, fn: fn(journal)), \
                    mock.patch.object(tasks.Scheduler, "_enqueue", create=True):
                try:
                    sched.request("w1", trigger)
                except Exception:   # noqa: BLE001 — queueing internals past the journal write are not under test
                    pass
            runs = list(journal["runs"].values())
            self.assertEqual(len(runs), 1, (by, trigger))
            self.assertIs(runs[0]["owner_started"], want, (by, trigger))
            # The actual scheduler mark is passed into the run scope; legacy CLI/manual/dispatch/resume
            # must still reach the same card-free host decision, without a journal migration.
            from agentj import provenance
            with provenance.task_run(owner=runs[0]["owner_started"]):
                with mock.patch.object(provenance, "_paired_source", return_value=provenance._UNSET):
                    source = approval_offlist.source(sched.host)
            self.assertEqual(source["type"], "scheduled_task")
            self.assertNotIn(source["type"], uv_list.FINGERPRINT_SOURCES)


def _hook_cmd(real_delete=False):
    from agentj import agent
    return agent.hook_settings([], real_delete=real_delete)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


class ClaudeHook(FakeHome):
    def run_hook(self, cmd, command, tool="Bash"):
        env = {**self.env, "PYTHONPATH": str(AJ)}
        p = subprocess.run(["sh", "-c", cmd], input=json.dumps({"tool_name": tool, "tool_input": {"command": command},
                           "cwd": self.home}), capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)["hookSpecificOutput"] if p.stdout.strip() else None

    def test_main_thread_denies_rm_with_the_trash_hint(self):
        cmd = _hook_cmd()
        self.assertNotIn("real-delete", cmd)
        out = self.run_hook(cmd, "rm -rf ./build")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("agentj delete", out["permissionDecisionReason"])
        self.assertIsNone(self.run_hook(cmd, "ls -la"))
        self.assertEqual(self.run_hook(cmd, "git push --force origin main")["permissionDecision"], "ask")
        self.assertEqual(self.run_hook(cmd, "rm -rf ~/")["permissionDecision"], "ask")   # the list wins over the trash
        self.assertEqual(self.run_hook(cmd, "stripe charges create --amount 1")["permissionDecision"], "ask")

    def test_agentj_delete_of_protected_target_is_denied(self):
        # security r1 H1 → r2 L3: `rm <protected>` is still sent to Host.ask; `agentj delete|rm <protected>` (also behind
        # --instance / interpreter flags) is denied outright — put() refuses it after any approval anyway
        os.makedirs(os.path.join(self.home, ".claude"))
        cmd = _hook_cmd()
        self.assertEqual(self.run_hook(cmd, "rm ~/.claude/settings.json")["permissionDecision"], "ask")
        for c in ("agentj delete ~/.claude/settings.json", "agentj rm .claude/settings.json",
                  "python3 -m agentj delete ~/.claude/settings.json", "python3 -I -m agentj delete ~/.claude/settings.json",
                  "agentj --instance 2 delete ~/.claude/settings.json"):
            out = self.run_hook(cmd, c)
            self.assertEqual(out["permissionDecision"], "deny", c)
            self.assertIn("the owner removes it locally", out["permissionDecisionReason"])
        self.assertIsNone(self.run_hook(cmd, "agentj delete ./build"))         # ordinary path: no card

    def test_user_automation_keeps_real_deletion(self):
        cmd = _hook_cmd(real_delete=True)
        self.assertIsNone(self.run_hook(cmd, "rm -rf ./build"))
        self.assertEqual(self.run_hook(cmd, "git push --force origin main")["permissionDecision"], "ask")

    def test_claude_adapter_wiring(self):
        from agentj import agent

        def command(cfg):
            argv = agent.ClaudeAgent(None, cfg).argv(None)
            return json.loads(argv[argv.index("--settings") + 1])["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        base = {"kind": "claude", "dir": self.home, "_workflow_ceo": True}
        self.assertNotIn("real-delete", command(base))                           # order / dispatched job
        self.assertIn("real-delete", command({**base, "_delete_mode": "real"}))  # scheduled task


class Guards(unittest.TestCase):
    def native(self, cfg):
        from agentj import shared_risk
        a = mock.Mock(kind="codex", cfg=cfg, tid="t1")
        a.host.ask = mock.AsyncMock(return_value={"behavior": "deny", "message": "no"})
        a.host.st.root = "/nonexistent-state"
        return shared_risk.NativePathGuard(a), a

    @staticmethod
    def ev(cmd):
        return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": "/tmp",
                "session_id": "t1"}

    def test_native_path_guard_main_denies_rm_no_card(self):
        g, a = self.native({"dir": "/tmp"})
        out = asyncio.run(g.hook_event(self.ev("rm -rf x")))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("agentj delete", out["hookSpecificOutput"]["permissionDecisionReason"])
        a.host.ask.assert_not_called()
        self.assertEqual(asyncio.run(g.hook_event(self.ev("ls"))), {})

    def test_native_path_guard_task_keeps_rm_and_routes_irreversible(self):
        g, a = self.native({"dir": "/tmp", "_delete_mode": "real"})
        self.assertEqual(asyncio.run(g.hook_event(self.ev("rm -rf x"))), {})
        out = asyncio.run(g.hook_event(self.ev("git push --force")))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")   # unauthenticated binding: refused

    def risk(self, cfg):
        from agentj import shared_risk
        a = mock.Mock(kind="claude", cfg=cfg, tid="t1")
        a.host.stopped.return_value = False
        a.host.st.root = "/nonexistent-state"
        a.host.lang = "zh"
        a.host.ask = mock.AsyncMock(return_value={"behavior": "deny", "message": "card expired"})
        return shared_risk.RiskGuard(a), a

    def test_risk_guard_rm_denied_irreversible_asked(self):
        g, a = self.risk({"dir": "/tmp"})
        with mock.patch("agentj.policy_registry.main_requirement", return_value="allow"):
            self.assertFalse(asyncio.run(g._check("Bash", {"command": "rm x"})))
            self.assertIn("agentj delete", g.reason())
            a.host.ask.assert_not_awaited()
            self.assertTrue(asyncio.run(g._check("Bash", {"command": "ls -la"})))
            self.assertFalse(asyncio.run(g._check("Bash", {"command": "mkfs.ext4 /dev/sdb1"})))
            self.assertTrue(a.host.ask.await_args.kwargs["_harness_permission"])
        g, a = self.risk({"dir": "/tmp", "_delete_mode": "real"})
        with mock.patch("agentj.policy_registry.main_requirement", return_value="allow"):
            self.assertTrue(asyncio.run(g._check("Bash", {"command": "rm x"})))


if __name__ == "__main__":
    unittest.main()
