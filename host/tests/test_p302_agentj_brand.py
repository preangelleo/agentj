"""P302 追加 27 (owner 2026-10-15: 「Agent 是通用称呼，AgentJ 是咱们的内部称呼……自动发出来的消息……都改成 AgentJ」):
system messages, status lines and notices that mean OUR main agent say 「Agent J」, never a bare 「Agent」. Generic uses
(子 Agent, 后台 Agent, Agent 好友 / Agent friends, Agent ID, the Agent fence) stay as they are."""
import _hermetic  # noqa: F401,I001
import json
import re
import unittest
from pathlib import Path

from agentj import auth_rescue, auto_update, handoff, mail, tg_media  # noqa: E402

# a bare 「Agent」: not part of 「Agent J」, a word (Agents, agentj), 「Agent ID」, 「Agent 好友」 or the generic 子 / 后台 Agent
BARE = re.compile(r"(?<![A-Za-z\-])(?<!子 )(?<!台 )(?<!子)(?<!台)Agent(?![ \-]?J\b|[A-Za-z]|\s*ID|\s*好友|\s*friends?)")
WEB = Path(__file__).resolve().parents[2] / "web/i18n"
# phone strings that name the main agent (a new one belongs here when it is written)
WEB_KEYS = ("r.agent.tip", "upg.k.turn", "media.unsent", "p207set.ct.note", "r.wr.hint", "r.wr.hintFrom",
            "p238.sent", "p244.fivepages35", "p244.fivepages37", "p299.useAny", "fr.q.send", "fr.q.auto",
            "fr.ask.done.pq_allow", "estop.bannerNote", "estop.confirmText", "estop.resumeText", "push.offer",
            "cmd.clearText", "mem.noAgent", "tasks.noAgent", "act.result.agent_gone", "act.result.policy",
            "r.usage.none", "r.pill.unsupported", "r.pill.queued", "r.pill.why.busy", "r.emptyReady",
            "r.q.cancelAria", "r.voice.asAttNoAsr", "r.say.stopped", "r.say.withdrawn", "r.say.failedLater",
            "r.say.err.seat", "r.queue.note", "preferences.notification", "elev.sudo.title", "elev.r.gone",
            "media.skip.secret", "media.skip.key_file", "media.skip.key_content", "lt.timeUp", "lt.done.needs",
            "lt.done.blocked", "lt.why.expired", "lt.why.changed", "lqr.expiredNote", "setup.needsSetup",
            "p181.sent.theme", "p181.sent.cmds", "mail.today", "mail.checkOk", "fr.ask.sent.tell")


def flat(d, pre=""):
    """the dictionaries nest ("estop": {"bannerNote": …}) and also carry dotted keys; → {"estop.bannerNote": …}"""
    out = {}
    for k, v in d.items():
        kk = pre + "." + k if pre else k
        if isinstance(v, dict):
            out.update(flat(v, kk))
        else:
            out[kk] = v
    return out


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from strings(v)


class AgentJBrand(unittest.TestCase):
    def assertBranded(self, texts, where):
        bad = [t for t in texts if BARE.search(t)]
        self.assertEqual(bad, [], where)

    def test_compaction_and_clear_lines(self):
        # P290 merge: P280's humane.json speaks in the first person (no bare "Agent" left to brand)
        self.assertEqual(handoff.phone_line("zh", "compact"), "压缩好了，我在翻交接笔记，马上接上思路。")
        self.assertEqual(handoff.phone_line("en", "compact"), "Compacted. I'm reading my handover notes to pick up the thread.")
        self.assertBranded([handoff.phone_line(lang, k) for lang in ("zh", "en") for k in ("compact", "clear")], "handoff")

    def test_host_message_tables(self):
        for name, table in (("mail.T", mail.T), ("tg_media.SKIP", tg_media.SKIP), ("auth_rescue.TEXT", auth_rescue.TEXT),
                            ("auto_update.IMPACT_TEXT", auto_update.IMPACT_TEXT)):
            self.assertBranded(list(strings(table)), name)

    def test_phone_strings(self):
        for f in ("web.zh.src.json", "web.zh.json", "web.zh.override.json", "web.en.json"):
            d = flat(json.loads((WEB / f).read_text(encoding="utf-8")))
            if f != "web.zh.override.json":   # the override file only carries polished exceptions
                self.assertEqual([k for k in WEB_KEYS if k not in d], [], f + ": every listed key exists")
            self.assertBranded([d[k] for k in WEB_KEYS if isinstance(d.get(k), str)], f)

    def test_generic_uses_stay(self):
        for ok in ("Claude Code 子 Agent", "后台 Agent", "Agent 好友", "我的 Agent ID", "Agent J"):
            self.assertIsNone(BARE.search(ok), ok)
        self.assertIsNotNone(BARE.search("压缩完成，Agent 正在恢复上下文。"))


if __name__ == "__main__":
    unittest.main()
