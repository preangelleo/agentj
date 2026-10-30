"""P424 #142 + #147 — the secret checks stop calling paths, names and shell references secrets; scripts are text cards; a
refusal says where.

Leo 10-19: `K=$(grep '^ARK_CODING_PLAN_API_KEY=' ~/work/.env | … | tr -d '"')` showed as `='<redacted>'` in his own chat
(the quote closing the grep pattern was read as the start of a value); `ark-check.sh` was 「这种文件发不了」; a `friends tell`
holding only `channels/Wiki4what/automation/LINUX_LAUNCHER_PROMPT.md`-style paths was refused as `credential:high_entropy`
with no hint where. Now (privacy relaxed modes — taskspec.secret_like, peer_guard.credential_shape, the owner's own text):
paths, file names, snake_case identifiers, environment-variable NAMES, `$VAR` / `$(…)` / `%VAR%` / `$env:VAR` / `os.environ[…]`
references and `Authorization: Bearer $k` are no value. Reverse: every real key shape (generated fakes only — never a real
key) is still caught by every gate, random high-entropy tokens with `_` / `-` in them still are, a known `.env` value is still
`secret_fragment`, and the strict feedback / plaza scan (the server mirror) is unchanged. A refusal carries the line, column,
length and a two-character masked hint, never the fragment.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import base64
import os
import pathlib
import random
import string
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

AJ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AJ))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import media, peer_guard, peer_service, privacy, serve, taskspec, tg_media  # noqa: E402

from test_l1 import _state  # noqa: E402

RNG = random.Random(424142)


def fake(n: int, alphabet: str = string.ascii_letters + string.digits) -> str:
    """A generated stand-in for a key value: upper, lower and digits, never a real credential."""
    while True:
        t = "".join(RNG.choice(alphabet) for _ in range(n))
        if any(c.isupper() for c in t) and any(c.islower() for c in t) and any(c.isdigit() for c in t):
            return t


# the reported shapes (no value anywhere; paths / names as Leo's messages had them)
ARK_CHECK = ("#!/usr/bin/env bash\n"
             "K=$(grep '^ARK_CODING_PLAN_API_KEY=' ~/work/.env | head -1 | cut -d= -f2- | tr -d '\"')\n"
             "curl -sS https://ark.cn-beijing.volces.com/api/coding/v3/models -H \"Authorization: Bearer $K\"\n"
             "echo \"ARK_CODING_PLAN_API_KEY length: ${#K}\"\n"
             "opencode run -m ark/doubao \"hi\" # reads ARK_CODING_PLAN_API_KEY from ~/.config/opencode/opencode.json\n")
W4W = ("核对结论：channels/Wiki4what/automation/LINUX_LAUNCHER_PROMPT.md 第 12-40 行写死了 "
       "~/.local/state/wiki4what/runs/2026-10-19/ ，align/segment/realign/doctor 四步都在 "
       "~/.local/state/wiki4what/runs/2026-10-19/align_segment_realign_doctor.log；"
       "WIKI4WHAT_OPENROUTER_API_KEY 只按名字读，脚本 LINUX_LAUNCHER_PROMPT_Wiki4what_2026v2.md 已对齐。")
NOT_VALUES = [
    "K=$(grep '^ARK_CODING_PLAN_API_KEY=' ~/work/.env | cut -d= -f2- | tr -d \"'\")",
    "K=$(grep -E '^ARK_CODING_PLAN_API_KEY=' ~/work/.env | sed 's/^[^=]*=//')",
    "API_KEY=$ark_key_from_vault",
    "TOKEN=%ARK_CODING_PLAN_API_KEY%",
    "$env:ARK_TOKEN = $env:ARK_CODING_PLAN_API_KEY",
    "api_key = os.environ['ARK_CODING_PLAN_API_KEY']",
    "token=os.getenv(\"ARK_TOKEN\")",
    "curl -H 'Authorization: Bearer $k' https://example.com/v1/models",
    "resolve_wiki4what_launcher_prompt_v2_and_realign2 WIKI4WHAT_LINUX_LAUNCHER_PROMPT_PATH_V2",
    "~/work/media_cn/channels/MediaCN/build_20261019/delivery/LINUX_LAUNCHER_PROMPT_Wiki4what.md",
]


class NotSecrets(unittest.TestCase):
    def test_ark_check_file_is_no_secret_anywhere(self):
        self.assertFalse(taskspec.secret_like(ARK_CHECK))
        self.assertFalse(taskspec.secret_like(ARK_CHECK, owner=True))
        self.assertIsNone(peer_guard.credential_shape(ARK_CHECK))
        self.assertNotIn(privacy.R, privacy.owner_text(ARK_CHECK), "the owner's own chat shows the command whole")
        self.assertEqual(privacy.credentials(ARK_CHECK), [])

    def test_wiki4what_paths_pass_the_friend_gates(self):
        self.assertEqual(peer_guard.outbound(W4W, None), peer_guard.Verdict(True))
        self.assertEqual(peer_guard.inbound(W4W), peer_guard.Verdict(True))
        self.assertFalse(taskspec.secret_like(W4W))
        self.assertNotIn(privacy.R, privacy.owner_text(W4W))

    def test_names_references_and_code_are_not_values(self):
        for t in NOT_VALUES:
            with self.subTest(t=t):
                self.assertFalse(taskspec.secret_like(t), t)
                self.assertFalse(taskspec.secret_like(t, owner=True), t)
                self.assertIsNone(peer_guard.credential_shape(t), t)
                self.assertNotIn(privacy.R, privacy.owner_text(t), t)
                self.assertIsNone(privacy.secret_at(t), t)

    def test_zero_count_is_not_a_hit(self):
        # a deep path judged segment by segment used to leave Counter({"high_entropy": 0}) → "secret"
        t = "~/work/media_cn/channels/MediaCN/build_20261019AbC/delivery/20261019AbC/LINUX_LAUNCHER_PROMPT.md"
        _, hits = privacy.redact_with_report(t, paths=True)
        self.assertNotIn("high_entropy", hits)
        self.assertFalse(taskspec.secret_like(t))

    def test_strict_feedback_scan_is_unchanged(self):
        # the feedback / plaza layer 1 mirrors the server scan (worker/src/scan.ts): not relaxed here
        self.assertIn(privacy.R, privacy.redact("K=$(grep '^API_KEY=' ~/work/.env | cut -d= -f2- | tr -d '\"')"))
        self.assertIn(privacy.R, privacy.redact("LINUX_LAUNCHER_PROMPT_Wiki4what_2026v2_" + "ABCdef123" * 2))


class StillSecrets(unittest.TestCase):
    def shapes(self):
        return {
            "anthropic": "sk-ant-api03-" + fake(40),
            "openai": "export OPENAI_API_KEY=sk-proj-" + fake(40),
            "github": "ghp_" + fake(36),
            "aws": "AKIA" + "".join(RNG.choice(string.ascii_uppercase + string.digits) for _ in range(16)),
            "google": "AIza" + fake(35),
            "assign": "ARK_CODING_PLAN_API_KEY=" + fake(32),
            "quoted": 'TOKEN="' + fake(24) + '"',
            "ps1": '$env:ARK_KEY = "' + fake(32) + '"',
            "bearer": "curl -H 'Authorization: Bearer " + fake(40) + "'",
            "basic": "Authorization: Basic " + base64.b64encode(os.urandom(18)).decode(),
            "private": "-----BEGIN " + "OPENSSH PRIVATE KEY-----\nAAAA\n-----END " + "OPENSSH PRIVATE KEY-----",
        }

    def test_real_shapes_are_caught_by_every_gate(self):
        for name, t in self.shapes().items():
            with self.subTest(name=name):
                self.assertTrue(taskspec.secret_like(t), name)
                self.assertTrue(taskspec.secret_like(t, owner=True), name)
                self.assertIsNotNone(peer_guard.credential_shape(t), name)
                self.assertFalse(peer_guard.outbound(t, None).ok, name)
                self.assertIn(privacy.R, privacy.owner_text(t), name)

    def test_unlabelled_random_tokens_still_caught_by_strict_gates(self):
        missed = []
        for i in range(3000):
            t = (fake(RNG.randint(32, 48), string.ascii_letters + string.digits + "_-") if i % 3 else
                 base64.urlsafe_b64encode(os.urandom(RNG.randint(24, 36))).decode())
            if not privacy._entropic(t):
                continue                       # not high-entropy to begin with (all one case, no digit …)
            if not (taskspec.secret_like("value " + t) and peer_guard.credential_shape("value " + t)):
                missed.append(t[:4] + "…")
        self.assertEqual(missed, [], "a random token must not pass as a name")

    def test_random_path_segment_and_known_env_value(self):
        self.assertTrue(taskspec.secret_like("see /srv/data/" + fake(30) + "/x.txt"))
        value = fake(28)
        idx = peer_guard.SecretIndex([value])
        v = peer_guard.outbound("look: ~/work/a/b.md and " + value, idx)
        self.assertEqual(v, peer_guard.Verdict(False, "secret_fragment"))
        self.assertEqual(v.where["hint"], value[:2] + "…")
        self.assertNotIn(value[2:6], repr(v.where))



# security review of 96c2738a2: shapes the relaxed rules let through although the base caught them (generated fakes only)
def review_leaks() -> dict:
    return {
        "quoted_call": '"password": "Winter2025(prod)"',
        "subscript_tail": "db_password: Qw3rty[42]zz",
        "bearer_call": "Authorization: Bearer x(" + fake(30) + ")",
        "env_name_value": "ADMIN_PASSWORD=BLUE_MONKEY_RIVER_77",
        "percent_value": "PASSWORD=%Hunter2%",
        "quote_space": "PASSWORD=' Winter2025prod'",
        "unquoted_call": "password=Winter2025(prod)",
        "random_arg": "token=getenv(" + fake(30) + ")",
    }


class ReviewLeaksStayCaught(unittest.TestCase):
    def test_friend_and_others_gates_refuse(self):
        with mock.patch.object(privacy, "_KNOWN_ENV", set()):
            for name, t in review_leaks().items():
                with self.subTest(name=name):
                    self.assertIsNotNone(peer_guard.credential_shape(t), name)
                    self.assertTrue(taskspec.secret_like(t), name)
                    self.assertFalse(peer_guard.outbound(t, None).ok, name)
                    self.assertIsNotNone(privacy.secret_at(t), name)

    def test_owner_channel_still_hides_the_value_shapes(self):
        for name in ("quoted_call", "subscript_tail", "bearer_call", "percent_value", "quote_space", "unquoted_call",
                     "random_arg"):
            with self.subTest(name=name):
                self.assertIn(privacy.R, privacy.owner_text(review_leaks()[name]), name)

    def test_code_and_grep_shapes_still_pass(self):
        for t in ('api_key = os.environ["ARK_CODING_PLAN_API_KEY"]', "secret=getenv('ARK_TOKEN')",
                  'token = os.environ.get("ARK_TOKEN")', "K=$(grep '^ARK_CODING_PLAN_API_KEY=' ~/.env | cut -d= -f2-)",
                  "PASS=' | sed 1q'", "TOKEN=%ARK_CODING_PLAN_API_KEY%", "curl -H \"Authorization: Bearer $K\" x"):
            with self.subTest(t=t):
                self.assertIsNone(peer_guard.credential_shape(t), t)
                self.assertFalse(taskspec.secret_like(t), t)

    def test_env_name_value_needs_a_known_name_toward_others(self):
        t = "ARK_TOKEN=ARK_CODING_PLAN_API_KEY_FALLBACK"
        with mock.patch.object(privacy, "_KNOWN_ENV", set()), mock.patch.dict(os.environ, clear=False):
            os.environ.pop("ARK_CODING_PLAN_API_KEY_FALLBACK", None)
            self.assertIsNotNone(peer_guard.credential_shape(t))
            self.assertTrue(taskspec.secret_like(t))
            self.assertFalse(taskspec.secret_like(t, owner=True), "the owner's own channel: a name stays a name")
            self.assertNotIn(privacy.R, privacy.owner_text(t))
            os.environ["ARK_CODING_PLAN_API_KEY_FALLBACK"] = "1"
            self.assertIsNone(peer_guard.credential_shape(t), "a variable of this process")
            self.assertFalse(taskspec.secret_like(t))

    def test_env_file_key_names_are_known_values_are_not(self):
        value = fake(28)
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as d, mock.patch.object(privacy, "_KNOWN_ENV", set()):
            env = pathlib.Path(d) / ".env"
            env.write_text("ARK_CODING_PLAN_API_KEY_FALLBACK=" + value + "\nWIKI_NOTE_DIR=~/notes\n")
            peer_guard.SecretIndex.build(environ={}, home=d, service_env=False)
            self.assertEqual(privacy._KNOWN_ENV, {"ARK_CODING_PLAN_API_KEY_FALLBACK", "WIKI_NOTE_DIR"})
            self.assertIsNone(peer_guard.credential_shape("ARK_TOKEN=ARK_CODING_PLAN_API_KEY_FALLBACK"))
            self.assertIsNotNone(peer_guard.credential_shape("ADMIN_PASSWORD=BLUE_MONKEY_RIVER_77"))

class WhereItIs(unittest.TestCase):
    def test_outbound_names_line_column_and_a_masked_hint(self):
        value = fake(40)
        text = "第一行：路径 channels/Wiki4what/automation/LINUX_LAUNCHER_PROMPT.md\n第二行 token: " + value + "\n"
        v = peer_guard.outbound(text, None)
        self.assertFalse(v.ok)
        self.assertEqual(v.rule, "credential:credential_assignment")
        self.assertEqual((v.where["line"], v.where["col"], v.where["len"]), (2, 12, 40))
        self.assertEqual(v.where["hint"], value[:2] + "…")
        self.assertNotIn(value[2:], repr(v.where))
        line = peer_service.where_text(v.where)
        self.assertIn("第 2 行第 12 列", line)
        self.assertNotIn(value[2:8], line)
        self.assertIn("$VAR", peer_service.gate_suggestion(v.rule))
        self.assertIn("进度", peer_service.gate_suggestion("asks_secret:ask_verb_object"))
        self.assertEqual(peer_service.where_text(None), "")

    def test_inbound_high_entropy_is_located(self):
        value = fake(44)
        v = peer_guard.inbound("hello\nhere " + value)
        self.assertEqual((v.ok, v.rule), (False, "credential:high_entropy"))
        self.assertEqual((v.where["line"], v.where["col"]), (2, 6))


class ScriptCards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.m = media.Media(self.st, str(self.work))

    def tearDown(self):
        self.tmp.cleanup()

    def test_scripts_are_text_cards_and_the_ark_check_is_offered(self):
        for name in ("ark-check.sh", "ark-check.txt", "probe.py", "setup.ps1"):
            (self.work / name).write_text(ARK_CHECK)
        items, skips = self.m.scan("[a](ark-check.sh) [b](ark-check.txt) [c](probe.py) [d](setup.ps1)", 1)
        self.assertEqual(skips, [])
        self.assertEqual([(i["name"], i["kind"], i["mime"]) for i in items],
                         [(n, "file", "text/plain") for n in ("ark-check.sh", "ark-check.txt", "probe.py", "setup.ps1")])
        rec, why = self.m.check(media.Ref("ark-check.sh", "ark-check.sh", True), owner=False)
        self.assertIsNone(why, "others: the strict scan passes the variable-only script too")

    def test_scripts_are_never_offered_implicitly(self):
        (self.work / "run.sh").write_text("echo hi\n")
        self.assertEqual(self.m.scan("我改了 `run.sh` 和 run.sh", 1), ([], []))

    def test_script_with_a_key_is_refused_with_its_line(self):
        value = "sk-proj-" + fake(40)
        (self.work / "leak.sh").write_text("#!/bin/sh\n# setup\nexport OPENAI_API_KEY=" + value + "\n")
        items, skips = self.m.scan("[s](leak.sh)", 1)    # P573: the owner's page gets the masked copy, no refusal
        self.assertEqual(([i["name"] for i in items], skips), (["leak.sh"], []))
        self.assertNotIn(value.encode(), b"".join(d for _, d, _ in self.m.window(items[0]["mid"], 0)))
        rec, why = self.m.check(media.Ref("leak.sh", "leak.sh", True), owner=True)     # without mask (WeChat): the note
        skips = [{"name": "leak.sh", "why": why, **media._at(self.m, why)}]
        self.assertEqual(skips, [{"name": "leak.sh", "why": "key_content", "line": 3, "hint": "sk…"}])
        rec, why = self.m.check(media.Ref("leak.sh", "leak.sh", True), owner=False)
        self.assertEqual((rec, why, self.m.last_where()["line"]), (None, "secret", 3))
        note = tg_media.skip_lines(skips, "zh")
        self.assertIn("（第 3 行）", note)
        self.assertNotIn("sk…", note, "Telegram gets the line only")
        self.assertNotIn(value[3:], note)
        self.assertIn("(line 3)", tg_media.skip_lines(skips, "en"))

    def test_line_is_counted_across_scan_slices(self):
        pad = ("x" * 99 + "\n") * (media.SCAN_SLICE // 100 + 50)     # > one slice of plain lines
        value = fake(40)
        (self.work / "big.txt").write_text(pad + "token: " + value + "\n")
        rec, why = self.m.check(media.Ref("big.txt", "big.txt", True), owner=False)
        self.assertEqual(why, "secret")
        self.assertEqual(self.m.last_where()["line"], pad.count("\n") + 1)


class NeverSilent(unittest.TestCase):
    def _host(self):
        sent = []
        s = SimpleNamespace(cid=1, device="d1", state="ready", pub=b"p")

        async def send_app(sess, obj):
            sent.append(obj)
            return True
        st = SimpleNamespace(log=lambda *a, **k: None, is_allowed=lambda pub: True)
        host = SimpleNamespace(st=st, sessions={1: s}, send_app=send_app, telegram=None, media=None)
        return host, s, sent

    def test_tg_forward_that_raises_still_answers(self):
        host, s, sent = self._host()

        async def boom(*a):
            raise RuntimeError("unexpected")
        host.telegram = SimpleNamespace(forward_media=boom, forward=boom)
        asyncio.run(serve.Host.on_tg_fwd(host, s, "r1", None, "A" * 22))
        self.assertEqual(sent, [{"t": "tg_fwd_res", "r": "r1", "ok": False, "why": "send_failed"}])

    def test_download_that_raises_says_failed(self):
        host, s, sent = self._host()
        host.media = SimpleNamespace(window=mock.Mock(side_effect=KeyError("root")))
        asyncio.run(serve.Host._media_window(host, s, "B" * 22, 0))
        self.assertEqual(sent, [{"t": "media_err", "mid": "B" * 22, "why": "failed"}])


class FriendsSkill(unittest.TestCase):
    def test_skill_says_what_a_leak_is_in_both_languages(self):
        text = (AJ / "agentj" / "skills" / "agentj-friends" / "SKILL.md").read_text(encoding="utf-8")
        for needle in ("什么算泄密", "What counts as a leak", "环境变量名", "environment-variable names", "$VAR",
                       "第几行第几列", "line and column"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
