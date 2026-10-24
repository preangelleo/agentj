"""P305: Kimi Code CLI (`kimi`, Moonshot AI, MIT, 2.2.0+) as Agent J's fourth harness, on the generic ACP adapter (acp.py).

Measured in an isolated HOME on 2.2.0 (reports/qa/p305/): `kimi acp` answers `initialize` at once (agentInfo "Kimi Code CLI",
loadSession, session list / resume / close / delete / fork, one `terminal` auth method "login" = `kimi login`), and with no
login `session/new` is refused with ACP `auth_required` (-32000) — that answer is the login-rescue signal.

Logins (both the owner's own, both outside the Agent's reach):
- the Kimi membership: `kimi login` — the official device-code flow (a link + a one-time code; the owner approves on any device
  signed in to Kimi; the CLI polls and writes `~/.kimi-code/credentials/<name>.json` itself). Two sites: `--region mainland-cn`
  (kimi.com, the default) and `--region global` (kimi.ai); a kimi.com member key also works on api.kimi.ai (measured 10-16). auth_rescue.KimiFlow runs it.
- an API key, only through the phone's key card (`agentj secret request`, P279): `KIMI_API_KEY` = a Kimi Code console key
  (the membership's key: api.kimi.com/coding/v1, or api.kimi.ai/coding/v1 for kimi.ai), `MOONSHOT_API_KEY` = the open platform
  pay-as-you-go key (api.moonshot.cn/v1 · api.moonshot.ai/v1). Kimi never reads a key from the shell by itself; Agent J hands
  it over as Kimi's own in-memory model variables (`KIMI_MODEL_*`) for this process only — nothing is written to Kimi's
  config — and only when Kimi has no login and no provider of its own. The owner's own scheduled tasks / workflow CEOs get
  the membership key too (P348, fence.PLAN_KEY_ENV), never MOONSHOT_API_KEY; support bots get none.

Security (the owner's computer, the owner's Kimi): Kimi has no OS sandbox and its hooks fail open on error / timeout, so
neither is relied on — the fence is Agent J's, every approval is an ACP permission request that serve answers from the phone
(acp.py), the session is forced to Kimi's "default" (manual) mode, and its dangerous-command guard is forced on
(KIMI_CODE_DANGEROUS_COMMAND_GUARD=true). The owner's own explicit `allow` rules in ~/.kimi-code/config.toml stay theirs (bridge ≤
session): `agentj doctor` / `agentj agent detect` say when they exist.
"""
from __future__ import annotations

import json
import os
import re
import selectors
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from .acp import AcpAgent, AUTH_REQUIRED, PROTOCOL, err_text, pick_options, selected

LABEL = "Kimi Code"
REGIONS = ("mainland-cn", "global")
SITE = {"mainland-cn": "kimi.com", "global": "kimi.ai"}
CODING_BASE = {"mainland-cn": "https://api.kimi.com/coding/v1", "global": "https://api.kimi.ai/coding/v1"}
PLATFORM_BASE = {"mainland-cn": "https://api.moonshot.cn/v1", "global": "https://api.moonshot.ai/v1"}
KEY_ENV = "KIMI_API_KEY"               # Kimi Code console key (the membership) — the same card as P279's OpenCode route
PLATFORM_KEY_ENV = "MOONSHOT_API_KEY"  # open platform, pay as you go
CODING_MODEL = "kimi-for-coding"
PLATFORM_MODEL = "kimi-k3.1-pro"
GUARD_ENV = "KIMI_CODE_DANGEROUS_COMMAND_GUARD"
MIN_VERSION = (2, 2, 0)
PROBE_PROMPT = "Hello. Reply with a short greeting only."


def home(environ=None) -> Path:
    env = os.environ if environ is None else environ
    h = env.get("KIMI_CODE_HOME")
    return Path(h).expanduser() if h else Path(env.get("HOME") or os.path.expanduser("~")) / ".kimi-code"


def _config_text(environ=None) -> str:
    try:
        p = home(environ) / "config.toml"
        return p.read_text(encoding="utf-8")[:200_000] if p.is_file() and not p.is_symlink() else ""
    except OSError:
        return ""


def config_facts(environ=None) -> dict:
    """What ~/.kimi-code/config.toml says that matters here — table names and two settings only, never a value of a key:
    {"providers": bool, "allow_rules": int, "default_mode": str | None, "guard_off": bool}."""
    t = _config_text(environ)
    try:
        import tomllib
        d = tomllib.loads(t) if t else {}
    except (ValueError, ImportError):
        d = {}
    prov = d.get("providers") if isinstance(d.get("providers"), dict) else {}
    perm = d.get("permission") if isinstance(d.get("permission"), dict) else {}
    rules = perm.get("rules") if isinstance(perm.get("rules"), list) else []
    mode = d.get("default_permission_mode")
    return {"providers": bool(prov), "allow_rules": sum(1 for r in rules if isinstance(r, dict) and r.get("decision") == "allow"),
            "default_mode": mode if isinstance(mode, str) and len(mode) <= 16 else None,
            "guard_off": perm.get("dangerous_command_guard") is False}


def oauth_files(environ=None) -> list[Path]:
    """Kimi's own OAuth credential files (existence only, never opened)."""
    d = home(environ) / "credentials"
    try:
        return sorted(p for p in d.iterdir() if p.suffix == ".json" and p.is_file() and not p.is_symlink())
    except OSError:
        return []


def login(environ=None) -> tuple[str, str]:
    """(ok | env | warn, where) — existence hints only, like the other harnesses; key validity is not checked here."""
    from .service import tilde
    env = os.environ if environ is None else environ
    files = oauth_files(env)
    if files:
        return "ok", f"login {tilde(str(files[0].parent))} (kimi login)"
    if config_facts(env)["providers"]:
        return "ok", f"provider in {tilde(str(home(env) / 'config.toml'))} (key validity unverified)"
    for k in (KEY_ENV, PLATFORM_KEY_ENV):
        if env.get(k):
            return "env", f"env {k} (name only; Agent J hands it to Kimi)"
    return "warn", "no login (kimi login · or a key card: KIMI_API_KEY)"


def version_ok(version: str | None) -> bool:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", version or "")
    return bool(m) and tuple(int(x) for x in m.groups()) >= MIN_VERSION


def fresh_keys(base: dict, names=(KEY_ENV, PLATFORM_KEY_ENV)) -> dict:
    """The key names from the service env file as it is NOW (a key card saved after serve started), else `base`."""
    out = {k: base[k] for k in names if base.get(k)}
    try:
        from . import service
        lines = Path(service.env_file(service.name())).read_text().splitlines()
    except Exception:  # noqa: BLE001 — no service: what serve's own environment has
        return out
    for line in lines:
        k, sep, v = line.strip().partition("=")
        if sep and k in names:
            try:
                parts = shlex.split(v, comments=False)
            except ValueError:
                continue
            if len(parts) == 1 and parts[0]:
                out[k] = parts[0]
    return out


def key_environment(env: dict, region: str | None, model: str | None, source: dict | None = None,
                    names=(KEY_ENV, PLATFORM_KEY_ENV)) -> dict:
    """Kimi's in-memory model variables for an API key — only when Kimi has no login / provider of its own. `source` = where
    the key is read from (serve's environment); the returned dict holds the variables to add (never logged)."""
    if oauth_files(env) or config_facts(env)["providers"]:
        return {}
    keys = fresh_keys(source if source is not None else os.environ, names)
    r = region if region in REGIONS else "mainland-cn"
    if keys.get(KEY_ENV):
        return {"KIMI_MODEL_NAME": model or CODING_MODEL, "KIMI_MODEL_API_KEY": keys[KEY_ENV],
                "KIMI_MODEL_BASE_URL": CODING_BASE[r], "KIMI_MODEL_PROVIDER_TYPE": "kimi"}
    if keys.get(PLATFORM_KEY_ENV):
        return {"KIMI_MODEL_NAME": model or PLATFORM_MODEL, "KIMI_MODEL_API_KEY": keys[PLATFORM_KEY_ENV],
                "KIMI_MODEL_BASE_URL": PLATFORM_BASE[r], "KIMI_MODEL_PROVIDER_TYPE": "kimi"}
    return {}


def login_text(region: str | None, lang: str = "zh") -> str:
    r = region if region in REGIONS else "mainland-cn"
    if lang == "en":
        return (f"Kimi Code is not signed in. On the phone: Settings → Emergency repair → AI login rescue (device code, {SITE[r]}); or at the Agent J host "
                f"`kimi login --region {r}` (where you bought it: kimi.com = mainland-cn, kimi.ai = global; a kimi.com key works on both). With a "
                f"Kimi Code console key instead: the Agent sends a key card (`agentj secret request --name {KEY_ENV} …`).")
    return (f"Kimi Code 没登录。手机 设置 → 紧急修复 →「AI 登录救援」用设备码登录（{SITE[r]}）；或在Agent J 主机上运行 `kimi login --region {r}`"
            f"（按会员在哪个站买的选：kimi.com = mainland-cn，kimi.ai = global；kimi.com 的 key 两个地址都能用）。用 Kimi Code 控制台的 key 也行："
            f"让 Agent 发手机密钥卡（`agentj secret request --name {KEY_ENV} …`），不要把 key 发在对话里。")


def quota_text(region: str | None, lang: str = "zh") -> str:
    """P423 #138: Kimi's monthly usage limit (sent as auth_required) — the membership's quota, not the login."""
    r = region if region in REGIONS else "mainland-cn"
    console = f"https://www.{SITE[r]}/code/console"
    if lang == "en":
        return (f"Kimi Code's usage for this month is used up — you are still signed in, no new login needed. Renew or add usage "
                f"(usage and plan: Kimi Code console {console}), then send again; or switch to another Agent for now "
                f"(/switch-harness).")
    return (f"Kimi 本月额度已用完——不是没登录，不用重新登录。续费 / 加量后再试（用量和套餐看 Kimi Code 控制台 {console}），"
            f"或先切到别的 Agent（菜单 /switch-harness）。")


class KimiAgent(AcpAgent):
    """`kimi acp`, one session per conversation, approvals to the phone."""
    kind = "kimi"
    LABEL = LABEL
    BIN_ENV, BIN_NAME = "AGENTJ_KIMI_BIN", "kimi"
    MODE_DEFAULT = "default"                 # Kimi: manual approvals, tools run normally
    MODES_LOOSER = ("auto", "yolo")

    def child_env(self, env: dict) -> dict:
        env = dict(env)
        env[GUARD_ENV] = "true"              # Kimi's own dangerous-command policy stays on whatever the owner's file says
        # P348: the owner's own tasks / workflow CEOs may use the membership key (fence.PLAN_KEY_ENV), never the
        # pay-as-you-go MOONSHOT_API_KEY (tasks otherwise authenticate from native login storage only)
        names = (KEY_ENV,) if self.cfg.get("_workflow_ceo") else (KEY_ENV, PLATFORM_KEY_ENV)
        env.update(key_environment(env, self.cfg.get("region"), self.cfg.get("model"), names=names))
        return env

    def login_hint(self) -> str:
        return login_text(self.cfg.get("region"), getattr(self.host, "lang", "zh"))

    def quota_hint(self) -> str:
        return quota_text(self.cfg.get("region"), getattr(self.host, "lang", "zh"))


# ------------------------------------------------------------------ one tool-free call (the switch gate, harness.probe)
class ProbeError(Exception):
    """args[0]: a fixed reason code."""


def probe(exe: str, env: dict, timeout: float = 60, model: str | None = None, region: str | None = None,
          argv: list[str] | None = None, key_env: bool = True) -> str:
    """initialize → session/new in an empty temporary folder → the harness's plan mode (read-only, no tool runs) → one
    greeting. Every permission request is refused; the prompt goes over stdin (never argv). → the reply text, or
    ProbeError(code). P310: `argv` / `key_env=False` = another ACP harness (agent_qwen.probe) that set its own variables."""
    env = dict(env)
    if key_env:
        env[GUARD_ENV] = "true"
        keyed = key_environment(env, region, model, source=env)
        env.update(keyed)
        if keyed:
            model = None                      # the key's model is KIMI_MODEL_NAME already
    with tempfile.TemporaryDirectory(prefix="agentj-acp-probe-") as cwd:
        try:
            proc = subprocess.Popen(argv or [exe, "acp"], cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        except FileNotFoundError:
            raise ProbeError("native_executable_missing") from None
        except PermissionError:
            raise ProbeError("native_executable_not_executable") from None
        except OSError:
            raise ProbeError("native_spawn_failed") from None
        deadline = time.monotonic() + timeout
        buf, text, n = b"", [], [0]
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)

        def send(obj):
            proc.stdin.write((json.dumps(obj) + "\n").encode())
            proc.stdin.flush()

        def req(method, params):
            n[0] += 1
            send({"jsonrpc": "2.0", "id": n[0], "method": method, "params": params})
            return n[0]

        def wait(rid):
            nonlocal buf
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise ProbeError("native_timeout")
                for key, _ in sel.select(min(1.0, left)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        raise ProbeError("native_failed")
                    buf += chunk
                    if len(buf) > 2 * 1024 * 1024:
                        raise ProbeError("native_output_limit")
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(m, dict):
                        continue
                    if "method" in m and "id" in m:       # a permission request (or anything else): refused
                        _, rej, _ = pick_options((m.get("params") or {}).get("options"))
                        if m["method"] == "session/request_permission":
                            send({"jsonrpc": "2.0", "id": m["id"], "result": selected(rej)})
                        else:
                            send({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "not supported"}})
                    elif m.get("id") == rid:
                        if "error" in m:
                            code = (m["error"] or {}).get("code") if isinstance(m["error"], dict) else None
                            from . import auth_rescue     # P423 #138: a used-up quota (even as auth_required) is no login
                            words = err_text(m["error"])
                            cls = "balance" if auth_rescue.quota_used(words) else auth_rescue.classify("kimi", words)
                            raise ProbeError("quota_exhausted" if cls == "balance" else "rate_limited" if cls == "rate_limit"
                                             else "not_logged_in" if code == AUTH_REQUIRED else "native_failed")
                        return m.get("result") or {}
                    elif m.get("method") == "session/update":
                        u = (m.get("params") or {}).get("update") or {}
                        if u.get("sessionUpdate") == "tool_call":
                            raise ProbeError("native_tool_refused")
                        c = u.get("content") if isinstance(u.get("content"), dict) else {}
                        if u.get("sessionUpdate") == "agent_message_chunk" and isinstance(c.get("text"), str):
                            text.append(c["text"])
        try:
            wait(req("initialize", {"protocolVersion": PROTOCOL, "clientCapabilities": {"fs": {"readTextFile": False,
                                    "writeTextFile": False}, "terminal": False},
                                    "clientInfo": {"name": "agentj-probe", "version": "1"}}))
            s = wait(req("session/new", {"cwd": cwd, "mcpServers": []}))
            sid = s.get("sessionId") if isinstance(s, dict) else None
            if not isinstance(sid, str):
                raise ProbeError("native_failed")
            try:
                wait(req("session/set_mode", {"sessionId": sid, "modeId": "plan"}))
            except ProbeError as e:
                if e.args[0] == "native_timeout":
                    raise
            if model:
                try:
                    wait(req("session/set_config_option", {"sessionId": sid, "configId": "model", "value": model}))
                except ProbeError as e:
                    if e.args[0] == "native_timeout":
                        raise
                    raise ProbeError("unknown_model") from None
            r = wait(req("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": PROBE_PROMPT}]}))
            if r.get("stopReason") not in ("end_turn", None):
                raise ProbeError("native_failed")
            return "".join(text)
        except (BrokenPipeError, OSError):
            raise ProbeError("native_failed") from None
        finally:
            sel.close()
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, 15)
                    proc.wait(timeout=3)
                except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(proc.pid, 9)
                    except (ProcessLookupError, PermissionError):
                        pass
            for f in (proc.stdin, proc.stdout):
                try:
                    f.close()
                except OSError:
                    pass
