"""P352: 「模型和 API Key」 — the owner's key manager on the phone (keys_get / keys_op / keys_set).

The host key file is `provider_runtime.env_path()` (the service EnvironmentFile, 0600 — the same file the provider key card
writes). The page lists catalog vendors (「基座模型」) and every other secret-looking NAME in that file (「第三方 API Key」):
names, set / not set, at most the last 4 characters of a long value — never the value.

- Copy / Delete need a passkey assertion (UP + UV) over `passkey.elevate_challenge(channel, device, page token, "key_"+op, n,
  sha256("agentjarvis-keys-v1\\n" op "\\n" name))`; `n` is a host one-time nonce of that page token (cleared on the first
  attempt, a fresh one in every reply). A device without a passkey is refused (`no_passkey`): there is no fallback.
- Copy sends the value to THAT session only (inside the Noise session); Delete rewrites the file without the NAME line
  (temp + rename, 0600, every other line verbatim).
- Edit / Add open the ordinary elevate secret card (its 同意 already needs Face ID when the device has a passkey): the
  value never passes through the Agent.
- Every copy / delete / set request is one line in `elevate.log` — name, op, result, device; never the value or its tail.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import pathlib
import re
import secrets
import shlex
import time
from collections import OrderedDict

from . import elevate, passkey, wire

CTX = "agentjarvis-keys-v1"
PHONE_TYPES = ("keys_get", "keys_op", "keys_set")
OPS = ("copy", "delete")
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
THIRD_RE = re.compile(r".*(KEY|TOKEN|SECRET|PASSWORD|PASS)$", re.I)
THIRD_SKIP = ("AGENTJ_", "AGENTJARVIS_")
TAIL_MIN = 24                 # a tail of 4 only for values at least this long
TOKENS_PER_DEVICE = 4
_HEX32 = re.compile(r"[0-9a-f]{32}")

# (id, {zh, en}, [(type, NAME)]) — type: oauth (status only) · sub (subscription / plan key) · key (pay-as-you-go)
CATALOG = (
    ("anthropic", {"zh": "Anthropic · Claude", "en": "Anthropic · Claude"},
     (("oauth", "claude"), ("sub", "CLAUDE_CODE_OAUTH_TOKEN"), ("key", "ANTHROPIC_API_KEY"))),
    ("openai", {"zh": "OpenAI · Codex", "en": "OpenAI · Codex"}, (("oauth", "codex"), ("key", "OPENAI_API_KEY"))),
    ("ark", {"zh": "火山方舟", "en": "Volcano Ark"}, (("sub", "ARK_CODING_PLAN_API_KEY"), ("key", "ARK_API_KEY"))),
    ("bailian", {"zh": "阿里云百炼 · 千问", "en": "Alibaba Bailian · Qwen"},
     (("sub", "BAILIAN_TOKEN_PLAN_API_KEY"), ("sub", "BAILIAN_CODING_PLAN_API_KEY"), ("key", "DASHSCOPE_API_KEY"))),
    ("kimi", {"zh": "Kimi (Moonshot)", "en": "Kimi (Moonshot)"}, (("sub", "KIMI_API_KEY"), ("key", "MOONSHOT_API_KEY"))),
    ("deepseek", {"zh": "DeepSeek", "en": "DeepSeek"}, (("key", "DEEPSEEK_API_KEY"),)),
    ("zhipu", {"zh": "智谱 GLM", "en": "Zhipu GLM"}, (("key", "ZHIPU_API_KEY"),)),
    ("minimax", {"zh": "MiniMax", "en": "MiniMax"}, (("key", "MINIMAX_API_KEY"),)),
    ("openrouter", {"zh": "OpenRouter", "en": "OpenRouter"}, (("key", "OPENROUTER_API_KEY"),)),
    ("agentsrelay", {"zh": "AgentsRelay", "en": "AgentsRelay"},
     (("key", "AGENTSRELAY_OPENAI_KEY"), ("key", "AGENTSRELAY_CLAUDE_KEY"))),
    ("google", {"zh": "Google Gemini", "en": "Google Gemini"}, (("key", "GEMINI_API_KEY"),)),
    ("siliconflow", {"zh": "硅基流动", "en": "SiliconFlow"}, (("key", "SILICONFLOW_CN_API_KEY"),)),
)
CATALOG_NAMES = frozenset(n for _, _, rows in CATALOG for t, n in rows if t != "oauth")
# The env names a harness itself reads (best effort, for `in_use`).
HARNESS_NAMES = {"claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
                 "codex": ("CODEX_API_KEY", "OPENAI_API_KEY", "AGENTSRELAY_OPENAI_KEY")}


def env_path() -> str:
    from . import provider_runtime
    return provider_runtime.env_path()


def digest(op: str, name: str) -> str:
    return hashlib.sha256(f"{CTX}\n{op}\n{name}".encode()).hexdigest()


def kind(op: str) -> str:
    return "key_" + op


# ---------------------------------------------------------------- the key file (names; a value only for copy)
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _unquote(v: str) -> str | None:
    try:
        parts = shlex.split(v, comments=False)
    except ValueError:
        return None
    if len(parts) == 1:
        return parts[0]
    return "" if not parts else None


def read_env(path: str | None = None) -> dict:
    """NAME → value (the last assignment wins, as systemd). Never through a symlink. Missing file → {}."""
    p = path or env_path()
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return {}
    out: dict = {}
    with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as f:
        for line in f.read().splitlines():
            m = _LINE.match(line)
            if not m:
                continue
            v = _unquote(m.group(2).strip())
            if v is not None:
                out[m.group(1)] = v
    return out


def delete_name(name: str, path: str | None = None) -> bool:
    """Rewrite the file without every `NAME=` / `export NAME=` line; everything else byte for byte; 0600; temp + rename.
    → False when the name is not there (nothing written)."""
    p = pathlib.Path(path or env_path())
    if p.is_symlink():
        raise elevate.Refused("dest", "the key file is a symlink")
    try:
        old = p.read_text("utf-8")
    except FileNotFoundError:
        return False
    pat = re.compile(r"^\s*(?:export\s+)?" + re.escape(name) + r"\s*=")
    lines = old.splitlines(keepends=True)
    keep = [ln for ln in lines if not pat.match(ln)]
    if len(keep) == len(lines):
        return False
    tmp = p.with_name("." + p.name + ".agentj-" + secrets.token_hex(4))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("".join(keep))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    os.chmod(p, 0o600)
    return True


# ---------------------------------------------------------------- the listing
def in_use_names(host) -> set:
    """Best effort: the env names the current harness reads, or the current OpenCode provider's key name."""
    cfg = getattr(host, "agent_cfg", None) or {}
    k = cfg.get("kind")
    if k in HARNESS_NAMES:
        return set(HARNESS_NAMES[k])
    if k == "opencode":
        model = cfg.get("model")
        if isinstance(model, str) and "/" in model:
            from . import provider_runtime
            with contextlib.suppress(Exception):
                n = provider_runtime.key_name(model.split("/", 1)[0])
                if n:
                    return {n}
    return set()


def _login_state(which: str) -> str:
    from . import harness
    try:
        r = harness.claude_login() if which == "claude" else harness.codex_login()
    except Exception:  # noqa: BLE001 — a status hint only
        return "out"
    return "in" if r[0] in ("ok", "env") else "out"


def _row(typ: str, name: str, env: dict, used: set) -> dict:
    v = env.get(name) or ""
    row = {"type": typ, "name": name, "set": bool(v)}
    if v and len(v) >= TAIL_MIN:
        row["tail"] = v[-4:]
    if v and name in used:
        row["in_use"] = True
    return row


def scan(host, path: str | None = None) -> dict:
    """{base, third} — blocking (file read, login probes): call it in a thread."""
    env = read_env(path)
    used = in_use_names(host)
    base = []
    for vid, vname, rows in CATALOG:
        out = []
        for typ, name in rows:
            if typ == "oauth":
                st = _login_state(name)
                out.append({"type": "oauth", "name": name, "set": st == "in", "state": st})
            else:
                out.append(_row(typ, name, env, used))
        base.append({"id": vid, "name": dict(vname), "rows": out})
    third = []
    for name in sorted(env):
        if name in CATALOG_NAMES or name.upper().startswith(THIRD_SKIP) or not THIRD_RE.fullmatch(name) or not env[name]:
            continue
        r = _row("key", name, env, used)
        r.pop("type")
        third.append(r)
    return {"base": base, "third": third}


# ---------------------------------------------------------------- page tokens + one-time nonces (per device)
def _tokens(host) -> dict:
    if not hasattr(host, "_keys_tokens"):
        host._keys_tokens = {}
    return host._keys_tokens


def issue(host, device: str) -> tuple[str, str]:
    toks = _tokens(host).setdefault(device, OrderedDict())
    tid, n = secrets.token_hex(16), secrets.token_hex(16)
    toks[tid] = n
    while len(toks) > TOKENS_PER_DEVICE:
        toks.popitem(last=False)
    return tid, n


def rearm(host, device: str, tid: str) -> str | None:
    toks = _tokens(host).get(device)
    if toks is None or tid not in toks:
        return None
    toks[tid] = n = secrets.token_hex(16)
    return n


def listing(host, s, path: str | None = None) -> dict:
    """The `keys` message body (without t / r): {id, n, fa?, base, third}."""
    body = scan(host, path)
    tid, n = issue(host, s.device)
    m = {"id": tid, "n": n}
    pk = host.st.passkey_of_device(s.device) if getattr(s, "device", None) else None
    if pk:
        m["fa"] = pk["id"]
    m.update(body)
    return m


# ---------------------------------------------------------------- checks and audit
def audit(host, op: str, name: str, result: str, device: str | None, **kw) -> None:
    rec = {"ts": int(time.time()), "kind": "keys", "op": op, "name": name, "result": result, "device": device,
           "channel": getattr(host, "channel", None)}
    rec.update({k: v for k, v in kw.items() if v is not None})
    host.st.append_private(elevate.log_path(host.st), json.dumps(rec, ensure_ascii=False))


def check(host, s, obj: dict) -> str | None:
    """→ None (a verified passkey assertion for this op / name / token / nonce) or why. The nonce is spent here."""
    op, name, tid = obj.get("op"), obj.get("name"), obj.get("id")
    toks = _tokens(host).get(s.device) or {}
    if not isinstance(tid, str) or tid not in toks:
        return "gone"
    n = toks[tid]
    toks[tid] = None                                    # one attempt per nonce
    if not n or obj.get("n") != n:
        return "passkey"
    pk = host.st.passkey_of_device(s.device)
    if not pk:
        return "no_passkey"
    if "fa" not in obj:
        return "passkey"
    try:
        f = passkey.approval_fields(obj["fa"])
        if f["id"] != pk["id"]:
            raise passkey.PasskeyError("bad", "credential")
        ch = passkey.elevate_challenge(host.channel, s.device, tid, kind(op), n, digest(op, name))
        count = passkey.verify_assertion(pk, f, ch)
    except (passkey.PasskeyError, ValueError) as e:
        host.st.log("keys_passkey", device=s.device, reason=getattr(e, "detail", "bad"))
        return "passkey"
    host.st.passkey_count(s.device, pk["id"], count)
    return None


# ---------------------------------------------------------------- the phone
async def on_phone(host, s, t: str, obj: dict) -> None:
    r = obj.get("r")
    if not wire.is_rid(r):
        return
    if t == "keys_get":
        body = await asyncio.to_thread(listing, host, s)
        return await host.send_app(s, {"t": "keys", "r": r, **body})
    if t == "keys_op":
        return await _op(host, s, r, obj)
    if t == "keys_set":
        return await _set(host, s, r, obj)


async def _op(host, s, r, obj: dict) -> None:
    op, name = obj.get("op"), obj.get("name")
    shown = name if isinstance(name, str) and NAME_RE.fullmatch(name) else ""
    opv = op if op in OPS else ""

    async def reply(ok: bool, why: str | None = None, **extra) -> None:
        tid = obj.get("id")
        n = rearm(host, s.device, tid) if isinstance(tid, str) else None
        m = {"t": "keys_res", "r": r, "ok": ok, "op": opv, "name": shown, **extra}
        if why:
            m["why"] = why
        if n:
            m["n"] = n
        await host.send_app(s, m)

    if not opv or not shown or not isinstance(obj.get("id"), str) or not _HEX32.fullmatch(obj["id"]):
        host.st.log("keys_refused", device=s.device, reason="shape")
        return await reply(False, "shape")
    if not host.st.sign_key(s.device):                 # only an approved device (same as F17 / F32)
        host.st.log("keys_refused", device=s.device, reason="no_key")
        audit(host, opv, shown, "refused", s.device, reason="no_key")
        return await reply(False, "passkey")
    why = check(host, s, obj)
    if why:
        host.st.log("keys_refused", device=s.device, reason=why)
        audit(host, opv, shown, "refused", s.device, reason=why)
        return await reply(False, why)
    if opv == "copy":
        env = await asyncio.to_thread(read_env)
        v = env.get(shown) or ""
        env = None  # noqa: F841 — drop the other values
        if not v:
            audit(host, opv, shown, "missing", s.device, passkey="uv")
            return await reply(False, "missing")
        try:
            await reply(True, value=v)
        finally:
            v = None  # noqa: F841 — str cannot be wiped; drop the reference
        audit(host, opv, shown, "copied", s.device, passkey="uv")
        host.st.log("keys_copy", device=s.device)
        return
    try:
        done = await asyncio.to_thread(delete_name, shown)
    except (elevate.Refused, OSError) as e:
        host.st.log("keys_refused", device=s.device, reason="write", detail=type(e).__name__)
        audit(host, opv, shown, "failed", s.device, passkey="uv")
        return await reply(False, "missing")
    audit(host, opv, shown, "deleted" if done else "missing", s.device, passkey="uv")
    host.st.log("keys_delete", device=s.device, result="deleted" if done else "missing")
    return await reply(True) if done else await reply(False, "missing")


def _purpose(host, name: str) -> str:
    zh = getattr(host, "lang", "en") == "zh"
    for _, vname, rows in CATALOG:
        if any(n == name for _, n in rows):
            return vname["zh" if zh else "en"] + " API Key"
    return name + " API Key"


async def _set(host, s, r, obj: dict) -> None:
    name = obj.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return await host.send_app(s, {"t": "keys_res", "r": r, "ok": False, "op": "set", "name": "", "why": "shape"})
    if not host.st.sign_key(s.device):
        audit(host, "set", name, "refused", s.device, reason="no_key")
        return await host.send_app(s, {"t": "keys_res", "r": r, "ok": False, "op": "set", "name": name, "why": "passkey"})
    if not hasattr(host, "_keys_cards"):
        host._keys_cards = set()
    if name in host._keys_cards:                       # one open card per name
        return await host.send_app(s, {"t": "keys_res", "r": r, "ok": False, "op": "set", "name": name, "why": "busy"})
    try:
        card = await asyncio.to_thread(build_card, host, name)
    except elevate.Refused as e:
        host.st.log("keys_refused", device=s.device, reason=e.why)
        audit(host, "set", name, "refused", s.device, reason=e.why)
        return await host.send_app(s, {"t": "keys_res", "r": r, "ok": False, "op": "set", "name": name, "why": "shape"})
    host._keys_cards.add(name)
    audit(host, "set", name, "card", s.device)
    await host.send_app(s, {"t": "keys_res", "r": r, "ok": True, "op": "set", "name": name})

    async def run():
        try:
            await host.elevate.request(card)
        finally:
            host._keys_cards.discard(name)
    host._bg(run(), "keys_set")


def build_card(host, name: str) -> dict:
    """The ordinary secret card for NAME into the host key file (no verify: any vendor, any third-party key)."""
    p = pathlib.Path(env_path())
    if not p.parent.exists():
        with contextlib.suppress(OSError):
            p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return elevate.norm_secret({"name": name, "purpose": _purpose(host, name), "dest": "env:" + str(p) + "#" + name},
                               host.st.root)
