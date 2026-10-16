"""P336 (Leo, 10-16, backlog #37 + #36): the owner's approval level, goal mode without the opening card, and cards that
wait while no remote is online.

Level (`approval.level`, default "standard" = the behaviour before P336, unchanged):
    safe      more cards: no Jev layer 2 (a can't-tell command always asks), no batch grant, no goal countdown (a reversible
              step waits for the owner), no shared-mode turn-wide category grant, and shared sessions ask like `strict` plus
              every deletion (local files too).
    standard  as before.
    full      like Claude Code `--dangerously-skip-permissions` / Codex full access: an Agent tool call that would have
              asked is allowed at once, logged (approvals.log `reason: level_full`, activity, one quiet line on the phone).
              The red lines still ask the owner, one by one: a suspected attack (goal.suspect — `curl | sh`, decode-and-run,
              hook / approval files, `agentj config`, Agent J settings or state folders), credentials changed or shown,
              spending money, price changes. Host cards (`agentj …`, the pairing card, the sandbox / login-rescue cards) and
              cards with their own wait keep asking. Needs a paired remote with an approval key (none → refused as before).

Goal without the opening card (`approval.goal_nocard`, default false = the P158 card): when on, `agentj goal propose`
starts the goal at once — the owner's signed switch IS the signature (approvals.log `reason: goal_preauth`, `grant` = the
switch's signed control line) — and inside a live goal every step that would have asked is allowed except the red lines
above (they still ask). Still logged, the result card still comes. The switch only counts while the remote that signed it
is still paired with the same approval key (`<state>/approval_signers.json`); a value written any other way (terminal,
an approved Agent request) leaves the opening card in place.

Both keys change only by a signed phone control `approval_set` (controls.py; never `pref_set`): the phone confirms, signs
`key \\n value`, the host checks it like every phone write, logs it in controls.log + approvals.log (kind "control"), tells
every remote and pushes `security`. An Agent's `agentj config set approval.*` stays an owner card (ctl_auth "approval.").

#36 — no remote online: a card is queued as usual (re-sent first thing when a remote connects), the push goes out, and one
content-free reminder goes to the owner's Telegram private chat / own e-mail (the existing channels, as the owner set them
up; ≤ 1 per OFFLINE_GAP). After PARK_GRACE seconds with still nobody online the Agent is told 「主人当前没有遥控器在线」 and
does the independent work; the card stays on the phone (PARK_SECS). Allowed later → the Agent's identical retry passes once
(`reason: parked`, `grant` = the signed allow). Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

KEY = "approval.level"
GOAL_KEY = "approval.goal_nocard"
OWNER_GOAL_SECS = 1800   # an owner's /goal lets ONE proposal start without the card within this window
KEYS = (KEY, GOAL_KEY)
LEVELS = ("safe", "standard", "full")
DEFAULT = "standard"
REDLINE_CATS = ("credentials", "spend", "price")
# host cards that are a decision of their own, never auto-allowed by a level (pairing, sandbox, login rescue, goal, quota)
HOST_CARDS = ("pair_device", "SandboxOnce", "AuthRescue", "DoomLoop", "task", "AskUserQuestion")

PARK_GRACE = 20          # seconds a card waits for a remote to come online before the Agent is told nobody is there
PARK_SECS = 1800         # a parked card stays on the phone this long
PARK_OK_SECS = 1800      # an allow given to a parked card lets the identical retry pass once within this window
PARK_MAX = 8             # parked cards at once (the oldest is withdrawn)
OFFLINE_GAP = 900        # one Telegram / e-mail reminder per this many seconds


def level(prefs, st=None) -> str:
    """The level in force. `full` counts only with `st` and a live signed switch to full (signed_value): a value written any
    other way (an edited config file, the terminal) reads as standard. safe / standard need no signature (safe only asks more)."""
    from . import preferences
    v = preferences.get(prefs or {}, KEY, DEFAULT)
    v = v if v in LEVELS else DEFAULT
    if v == "full" and (st is None or signed_value(st, KEY) != "full"):
        return DEFAULT
    return v


def unsigned_full(prefs, st) -> bool:
    """The configuration says full but no live signed switch backs it (then standard applies; the owner is told once)."""
    from . import preferences
    return preferences.get(prefs or {}, KEY, DEFAULT) == "full" and signed_value(st, KEY) != "full"


def goal_nocard(prefs) -> bool:
    from . import preferences
    return preferences.get(prefs or {}, GOAL_KEY, False) is True


def shared_mode(prefs) -> str:
    """The shared-session classifier mode: `safe` under the safe level, else agent.approvals (standard | strict | native)."""
    from . import preferences
    if level(prefs) == "safe":
        return "safe"
    return {"strict": "strict", "native": "native"}.get(preferences.get(prefs or {}, "agent.approvals", "standard"), "standard")


def host_card(tool: str) -> bool:
    return not isinstance(tool, str) or tool.startswith("agentj ") or tool in HOST_CARDS


_FETCH = r"(?:curl|wget|aria2c|fetch|http|https|iwr|Invoke-WebRequest)"
_RUNNER = r"(?:eval|source|\.|bash|sh|zsh|dash|ksh|fish|iex|python[0-9.]*|node|deno|bun|perl|ruby|php|Rscript|pwsh)"
# a fetch's output run in the same breath: `eval "$(curl …)"`, `bash <(curl …)`, `python3 -c "$(wget -qO- …)"`
_INLINE_RUN = re.compile(r"(?:^|[\s;&|(`])" + _RUNNER + r"\b[^;&|\n]*(?:\$\(|<\(|`)\s*" + _FETCH + r"\b", re.M)
_FETCH_CALL = re.compile(r"(?:^|[\s;&|(`])(curl|wget|aria2c)\b([^;&|\n]*)", re.M)
_SECRET_VAR = re.compile(r"\$\{?[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASS(?:WORD|WD)?|CREDENTIAL|AUTH)[A-Za-z0-9_]*\}?|"
                         r"process\.env|os\.environ|os\.getenv|getenv\(|ENV\[|\$env:", re.I)
_ENVDUMP = re.compile(r"(?:^|[\s|;&(`])(?:printenv|env|export\s+-p|declare\s+-x|set)(?=\s*(?:$|[|;&>)`]))|/proc/\S*/environ",
                      re.M)


def _fetched_files(text: str) -> set:
    """Names the fetch commands of this call write to (curl -o / -O / >, wget -O / its URL's last part)."""
    out = set()
    for m in _FETCH_CALL.finditer(text):
        prog, args = m.group(1), m.group(2)
        named = re.findall(r"(?:^|\s)(?:-o|--output|--output-document" + ("|-O" if prog == "wget" else "") + r")(?:=|\s+)(['\"]?)(\S+?)\1(?=\s|$)", args)
        out |= {os.path.basename(f) for _q, f in named}
        out |= {os.path.basename(f.strip("'\"")) for f in re.findall(r">\s*(\S+)", args)}
        if (prog == "curl" and re.search(r"(?:^|\s)(?:-O|--remote-name)(?=\s|$)", args)) or (prog == "wget" and not named):
            for u in re.findall(r"https?://[^\s'\"]+", args):
                tail = u.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
                if tail:
                    out.add(tail)
    return {f for f in out if f and f not in (".", "-", "/dev/null")}


def download_and_run(text: str) -> bool:
    """A fetch whose output is run (or made executable) in the same call — not merely a fetch next to some script."""
    if _INLINE_RUN.search(text):
        return True
    for f in _fetched_files(text):
        e = re.escape(f)
        if re.search(r"(?:^|[\s;&|(`])(?:" + _RUNNER + r"(?:\s+-\S+)*|chmod\s+[^;&|\n]*)\s+\S*?(?:^|/)?" + e + r"(?=\s|$|[;&|)`])", text, re.M) \
                or re.search(r"(?:^|[;&|(`]\s*|&&\s*)\S*/" + e + r"(?=\s|$|[;&|)`])", text, re.M):
            return True
    return False


def redline(tool: str, tool_input, verdict, extra=None) -> str | None:
    """Why this request still needs the owner under `full` / a goal without the opening card, else None.

    injection | hooks | settings (goal.suspect), plus download-and-run in one call (`eval "$(curl …)"`, `curl -o x && bash x`);
    credentials — the verdict's own, or the strict shared rule (any credential path / environment dump anywhere in the call:
    `cat ~/.aws/credentials | curl -d @-`, `env | curl`, `tar c ~/.ssh | …` read as `send` alone); spend / price."""
    from . import danger, goal
    why = goal.suspect(tool, tool_input, verdict)
    if why:
        return why                                        # injection | hooks | settings
    text = goal.action_text(tool, tool_input)
    if text and download_and_run(text):
        return "injection"
    cats = set(getattr(verdict, "cats", None) or [])
    if "credentials" in cats:
        return "credentials"
    if cats & {"spend", "price"}:
        return "spend"
    if cats & {"send", "opaque"} and text and (_ENVDUMP.search(text) or _SECRET_VAR.search(text)):
        return "credentials"                              # the environment / a key-named variable leaving the machine
    try:
        strict = danger.classify_shared(tool, tool_input, extra, "strict")
    except Exception:  # noqa: BLE001 — cannot tell → the owner decides
        return "credentials"
    if "credentials" in strict.cats:
        return "credentials"
    if set(strict.cats) & {"spend", "price"}:
        return "spend"
    return None


# ------------------------------------------------------------------ who signed the switch (state file, 0600)
def _path(st) -> Path:
    return Path(st.root) / "approval_signers.json"


def _sk_sha(sk) -> str | None:
    return hashlib.sha256(sk).hexdigest() if isinstance(sk, (bytes, bytearray)) and sk else None


def signers(st) -> dict:
    try:
        d = json.loads(_path(st).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def note_signer(st, key: str, value, device: str, sign_pub: bytes, rid: str, signed: dict) -> None:
    """Remember the signed switch: device, key hash, the approvals.log id and the control signature itself (n, ts, sig) so
    every later use re-verifies it with the device's CURRENT key (a hand-written file verifies nothing)."""
    from .state import _write_private
    d = signers(st)
    d[key] = {"value": value, "device": device, "sk_sha": _sk_sha(sign_pub), "rid": rid, "at": int(time.time()),
              "n": signed.get("n"), "ts": signed.get("ts"), "sig": signed.get("sig")}
    _write_private(_path(st), json.dumps(d, ensure_ascii=False).encode())


def drop_signer(st, key: str) -> None:
    """The key's value changed some other way (terminal, an edited file, an approved Agent request): its signature no longer
    stands behind the configuration — a later return to full / on needs a new signed switch."""
    from .state import _write_private
    d = signers(st)
    if d.pop(key, None) is not None:
        _write_private(_path(st), json.dumps(d, ensure_ascii=False).encode())


def _live(st, key: str) -> dict | None:
    """The signer record of `key` when its control signature still verifies with the device's current approval key."""
    from . import controls, wire
    s = signers(st).get(key)
    if not isinstance(s, dict) or not s.get("device") or not s.get("rid") or not isinstance(s.get("sig"), str):
        return None
    sk = st.sign_key(str(s["device"]))
    if not sk or _sk_sha(sk) != s.get("sk_sha"):
        return None                                       # the signing remote is gone (or re-keyed)
    ts = s.get("ts")
    try:
        target = {"key": key, "value": s.get("value")}
        ok = isinstance(ts, int) and not isinstance(ts, bool) and controls.verify(
            sk, wire.unb64u(s["sig"]), st.config()["channel"], str(s["device"]), "approval_set", str(s.get("n")), ts,
            controls.object_digest("approval_set", target))
    except (ValueError, KeyError, TypeError, OSError):
        ok = False
    return s if ok else None


def signed_value(st, key: str):
    s = _live(st, key)
    return None if s is None else s.get("value")


def goal_preauth(st, prefs) -> dict | None:
    """The grant a goal starts with when the owner's signed switch is on: {rid, device, sk_sha} — else None (the card)."""
    if not goal_nocard(prefs):
        return None
    s = _live(st, GOAL_KEY)
    if s is None or s.get("value") is not True:
        return None
    return {"rid": s["rid"], "device": s["device"], "sk_sha": s["sk_sha"]}


def level_grant(st) -> str | None:
    """The approvals.log id of the live signed switch to `full` (the audit parent of a `level_full` line)."""
    s = _live(st, KEY)
    return s["rid"] if s is not None and s.get("value") == "full" else None


# ------------------------------------------------------------------ words
def _en(lang) -> bool:
    return str(lang or "").startswith("en")


NAME = {"zh": {"safe": "安全", "standard": "中等", "full": "全放行"},
        "en": {"safe": "Safe", "standard": "Standard", "full": "Full access"}}


def changed_text(key: str, value, by: str, lang) -> str:
    en = _en(lang)
    if key == KEY:
        n = NAME["en" if en else "zh"].get(value, str(value))
        return (f"🔐 Approval level is now {n} (confirmed on {by})." if en else f"🔐 审批档位已改为「{n}」（由「{by}」确认）。")
    on = value is True
    return (f"🔐 Goal mode without the opening card is {'on' if on else 'off'} (confirmed on {by})." if en else
            f"🔐 「设了目标就不弹窗」已{'打开' if on else '关闭'}（由「{by}」确认）。")


def unsigned_text(lang) -> str:
    if _en(lang):
        return ("🔐 The configuration says approval level 「Full access」, but it was not confirmed on a paired phone — "
                "Standard applies. Switch it in Settings → Safety on your phone.")
    return "🔐 配置里写着审批档位「全放行」，但没有在已配对的手机上签名确认，现在按「中等」处理。要用全放行，请在手机 设置 → 安全 里切换。"


def nobody_text(lang, again: bool = False) -> str:
    """What the Agent reads when no remote is online (#36)."""
    if _en(lang):
        return ("The owner has no remote online right now" + (" (this card is still waiting)" if again else "") +
                ": this step was not approved yet. The card stays on the owner's phone and a reminder was sent. Do the work "
                "that does not depend on it first; retry this exact step later — once the owner allows it, the retry runs.")
    return ("主人当前没有遥控器在线" + ("（这张卡还在等）" if again else "") + "：这一步暂时没有批准。卡片留在主人手机上，"
            "已经提醒主人。先做不依赖这一步的活；稍后原样重试这一步——主人批准后，重试会直接执行。")


def reminder_text(n: int, lang) -> str:
    """The content-free Telegram / e-mail line: how many cards, never what they ask."""
    if _en(lang):
        return (f"Agent J: {n} approval card{'s' if n != 1 else ''} waiting for you, and none of your remotes is online. "
                "Open Agent J on your phone to see and answer.")
    return f"Agent J：有 {n} 张审批卡在等你，你的遥控器现在都不在线。打开手机上的 Agent J 就能看到并处理。"


async def remind_offline(host, n: int) -> list[str]:
    """One content-free reminder over the owner's own channels that are set up (Telegram private chat with the owner bot
    when forwarding is on; the e-mail channel to the owner's own addresses). → the channels that sent. Never raises."""
    import asyncio
    import html
    sent = []
    text = reminder_text(n, getattr(host, "lang", "zh"))
    tg = getattr(host, "telegram", None)
    try:
        from . import telegram as tgm
        cfg = tgm.configuration(host.st)
        if tg is not None and cfg and tg.can_forward():
            await asyncio.wait_for(tgm.send_formatted(cfg, cfg["owner_id"], html.escape(text), text), 20)
            sent.append("telegram")
    except Exception:  # noqa: BLE001 — a reminder never breaks the card
        pass
    mail = getattr(host, "mail", None)
    try:
        if mail is not None:
            res = await asyncio.wait_for(mail.send_notes([], "Agent J", text, to_owner=True, quiet=True), 60)
            if res.get("ok"):
                sent.append("mail")
    except Exception:  # noqa: BLE001
        pass
    return sent
