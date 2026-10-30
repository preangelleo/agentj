"""P539 (#299): a model's safety flag must never strand the owner on the phone.

Claude Code (probed 2.1.296) has two owner-facing steps when a model's safeguards flag a message:
1. the first time (no stored preference) it asks through canUseTool → AskUserQuestion, `metadata.source =
   "refusal_fallback_preference"` (required: the only part a model cannot write itself), header "Model switch", options "Switch automatically" / "Stay on <Model>". Claude Code
   matches the answer to the option label exactly, so the phone card is rewritten in the owner's language with the
   consequence of each choice and the pick is mapped BACK to the original English label (`localize`). "Stay" makes Claude
   Code put the prompt back ("edit_prompt") and end the turn: the owner's own words go back to the phone composer
   (`draft_restore`) with a notice (`after_stay`), so the phone never waits on a turn that will not continue.
2. later flags (preference "ask") open the native "Session paused" modal — shared_menu reads it and offers four choices
   (`paused_question`).

The draft text is the owner's own phone message; it only travels inside the paired Noise channel and is never logged.
"""
from __future__ import annotations

import re

from .text import pick

SOURCE = "refusal_fallback_preference"
HEADER = "Model switch"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .\-]{0,39}")
DRAFT_MAX = 20000


def _name(s) -> str | None:
    s = s.strip() if isinstance(s, str) else ""
    return s if NAME.fullmatch(s) else None


def first_flag(tool_input) -> dict | None:
    """The first-flag preference question → {"q", "switch", "stay", "model", "fallback"} (original texts), else None."""
    if not isinstance(tool_input, dict):
        return None
    qs = tool_input.get("questions")
    if not isinstance(qs, list) or len(qs) != 1 or not isinstance(qs[0], dict):
        return None
    q = qs[0]
    meta = tool_input.get("metadata") if isinstance(tool_input.get("metadata"), dict) else {}
    opts = q.get("options") if isinstance(q.get("options"), list) else []
    labels = [o.get("label") for o in opts if isinstance(o, dict)]
    if len(labels) != 2 or not all(isinstance(x, str) for x in labels) or not isinstance(q.get("question"), str):
        return None
    stay = [x for x in labels if x.startswith("Stay on ")]
    if len(stay) != 1:
        return None
    if meta.get("source") != SOURCE:
        # L1 (P539 review): Claude Code's own marker is required — a header, a "Stay on …" label or a question text are
        # words any model can write in its own AskUserQuestion; without the marker it is shown as the question it is.
        return None
    switch = next(x for x in labels if x != stay[0])
    model = _name(stay[0][len("Stay on "):])
    m = re.search(r"Switch to (.+?) and keep going", q["question"])
    fallback = _name(m.group(1)) if m else None
    if fallback is None:
        d = next((o.get("description") for o in opts if isinstance(o, dict) and o.get("label") == switch), "")
        m = re.match(r"Continue on (.+?) now", d if isinstance(d, str) else "")
        fallback = _name(m.group(1)) if m else None
    return {"q": q["question"], "switch": switch, "stay": stay[0], "model": model or "Claude",
            "fallback": fallback}


def localize(tool_input, lang: str):
    """(questions for the phone card, back(answers) → answers for Claude Code, info) — or None when this is not the
    first-flag question (every other AskUserQuestion is shown as it is)."""
    info = first_flag(tool_input)
    if info is None:
        return None
    m, f = info["model"], info["fallback"] or pick(lang, "备用模型", "the fallback model")
    q = pick(lang, f"{m} 的安全检查拦下了这条消息（偶尔会误拦正常的对话），这一轮停住了，没有丢东西。以后再遇到这种情况怎么办？",
             f"{m}'s safety check flagged this message (it sometimes flags normal conversations). This turn is paused; "
             "nothing was lost. What should happen when this comes up?")
    sw = pick(lang, f"换到 {f} 继续（以后再被拦也自动换，不再问）", f"Switch to {f} and continue (switch automatically from now on)")
    sw_d = pick(lang, f"现在就用 {f} 接着回答；以后再被拦时自动换，不再问你。可在 Agent J 主机上 Claude Code 的 /config 里改。",
                f"{f} answers this now, and later flags switch without asking. You can change this in Claude Code's /config.")
    st = pick(lang, f"留在 {m}", f"Stay on {m}")
    st_d = pick(lang, f"这一轮不会自动继续：你的原话会放回手机输入框，改一改或原样再发都行。以后再被拦，手机上会给你：换模型继续 / 原话重试 / 改一改再发 / 开新对话。",
                f"This turn will not continue by itself: your words go back into the phone's message box to edit or resend. "
                "Later flags give you on the phone: switch model / retry as is / edit and resend / new conversation.")
    card_q = {"question": q, "header": pick(lang, "换模型", "Model switch"), "multiSelect": False,
              "options": [{"label": sw, "description": sw_d}, {"label": st, "description": st_d}]}
    to_orig = {sw: info["switch"], st: info["stay"]}

    def back(answers: dict) -> dict:
        v = answers.get(q) if isinstance(answers, dict) else None
        return {info["q"]: to_orig.get(v, v) if isinstance(v, str) else v}

    return [card_q], back, {**info, "local_stay": st, "local_switch": sw}


def stayed(answers: dict, info: dict) -> bool:
    """The mapped answers picked "Stay on <Model>"."""
    return isinstance(answers, dict) and answers.get(info["q"]) == info["stay"]


# ------------------------------------------------------------------ the owner's words back to the phone composer
def phone_draft(agent, host) -> tuple:
    """(device | None, text) of the owner's phone message the flagged turn carried; (None, "") when unknown (a desktop
    prompt, a host / group / delegated turn, no history)."""
    from . import provenance
    send = getattr(agent, "last_phone_prompt", None)
    if send is None and getattr(agent, "phone_turn", True):
        send = getattr(agent, "cur_send", None)
    if send is None or not provenance.is_owner(getattr(send, "source", None)):
        return None, ""
    turn = getattr(send, "turn", None)
    turns = getattr(getattr(host, "hist", None), "turns", None)
    page = turns.get(turn) if isinstance(turns, dict) and isinstance(turn, int) and turn > 0 else None
    src = page.get("src") if isinstance(page, dict) and isinstance(page.get("src"), dict) else {}
    text = src.get("text") if src.get("k") == "phone" and isinstance(src.get("text"), str) else ""
    return (getattr(send, "device", None) if text else None), text[:DRAFT_MAX]


def draft_restore(host, device, text: str) -> int:
    """Host → phone app message {"t": "draft_restore", "text"} to the ready sessions of `device` (all paired ready
    sessions when unknown). Returns how many got it queued. Never logs the text."""
    obj = {"t": "draft_restore", "text": text if isinstance(text, str) else ""}
    st, post, send = getattr(host, "st", None), getattr(host, "_post", None), getattr(host, "send_app", None)
    if st is None or post is None or send is None:
        return 0
    targets = [s for s in list((getattr(host, "sessions", None) or {}).values())
               if getattr(s, "state", None) == "ready" and getattr(s, "p33", False) and st.is_allowed(s.pub)
               and (device is None or s.device == device)]
    if not targets and device is not None:
        return draft_restore(host, None, text)
    for s in targets:
        post(send, s, obj)
    st.log("draft_restore", n=len(targets), has_text=bool(obj["text"]))
    return len(targets)


def restore_note(lang: str, has: bool) -> str:
    return (pick(lang, "你的原话已放回手机输入框，改好再发就行。", "Your words are back in the phone's message box; edit and send.")
            if has else pick(lang, "这条消息没法自动放回输入框，请重新输入一遍再发。",
                             "This message could not be put back in the message box; please type it again."))


def after_stay(host, agent, info: dict) -> None:
    """The owner picked "Stay" on the first-flag card: Claude Code ends the turn — the phone gets its words back and a
    notice, and stops waiting for a reply that will not come."""
    lang = getattr(host, "lang", "zh")
    device, text = phone_draft(agent, host)
    n = draft_restore(host, device, text)
    note = pick(lang, f"已留在 {info['model']}，这一轮停下了（没有丢东西）。", f"Stayed on {info['model']}; this turn stopped (nothing was lost). ")
    host.agent_notice(note + restore_note(lang, bool(text) and n > 0)
                      + pick(lang, "想换模型可以点顶部的模型名。", " To switch model, tap the model name at the top."))
    if getattr(agent, "phone_turn", False) and getattr(agent, "done", None) is not None:
        agent.phone_reply_seen = True
        agent.done.set()


# ------------------------------------------------------------------ the native "Session paused" modal (shared_menu)
PAUSED_ACTIONS = ("switch", "retry", "edit", "new")


def paused_question(info: dict, lang: str) -> dict:
    m, f = info.get("model") or "Claude", info.get("fallback") or pick(lang, "备用模型", "the fallback model")
    q = pick(lang, f"{m} 的安全检查拦下了这一轮（偶尔会误拦正常的对话），Claude Code 停下来等你选择。对话没有丢，选一个继续：",
             f"{m}'s safety check stopped this turn (it sometimes flags normal conversations). Claude Code is waiting for "
             "your choice; nothing was lost. Pick one:")
    o = [(pick(lang, f"换到 {f} 继续这一轮", f"Continue this turn on {f}"),
          pick(lang, f"改用 {f} 接着回答；这段对话之后也用它（想换回点顶部模型名）",
               f"{f} answers it, and this conversation keeps using it (tap the model name to switch back)")),
         (pick(lang, f"用 {m} 原话重试这一轮", f"Retry this turn on {m} as is"),
          pick(lang, f"把同一句话再发给 {m} 一次；可能还会被拦", f"Sends the same words to {m} again; it may be flagged again")),
         (pick(lang, "改一改再发", "Edit and resend"),
          pick(lang, "取消这一轮，你的原话放回手机输入框，改好再发", "Cancels this turn and puts your words back in the phone's message box")),
         (pick(lang, "开新对话", "New conversation"),
          pick(lang, "取消这一轮，清空对话重新开始（之前的内容不再带上）", "Cancels this turn and starts a fresh conversation (earlier context is dropped)"))]
    return {"q": q, "h": "Claude Code", "m": False, "o": [{"l": a, "d": b} for a, b in o]}


def paused_done(action: str, info: dict, lang: str, extra: str = "") -> str:
    m, f = info.get("model") or "Claude", info.get("fallback") or pick(lang, "备用模型", "the fallback model")
    return {"switch": pick(lang, f"已换到 {f} 继续这一轮。", f"Switched to {f}; this turn continues."),
            "retry": pick(lang, f"已让 {m} 用原话重试这一轮。", f"Retrying this turn on {m} with the same words."),
            "edit": pick(lang, "这一轮已取消。", "This turn was cancelled. ") + extra,
            "new": pick(lang, "这一轮已取消，正在开新对话。", "This turn was cancelled; starting a new conversation."),
            }[action]
