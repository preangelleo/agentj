"""`agentj agent detect [--json]` (seat setup §4.1, item 9): which agent harnesses the Agent J host has, and which are usable.

For `claude`, `codex` and `opencode`: installed = the binary is on PATH (or AGENTJ_CLAUDE_BIN / AGENTJ_CODEX_BIN /
AGENTJ_OPENCODE_BIN, the same lookup `serve` uses) and `--version` answers; logged in = a credential file / Keychain item
*exists* — native auth stores are not opened; Claude provider settings are checked for an auth hint (OpenCode: its own credential store `<XDG_DATA_HOME or ~/.local/share>/opencode/auth.json`,
written by `opencode auth login`, or a model vendor's API-key variable set, by NAME). Decision: 0 usable → "none", exactly 1 →
"use:<name>", ≥ 2 → "ask_owner" (the installing agent must ask its human — it never picks one itself, never the one running
the install). OpenCode counts like the other two.

Logged-in values: True (file / Keychain item / env var by NAME), False (nothing found), None = cannot tell: Claude Code
with no saved login file while this command runs inside Claude Code itself (CLAUDECODE=1), which hides its own token from
the commands it runs — counted as usable, so the human is asked rather than silently skipped.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .service import tilde
from .envcompat import getenv
from .text import clean_line

LABEL = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode", "kimi": "Kimi Code", "qwen": "Qwen Code", "dsh": "DeepSeek Harness",
         "core": "Agent J Harness"}   # P359: our own kernel (agentj/core), offered only with AGENTJ_CORE=1
BIN_ENV = {"claude": "AGENTJ_CLAUDE_BIN", "codex": "AGENTJ_CODEX_BIN", "opencode": "AGENTJ_OPENCODE_BIN", "kimi": "AGENTJ_KIMI_BIN",
           "qwen": "AGENTJ_QWEN_BIN", "dsh": "AGENTJ_DSH_BIN"}
# P305: Kimi Code CLI (`kimi` 2.2.0+) over ACP (agent_kimi.py / acp.py)
SUPPORTED = ("claude", "codex", "opencode", "kimi", "qwen", "dsh")   # P310: + Qwen Code (`qwen --acp`, agent_qwen.py)
# P329: + DeepSeek Harness (`dsh --profile acp`, agent_dsh.py) — the engine inside Olares' Lares
# API-key variables of the model vendors OpenCode knows (its bundled models.dev list, 1.18.32): checked by NAME only.
# KIMI_API_KEY (P279): Kimi For Coding (kimi-code-plan-cn / -global) — launchd loads only names listed here.
OPENCODE_KEY_ENV = ("ZHIPU_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY", "ARK_API_KEY",
                    "MINIMAX_API_KEY", "SILICONFLOW_CN_API_KEY", "OPENROUTER_API_KEY", "OPENCODE_API_KEY", "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY",
                    "BAILIAN_TOKEN_PLAN_API_KEY",   # P310: bailian.py presets
                    "ARK_CODING_PLAN_API_KEY",   # P315: ark.py preset
                    "ARK_AGENT_PLAN_API_KEY")    # P395: ark.py Agent Plan preset
NAMES = ("claude", "codex", "opencode", "kimi", "qwen", "dsh")
LATER = "coming in a later version"
VERSION_TIMEOUT = 15


def agent_bin(kind: str) -> str | None:
    env = BIN_ENV.get(kind)
    from .binaries import resolve
    return resolve(kind)["path"] if env else None


def version_of(exe: str) -> str | None:
    """First line of `<exe> --version`, cleaned (no control characters, ≤ 40 chars); None when it does not answer."""
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=VERSION_TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if r.returncode != 0:
        return None
    out = (r.stdout or r.stderr or "").splitlines()
    line = clean_line(out[0], 200) if out else ""
    return (line if len(line) <= 40 else line[:39] + "…") or "?"


def claude_login(environ=None) -> tuple[str, str]:
    """(ok | env | warn, where) — auth hints only; never return token content.

    P282: every source Claude Code itself reads (its authentication docs): a cloud provider switch (Bedrock / Vertex /
    Foundry), ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY, an apiKeyHelper or `env` in settings.json, CLAUDE_CODE_OAUTH_TOKEN,
    then the /login credentials (macOS: the Keychain item; else <config>/.credentials.json). A token that lives only in the
    owner's shell (~/.zshenv) is invisible to the background service — that is what "warn" means there."""
    env = os.environ if environ is None else environ
    cfg = env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    for k in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        if env.get(k) not in (None, "", "0", "false"):
            return "env", f"env {k} (cloud provider; name only)"
    if os.path.isfile(os.path.join(cfg, ".credentials.json")):
        return "ok", f"login {tilde(os.path.join(cfg, '.credentials.json'))}"
    if sys.platform == "darwin" and shutil.which("security"):
        try:   # no -w: asks only whether the Keychain item exists, never its value
            r = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials"],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                return "ok", "login in Keychain"
        except (OSError, subprocess.TimeoutExpired):
            pass
    # Native provider settings (including official AgentsRelay) are read as data
    # only to detect a nonempty auth hint; no token value reaches the result.
    try:
        from .provider_profiles import secure_read
        from .opencode_provider import ProviderError
        settings = json.loads(secure_read(Path(cfg) / 'settings.json'))
        native_env = settings.get('env', {}) if isinstance(settings, dict) else {}
        if isinstance(native_env, dict) and any(isinstance(native_env.get(k), str) and native_env[k]
                for k in ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN')):
            return "ok", "native provider settings (key validity unverified)"
        if isinstance(settings, dict) and isinstance(settings.get('apiKeyHelper'), str) and settings['apiKeyHelper'].strip():
            return "ok", "apiKeyHelper in settings.json (validity unverified)"
    except (OSError, ValueError, ProviderError):
        pass
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if env.get(k):
            return "env", f"env {k} (name only)"
    return "warn", "no login found"


def codex_login() -> tuple[str, str]:
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    if os.path.isfile(os.path.join(home, "auth.json")):
        return "ok", f"login {tilde(os.path.join(home, 'auth.json'))}"
    for k in ("CODEX_API_KEY", "OPENAI_API_KEY", "AGENTSRELAY_OPENAI_KEY"):
        if os.environ.get(k):
            return "env", f"env {k} (name only)"
    return "warn", "no login found"


def opencode_v2(version):
    return bool(version and re.search(r'(?:^|\s)v?2\.', version))


def opencode_login(version: str | None = None, model: str | None = None, exe: str | None = None) -> tuple[str, str]:
    """Storage existence is a hint, never proof of a usable key. No DB/content read.
    P298: "free" — no key, but the model is one of OpenCode's free models (opencode_free_login): usable all the same."""
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    root = os.path.join(data, "opencode")
    is_v2 = opencode_v2(version)
    stores = [("opencode.db", "v2 SQLite database exists; credential rows and key validity unverified"),
              ("auth.json", "v1 credential store exists; key validity unverified")]
    if version and not is_v2:
        stores.reverse()
    for filename, description in stores:
        if version and ((is_v2 and filename != "opencode.db") or (not is_v2 and filename != "auth.json")):
            continue
        p = os.path.join(root, filename)
        if os.path.isfile(p):
            return "ok", f"{description}: {tilde(p)}"
    for k in OPENCODE_KEY_ENV:
        if os.environ.get(k):
            return "env", f"env {k} (name only)"
    free = opencode_free_login(model, exe)
    if free:
        return "free", free
    return "warn", "no model key store found (opencode auth login); Agent J restarts its own OpenCode before the next message after a key change"


def _jsonc(text: str):
    """OpenCode's config may be JSONC: drop // and /* */ comments outside strings, and trailing commas."""
    out, i, n, q = [], 0, len(text), None
    while i < n:
        c = text[i]
        if q:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == q:
                q = None
        elif c == '"':
            q = c
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


def opencode_model(environ=None) -> str | None:
    """P298: the model OpenCode would run for the phone — Agent J's own setting (`agentj agent opencode --model`), else
    OpenCode's own `model` (OPENCODE_CONFIG_CONTENT, then the global config file). None = OpenCode's own pick."""
    env = os.environ if environ is None else environ
    try:
        from .state import State
        st = State()
        a = st.agent_config() if st.exists() else None
        if a and a.get("kind") == "opencode" and a.get("model"):
            return a["model"]
    except Exception:  # noqa: BLE001 — before `agentj init` there is no state
        pass
    conf = env.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    sources = [env.get("OPENCODE_CONFIG_CONTENT")]
    for name in ("opencode.json", "opencode.jsonc", "config.json"):
        try:
            sources.append(Path(conf, "opencode", name).read_text(errors="replace")[:256 * 1024])
        except OSError:
            pass
    for text in sources:
        try:
            m = _jsonc(text).get("model") if text else None
        except (ValueError, AttributeError, RecursionError):
            m = None
        if isinstance(m, str) and "/" in m:
            return m
    return None


def opencode_free_login(model: str | None = None, exe: str | None = None) -> str | None:
    """P298: no key needed — the model OpenCode runs is one of its own free models (or none is set and OpenCode offers free
    ones, which is what it then picks: with no key only those are listed). The description, or None."""
    from . import opencode_free
    exe = exe or agent_bin("opencode")
    model = model or opencode_model()
    if model:
        how = opencode_free.status(model, exe)
        if how == "listed":
            return f"free model {model} (OpenCode free tier, no key needed)"
        if how == "name":
            return f"free model {model} by its name (OpenCode's model list unavailable; no key needed)"
        return None
    free = opencode_free.listed(exe)
    if free:
        return f"no key: OpenCode's free models, e.g. {opencode_free.pick(free)} (agentj agent opencode --model …)"
    return None


def login_of(name: str) -> tuple[str, str]:
    if name == "kimi":
        from .agent_kimi import login
        return login()
    if name == "qwen":
        from .agent_qwen import login
        return login()
    if name == "dsh":
        from .agent_dsh import login
        return login()
    return {"claude": claude_login, "codex": codex_login, "opencode": opencode_login}[name]()


def names() -> tuple:
    """NAMES, plus Agent J Harness when the owner turned it on (P359: off by default, AGENTJ_CORE=1)."""
    from .core import enabled
    return NAMES + (("core",) if enabled() else ())


def _one(name: str, environ=None) -> dict:
    if name == "core":                  # P359: built in — nothing to install; "logged in" = a preset key name is set
        from .core.presets import record
        return record(environ)
    from .binaries import resolve, installations, wrapper
    resolution = resolve(name, environ)
    exe = resolution["path"]
    version = version_of(exe) if exe else None
    versions = {exe: version} if exe else {}
    installs = []
    for path in installations(name, environ):
        wrapped = wrapper(path)
        real = os.path.realpath(path)
        if not wrapped and real not in versions:
            versions[real] = version_of(real)
        installs.append({"path": path, "wrapper": wrapped, "selected": bool(exe and real == exe),
                         "version": None if wrapped else versions.get(real)})
    rec = {"name": name, "installed": version is not None, "logged_in": False, "supported": name in SUPPORTED,
           "version": version, "executable": exe, "resolution": resolution,
           "installations": installs}
    if name not in SUPPORTED:
        rec.update(logged_in=None, note=LATER)
        return rec
    if not rec["installed"]:
        return rec
    if name == "opencode" and opencode_v2(version):   # P59 measured 2.0.23; P60: Agent J's own serve speaks v2 (agent_opencode2)
        rec.update(logged_in=None,
                   note="OpenCode v2: keys live in its database (several per provider: `opencode auth switch <provider>` picks "
                        "the active one); the running server's connections are the proof, not the file. Prefer Claude Code.")
        from . import opencode_free
        free = opencode_free.listed(exe)
        if free:
            rec["free_models"] = free
            model = opencode_model()
            rec["free_model"] = model if model in free else opencode_free.pick(free)
            note = opencode_free.privacy_note(rec["free_model"], "en")
            if note:
                rec["privacy"] = note
        return rec
    if name == "kimi":
        from . import agent_kimi
        if not agent_kimi.version_ok(version):
            rec.update(logged_in=None, note="Kimi Code older than 2.2.0: the ACP session methods Agent J uses may be missing; "
                                            "update it (`kimi upgrade`)")
            return rec
        facts = agent_kimi.config_facts()
        if facts["allow_rules"] or facts["guard_off"] or (facts["default_mode"] or "manual") != "manual":
            rec["note"] = ("~/.kimi-code/config.toml: " + ", ".join(x for x in (
                f"{facts['allow_rules']} allow rule(s) run without a phone card" if facts["allow_rules"] else "",
                "dangerous_command_guard = false (Agent J turns it back on)" if facts["guard_off"] else "",
                f"default_permission_mode = {facts['default_mode']} (Agent J asks anyway)"
                if (facts["default_mode"] or "manual") != "manual" else "") if x))
    if name == "qwen":
        from . import agent_qwen
        if not agent_qwen.version_ok(version):
            rec.update(logged_in=None, note="Qwen Code older than 0.25: its ACP mode may lack the session methods Agent J uses; "
                                            "update it (`npm install -g @qwen-code/qwen-code@latest`)")
            return rec
        facts = agent_qwen.settings_facts()
        if facts["allow_rules"] or (facts["mode"] or "default") != "default":
            rec["note"] = ("~/.qwen/settings.json: " + ", ".join(x for x in (
                f"{facts['allow_rules']} permissions.allow rule(s) run without a phone card" if facts["allow_rules"] else "",
                f"tools.approvalMode = {facts['mode']} (Agent J asks anyway)" if (facts["mode"] or "default") != "default"
                else "") if x))
    if name == "dsh":
        from . import agent_dsh
        if not agent_dsh.version_ok(version):
            rec.update(logged_in=None, note="DeepSeek Harness older than 0.2.0: its ACP profile may be missing; update it "
                                            "(`npm install -g @deepseek-ai/dsh@latest`)")
            return rec
        facts = agent_dsh.own_facts()
        rec["note"] = ("developer preview (breaking changes announced); Agent J runs it in the read-only preset, every write / "
                       "command asks the phone" + (" · ~/.dsh would change approvals or plugins: Agent J does not start "
                                                   "dsh until it is moved aside (agentj doctor)" if facts["loose"] else ""))
    status, where = opencode_login(version, exe=exe) if name == "opencode" else login_of(name)
    if name == "opencode":
        rec["note"] = "Local credential hints only; verify the running selected provider connection. Prefer Claude Code."
    if status == "free":       # P298: OpenCode's free models need no login — usable; the installer passes free_model on
        from . import opencode_free
        model = opencode_model()
        rec.update(login="free", note=where + ". Data terms differ per free model; a model of the owner's own is better "
                   "for everyday use. Prefer Claude Code.")
        rec["free_models"] = opencode_free.listed(exe) or []
        rec["free_model"] = model if model else opencode_free.pick(rec["free_models"])
        if opencode_free.privacy_note(rec["free_model"], "en"):
            rec["privacy"] = opencode_free.privacy_note(rec["free_model"], "en")
    if status in ("ok", "env", "free"):
        rec["logged_in"] = True
    elif name == "claude" and os.environ.get("CLAUDECODE") == "1":
        rec.update(logged_in=None, note="running inside Claude Code, which hides its own login from commands: ask the human")
    return rec


def detect(environ=None) -> dict:
    """{"harnesses": [{name, installed, logged_in, supported, version[, note]}], "usable": [...], "decision": ...}."""
    hs = [_one(n, environ) for n in names()]
    usable = [h["name"] for h in hs if h["supported"] and h["installed"] and h["logged_in"] is not False]
    decision = "none" if not usable else f"use:{usable[0]}" if len(usable) == 1 else "ask_owner"
    return {"harnesses": hs, "usable": usable, "decision": decision}


PROBE_TIMEOUT = 60   # s per harness (P155: the switch gate's bound)
SIGNED_OUT_CODES = ("native_failed", "call_failed")   # a timeout / empty reply keeps its own code


def no_login(name: str, model: str | None = None, version: str | None = None) -> bool:
    """P282: True only when this environment shows no login at all for Claude Code / Codex. P298: OpenCode too — no key
    store, no key variable AND the model is not one of its free models (v2 keeps keys in its database: never guessed)."""
    try:
        if name == "opencode":
            return not opencode_v2(version) and opencode_login(version, model)[0] == "warn"
        return name in ("claude", "codex", "kimi", "qwen", "dsh") and login_of(name)[0] == "warn"
    except Exception:  # noqa: BLE001
        return False


def _free_path(model: str | None, h: dict) -> bool:
    """P298: probe OpenCode on its formal chat path when the model is one of its free models, or no model is set and only
    the free models stand behind it (no key store / key variable): the bots' tool-free agent is refused there."""
    from . import opencode_free
    if model:
        return opencode_free.status(model, h.get("executable")) is not None
    return h.get("login") == "free"


def _formal_probe(exe, model, env, timeout) -> str:
    from . import opencode_free
    from .bots.store import BotError
    try:
        return opencode_free.probe(exe, model, env, timeout)
    except opencode_free.ProbeError as e:
        raise BotError(str(e.args[0]) if e.args else "call_failed") from None


def _v1_line(out: str, env: dict) -> str | None:
    """P395: OpenCode 1.x `run --format json` says a failure as an `error` event ({error: {name, data: {message, statusCode}}})
    → the same one redacted line as 2.x (agent_opencode2.probe_line)."""
    from .agent_opencode2 import probe_line
    for raw in reversed(out.splitlines()):
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        err = ev.get("error") if isinstance(ev, dict) and ev.get("type") == "error" else None
        if isinstance(err, dict):
            data = err.get("data") if isinstance(err.get("data"), dict) else {}
            return probe_line({"type": err.get("name"), "status": data.get("statusCode"), "message": data.get("message")},
                              [v for k, v in env.items() if k.endswith(("_KEY", "_TOKEN"))])
    return None


def _limit_code(out: str) -> str | None:
    """P423 #138: a failed probe's own output (read in memory, never kept) → "quota_exhausted" or None. Only the error words
    are read (Claude's `result`, Codex / OpenCode `message` / `error`), never JSON keys (a usage block is no quota)."""
    from . import auth_rescue
    words = []

    def pick(v, key=None):
        if isinstance(v, str) and key in ("result", "message", "error", "detail"):
            words.append(v)
        elif isinstance(v, (dict, list)):
            for k, x in (v.items() if isinstance(v, dict) else ((key, x) for x in v)):
                pick(x, k)
    for line in out.splitlines()[-200:]:
        try:
            pick(json.loads(line))
        except ValueError:
            words.append(line)
    return "quota_exhausted" if auth_rescue.quota_used("\n".join(words)) else None


def _kimi_region() -> str | None:
    try:
        from .state import State
        st = State()
        a = st.agent_config() if st.exists() else None
        return a.get("region") if a else None
    except Exception:  # noqa: BLE001
        return None


def probe(only: tuple | list | None = None, model: str | None = None, timeout: float = PROBE_TIMEOUT) -> dict:
    """One tool-free call per installed supported CLI; never return native output or credentials.

    Detection is advisory. Only a parsed nonempty model reply qualifies. Reuse the
    bots' sandbox/disable-tools contract and binary resolver; never resume a thread.
    P155: `only` limits the calls to those harnesses (the others keep their detect fields, callable False, probe_reason
    "not_probed"); `model` is passed to the single harness probed; each call is bounded by `timeout` seconds and a failure
    carries a fixed code in probe_reason (never native output).
    """
    from types import SimpleNamespace
    from .bots import native
    from .bots.store import BotError
    from pathlib import Path
    import tempfile
    d = detect()
    usable = []
    for h in d["harnesses"]:
        h["callable"] = False
        if only is not None and h["name"] not in only:
            h["probe_reason"] = "not_probed"
            continue
        if not h["installed"] or not h["supported"]:
            h["probe_reason"] = ("ambiguous_install" if h.get("resolution", {}).get("reason") == "ambiguous_install" else "not_installed") if not h["installed"] else "not_supported"
            continue
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("AGENTJ_", "AGENTJARVIS_", "HERDR_", "VIBE_REMOTE_", "TMUX", "SSH_"))}
        try:
            with tempfile.TemporaryDirectory(prefix="agentj-login-probe-") as folder:
                root = Path(folder)
                (root / "system.md").write_text("Reply to the supplied greeting only. No tools are available.")
                cfg = SimpleNamespace(name=h["name"], model=model if only is not None and len(only) == 1 else None)
                oc_model = (cfg.model or opencode_model()) if h["name"] == "opencode" else None
                if h["name"] == "core":          # P359: one tool-free call on the chosen preset, key by name
                    from .core.llm import hello
                    try:
                        reply = hello(cfg.model, timeout)
                    except Exception as e:  # noqa: BLE001 — a fixed code, never the vendor's text
                        raise BotError("quota_exhausted" if _limit_code(str(getattr(e, "detail", "") or ""))   # P423 #138
                                       else "not_logged_in" if getattr(e, "kind", "") == "auth" else "call_failed") from None
                elif h["name"] == "kimi":          # P305: over ACP (stdin), plan mode, every permission refused
                    from . import agent_kimi
                    try:
                        reply = agent_kimi.probe(h["executable"], env, timeout, cfg.model, _kimi_region())
                    except agent_kimi.ProbeError as e:
                        raise BotError(str(e.args[0]) if e.args else "call_failed") from None
                elif h["name"] == "qwen":        # P310: the same ACP probe, `qwen --acp`, the key card's key
                    from . import agent_kimi, agent_qwen
                    try:
                        reply = agent_qwen.probe(h["executable"], env, timeout, cfg.model)
                    except agent_kimi.ProbeError as e:
                        raise BotError(str(e.args[0]) if e.args else "call_failed") from None
                elif h["name"] == "dsh":         # P329: the same ACP probe, `dsh --profile acp`, Agent J's route / key card
                    from . import agent_kimi, agent_dsh
                    try:
                        reply = agent_dsh.probe(h["executable"], env, timeout, cfg.model)
                    except agent_kimi.ProbeError as e:
                        raise BotError(str(e.args[0]) if e.args else "call_failed") from None
                elif h["name"] == "opencode" and opencode_v2(h.get("version")):   # P395 (#144): 2.x on its own /api
                    from . import agent_opencode2, opencode_free
                    try:
                        reply = agent_opencode2.probe(h["executable"], oc_model, env, timeout,
                                                      waits=opencode_free.WARM_WAITS if _free_path(oc_model, h) else ())
                    except opencode_free.ProbeError as e:
                        if len(e.args) > 1 and e.args[1]:
                            h["probe_error"] = e.args[1]
                        raise BotError(str(e.args[0]) if e.args else "call_failed") from None
                elif h["name"] == "opencode" and _free_path(oc_model, h):
                    reply = _formal_probe(h["executable"], oc_model, env, timeout)   # P298: the free tier's own path
                else:
                    cmd = native.command(cfg, root, env, selected=h["executable"])
                    why, raw = [], None
                    try:
                        raw = native.run(cmd, "Hello. Reply with a short greeting only.", env, root, timeout=timeout, why=why)
                        reply, _ = native.parse(h["name"], raw)
                    except BotError as e:      # P298: refused as "not OpenCode's own client" → the formal chat path
                        if h["name"] != "opencode" or not e.args or e.args[0] != "free_tier":
                            line = _v1_line(why[0] if why else "", env) if h["name"] == "opencode" else None
                            if line:
                                h["probe_error"] = line       # P395: OpenCode's own error, one redacted line
                            limit = _limit_code((why[0] if why else "") + "\n" + (raw or ""))
                            if limit:                         # P423 #138: a used-up quota is no login
                                raise BotError(limit) from None
                            raise
                        reply = _formal_probe(h["executable"], oc_model, env, timeout)
                if not isinstance(reply, str) or not reply.strip():
                    raise BotError("empty_probe_reply")
            h["callable"] = True
            usable.append(h["name"])
        except BotError as e:
            code = str(e.args[0]) if e.args else ""
            h["probe_reason"] = code if re.fullmatch(r"[a-z_]{1,40}", code) else "call_failed"
        except (OSError, ValueError, TypeError):
            h["probe_reason"] = "call_failed"
        if not h["callable"] and h.get("probe_reason") in SIGNED_OUT_CODES and _limit_code(h.get("probe_error") or ""):
            h["probe_reason"] = "quota_exhausted"   # P423 #138: OpenCode 2.x said its quota is used up (its own line)
        if not h["callable"] and h.get("probe_reason") in SIGNED_OUT_CODES and no_login(
                h["name"], model if only is not None and len(only) == 1 else None, h.get("version")):
            h["probe_reason"] = "not_logged_in"     # P282: say why — no login this environment can see — not "failed"
    d.update(usable=usable, decision="none" if not usable else f"use:{usable[0]}" if len(usable) == 1 else "ask_owner")
    return d


def summary(d: dict) -> str:
    """One line for `agentj doctor`."""
    parts = []
    for h in d["harnesses"]:
        if not h["installed"]:
            parts.append(f"{LABEL[h['name']]} —")
        elif not h["supported"]:
            parts.append(f"{LABEL[h['name']]} (not supported yet)")
        else:
            parts.append(f"{LABEL[h['name']]} " + {True: "✓", False: "not logged in", None: "login ?"}[h["logged_in"]])
    return " · ".join(parts) + f" → {d['decision']}"


DECISION_TEXT = {
    "none": "没有可用的 Agent：优先安装并登录 Claude Code；也可使用 Codex 或 OpenCode 并配好模型（install.md 第 3 步）/ no usable "
            "agent: prefer Claude Code; otherwise use Codex or configure an OpenCode provider (install.md Step 3)",
    "ask_owner": "有多个可用：问你的人类要用哪一个，不要替他选 / more than one is usable: ask your human which one — never pick one yourself",
}


def reach(d: dict) -> dict:
    """P175: Codex installed → is OpenAI reachable from here (advice only; `decision` is unchanged)."""
    if any(h["name"] == "codex" and h["installed"] for h in d["harnesses"]):
        from . import chatgpt_reach
        r = chatgpt_reach.probe()
        if r == "skipped":
            return d
        d["chatgpt"] = r
        if r in chatgpt_reach.ADVICE:
            d["advice"] = chatgpt_reach.ADVICE[r]
    return d


def main(as_json: bool = False, call_probe: bool = False, only=None, model: str | None = None) -> int:
    d = reach(probe(only=only, model=model) if call_probe else detect())
    if as_json:
        def local_paths(value):
            if isinstance(value, dict): return {k: local_paths(v) for k, v in value.items()}
            if isinstance(value, list): return [local_paths(v) for v in value]
            if isinstance(value, str):
                home = os.path.expanduser("~")
                return "~" + value[len(home):] if value == home or value.startswith(home + os.sep) else value
            return value
        print(json.dumps(local_paths(d), ensure_ascii=False))
        return 0
    for h in d["harnesses"]:
        state = ("没装 / not installed" if not h["installed"] else
                 f"{h['version']} · " + ("暂不支持 / not supported yet (" + LATER + ")" if not h["supported"] else
                                          {True: "已登录 / logged in", False: "没登录 / not logged in",
                                           None: "登录状态未知 / login unknown"}[h["logged_in"]]))
        if h.get("probe_reason") in ("quota_exhausted", "rate_limited"):   # P423 #138: the probe's word, not the login file's
            state += (" · 额度 / 用量已用完，不是没登录 / quota used up, not a login problem" if h["probe_reason"] == "quota_exhausted"
                      else " · 被限流，等一会儿再试 / rate limited, try again later")
        print(f"{LABEL[h['name']]:<12} {state}")
    d_ = d["decision"]
    print("→ " + (DECISION_TEXT[d_] if d_ in DECISION_TEXT else
                  f"只有 {LABEL[d_[4:]]} 可用 / only {LABEL[d_[4:]]} is usable: agentj agent {d_[4:]} --dir <folder>"))
    if d.get("advice"):
        print("⚠ Codex: " + d["advice"])
    return 0
