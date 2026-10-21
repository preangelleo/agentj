"""P426 #181-4 (PROTOCOL §39): "you have asked me to do this three times — save it as a skill?" Deterministic, no model.

What is counted (only what this host sees, owner's side):
- `ask` — the owner's own words to the main Agent (a remote, Telegram / e-mail owner messages: serve._accept; a prompt typed
  on the desktop of the shared Claude session: its UserPromptSubmit hook);
- `cmd` — a shell command the main Agent ran that is worth a skill: a pipeline / chain (`|` `&&` `;`) or a script
  (`x.py`, `run.sh` …) — the shared Claude session's PostToolUse Bash and the independent Claude session's tool_use.
  Plain look-around commands (ls, cat, git, grep, agentj …) never count.

Each text is redacted first (privacy.redact: keys, tokens, e-mail, home paths), then normalised — URLs, paths, quoted text,
numbers out (a script keeps its file name) — and only that normalised signature (≤ 60 characters) and the times are kept in
<state>/skill-ops.json. No original text, no credential. Same signature ≥ THRESHOLD times within WINDOW, not covered by an
installed skill (an ask naming a skill, a command inside a skills folder), never declined → ready. The next owner message
(the 3rd itself included) carries ONE short 〔Agent J 系统提示〕 asking the Agent to finish the job, then offer once; at most
one offer per GAP, each signature offered once. The owner says yes → the Agent follows agentj-commands §5 (skill + a test);
no → `agentj skills suggest no <id>` and it is never offered again. `skills.suggest: false` turns offers off."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

LEDGER = "skill-ops.json"
LOCK = "skill-ops"
WINDOW = 14 * 86400
THRESHOLD = 3
GAP = 86400                 # at most one offer a day
MAX_SIGS = 300
KEEP_HITS = 10
SIG_MAX = 60
ASK_MAX = 300               # a longer message is not a repeated instruction
ID = re.compile(r"^[0-9a-f]{12}$")
STATES = ("new", "ready", "told", "yes", "no")

_URL = re.compile(r"\b[a-z][a-z0-9+.-]{1,15}://\S+", re.I)
_QUOTE = re.compile(r"「[^」]{0,200}」|“[^”]{0,200}”|‘[^’]{0,200}’|\"[^\"\n]{0,200}\"|'[^'\n]{0,200}'|`[^`\n]{0,200}`|《[^》]{0,200}》")
_PATH = re.compile(r"(?<![\w])(?:~|\.{1,2})?/[^\s，。；;|&<>]*|\b[\w.-]+/[\w./-]+")
_NUM = re.compile(r"(?<![A-Za-z])\d+(?:[.:/-]\d+)*")
_PUNCT = re.compile(r"[\s，。！？；：、,.!?;:~～…·()（）\[\]【】<>{}\"'“”‘’「」《》*#_=+|\\-]+")
_ACK = re.compile(r"^(?:好的?|好吧|可以|行|嗯+|对|是的?|没问题|继续|接着|谢谢|多谢|收到|ok(?:ay)?|yes|no|thanks?|thank you|go on|continue|sure)+",
                  re.I)
SCRIPT = re.compile(r"\.(?:py|sh|bash|zsh|js|mjs|cjs|ts|rb|pl|php|ps1)$")
TRIVIAL = frozenset(("ls", "ll", "la", "cd", "pwd", "cat", "less", "more", "head", "tail", "echo", "printf", "true", "false",
                     "grep", "rg", "egrep", "fgrep", "find", "fd", "sed", "awk", "wc", "sort", "uniq", "cut", "tr", "tee",
                     "which", "whereis", "type", "command", "file", "stat", "date", "env", "export", "source", ".", "set",
                     "sleep", "test", "[", "mkdir", "rm", "rmdir", "cp", "mv", "ln", "touch", "chmod", "chown", "tree",
                     "jq", "diff", "git", "gh", "agentj", "aj", "ps", "pgrep", "kill", "pkill", "top", "du", "df", "free",
                     "uname", "whoami", "id", "history", "clear", "xargs", "basename", "dirname", "realpath", "readlink",
                     "timeout", "time", "nohup", "setsid", "exit", "read", "wait"))
RUNNERS = frozenset(("python", "python3", "node", "bash", "sh", "zsh", "deno", "bun", "ruby", "perl", "uv", "npx", "tsx"))


# ------------------------------------------------------------------ normalisation (pure)
def _redact(text: str) -> str:
    from . import privacy
    try:
        return privacy.redact(text)
    except Exception:  # noqa: BLE001 — never keep a text the masker could not read
        return ""


def norm_ask(text) -> tuple[str, str] | None:
    """The owner's words → (key, label) or None (too short / long, a command, an acknowledgement)."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    if not t or len(t) > ASK_MAX or t[0] in "/／" or "\n\n〔" in t:
        return None
    t = _redact(t)
    if not t:
        return None
    t = _URL.sub(" <url> ", t)
    t = _QUOTE.sub(" … ", t)
    t = _PATH.sub(" <path> ", t)
    t = _NUM.sub("N", t)
    label = re.sub(r"\s+", " ", t).strip().lower()
    key = _PUNCT.sub("", label)
    if _ACK.fullmatch(key) or len(key) < 8 or (key.isascii() and len(label.split()) < 4):
        return None
    return key, label[:SIG_MAX]


def _prog(tokens: list[str]) -> str | None:
    for tok in tokens:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", tok) or tok in ("sudo", "env", "command", "exec", "nice", "nohup", "time"):
            continue
        return tok.rsplit("/", 1)[-1]
    return None


def norm_cmd(cmd) -> tuple[str, str] | None:
    """A shell command → (key, label) or None (look-around commands, a skill's own script, too little to save)."""
    if not isinstance(cmd, str) or not cmd.strip() or "/skills/" in cmd:
        return None
    line = cmd.strip().split("\n", 1)[0][:400]
    line = _redact(line)
    if not line:
        return None
    line = _URL.sub(" U ", line)
    line = _QUOTE.sub(" Q ", line)
    out = []
    for tok in re.findall(r"&&|\|\||[|;]|[^\s|;&]+", line):
        if SCRIPT.search(tok):
            out.append(tok.rsplit("/", 1)[-1])
        elif "/" in tok or tok.startswith("~"):
            out.append("P")
        else:
            out.append(_NUM.sub("N", tok))
    chain = any(t in ("|", "&&", "||", ";") for t in out)
    script = any(SCRIPT.search(t) for t in out)
    prog = _prog(out)
    if not prog or not (chain or script):
        return None
    segs, cur = [], []
    for t in out:
        if t in ("|", "&&", "||", ";"):
            segs.append(cur)
            cur = []
        else:
            cur.append(t)
    segs.append(cur)
    progs = [_prog(s) for s in segs if s]
    if all(p in TRIVIAL for p in progs if p) and not script:
        return None
    if prog in TRIVIAL and not chain and not script:
        return None
    label = " ".join(out[:10])[:SIG_MAX]
    return label.lower(), label


def sig_id(kind: str, key: str) -> str:
    return hashlib.sha256(f"{kind}\0{key}".encode()).hexdigest()[:12]


def covered(kind: str, key: str, names) -> bool:
    """An installed skill already does this: an ask that names one (≥ 4 characters), a command that runs one."""
    k = key.lower()
    for n in names or ():
        n = str(n).lower()
        if len(n) >= 4 and (n in k or n.replace("-", "") in k):
            return True
    return False


# ------------------------------------------------------------------ the ledger
def _path(st) -> Path:
    return Path(st.root) / LEDGER


def load(st) -> dict:
    try:
        d = json.loads(_path(st).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        d = {}
    ops = d.get("ops") if isinstance(d, dict) and isinstance(d.get("ops"), dict) else {}
    clean = {}
    for i, e in ops.items():
        if not (isinstance(i, str) and ID.fullmatch(i) and isinstance(e, dict) and e.get("k") in ("ask", "cmd")
                and isinstance(e.get("sig"), str)):
            continue
        hits = [int(h) for h in e.get("hits") or [] if type(h) in (int, float)][-KEEP_HITS:]
        clean[i] = {"k": e["k"], "sig": e["sig"][:SIG_MAX], "hits": hits,
                    "state": e.get("state") if e.get("state") in STATES else "new",
                    **({"at": int(e["at"])} if type(e.get("at")) in (int, float) else {})}
    told = d.get("told_at") if isinstance(d, dict) and type(d.get("told_at")) in (int, float) else 0
    return {"v": 1, "ops": clean, "told_at": int(told)}


def _save(st, d: dict) -> None:
    ops = d["ops"]
    if len(ops) > MAX_SIGS:                  # the least recent "new" ones go first; an answer (yes / no) is kept longest
        order = sorted(ops, key=lambda i: (ops[i]["state"] in ("yes", "no"), max(ops[i]["hits"] or [0])))
        for i in order[:len(ops) - MAX_SIGS]:
            ops.pop(i, None)
    st.write_private(_path(st), (json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n").encode())


def owner_turn(agent) -> bool:
    """Is the turn now running the owner's own (N5)? Only then do its commands count towards a suggestion: not a group /
    guest / e-mail-guest / automation message, not a same-owner or friend task (this turn's Send, or — the shared session —
    the source the native session may still be running), not a phone turn whose source is not the owner. Errs to False."""
    from . import provenance
    try:
        if provenance.untrusted_turn(agent) or provenance.same_owner_source(agent):
            return False
        if getattr(agent, "phone_turn", False) and not provenance.is_owner(getattr(agent, "pending_source", None)):
            return False
        return True
    except Exception:  # noqa: BLE001 — never counts on a doubt
        return False


def record(st, kind: str, text, names=(), now: float | None = None) -> str | None:
    """One occurrence of an ask / a command. → the id when this occurrence made it ready, else None."""
    got = norm_ask(text) if kind == "ask" else norm_cmd(text) if kind == "cmd" else None
    if got is None:
        return None
    key, label = got
    if covered(kind, key, names):
        return None
    now = int(time.time() if now is None else now)
    i = sig_id(kind, key)
    with st.file_lock(LOCK):
        d = load(st)
        e = d["ops"].get(i) or {"k": kind, "sig": label, "hits": [], "state": "new"}
        e["hits"] = [h for h in e["hits"] if now - h < WINDOW and h <= now][-(KEEP_HITS - 1):] + [now]
        e["sig"] = label
        fresh = e["state"] == "new" and len(e["hits"]) >= THRESHOLD
        if fresh:
            e["state"] = "ready"
        d["ops"][i] = e
        _save(st, d)
    return i if fresh else None


def enabled(prefs=None, st=None) -> bool:
    try:
        from . import preferences
        doc = prefs if isinstance(prefs, dict) else preferences.effective(st)
        return preferences.get(doc, "skills.suggest", True) is not False
    except Exception:  # noqa: BLE001
        return True


NOTE = {
    "zh": ("\n\n〔Agent J 系统提示，不是主人说的话，不要复述〕同类事已做 {n} 次：「{sig}」。先把这次办完，再用一句话问主人要不要存成技能"
           "（照 agentj-commands 第 5 节，带自检）；主人说不用就运行 agentj skills suggest no {id}。"),
    "en": ("\n\n[Agent J system note, not from the owner; do not repeat it] The same kind of task has come up {n} times: \"{sig}\". "
           "Finish this one, then ask the owner in one sentence whether to save it as a skill (agentj-commands section 5, with a "
           "self-check); if they say no, run agentj skills suggest no {id}."),
}


def take_note(st, lang: str = "zh", prefs=None, now: float | None = None) -> str:
    """The one-time note for the Agent (rides on the owner's next message), or "" — at most one per GAP, each id once."""
    if not enabled(prefs, st):
        return ""
    now = int(time.time() if now is None else now)
    d = load(st)
    if not any(e["state"] == "ready" for e in d["ops"].values()) or 0 <= now - d["told_at"] < GAP:
        return ""
    with st.file_lock(LOCK):
        d = load(st)
        ready = [(i, e) for i, e in d["ops"].items() if e["state"] == "ready"]
        if not ready or 0 <= now - d["told_at"] < GAP:
            return ""
        i, e = max(ready, key=lambda x: (len(x[1]["hits"]), max(x[1]["hits"] or [0])))
        e["state"], e["at"] = "told", now
        d["told_at"] = now
        _save(st, d)
    sig = e["sig"].replace("「", "").replace("」", "").replace('"', "")
    return NOTE["en" if lang == "en" else "zh"].format(n=len(e["hits"]), sig=sig, id=i)


def answer(st, i, yes: bool, now: float | None = None) -> dict:
    if not isinstance(i, str) or not ID.fullmatch(i):
        return {"ok": False, "why": "shape"}
    with st.file_lock(LOCK):
        d = load(st)
        e = d["ops"].get(i)
        if e is None:
            return {"ok": False, "why": "not_found"}
        e["state"], e["at"] = ("yes" if yes else "no"), int(time.time() if now is None else now)
        _save(st, d)
    return {"ok": True, "id": i, "state": e["state"]}


def suggestions(st, now: float | None = None, everything: bool = False) -> list[dict]:
    """Current suggestions (ready / offered), newest first; `everything` adds the answered and the counting ones."""
    now = int(time.time() if now is None else now)
    out = []
    for i, e in load(st)["ops"].items():
        recent = [h for h in e["hits"] if now - h < WINDOW]
        if not everything and e["state"] not in ("ready", "told"):
            continue
        if not everything and not recent and e["state"] == "ready":
            continue
        out.append({"id": i, "kind": e["k"], "sig": e["sig"], "count": len(recent), "state": e["state"],
                    "last": max(e["hits"] or [0])})
    return sorted(out, key=lambda x: -x["last"])


__all__ = ["norm_ask", "norm_cmd", "record", "take_note", "answer", "suggestions", "covered", "sig_id"]
