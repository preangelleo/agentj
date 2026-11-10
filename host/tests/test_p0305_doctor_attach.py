"""ADR-0305-doctor-attach: `agentj support … --attach-doctor` carries the doctor rows without this host's identifiers, the
owner's own file names or the owner-facing auto-update notice — generated that way BEFORE layer 1, so layer 2 (Jev) is not
asked to judge them. Offline: fake rows (`checks` seam), a real State in a temp dir, a stub Channel and a recording Jev
transport; nothing reaches the network or the real home."""
import _hermetic  # noqa: F401,I001
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import auto_update, cloud, doctor_attach, peer_guard, privacy, support  # noqa: E402
from agentj.peer import PeerKeys  # noqa: E402
from agentj.state import State  # noqa: E402

MACHOME = privacy.FAKE_SECRETS["machome"]
LINUXHOME = "/ho" + "me/"
SLUG, TENANT = "kq3r-7m2x", "Kestrel Ops"
CHANNEL = "Zq3mT7vK1pR9xW2nB5cD8e"          # 22-char urlsafe, invented
HOST_ID = "h_8Fq2Lz0Ww"
AGENT_NAME = "Pumpkin"
HONORIFIC = "老板"
RELEASE_SUMMARY = "修好了周报导出卡住的问题"
NOTICE = f"{HONORIFIC}，我夜里自己升级到 0.28.6 了：{RELEASE_SUMMARY}"
PRIVATE_FILES = ("documentation/reports/2026-11-07-家庭理财年度复盘.md", "documentation/reports/q3-board-memo-draft.md",
                 "documentation/notes/离职面谈记录.txt")
TID = "st_" + "T" * 22
FAKE_OR = privacy.FAKE_SECRETS["openrouter"]


def _row(cid, status, summary, hint=""):
    return {"id": cid, "status": status, "summary": summary, "hint": hint}


def fixture_rows(kind, peer_id):
    mac = kind == "mac"
    home = (MACHOME + "kai/") if mac else (LINUXHOME + "kai/")
    not_listed = "; ".join(f"{p} exists but is not listed" for p in PRIVATE_FILES)
    return [
        _row("version", "ok", "agentj 0.30.4"),
        _row("python", "ok", "3.13.1"),
        _row("platform", "ok", "macOS 27.0.1 arm64" if mac else "Linux x86_64 · Ubuntu 24.04"),
        _row("state", "ok", f"~/.local/state/agentj · 通道 / channel {CHANNEL}"),
        _row("bound", "ok", f"Agent J 账号 / Agent J account {SLUG} · 用设置码加入 / joined with a setup code"),
        _row("friends", "ok", f"开 / on · {peer_id} · 证书到 / certificate until 2026-12-01 · 信箱已连上 / mailbox connected"),
        _row("agent", "ok", f"claude · {AGENT_NAME} · {TENANT}"),
        _row("agent_cli", "ok", "claude 2.1.309 · codex 0.61.0"),
        _row("harness", "ok", "Claude Code 2.1.309 (2 candidates)\nclaude: ~/.local/bin/claude 2.1.309 [Agent J uses this]\n"
                              f"claude: {home}.asdf/shims/claude 2.1.300 [mise/asdf wrapper: never executed]"),
        _row("fence", "ok", "sandbox-exec ✓" if mac else "bubblewrap 0.9.0 ✓"),
        _row("service", "ok", "net.agentj.host · running" if mac else "agentj · systemd user unit active"),
        _row("resources", "ok", "agentj.service: cpu.weight, io.weight, io, oom_score_adj"),
        _row("hardware", "ok", json.dumps({"cpu": "Apple M5" if mac else "x86_64", "cores": 10, "mem_gb": 32})),
        _row("service-environment", "ok", json.dumps({"AJ_ENV_PRESENCE_MATCH": {"PATH": True, "HTTPS_PROXY": False}})),
        _row("root-structure", "fail", not_listed + "; boot_set must list exactly the 7 boot documents", "agentj wizard doctor"),
        _row("work-root", "warn", f"~/{TENANT.split()[0]}-work", "agentj init --working-root <existing root>; existing files stay put"),
        _row("skills", "ok", "内置技能已是最新 / built-in skills up to date"),
        _row("auto-update", "ok", "on · 03:00–05:00 local · " + NOTICE),
    ]


class Base(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("TEST_TMP") or None
        self.tmp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        env = {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "xdg-config"), "XDG_STATE_HOME": str(root / "xdg-state")}
        for v in env.values():
            pathlib.Path(v).mkdir(parents=True, exist_ok=True)
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.st = State(root / "state")
        self.st.init(relay="ws://127.0.0.1:1")
        cfg = self.st.config()
        cfg["channel"] = CHANNEL
        self.st.write_private(self.st.config_path, json.dumps(cfg).encode())
        cloud.write_cloud(self.st, {"api": "http://127.0.0.1:9", "host_id": HOST_ID, "tenant": {"slug": SLUG, "name": TENANT},
                                    "linked_at": 1, "last_seq": 0, "via": "seat"})
        self.st.set_agent_name(AGENT_NAME)
        self.peer_id = PeerKeys.load_or_create(self.st).id
        auto_update.write(self.st, {"result": {"result": "ok", "from": "0.28.5", "to": "0.28.6", "reason": "upgraded",
                                               "summary": RELEASE_SUMMARY, "when": "night"}})

    def leaks(self):
        return (CHANNEL, SLUG, TENANT, HOST_ID, AGENT_NAME, self.peer_id, HONORIFIC, RELEASE_SUMMARY, "家庭理财", "q3-board-memo",
                "离职面谈", *PRIVATE_FILES)


class AttachIsScrubbed(Base):
    def test_mac_and_linux_rows_lose_identifiers_files_and_notice(self):
        for kind in ("mac", "linux"):
            with self.subTest(kind=kind):
                text = support.doctor_text(st=self.st, checks=fixture_rows(kind, self.peer_id))
                draft, _ = support.prepare("doctor attached", text, identity=("devbox.lan", "kai"))
                attach = draft["attach"]
                for leak in self.leaks():
                    self.assertNotIn(leak, attach)
                self.assertNotIn(MACHOME + "kai", attach)
                self.assertNotIn(LINUXHOME + "kai", attach)
                for keep in ("previous: ok 0.28.5→0.28.6 (upgraded)", "on · 03:00–05:00 local", "root-structure: 4 finding(s)",
                             "agentj.service: cpu.weight, io.weight, io, oom_score_adj",
                             "claude 2.1.309", "AJ_ENV_PRESENCE_MATCH"):
                    self.assertIn(keep, attach)
                self.assertIn("macOS 27.0.1 arm64" if kind == "mac" else "Linux x86_64", attach)
                self.assertIn("net.agentj.host" if kind == "mac" else "systemd user unit", attach)
                self.assertIn("sandbox-exec" if kind == "mac" else "bubblewrap 0.9.0", attach)
                self.assertTrue(text.splitlines()[0].startswith("agentj "))

    def test_root_rows_never_forward_raw_detail_hint_or_unknown_category(self):
        workflow_id = "cedar-invoice-review"  # synthetic extensionless workflow ID
        rows = [
            _row("root-structure", "fail", f"workflow {workflow_id} needs its own CEO entry file; "
                 f"{workflow_id} is installed but not listed in STRUCTURE.json workflows", f"repair {workflow_id}"),
            _row("root-" + workflow_id, "warn", "private detail", "private hint"),
            _row("root-entry", "ok", f"workflow {workflow_id} is ready", ""),
        ]
        text = support.doctor_text(st=self.st, checks=rows)
        draft, _ = support.prepare("doctor attached", text, identity=("devbox.lan", "kai"))
        for blob in (text, draft["attach"]):
            self.assertNotIn(workflow_id, blob)
            self.assertNotIn("private detail", blob)
            self.assertNotIn("private hint", blob)
            self.assertNotIn("repair", blob)
            self.assertIn("root-structure: 2 finding(s)", blob)
            self.assertIn("root-other: 1 finding(s)", blob)
            self.assertIn("root-entry: 0 finding(s)", blob)

    def test_labelled_fallback_without_state(self):
        rows = [_row("state", "ok", f"~/.local/state/agentj · 通道 / channel {CHANNEL}"),
                _row("bound", "ok", f"Agent J 账号 / Agent J account {SLUG} · 用设置码加入 / joined with a setup code"),
                _row("x", "ok", f"host_id: {HOST_ID} · tenant=kq3r-7m2x")]
        text = "\n".join(doctor_attach.support_rows(rows, None))
        for leak in (CHANNEL, SLUG, HOST_ID):
            self.assertNotIn(leak, text)
        self.assertIn("channel <id>", text)
        self.assertIn("account <id>", text)


class Whitelist(unittest.TestCase):
    KEEP = ("0.30.4", "2.1.309", "1.13.8", "macOS 27.0.1 arm64", "Linux x86_64", "Ubuntu 24.04", "Claude Code", "Codex", "OpenCode",
            "root-structure", "service-environment", "auto-update", "agentj service install", "agentj wizard doctor",
            "CLAUDE.md", "AGENTS.md", "README.md", "install.md", "documentation/STRUCTURE.json", "STRUCTURE.json",
            "documentation/NEXT_SESSION.md", "net.agentj.host", "agentj.service: cpu.weight, io.weight", "cpu.weight",
            "mise/asdf wrapper", "seat ✓ / seat", "on|off", "I/O", "2026-11-12 22:30", "03:00–05:00 local", "127.0.0.1",
            "localhost", "not in an Agent J account yet", "account dashboard", "account page", "channel open",
            "https://agentj.app/docs/install/zh.md", "https://agentj.app/llms.txt", "sandbox-exec", "bubblewrap 0.9.0",
            "systemctl --user status agentj", "(--offline)", "files 600", "tokens: —")

    def test_technical_strings_survive_unchanged(self):
        for s in self.KEEP:
            with self.subTest(s=s):
                self.assertEqual(doctor_attach.scrub(s, {"kq3r-7m2x"}), s)

    def test_owner_files_go(self):
        for s, want in (("plan.docx", "<file>"), ("notes/预算.xlsx", "<file>"), ("workflows/kestrel/CLAUDE.md", "<file>"),
                        ("see 私人笔记.md;", "see <file>;")):
            with self.subTest(s=s):
                self.assertEqual(doctor_attach.scrub(s), want)

    def test_absolute_paths_are_left_to_redact_paths(self):
        for s in ("~/notes/x.md", "/var/lib/x.json"):
            self.assertEqual(doctor_attach.scrub(s), s)
            self.assertEqual(support.redact_paths(s), "<redacted-path>")


class SecretsStillCaught(Base):
    def test_layer1_still_deletes_credentials(self):
        rows = fixture_rows("linux", self.peer_id) + [
            _row("jev-key", "warn", f"OPENROUTER_API_KEY {FAKE_OR} in documentation/notes/keys.md"),
            _row("custom", "warn", "password=Fixture-Pass-4471x for account kq3r-7m2x")]
        text = support.doctor_text(st=self.st, checks=rows)
        draft, hits = support.prepare("doctor attached", text, identity=("devbox.lan", "kai"))
        self.assertNotIn(FAKE_OR, draft["attach"])
        self.assertNotIn("Fixture-Pass-4471x", draft["attach"])
        self.assertGreaterEqual(hits.get("credential_line", 0), 1)

    def test_secret_index_hit_line_is_not_split_by_scrub(self):
        """Security review F1: a low-entropy known secret (index hit, no credential shape) shaped like a file name must
        reach layer 1 unchanged, so layer 1 deletes the whole line — no `<file>-staple-cat` remainder goes out."""
        value = "Bluehorse.md-staple-cat"            # invented
        self.assertIsNone(peer_guard.credential_shape(value))
        index = peer_guard.SecretIndex([value])
        line = f"✗ config: bad value {value} at voice.tts"
        rows = fixture_rows("linux", self.peer_id) + [_row("config", "fail", f"bad value {value} at voice.tts", "agentj config validate --json")]
        with mock.patch.object(peer_guard.SecretIndex, "build", return_value=index):
            self.assertEqual(doctor_attach.scrub(line, set(), index), line)
            text = support.doctor_text(st=self.st, checks=rows)
            self.assertIn(f"✗ config: bad value {value} at voice.tts", text)      # untouched into layer 1
            draft, hits = support.prepare("doctor attached", text, identity=("devbox.lan", "kai"))
        for part in (value, "Bluehorse", "staple", "-cat"):
            self.assertNotIn(part, draft["attach"])
        self.assertGreaterEqual(hits.get("credential_line", 0), 1)
        self.assertIn("previous: ok 0.28.5→0.28.6 (upgraded)", draft["attach"])

    def test_index_build_failure_is_the_doctor_failed_line(self):
        with mock.patch.object(peer_guard.SecretIndex, "build", side_effect=OSError("local_secret_scan_incomplete")):
            text = support.doctor_text(st=self.st, checks=fixture_rows("mac", self.peer_id))
        self.assertEqual(text.splitlines()[1:], ["doctor failed: OSError"])
        incomplete = peer_guard.SecretIndex()
        incomplete.complete = False
        with mock.patch.object(peer_guard.SecretIndex, "build", return_value=incomplete):
            text = support.doctor_text(st=self.st, checks=fixture_rows("mac", self.peer_id))
        self.assertEqual(text.splitlines()[1:], ["doctor failed: OSError"])
        for leak in self.leaks():
            self.assertNotIn(leak, text)

    def test_credential_line_is_not_split_by_scrub(self):
        line = f"key {FAKE_OR} channel {CHANNEL}"
        self.assertEqual(doctor_attach.scrub(line, {CHANNEL}), line)


class AutoUpdateFacts(unittest.TestCase):
    def test_closed_values_only(self):
        f = doctor_attach.auto_update_facts
        self.assertEqual(f({"result": "rolled_back", "from": "0.28.6", "to": "0.28.7", "reason": "health_check"}),
                         "previous: rolled_back 0.28.6→0.28.7 (health_check)")
        hostile = f({"result": "董事长 pwned", "from": "../../etc", "to": "0.28.7; rm -rf", "reason": "因为 老板 说的"})
        self.assertEqual(hostile, "previous: other")
        self.assertEqual(f({"result": "failed", "to": "0.29.0a1", "reason": "Network Error"}), "previous: failed →0.29.0a1")
        self.assertEqual(f(None), "no previous result")
        self.assertEqual(f({"result": "ok", "from": 3, "to": ["x"]}), "previous: ok")

    def test_row_uses_record_not_notice(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP") or None) as d:
            st = State(pathlib.Path(d) / "s")      # not initialised: no record → `no previous result`
            line = doctor_attach.support_rows([_row("auto-update", "ok", "on · 03:00–05:00 local · " + NOTICE)], st)[0]
        self.assertNotIn(HONORIFIC, line)
        self.assertIn("03:00–05:00 local · no previous result", line)


class BrokenInstall(unittest.TestCase):
    def test_identifiers_never_raise(self):
        self.assertEqual(doctor_attach.identifiers(None), set())
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP") or None) as d:
            self.assertEqual(doctor_attach.identifiers(State(pathlib.Path(d) / "missing")), set())
            root = pathlib.Path(d) / "broken"
            root.mkdir()
            (root / "config.json").write_text("{not json")
            (root / "cloud.json").write_text("[]")
            self.assertEqual(doctor_attach.identifiers(State(root)), set())

        class Exploding:
            root = pathlib.Path("/nonexistent-p0305")

            def __getattr__(self, name):
                raise RuntimeError("boom")
        self.assertEqual(doctor_attach.identifiers(Exploding()), set())

    def test_doctor_failure_fallback(self):
        def boom():
            raise RuntimeError("no doctor")
        text = support.doctor_text(st=SimpleNamespace(), checks=boom)
        self.assertEqual(text.splitlines()[1], "doctor failed: RuntimeError")


class RecordingJev:
    def __init__(self):
        self.drafts = []

    def __call__(self, url, headers, body, timeout):
        assert url.startswith("https://") or url.startswith("http://127.0.0.1")
        self.drafts.append(json.loads(body)["state"]["feedback_draft"])
        return 200, json.dumps({"answers": {"exposure": {"noul": 0.05}, "kind": {"choice": "none"}}, "model": "jev"}).encode()


class RunSendEndToEnd(Base):
    def test_attach_doctor_reaches_layer2_and_send_scrubbed(self):
        send = mock.Mock(return_value=(201, {"thread": TID, "seq": 1}))
        ch = SimpleNamespace(signed=True, st=self.st, send=send)
        jev = RecordingJev()
        rows = fixture_rows("mac", self.peer_id)
        with mock.patch.object(cloud, "post_json", side_effect=AssertionError("no network")):
            rc = support.run_send("report", "attach-doctor refused on my host", attach_doctor=True, wait=0, ch=ch,
                                  env={"OPENROUTER_API_KEY": FAKE_OR}, transport=jev, identity=("devbox.lan", "kai"),
                                  doctor_fn=lambda: support.doctor_text(st=self.st, checks=rows), out=io.StringIO())
        self.assertEqual(rc, support.EXIT_PENDING)
        self.assertEqual(len(jev.drafts), 1)
        sent = send.call_args.args[0]
        for blob in (jev.drafts[0]["attach"], sent["attach"]):
            for leak in self.leaks():
                self.assertNotIn(leak, blob)
            self.assertIn("previous: ok 0.28.5→0.28.6 (upgraded)", blob)
        self.assertNotIn(FAKE_OR, json.dumps(jev.drafts + [sent]))


class PrivacyCases(unittest.TestCase):
    def test_doctor_attach_cases_present(self):
        cases = {c["id"]: c for c in privacy.load_cases()}
        self.assertEqual(cases["doctor_attach_scrubbed"]["expect"], "clean")
        self.assertEqual(cases["doctor_attach_raw_ids"]["expect"], "leaky")
        clean = cases["doctor_attach_scrubbed"]["draft"]["attach"]
        self.assertIn("<id>", clean)
        self.assertEqual(doctor_attach.scrub(clean), clean)      # already scrubbed: a fixed point
        raw = cases["doctor_attach_raw_ids"]["draft"]["attach"]
        self.assertNotEqual(doctor_attach.scrub(raw, {"kq3r-7m2x"}), raw)


if __name__ == "__main__":
    unittest.main()
