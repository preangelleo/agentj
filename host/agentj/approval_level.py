"""P336 (Leo, 10-16, backlog #37 + #36): the owner's approval level, goal mode without the opening card, and cards that
wait while no remote is online.

Level (`approval.level`, default "standard" = the behaviour before P336, unchanged):
    safe      more cards: no Jev layer 2 (a can't-tell command always asks), no batch grant, no goal countdown (a reversible
              step waits for the owner), no shared-mode turn-wide category grant, and shared sessions ask like `strict` plus
              every deletion (local files too).
    standard  as before.
    full      P552 (#325 ③): merged into `harness` — one switch, 「全放行」 (P556; P552 called it 「减少弹窗」). A released configuration
              that says `full` with its live signed switch is in force as `harness` (MERGED; the file is not rewritten,
              the signature covers the word; no re-confirmation — chairman 10-26). What `full` used to mean, kept for the audit trail of old approvals.log lines:
              like Claude Code `--dangerously-skip-permissions` / Codex full access: an Agent tool call that would have
              asked is allowed at once, logged (approvals.log `reason: level_full`, activity, one quiet line on the phone).
              The red lines still ask the owner, one by one: a suspected attack (goal.suspect — `curl | sh`, decode-and-run,
              hook / approval files, `agentj config`, Agent J settings or state folders), credentials changed or shown,
              spending money, price changes. Host cards (`agentj …`, the pairing card, the sandbox / login-rescue cards) and
              cards with their own wait keep asking. Needs a paired remote with an approval key (none → refused as before).
    harness   P556 「全放行」 / Full access (P551 #320 「审批跟随底层 Harness」), expert mode — the AI coding tool Agent J starts
              runs at its widest permissions (MAX_HOW) and Agent J adds no approval layer of its own — its own `agentj …`
              cards (mail, calendar, same-owner changes), goal countdowns, batch / brief grants and the danger hints of the
              Claude Code hook are off; nothing the harness would ask comes as a card any more (P556: it runs at its widest
              permissions; before P556 its own prompts came to the phone as they were).
              The one layer kept: the four ③ kinds (confirm_tier.harness_keeps: money, the owner's data deleted, a seat, a key
              going out) need the owner's Face ID, whoever starts them. Switching to it is signed AND needs this remote's
              passkey (no passkey → refused `no_passkey`); it takes effect when the harness restarts (done at once, before
              its next turn, conversation kept).

Goal without the opening card (`approval.goal_nocard`, default false = the P158 card): when on, `agentj goal propose`
starts the goal at once — the owner's signed switch IS the signature (approvals.log `reason: goal_preauth`, `grant` = the
switch's signed control line) — and inside a live goal every step that would have asked is allowed except the red lines
above (they still ask). Still logged, the result card still comes. The switch only counts while the remote that signed it
is still paired with the same approval key (`<state>/approval_signers.json`); a value written any other way (terminal,
an approved Agent request) leaves the opening card in place.

P552 (#325 ①): a switch the configuration turns on but no live signature backs (the signing remote unpaired or re-keyed)
is never silent: `unsigned()` names it and why, Settings shows it 「未生效」 with 「用本机重新确认」, and the OWNER is told
once per change (phone line + security push + Telegram private chat / own e-mail, `tell_owner`), not only the Agent.

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
KEYS = (KEY, GOAL_KEY, "jev.mode")
LEVELS = ("safe", "standard", "full", "harness")
SIGNED = ("full", "harness")     # levels that ask less: in force only with a live signed switch
DEFAULT = "standard"
REDLINE_CATS = ("credentials", "spend", "price")
# host cards that are a decision of their own, never auto-allowed by a level (pairing, sandbox, login rescue, goal, quota)
from . import policy_registry
HOST_CARDS = tuple(tool for tool in policy_registry.HOST_TOOLS if not tool.startswith("agentj "))
# P551: under `harness` these still ask — the harness's own cards (OpenCode doom-loop / task, questions) and decisions that are
# not an approval layer (pairing, sandbox, login rescue, resident hooks, the terminal); a same-owner change is Agent J's own ②
# (review B1) changing Agent J's own settings, quota account or a paused automation is not an approval the harness has —
# it keeps its card
HARNESS_KEEPS = tuple(tool for tool in HOST_CARDS if tool != "AgentJSameOwnerAction") + (
    "agentj config", "agentj quota switch", "agentj automation resume")


PARK_GRACE = 20          # seconds a card waits for a remote to come online before the Agent is told nobody is there
PARK_SECS = 1800         # a parked card stays on the phone this long
PARK_OK_SECS = 1800      # an allow given to a parked card lets the identical retry pass once within this window
PARK_MAX = 8             # parked cards at once (the oldest is withdrawn)
OFFLINE_GAP = 900        # one Telegram / e-mail reminder per this many seconds


# P552 (#325 ③): 「全放行」 and 「跟随 AI 编程工具」 are one switch now, 「减少弹窗」 (`harness`). A released `full` (config + its
# signed record) is read as written (nothing is rewritten) and, while its signature is live, is in force as `harness` — no
# re-confirmation, no notice (chairman 10-26 23:5x, accepting security review B3). A lapsed one is shown like any other.
MERGED = {"full": "harness"}     # what a released value is in force as (and what a re-confirmation signs)


def level(prefs, st=None) -> str:
    """The level in force. `harness` counts only with `st` and a live signed switch to it (signed_value): a value written any
    other way (an edited config file, the terminal) reads as standard. safe / standard need no signature (safe only asks
    more). P552: a signed `full` is in force as `harness` (MERGED)."""
    from . import preferences
    v = preferences.get(prefs or {}, KEY, DEFAULT)
    v = v if v in LEVELS else DEFAULT
    if v in SIGNED and (st is None or signed_value(st, KEY) != v):
        return DEFAULT
    return MERGED.get(v, v)


def unsigned_full(prefs, st) -> bool:
    """The configuration says full / harness but it is not in force (then standard applies; the owner is told once)."""
    from . import preferences
    v = preferences.get(prefs or {}, KEY, DEFAULT)
    return v in SIGNED and signed_value(st, KEY) != v


def goal_nocard(prefs) -> bool:
    from . import preferences
    return preferences.get(prefs or {}, GOAL_KEY, False) is True


def follow_harness(prefs, st) -> bool:
    """P551 (#320): the signed `harness` level is in force."""
    return level(prefs, st) == "harness"


def shared_mode(prefs, st=None) -> str:
    """The shared-session classifier mode: `safe` under the safe level, `native` (the harness's own prompts only) under the
    signed harness level, else agent.approvals (standard | strict | native)."""
    from . import preferences
    if level(prefs) == "safe":
        return "safe"
    if st is not None and follow_harness(prefs, st):
        return "native"
    return {"strict": "strict", "native": "native"}.get(preferences.get(prefs or {}, "agent.approvals", "standard"), "standard")


def host_card(tool: str, tool_input=None, *, requirement=None) -> bool:
    return policy_registry.main_host_card(tool, tool_input, requirement=requirement)


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


def note_signer(st, key: str, value, device: str, sign_pub: bytes, rid: str, signed: dict, name: str = "") -> None:
    """Remember the signed switch: device, key hash, the approvals.log id and the control signature itself (n, ts, sig) so
    every later use re-verifies it with the device's CURRENT key (a hand-written file verifies nothing)."""
    from .state import _write_private
    d = signers(st)
    d[key] = {"value": value, "device": device, "sk_sha": _sk_sha(sign_pub), "rid": rid, "at": int(time.time()),
              "n": signed.get("n"), "ts": signed.get("ts"), "sig": signed.get("sig"),
              **({"name": str(name)[:60]} if name else {})}     # P552: additive — older records have no name
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


def why_unsigned(st, key: str, value) -> str | None:
    """P552 (#325 ①): why the configured `value` of `key` is NOT backed by a live signed switch, else None.
    missing — never switched on a phone (or the record was dropped: written another way) · unpaired — the remote that
    signed it is no longer paired · rekeyed — that remote has a new approval key now · changed — the record is for another
    value · bad_sig — the stored signature does not verify."""
    s = signers(st).get(key)
    if not isinstance(s, dict) or not s.get("device") or not isinstance(s.get("sig"), str):
        return "missing"
    if s.get("value") != value:
        return "changed"
    sk = st.sign_key(str(s["device"]))
    if not sk:
        return "unpaired"
    if _sk_sha(sk) != s.get("sk_sha"):
        return "rekeyed"
    return None if _live(st, key) is not None else "bad_sig"


def unsigned(prefs, st) -> list:
    """P552: the switches the configuration turns on that are not in force — [{key, value, why, device}] (value: what a
    re-confirmation signs, `full` already as `harness`). [] = everything in the configuration is in force."""
    from . import preferences
    out = []
    v = preferences.get(prefs or {}, KEY, DEFAULT)
    if v in SIGNED:
        why = why_unsigned(st, KEY, v)
        if why:
            out.append({"key": KEY, "value": MERGED.get(v, v), "why": why, "device": (signers(st).get(KEY) or {}).get("device")})
    if goal_nocard(prefs):
        why = why_unsigned(st, GOAL_KEY, True)
        if why:
            out.append({"key": GOAL_KEY, "value": True, "why": why, "device": (signers(st).get(GOAL_KEY) or {}).get("device")})
    return out


def unsigned_mark(items: list) -> str:
    """One fingerprint of what is not in force (the owner is told once per change, not at every restart)."""
    return hashlib.sha256(json.dumps([[i["key"], i["value"], i["why"], i.get("device")] for i in items],
                                     sort_keys=True).encode()).hexdigest()[:24] if items else ""


def _told_path(st) -> Path:
    return Path(st.root) / "approval_unsigned_told.json"


def told(st) -> str:
    try:
        d = json.loads(_told_path(st).read_text())
        return str(d.get("mark") or "") if isinstance(d, dict) else ""
    except (OSError, ValueError):
        return ""


def set_told(st, mark: str) -> None:
    from .state import _write_private
    _write_private(_told_path(st), json.dumps({"mark": mark, "at": int(time.time())}).encode())


def goal_preauth(st, prefs) -> dict | None:
    """The grant a goal starts with when the owner's signed switch is on: {rid, device, sk_sha} — else None (the card)."""
    if not goal_nocard(prefs):
        return None
    s = _live(st, GOAL_KEY)
    if s is None or s.get("value") is not True:
        return None
    return {"rid": s["rid"], "device": s["device"], "sk_sha": s["sk_sha"]}


def level_grant(st) -> str | None:
    """The approvals.log id of the live signed switch to `harness` / a released `full` (the audit parent of a `level_full` line)."""
    s = _live(st, KEY)
    return s["rid"] if s is not None and s.get("value") in SIGNED else None


# ------------------------------------------------------------------ words
def _en(lang) -> bool:
    return str(lang or "").startswith("en")


# P552 (#325 ③) + P556 (chairman 10-27): one name for the merged switch, 「全放行」 / Full access (`full` is the same switch,
# kept for released configurations; the stored value stays `harness`, nothing is migrated on disk)
NAME = {"zh": {"safe": "安全", "standard": "中等", "full": "全放行", "harness": "全放行"},
        "en": {"safe": "Safe", "standard": "Standard", "full": "Full access", "harness": "Full access"}}

# P556: what 「全放行」 does to the AI coding tool Agent J starts itself (read at its start; safe / standard → its normal start
# again, restarted before the next turn). Only Agent J's own launch changes — never the owner's config files. No harness gets
# its own allow-everything switch: each one skips something Agent J relies on (security review P556) —
#   Claude Code `--dangerously-skip-permissions` / bypassPermissions: its prompts never reach Host.ask, so a Telegram group
#     member's / visitor's turn (group_source), an avatar step and the floor below go unchecked, and the PreToolUse hook alone
#     can be skipped (a hook timeout, a managed policy); Codex `never` / `--dangerously-bypass-approvals-and-sandbox`: Codex
#     never asks Agent J at all; OpenCode `allow`, Kimi / Qwen yolo, DeepSeek workspace-write: the permission request goes.
# So the harness keeps asking Agent J and Agent J answers every request itself (same effect for the owner):
#   answer      Claude Code (its own bash sandbox off for this start) · OpenCode · Kimi · Qwen · DeepSeek · Agent J Harness
#   no_sandbox  Codex: also the thread sandbox danger-full-access whatever config.toml says (approvals stay `untrusted`)
MAX_HOW = {"codex": "no_sandbox"}


def max_how(kind) -> str:
    return MAX_HOW.get(kind or "", "answer")


_READERS = {"cat", "less", "more", "head", "tail", "grep", "rg", "ls", "wc", "diff", "file", "stat", "du", "md5sum",
            "sha256sum", "shasum", "realpath", "readlink", "tree", "bat"}


def _own_code(tool: str, tool_input) -> bool:
    """A write into the code this Agent J host runs (fence.code_paths: the installed package, its venv / source checkout) —
    after that no check of it can be trusted, so not even 「全放行」 lets it pass without the owner (review B2). Reads are
    fine; an indirect write (a script that writes there) is not seen — the documented limit."""
    from . import danger, fence
    try:
        roots = fence.code_paths()
    except Exception:  # noqa: BLE001
        return False
    if not roots:
        return False

    def under(p) -> bool:
        p = p.strip().strip("'\"") if isinstance(p, str) else ""
        if not p.startswith(("/", "~")):      # a relative name says nothing about where the Agent's shell is
            return False
        r = os.path.realpath(os.path.expanduser(p))
        return any(r == d or r.startswith(d.rstrip("/") + "/") for d in roots)
    inp = tool_input if isinstance(tool_input, dict) else {}
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Delete"):
        return any(under(inp.get(k)) for k in ("file_path", "path", "notebook_path"))
    raw = inp.get("command", inp.get("cmd", "")) if tool in ("Bash", "exec_command", "shell_command", "bash") else ""
    text = " ".join(map(str, raw)) if isinstance(raw, list) else str(raw or "")
    if not text:
        return False
    try:
        cmds = [s for c in danger.lex(text) for s in danger.unwrap(c)]
    except Exception:  # noqa: BLE001 — cannot read it: any mention of the code is enough
        return any(d in text for d in roots)
    for c in cmds:
        if c.name not in _READERS and any(under(a) or (">" in a and under(a.split(">")[-1])) for a in c.args):
            return True
    return any(re.search(r">>?\s*" + re.escape(d), text) for d in roots)   # a redirection into it


_EXEC = re.compile(r"(?:^|[\s;&|(`])(?:python[0-9.]*|node|deno|bun|perl|ruby|php)\s+(?:-[A-Za-z]*[ce]|--eval)\b")


def harness_floor(tool: str, tool_input, verdict) -> str | None:
    """P556 (security review B2 / S2): under 「全放行」 these still need the owner's Face ID besides the four kinds — a suspected
    attack on Agent J itself (goal.suspect: hook / approval files, Agent J's settings, state or own code), a fetch run in
    the same call, and an inline program that reads a key-named variable / the environment while it can reach the network
    (a key going out the four-kinds parser cannot read). → why, else None."""
    from . import goal
    why = goal.suspect(tool, tool_input, verdict)
    if why:
        return why
    if _own_code(tool, tool_input):
        return "settings"
    text = goal.action_text(tool, tool_input)
    if not text:
        return None
    if download_and_run(text):
        return "injection"
    if _EXEC.search(text) and (_SECRET_VAR.search(text) or _ENVDUMP.search(text)) and re.search(
            r"urlopen|requests\.|http\.client|fetch\(|socket|curl|wget|https?://", text):
        return "credentials"
    return None


WHY = {"zh": {"missing": "没有在已配对的遥控器上确认过", "changed": "确认记录和当前设置对不上",
              "unpaired": "当初确认它的遥控器「{dev}」已经解绑", "rekeyed": "当初确认它的遥控器「{dev}」重新设置了 Face ID / 指纹",
              "bad_sig": "当初的确认签名核对不上"},
       "en": {"missing": "it was never confirmed on a paired remote", "changed": "its confirmation is for another setting",
              "unpaired": "the remote that confirmed it (「{dev}」) is no longer paired",
              "rekeyed": "the remote that confirmed it (「{dev}」) set up Face ID / fingerprint again", "bad_sig": "its confirmation no longer verifies"}}


def changed_text(key: str, value, by: str, lang) -> str:
    en = _en(lang)
    if key == "jev.mode":
        return (f"🔐 Jev: {value} (confirmed on {by})." if en else f"🔐 Jev 已设为 {value}（由「{by}」确认）。")
    if key == KEY:
        n = NAME["en" if en else "zh"].get(value, str(value))
        return (f"🔐 Approval level is now {n} (confirmed on {by})." if en else f"🔐 审批档位已改为「{n}」（由「{by}」确认）。")
    on = value is True
    return (f"🔐 Goal mode without the opening card is {'on' if on else 'off'} (confirmed on {by})." if en else
            f"🔐 「设了目标就不弹窗」已{'打开' if on else '关闭'}（由「{by}」确认）。")


def unsigned_text(lang, value: str = "full") -> str:
    n = NAME["en" if _en(lang) else "zh"].get(value, NAME["en" if _en(lang) else "zh"]["full"])
    if _en(lang):
        return (f"🔐 The configuration says approval level 「{n}」, but it was not confirmed on a paired phone — "
                "Standard applies. Switch it in Settings → Safety on your phone.")
    return f"🔐 配置里写着审批档位「{n}」，但没有在已配对的手机上签名确认，现在按「中等」处理。要用这一档，请在手机 设置 → 安全 里切换。"


def why_text(why: str, dev: str, lang) -> str:
    w = WHY["en" if _en(lang) else "zh"]
    return w.get(why, w["bad_sig"]).replace("{dev}", dev or "?")


def owner_unsigned_text(items: list, names: dict, lang) -> str:
    """P552 (#325 ①): what the OWNER reads (phone line, Telegram private chat / own e-mail) when a switch is not in force."""
    en = _en(lang)
    parts = []
    for i in items:
        what = (NAME["en" if en else "zh"].get(i["value"], str(i["value"])) if i["key"] == KEY else
                ("Goal mode without the opening card" if en else "设了目标就不弹窗"))
        why = why_text(i["why"], names.get(i.get("device")) or "", lang)
        parts.append(f"「{what}」 — {why}" if en else f"「{what}」：{why}")
    if en:
        return ("🔐 Agent J: " + "; ".join(parts) + ". So it is NOT in force — Standard applies and cards come as usual. "
                "To turn it back on: phone Settings → Safety → 「Confirm again on this device」 (Face ID once).")
    return ("🔐 Agent J：" + "；".join(parts) + "，所以它现在没有生效，按「中等」处理，审批卡照常会弹。要恢复：手机 设置 → 安全 → "
            "点「用本机重新确认」（Face ID 一次）。")


def resign_hint_text(lang) -> str:
    """P552: a remote just got its (new) approval key while a switch is not in force — one tap re-signs it."""
    if _en(lang):
        return "🔐 This remote has Face ID / fingerprint set up now. 「Full access」 is not in force — tap 「Confirm again on this device」 to turn it back on."
    return "🔐 这台遥控器的 Face ID / 指纹已经设好了。「全放行」现在没有生效——点「用本机重新确认」就能恢复。"


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
    return await tell_owner(host, reminder_text(n, getattr(host, "lang", "zh")))


async def tell_owner(host, text: str) -> list[str]:
    """`text` to the owner's own channels that are set up (Telegram private chat / own e-mail) — never the Agent. Never raises."""
    import asyncio
    import html
    sent = []
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
