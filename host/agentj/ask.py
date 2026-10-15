"""P322: `agentj ask` — the Agent J question card (PROTOCOL §10.7) for any harness.

Claude Code has AskUserQuestion and Codex has `request_user_input` (refused outside Plan mode), others have nothing the
phone can show. This command puts the same signed card on the paired phones from a plain shell call: single choice,
multiple choice, an "Other" choice (the owner then types in the chat), an open question (no options) and a short preview
under an option. The signed protocol is unchanged — the phone still signs option numbers only; typed text arrives as the
owner's next chat message.

The card lives in serve, not in this process: if the shell call is cut off (a harness timeout), the card stays on the
phone and the answer is handed to the Agent as an 〔Agent J 系统提示〕 turn, or picked up with `agentj ask --wait <id>`.
Nothing here claims "shown" unless serve put the card on the phones."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

WAIT_MAX = 170          # under serve's Q_TTL (180 s): one call can see the whole life of a card
KEEP_S = 1800           # finished results kept for `--wait <id>`
KEEP_MAX = 32

OTHER = {"zh": "其他（我在聊天框里写）", "en": "Other (I'll type it in the chat)"}
OPEN = {"zh": "好，我在聊天框里回答", "en": "OK, I'll answer in the chat"}

NOTE = {
    "answered": {"zh": "主人已在手机上作答，按上面的答案继续。",
                 "en": "The owner answered on the phone; carry on with the answers above."},
    "typed": {"zh": "主人选择在聊天框里写答案：现在结束这一轮，等主人的下一条消息，不要替主人填写。",
              "en": "The owner will type the answer in the chat: end this turn now and wait for their next message; never "
                    "fill it in for them."},
    "cancelled": {"zh": "主人在手机上取消了这张卡片，没有选择。不要替主人选，停下来等他直接输入文字。",
                  "en": "The owner cancelled the card without choosing. Do not choose for them; stop and wait for their words."},
    "pending": {"zh": "卡片已在手机上，主人还没作答。不要说主人已回答；主人作答后答案会以〔Agent J 系统提示〕送到你这里，"
                      "也可以运行 agentj ask --wait {id} 继续等。",
                "en": "The card is on the phone and not answered yet. Do not say the owner answered; their answer reaches you "
                      "as an [Agent J system notice], or run agentj ask --wait {id} to keep waiting."},
    "timeout": {"zh": "没有人作答，卡片已关闭。改用文字向主人列出选项。",
                "en": "Nobody answered and the card closed. List the options to the owner in text instead."},
    "no_device": {"zh": "没有可作答的已配对遥控器（或卡片太多），卡片没有弹出。不要说已弹出；改用文字提问。",
                  "en": "No paired remote can answer (or too many cards are open): no card was shown. Do not say it was "
                        "shown; ask in text."},
    "gone": {"zh": "卡片在作答前结束了（Agent J 正在停止或重启）。不要说已弹出或已回答；改用文字提问。",
             "en": "The card ended before an answer (Agent J is stopping or restarting). Do not say it was shown or answered; "
                   "ask in text."},
    "stopped": {"zh": "主人按了紧急停止，卡片没有弹出。不要说已弹出；等主人恢复。",
                "en": "The owner pressed emergency stop: no card was shown. Do not say it was shown; wait for the owner."},
    "unknown": {"zh": "找不到这张卡片（已过期或 Agent J 重启过）。不要说已回答；需要的话重新运行 agentj ask。",
                "en": "No such card (expired, or Agent J restarted). Do not say it was answered; run agentj ask again if needed."},
    "not_running": {"zh": "Agent J 没有在运行，卡片没有弹出。不要说已弹出；改用文字提问。",
                    "en": "Agent J is not running: no card was shown. Do not say it was shown; ask in text."},
    "bad": {"zh": "卡片格式不对，没有弹出：{detail}。改正后重试，或改用文字提问。",
            "en": "The card is malformed and was not shown: {detail}. Fix it and retry, or ask in text."},
}

EXIT = {"answered": 0, "cancelled": 3, "pending": 4, "bad": 2, "not_running": 1}


class AskError(ValueError):
    pass


def _lang(lang) -> str:
    return "en" if lang == "en" else "zh"


def build(questions, lang: str = "zh") -> tuple[list, list]:
    """AskUserQuestion-shaped questions → (the §10.7 card, meta per question). Accepted per question: `question`,
    `header`, `multiSelect`, `options` [{label, description, preview}] (none / empty = an open question), `other`
    (default true when there are options: adds the "type it in the chat" choice). A preview is shown under its option's
    description. Raises AskError naming what is wrong — the card is never truncated (the signature binds the exact text)."""
    from .approvals import Q_MAX_OPTS, Q_MAX_QS, norm_questions
    lang = _lang(lang)
    if isinstance(questions, dict):
        questions = [questions]
    if not isinstance(questions, list) or not 1 <= len(questions) <= Q_MAX_QS:
        raise AskError(f"1–{Q_MAX_QS} questions")
    raw, meta = [], []
    for i, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            raise AskError(f"question {i} is not an object")
        opts = q.get("options") or []
        if not isinstance(opts, list):
            raise AskError(f"question {i}: options must be a list")
        out = []
        for o in opts:
            if isinstance(o, str):
                o = {"label": o}
            if not isinstance(o, dict):
                raise AskError(f"question {i}: an option is not an object")
            d, pv = o.get("description") or "", o.get("preview") or ""
            if not isinstance(d, str) or not isinstance(pv, str):
                raise AskError(f"question {i}: description / preview must be text")
            out.append({"label": o.get("label"), "description": (d + "\n" + pv if d and pv else d or pv)})
        is_open = not out
        other = None
        if is_open:
            out = [{"label": OPEN[lang], "description": ""}]
        elif q.get("other", True) is not False:
            other = len(out) + 1
            out.append({"label": OTHER[lang], "description": ""})
        if len(out) > Q_MAX_OPTS:
            raise AskError(f"question {i}: at most {Q_MAX_OPTS - (other is not None)} options")
        raw.append({"question": q.get("question"), "header": q.get("header") or "",
                    "multiSelect": q.get("multiSelect") is True and not is_open, "options": out})
        meta.append({"open": is_open, "other": other})
    card = norm_questions(raw)
    if card is None:
        raise AskError("each question needs text (≤ 1000), a header ≤ 64, distinct option labels (≤ 200) and "
                       "descriptions + preview ≤ 500, no control characters, no duplicate questions")
    return card, meta


def answers(card: list, meta: list, picks) -> list:
    """Option numbers (from the signed answer) → what the Agent reads: per question the picked labels (from the card
    itself) and whether the owner will type the answer in the chat."""
    res = []
    for q, m, p in zip(card, meta, picks or []):
        typed = m["open"] or (m["other"] is not None and m["other"] in p)
        labels = [q["o"][n - 1]["l"] for n in p if not (m["other"] is not None and n == m["other"]) and not m["open"]]
        res.append({"question": q["q"], "header": q.get("h") or "", "picked": labels, "typed_in_chat": typed})
    return res


def note(result: str, lang: str, typed: bool = False, **kw) -> str:
    key = "typed" if result == "answered" and typed else result
    return NOTE.get(key, NOTE["unknown"])[_lang(lang)].format(**kw)


def notice_text(rec: dict, lang: str) -> str:
    """The host turn that hands a late answer to the Agent (nobody was waiting on `agentj ask` any more)."""
    zh = _lang(lang) == "zh"
    lines = ["〔Agent J 系统提示〕" if zh else "[Agent J system notice]",
             (f"主人回答了你用 agentj ask 弹出的卡片（{rec['id']}）：" if zh else
              f"The owner answered the card you showed with agentj ask ({rec['id']}):")]
    if rec["result"] != "answered":
        lines[1] = (f"你用 agentj ask 弹出的卡片（{rec['id']}）已结束：" if zh else
                    f"The card you showed with agentj ask ({rec['id']}) ended:")
    for a in rec.get("answers") or []:
        parts = list(a["picked"]) + ([("会在聊天框里写" if zh else "will type it in the chat")] if a["typed_in_chat"] else [])
        pick = ("、" if zh else ", ").join(parts)
        lines.append(f"- {a['question']} → {pick}")
    lines.append(rec["note"])
    return "\n".join(lines)


class CliAsks:
    """serve's side: the cards opened by `agentj ask`, their results, and hand-over of a late answer as a host turn."""

    def __init__(self, host):
        self.host = host
        self.items: dict[str, dict] = {}

    def _sweep(self) -> None:
        now = time.monotonic()
        done = [k for k, r in self.items.items() if r["task"].done() and now - r.get("ended", now) > KEEP_S]
        for k in done:
            self.items.pop(k, None)
        while len(self.items) > KEEP_MAX:
            old = next((k for k, r in self.items.items() if r["task"].done()), None)
            if old is None:
                break
            self.items.pop(old)

    async def open(self, req: dict) -> dict:
        h = self.host
        try:
            card, meta = build(req.get("questions"), h.lang)
        except AskError as e:
            return self.view(None, "bad", detail=str(e))
        self._sweep()
        rec = {"card": card, "meta": meta, "waiters": 0, "delivered": False}
        before = set(h.questions)               # the card's id is serve's own (Question.qid): the one that appears now
        rec["task"] = asyncio.get_running_loop().create_task(h.question(card, None, task=getattr(h, "task_label", None)))
        await asyncio.sleep(0)                   # question() runs up to its first await: the card is posted or refused
        new = [k for k in h.questions if k not in before]
        qid = new[0] if new else None
        if rec["task"].done() or qid is None:
            decision, _ = await rec["task"]
            return self.view(None, decision if decision in NOTE else "gone")
        rec["id"] = qid
        self.items[qid] = rec
        rec["task"].add_done_callback(lambda t, qid=qid: self._finished(qid))
        return await self.wait({"id": qid, "wait": req.get("wait")})

    async def wait(self, req: dict) -> dict:
        qid = req.get("id")
        rec = self.items.get(qid) if isinstance(qid, str) else None
        if rec is None:
            return self.view(None, "unknown")
        wait = req.get("wait")
        wait = min(max(float(wait), 0.0), WAIT_MAX) if isinstance(wait, (int, float)) and not isinstance(wait, bool) \
            else WAIT_MAX
        if not rec["task"].done():
            rec["waiters"] += 1
            try:
                await asyncio.wait({asyncio.shield(rec["task"])}, timeout=wait)
            finally:
                rec["waiters"] -= 1
        if not rec["task"].done():
            return self.view(qid, "pending")
        rec["delivered"] = True
        return self.result(qid, rec)

    def result(self, qid: str, rec: dict) -> dict:
        try:
            decision, picks = rec["task"].result()
        except Exception:  # noqa: BLE001 — fail closed: nobody answered
            decision, picks = "gone", None
        # serve's decisions: answer | cancel | timeout | gone | stopped | no_device
        decision = "answered" if decision == "answer" and picks else "cancelled" if decision == "cancel" else decision
        if decision not in NOTE or decision == "answer":
            decision = "gone"
        ans = answers(rec["card"], rec["meta"], picks) if decision == "answered" else []
        return self.view(qid, decision, answers=ans)

    def view(self, qid, result: str, answers: list | None = None, detail: str = "") -> dict:
        lang = getattr(self.host, "lang", "zh")
        typed = any(a["typed_in_chat"] for a in answers or [])
        out = {"ok": result in ("answered", "cancelled", "pending"), "result": result,
               "shown": result in ("answered", "cancelled", "pending", "timeout")}
        if qid:
            out["id"] = qid
        if answers:
            out["answers"] = answers
        out["note"] = note(result, lang, typed, id=qid or "", detail=detail)
        return out

    def _finished(self, qid: str) -> None:
        rec = self.items.get(qid)
        if rec is None:
            return
        rec["ended"] = time.monotonic()
        if rec["waiters"] or rec["delivered"]:
            return
        # nobody is waiting on `agentj ask` any more (the shell call was cut off): hand the outcome to the Agent
        loop = asyncio.get_running_loop()
        loop.call_soon(self._hand_over, qid)

    def _hand_over(self, qid: str) -> None:
        rec = self.items.get(qid)
        h = self.host
        if rec is None or rec["waiters"] or rec["delivered"]:
            return
        res = self.result(qid, rec)
        rec["delivered"] = True
        if res["result"] not in ("answered", "cancelled", "timeout") or not getattr(h, "agent", None):
            return
        from .compose import Send
        from .provenance import system
        try:
            h.agent.submit(Send("agentj-ask", qid, 0, text=notice_text(res, h.lang), by="Agent J", source=system()))
            h.st.log("ask_handover", id=qid, result=res["result"])
        except Exception:  # noqa: BLE001 — the result stays for `agentj ask --wait`
            rec["delivered"] = False


# ---------------------------------------------------------------- CLI
def _questions(a) -> list:
    if a.json is not None:
        try:
            text = sys.stdin.read() if a.json == "-" else open(a.json, encoding="utf-8").read()
            data = json.loads(text)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise AskError(f"--json: {type(e).__name__}") from None
        return data.get("questions") if isinstance(data, dict) and "questions" in data else data
    if not a.question:
        raise AskError("a question (or --json)")
    opts = [{"label": x} for x in a.option or []]
    for i, pv in enumerate(a.preview or []):
        if i < len(opts):
            opts[i]["preview"] = pv
    return [{"question": a.question, "header": a.header or "", "multiSelect": bool(a.multi), "options": opts,
             "other": not a.no_other}]


def cmd_ask(a) -> None:
    from .names import ServeBusy, ctl_call
    from .state import State
    st = State()
    lang = "zh"
    try:
        from . import main_identity
        lang = main_identity.language_of(st.agent_config() or {})
    except Exception:  # noqa: BLE001
        pass
    wait = min(max(a.wait_s, 0), WAIT_MAX)
    try:
        if a.wait_id:
            req = {"cmd": "ask_wait", "id": a.wait_id, "wait": wait}
        else:
            req = {"cmd": "ask_open", "questions": _questions(a), "wait": wait}
        res = ctl_call(st, req, timeout=wait + 20, max_bytes=256 * 1024)
    except AskError as e:
        res = {"ok": False, "result": "bad", "shown": False, "note": note("bad", lang, detail=str(e))}
    except ServeBusy:
        res = None
    if res is None:
        res = {"ok": False, "result": "not_running", "shown": False, "note": note("not_running", lang)}
    print(json.dumps(res, ensure_ascii=False, indent=None if a.compact else 2))
    sys.exit(EXIT.get(res.get("result"), 5))


def add_parser(sub) -> None:
    p = sub.add_parser("ask", help="在手机上弹 Agent J 选择卡片并等主人作答（任何 harness 都能用）/ show an Agent J question card "
                                   "on the phone and wait for the owner's answer (works under any harness)",
                       description="单选、多选、「其他（聊天框里写）」、开放题、选项预览。输出 JSON：result = answered | cancelled | "
                                   "pending | timeout | no_device | gone | stopped | not_running | bad；只有 shown=true 才算弹出。"
                                   " / Single or multiple choice, an Other choice, open questions, option previews. Prints JSON; "
                                   "only shown=true means the card was on the phone. Exit 0 answered, 3 cancelled, 4 pending, "
                                   "5 not shown / timed out, 2 bad card, 1 Agent J not running.")
    p.add_argument("question", nargs="?", help="问题 / the question")
    p.add_argument("-o", "--option", action="append", metavar="LABEL", help="选项（可重复；不给 = 开放题）/ an option "
                   "(repeat; none = an open question)")
    p.add_argument("--preview", action="append", metavar="TEXT", help="按顺序给选项附预览 / a preview for the option in the "
                   "same position")
    p.add_argument("--header", help="短标题 ≤ 64 / a short header")
    p.add_argument("--multi", action="store_true", help="多选 / multiple choice")
    p.add_argument("--no-other", action="store_true", help="不加「其他（聊天框里写）」/ no Other choice")
    p.add_argument("--json", metavar="FILE|-", help="AskUserQuestion 形状的 questions（文件或 - = stdin），可多题 / "
                   "AskUserQuestion-shaped questions (file or - for stdin), several questions")
    p.add_argument("--wait", dest="wait_id", metavar="ID", help="继续等一张已弹出的卡片 / keep waiting on a card already shown")
    p.add_argument("--wait-s", type=float, default=WAIT_MAX, help=f"最多等几秒（≤ {WAIT_MAX}）/ seconds to wait")
    p.add_argument("--compact", action="store_true", help="单行 JSON / one-line JSON")
    p.set_defaults(fn=cmd_ask)
