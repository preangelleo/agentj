"""P329: DeepSeek Harness (`dsh`, DeepSeek, MIT, developer preview 0.2.0-rc.2) as Agent J's sixth harness — the third on
P305's generic ACP adapter (acp.py). It is also the harness inside Olares' own agent Lares (Lares 0.26.42 bundles dsh
0.2.0-rc.2), so on an Olares box Agent J drives the same engine, pointed at the box's own Router.

Measured on 0.2.0-rc.2 in an isolated HOME / DSH_HOME, against an Olares Router (Qwen3.8-27B) — reports/qa/p329/:
- `dsh --profile acp` answers `initialize` at once: agentInfo deepseek-harness-acp, session list / resume / close (no load,
  no fork), NO authMethods, promptCapabilities image / embeddedContext false (acp._block sends the identity as text);
- a session has NO ACP modes: only a `model` config option, whose values are JSON pairs '["<provider>","<model>"]'
  (shown and stored here as the plain model id);
- approvals: dsh's default preset `workspace-write` runs shell commands and file writes inside its own bwrap sandbox WITHOUT
  asking. Agent J starts it with DSH_PERMISSION_MODE=read-only (approval "ask"): anything that writes or runs outside a
  read-only sandbox becomes an escalation retry, which arrives as `session/request_permission` with exactly allow_once /
  reject_once → the phone card (acp._permission). Reads run without a card, as with the other harnesses;
- tool titles are lower case (bash, write, edit, read, glob, grep, web_fetch …) with kind "other": TOOL_ALIAS maps them;
- `session/cancel` ends a turn with stopReason "cancelled"; `session/resume` in a new process continues the conversation;
- `/compact` is not an ACP command (sent as a prompt it is just answered): dsh compacts by itself near the window;
- its own bwrap nests inside Agent J's fence (both unprivileged user namespaces) on Linux;
- dsh loads `<process cwd>/.env` into its own environment, and the model's shell sees it. So the process starts in DSH_HOME
  (the owner's own dsh folder, whose .env dsh reads anyway), never in the working folder, which may hold a project's or the
  owner's keys; each session still opens in the working folder (`session/new` cwd) — shell and edits happen there.
- telemetry: off (DSH_TELEMETRY_MODE=DISABLED — no session record leaves the Agent J host, not even after /feedback).

Logins (the owner's, never the Agent's):
- the owner's own dsh setup: `$DSH_HOME/.credentials.yaml` (dsh web's Models page), `$DSH_HOME/.env`, or providers in
  `$DSH_HOME/cordis.patch.yml` — used as they are;
- an OpenAI-compatible route Agent J writes (`agentj agent dsh --base-url <…/v1> --model <id>`): `$DSH_HOME/agentj-route.yml`
  (address + model, the key only by NAME: DSH_ROUTER_API_KEY from the phone's key card; a keyless local server gets a
  placeholder). This is how an Olares Router, LiteLLM, Ollama or llama.cpp server is used;
- a DeepSeek platform key from the key card: DEEPSEEK_API_KEY (pay as you go).
Keys reach dsh only as in-memory variables of this process; tasks never get them (fence.auxiliary_environment).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shlex
from pathlib import Path
from urllib.parse import urlparse

from .acp import AcpAgent
from .slash import MODEL_RE, Result
from .text import clean_line

LABEL = "DeepSeek Harness"
MIN_VERSION = (0, 2, 0)
ROUTE_KEY_ENV = "DSH_ROUTER_API_KEY"
DEEPSEEK_KEY_ENV = "DEEPSEEK_API_KEY"
KEY_NAMES = (ROUTE_KEY_ENV, DEEPSEEK_KEY_ENV)
# dsh's shell tool drops every inherited DSH_* variable from the model's shell calls: the keys dsh itself reads go in under
# DSH_* names only (the DeepSeek key through a canonical patch that points dsh's DeepSeek plugins at DSH_DEEPSEEK_API_KEY)
DEEPSEEK_INNER_ENV = "DSH_DEEPSEEK_API_KEY"
DEEPSEEK_FILE = "agentj-deepseek.yml"
STATE_COPY = "dsh-route.json"           # the route's source of truth, in Agent J's state (hidden from the fenced Agent)
# rows of an owner / profile patch layer that may stay: models, providers, web search — nothing about approvals, sandboxes,
# shells, plugins or telemetry (P329 security review H1: a patch row replaces the row that reads DSH_PERMISSION_MODE)
SAFE_ROWS = frozenset({"llm-pi-ai", "agent-default-model", "acp", "llm-retry", "web", "web-search-deepseek", "web-fetch-http",
                       "tool-web", "session-title", "session-title-llm", "compaction-basic", "spill-policy",
                       "tool-result-pruner", "agent-instructions", "skill-filesystem", "llm-deepseek"})
DEFAULT_BUNDLES = ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-acp-app"]
NO_KEY = "agentj-no-key"                  # pi-ai's OpenAI-compatible client needs some key; a keyless local server ignores it
ROUTE_FILE = "agentj-route.yml"
ROUTE_PROVIDER = "agentj-router"
FORCED = {"DSH_PERMISSION_MODE": "read-only",     # approval "ask": every write / exec escalation is a phone card
          "DSH_TELEMETRY_MODE": "DISABLED", "DSH_TELEMETRY_DISABLED": "1"}
TOOL_ALIAS = {"bash": "Bash", "write": "Write", "edit": "Edit", "read": "Read", "read_image": "Read", "glob": "Glob",
              "grep": "Grep", "web_fetch": "WebFetch", "web_search": "WebSearch"}


def home(environ=None) -> Path:
    env = os.environ if environ is None else environ
    h = env.get("DSH_HOME")
    return Path(h).expanduser() if h else Path(env.get("HOME") or os.path.expanduser("~")) / ".dsh"


def _small(p: Path, limit: int = 200_000) -> str:
    try:
        return p.read_text(encoding="utf-8")[:limit] if p.is_file() and not p.is_symlink() else ""
    except (OSError, UnicodeDecodeError):
        return ""


def own_facts(environ=None) -> dict:
    """What the owner's own dsh files say that matters here — existence and names only, never a value:
    {"credentials": bool, "dotenv_key": bool, "providers": bool, "loose": bool (a layer config_problem refuses)}."""
    h = home(environ)
    dotenv = _small(h / ".env")
    patch = _small(h / "cordis.patch.yml")
    names = {ln.split("=", 1)[0].strip().removeprefix("export ").strip() for ln in dotenv.splitlines() if "=" in ln}
    return {"credentials": (h / ".credentials.yaml").is_file(),
            "dotenv_key": any(n.endswith(("_API_KEY", "_TOKEN")) for n in names),
            "providers": bool(re.search(r"^\s*providers\s*:", patch, re.M)),
            "loose": bool(config_problem(environ))}


# ------------------------------------------------------------------ the route Agent J writes (address + model, no key)
def route_path(environ=None) -> Path:
    return home(environ) / ROUTE_FILE


def check_url(url: str) -> str | None:
    """None when usable; else a reason. https anywhere, http only on the Agent J host / a private LAN address."""
    try:
        u = urlparse(url)
    except ValueError:
        return "bad_url"
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password or u.query or u.fragment or \
            len(url) > 300 or any(c.isspace() for c in url):
        return "bad_url"
    if u.scheme == "http":
        h = u.hostname
        private = h in ("localhost", "::1") or re.fullmatch(r"(127|10)\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|"
                                                            r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+", h)   # not *.local (mDNS)
        if not private:
            return "http_not_local"
    return None


def route_text(base_url: str, model: str, context: int | None = None) -> str:
    """The patch file: JSON strings are YAML double-quoted scalars, so nothing in a value can change the structure.
    `context` = the server's own context size (else pi-ai assumes 262,144 and dsh would compact too late)."""
    q = json.dumps
    return ("# Written by Agent J (`agentj agent dsh --base-url … --model …`). Delete this file to stop using the route.\n"
            "# The key is read by NAME only (DSH_ROUTER_API_KEY); no secret is stored here.\n"
            "- id: llm-pi-ai\n  config:\n    providers:\n"
            f"      {ROUTE_PROVIDER}:\n        displayName: {q('Agent J route')}\n        api: openai-completions\n"
            f"        baseURL: {q(base_url)}\n        apiKeyEnv: {ROUTE_KEY_ENV}\n        models:\n"
            f"          - id: {q(model)}\n" + (f"            contextWindow: {int(context)}\n" if context else "") +
            f"- id: acp\n  config:\n    provider: {ROUTE_PROVIDER}\n    model: {q(model)}\n"
            f"- id: agent-default-model\n  config:\n    provider: {ROUTE_PROVIDER}\n    model: {q(model)}\n")


def server_context(base_url: str, model: str, key: str | None, timeout: float = 10) -> int | None:
    return server_probe(base_url, model, key, timeout)[0]


def server_probe(base_url: str, model: str, key: str | None, timeout: float = 10) -> tuple[int | None, bool]:
    """(context, needs_key). context = the model's context size from the server's own list (GET <base>/models: Olares
    Router `context_size`, vLLM `max_model_len`, OpenRouter `context_length`), None when the list says nothing or does not
    answer; needs_key = asked without a key, the server answered 401 / 403 (P329 live: the Olares Router). The key, if any,
    goes only into this request's header."""
    import urllib.error
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):     # urllib would copy the Authorization header to the new address
            return None
    req = urllib.request.Request(base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as r:   # noqa: S310 — check_url'd address
            data = json.loads(r.read(2_000_000))
    except urllib.error.HTTPError as e:
        return None, not key and e.code in (401, 403)
    except Exception:  # noqa: BLE001 — optional: the default window stays
        return None, False
    for m in data.get("data") or [] if isinstance(data, dict) else []:
        if isinstance(m, dict) and model in (m.get("id"), m.get("qualified_id")):
            for k in ("context_size", "max_model_len", "context_length", "context_window"):
                v = m.get(k)
                if type(v) is int and 1024 <= v <= 10_000_000:
                    return v, False
    return None, False


def _write_file(p: Path, text: str) -> None:
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".agentj-tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, p)


def _state_copy() -> Path | None:
    try:
        from .state import state_dir
        return Path(state_dir()) / STATE_COPY
    except Exception:  # noqa: BLE001 — no state yet (detect before init): the dsh file alone
        return None


def saved_route() -> dict | None:
    """The route as `agentj agent dsh --base-url` saved it in Agent J's state, or None."""
    p = _state_copy()
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p and p.is_file() and not p.is_symlink() else None
    except (OSError, ValueError):
        return None
    if not (isinstance(d, dict) and isinstance(d.get("base_url"), str) and isinstance(d.get("model"), str)) or \
            check_url(d["base_url"]) or not MODEL_RE.match(d["model"]):
        return None
    c = d.get("context")
    return {"base_url": d["base_url"], "model": d["model"], "context": c if type(c) is int and 1024 <= c <= 10_000_000 else None,
            "needs_key": d.get("needs_key") is True, "keyed_probe": d.get("keyed_probe") is True}


def write_route(base_url: str, model: str, environ=None, context: int | None = None, needs_key: bool = False,
                keyed_probe: bool = False) -> Path:
    why = check_url(base_url)
    if why:
        raise ValueError(why)
    if not MODEL_RE.match(model or ""):
        raise ValueError("bad_model")
    base_url = base_url.rstrip("/")
    p = route_path(environ)
    _write_file(p, route_text(base_url, model, context))
    sc = _state_copy()
    if sc is not None and sc.parent.is_dir():          # never creates the state folder (before `agentj init`)
        with contextlib.suppress(OSError):
            _write_file(sc, json.dumps({"base_url": base_url, "model": model, "context": context, "needs_key": needs_key,
                                        "keyed_probe": keyed_probe}))
    return p


def deepseek_text() -> str:
    return ("# Written by Agent J: dsh reads the DeepSeek key from the phone's key card under a DSH_* name, which dsh never\n"
            "# passes to the model's shell. No secret is stored here.\n"
            f"- id: llm-deepseek\n  config:\n    apiKeyEnv: {DEEPSEEK_INNER_ENV}\n"
            f"- id: web-search-deepseek\n  config:\n    apiKeyEnv: {DEEPSEEK_INNER_ENV}\n")


def patch_problem(text: str) -> str | None:
    """None when an owner / profile patch layer only touches SAFE_ROWS with plain values; else a short reason. A line-based
    check that refuses what it cannot vouch for: YAML tags (`!!js` runs code in dsh), anchors / aliases / merges, flow
    collections at entry level, entries by plugin `name`, and any entry key other than `config`."""
    body = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not body or [ln.strip() for ln in body] == ["[]"]:
        return None
    for ln in body:
        if re.search(r"!!|![A-Za-z]|(^|[\s\[{,])[&*][A-Za-z0-9_-]|<<\s*:", ln):
            return "yaml_code_or_alias"
        if ln.startswith("-"):
            m = re.fullmatch(r"-\s+id:\s*['\"]?([A-Za-z0-9_-]+)['\"]?\s*", ln)
            if not m:
                return "entry_not_by_id"
            if m.group(1) not in SAFE_ROWS:
                return "row:" + m.group(1)[:40]
        elif re.match(r"\S", ln):
            return "not_a_list"
        elif re.match(r" {1,3}\S", ln) and not re.fullmatch(r" {1,3}config:\s*", ln):
            return "entry_key"
    return None


def config_problem(environ=None) -> str | None:
    """Checked by serve OUTSIDE the fence before every dsh start (the fenced Agent can write ~/.dsh): None = safe to start.
    The route file is regenerated from Agent J's saved copy; the owner's / profile's patch layers and plugins must not
    change approvals, sandboxes, shells or telemetry."""
    h = home(environ)
    for rel in ("cordis.patch.yml", "profiles/acp/cordis.patch.yml"):
        p = h / rel
        if p.is_symlink():
            return f"{rel}: symlink"
        why = patch_problem(_small(p, 500_000)) if p.is_file() else None
        if why:
            return f"{rel}: {why}"
    pkg = h / "profiles" / "acp" / "package.json"
    if pkg.exists():
        try:
            d = json.loads(_small(pkg, 200_000) or "null")
        except ValueError:
            d = None
        prof = ((d or {}).get("dsh") or {}).get("profile") if isinstance(d, dict) else None
        if not isinstance(d, dict) or d.get("dependencies") not in (None, {}) or \
                not isinstance(prof, dict) or prof.get("bundles") != DEFAULT_BUNDLES:
            return "profiles/acp/package.json: plugins"
    if (h / "profiles" / "acp" / "cordis.yml").is_file() and patch_problem(_small(h / "profiles" / "acp" / "cordis.yml")):
        return "profiles/acp/cordis.yml: changed"
    return None


def prepare(environ=None) -> str | None:
    """Before a start (serve, outside the fence): regenerate Agent J's own files from Agent J's state, then check the
    owner's layers. → None, or the reason dsh must not start."""
    saved = saved_route()
    rp = route_path(environ)
    if saved and saved["needs_key"] and not saved["context"] and not saved["keyed_probe"]:
        # P329 live: the Olares Router lists its models only with a key, so `agentj agent dsh` could not read the context
        # size (dsh would assume 262,144 and compact far too late). Once the key card is saved: ask once, keep the answer.
        k = fresh_keys(dict(os.environ)).get(ROUTE_KEY_ENV)
        if k:
            ctx = server_probe(saved["base_url"], saved["model"], k, timeout=5)[0]
            with contextlib.suppress(OSError, ValueError):
                write_route(saved["base_url"], saved["model"], environ, context=ctx, needs_key=True, keyed_probe=True)
                saved = saved_route() or saved
    try:
        if saved:
            want = route_text(saved["base_url"], saved["model"], saved["context"])
            if _small(rp, 20_000) != want or rp.is_symlink():
                _write_file(rp, want)
        elif rp.exists() or rp.is_symlink():
            return f"{ROUTE_FILE}: not written by `agentj agent dsh` (no saved copy)"
        dp = home(environ) / DEEPSEEK_FILE
        if _small(dp, 20_000) != deepseek_text() or dp.is_symlink():
            _write_file(dp, deepseek_text())
    except OSError:
        return "dsh folder not writable"
    return config_problem(environ)


def route(environ=None) -> dict | None:
    """{"base_url", "model", "host"} of the route file, or None (no file / not ours)."""
    t = _small(route_path(environ), 10_000)
    m_url = re.search(r'^\s*baseURL: ("(?:[^"\\]|\\.)*")\s*$', t, re.M)
    m_mod = re.search(r'^\s*- id: ("(?:[^"\\]|\\.)*")\s*$', t, re.M)
    if not (m_url and m_mod and f"{ROUTE_PROVIDER}:" in t):
        return None
    try:
        url, model = json.loads(m_url.group(1)), json.loads(m_mod.group(1))
    except ValueError:
        return None
    return {"base_url": url, "model": model, "host": urlparse(url).hostname or "?"}


# ------------------------------------------------------------------ keys (the key card's, by name; values never logged)
def fresh_keys(base: dict) -> dict:
    """The key names from the service env file as it is NOW (a key card saved after serve started), else `base`."""
    out = {k: base[k] for k in KEY_NAMES if base.get(k)}
    try:
        from . import service
        lines = Path(service.env_file(service.name())).read_text().splitlines()
    except Exception:  # noqa: BLE001 — no service: what serve's own environment has
        return out
    for line in lines:
        k, sep, v = line.strip().partition("=")
        if sep and k in KEY_NAMES:
            try:
                parts = shlex.split(v, comments=False)
            except ValueError:
                continue
            if len(parts) == 1 and parts[0]:
                out[k] = parts[0]
    return out


def key_environment(env: dict, source: dict | None = None) -> dict:
    """The variables to add for this process: the route's key (or the keyless placeholder) when Agent J's route is in use,
    else a DeepSeek key — only when the owner's own dsh setup has no login of its own."""
    keys = fresh_keys(source if source is not None else os.environ)
    if route(env):
        return {ROUTE_KEY_ENV: keys.get(ROUTE_KEY_ENV) or NO_KEY}
    f = own_facts(env)
    if not (f["credentials"] or f["dotenv_key"] or f["providers"]) and keys.get(DEEPSEEK_KEY_ENV):
        return {DEEPSEEK_INNER_ENV: keys[DEEPSEEK_KEY_ENV]}
    return {}


def argv_for(exe: str, environ=None, keyed: dict | None = None) -> list[str]:
    a = [exe, "--profile", "acp"]
    if route(environ):
        a += ["--patch", str(route_path(environ))]
    elif keyed and DEEPSEEK_INNER_ENV in keyed:
        a += ["--patch", str(home(environ) / DEEPSEEK_FILE)]
    return a


def login(environ=None) -> tuple[str, str]:
    """(ok | env | warn, where) — existence hints only, like the other harnesses; key validity is not checked here."""
    from .service import tilde
    env = os.environ if environ is None else environ
    r = route(env)
    if r:
        keyed = bool(fresh_keys(env).get(ROUTE_KEY_ENV))
        where = f"route {tilde(str(route_path(env)))} → {r['host']} · {r['model']}"
        if not keyed and (saved_route() or {}).get("needs_key"):
            return "warn", where + f" — the server wants a key: key card {ROUTE_KEY_ENV} ({key_card()})"
        return "ok", where + (f" (key {ROUTE_KEY_ENV}, name only)" if keyed else " (no key: a keyless local server)")
    f = own_facts(env)
    if f["credentials"]:
        return "ok", f"login {tilde(str(home(env) / '.credentials.yaml'))} (key validity unverified)"
    if f["dotenv_key"] or f["providers"]:
        return "ok", f"settings in {tilde(str(home(env)))} (key validity unverified)"
    if fresh_keys(env).get(DEEPSEEK_KEY_ENV):
        return "env", f"env {DEEPSEEK_KEY_ENV} (name only; Agent J hands it to DeepSeek Harness)"
    return "warn", "no model route (agentj agent dsh --base-url … --model … · or a key card: DEEPSEEK_API_KEY)"


def _mark(keyed: dict) -> str:
    """A digest of the key variables a dsh process got — compared, never logged."""
    return hashlib.sha256(json.dumps(sorted(keyed.items())).encode()).hexdigest()


def version_ok(version: str | None) -> bool:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", version or "")
    return bool(m) and tuple(int(x) for x in m.groups()) >= MIN_VERSION


def key_card(base_url: str | None = None) -> str:
    """The exact key-card line for the route's key (the service env file, read back by fresh_keys at the next start)."""
    from . import service
    url = base_url or (route() or saved_route() or {}).get("base_url") or "https://<server>/v1"
    return (f"agentj secret request --name {ROUTE_KEY_ENV} --purpose 'DeepSeek Harness model server' "
            f"--dest 'env:{service.env_file(service.name())}#{ROUTE_KEY_ENV}' --verify-url {url.rstrip('/')}/models")


def login_text(lang: str = "zh", routed: bool = False) -> str:
    if routed:      # P329 live: the route is there, the server refused (no key yet, or a wrong one)
        if lang == "en":
            return (f"DeepSeek Harness's model server refused the login (no key yet, or a wrong one). The Agent J command "
                    f"at the Agent J host: `{key_card()}` — paste the key only on the phone's key card, then send your message "
                    f"again. Never paste a key into chat.")
        return (f"DeepSeek Harness 的模型服务拒绝登录（还没有 key，或 key 不对）。在Agent J 主机上运行 `{key_card()}`，"
                f"只在手机密钥卡里贴 key，存好后再发一次消息。不要把 key 发在对话里。")
    if lang == "en":
        return ("DeepSeek Harness has no model route. On Olares or with any OpenAI-compatible server: `agentj agent dsh "
                "--base-url https://<router>/v1 --model <model id>` (the Agent sends a key card for DSH_ROUTER_API_KEY if the "
                "server needs a key). With DeepSeek's own API: a key card for DEEPSEEK_API_KEY. Never paste a key into chat.")
    return ("DeepSeek Harness 还没有模型路线。Olares 或任何 OpenAI 兼容服务：`agentj agent dsh --base-url https://<Router>/v1 "
            "--model <模型 id>`（服务要 key 时让 Agent 发手机密钥卡存 DSH_ROUTER_API_KEY）；用 DeepSeek 官方 API：密钥卡存 "
            "DEEPSEEK_API_KEY。不要把 key 发在对话里。")


def _plain(value) -> str | None:
    """'["provider","model"]' → "model" (dsh's config-option value); a plain string stays as it is."""
    if not isinstance(value, str):
        return None
    if value.startswith("["):
        try:
            pair = json.loads(value)
        except ValueError:
            return None
        return pair[1] if isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) for x in pair) else None
    return value


class DshAgent(AcpAgent):
    """`dsh --profile acp`, one session per conversation, approvals to the phone (read-only preset, escalations asked)."""
    kind = "dsh"
    LABEL = LABEL
    BIN_ENV, BIN_NAME = "AGENTJ_DSH_BIN", "dsh"
    MODE_DEFAULT = None                       # no ACP modes: DSH_PERMISSION_MODE=read-only makes the process ask
    TOOL_ALIAS = TOOL_ALIAS
    LOGIN_ERRORS = ("no API key for provider route", "MISSING_CREDENTIAL", "INVALID_CREDENTIAL")

    async def _ready(self) -> bool:
        """No route, no key, no setup of the owner's own: say so at once (measured: dsh then spends ~2.5 min on
        session/new before the first prompt fails with "no API key for provider route"). P329 live: a key card saved
        while dsh runs never reaches it (dsh reads its environment once) — a changed key starts dsh again."""
        if self.proc is not None and not self.cfg.get("_workflow_ceo") and \
                getattr(self, "_key_mark", None) not in (None, _mark(self._keyed())):
            self.host.st.log("agent_key_changed", agent=self.kind)
            await self._kill(self.proc)
        if self.proc is None and not self.cfg.get("_workflow_ceo") and login()[0] == "warn":
            self._login_needed()
            return False
        return await super()._ready()

    def _keyed(self) -> dict:
        return {} if self.cfg.get("_workflow_ceo") else key_environment(dict(os.environ))

    def argv(self) -> list[str]:
        from . import agent as _agentmod
        return argv_for(_agentmod._bin(self.BIN_ENV, self.BIN_NAME) or self.BIN_NAME, keyed=self._keyed())

    def spawn_cwd(self) -> str:
        """DSH_HOME — never the working folder (dsh loads `<process cwd>/.env`); no fallback: _spawn refuses instead."""
        return str(home())

    def launch_argv(self, argv: list[str]) -> list[str] | None:
        """The fence's bwrap starts the program in the working folder (--chdir before `--`): dsh's starts in DSH_HOME."""
        a = super().launch_argv(argv)
        if a is not None and a is not argv and "--chdir" in a:
            from .fence import _real
            stop = a.index("--") if "--" in a else len(a)
            i = a.index("--chdir")
            if i + 1 < stop:
                a = a[:i + 1] + [_real(self.spawn_cwd())] + a[i + 2:]
        return a

    async def _spawn(self) -> bool:
        """P329 (security review H1): Agent J's files regenerated and the owner's dsh layers checked, outside the fence,
        before every start — fail closed."""
        h = home()
        try:
            h.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            pass
        why = prepare() if h.is_dir() and not h.is_symlink() else "dsh folder missing"
        if why:
            self.failed_start = True
            self.host.st.log("agent_prepare_fail", agent=self.kind, reason="dsh_config")
            self.fail_notice(config_text(why, getattr(self.host, "lang", "zh")))
            return False
        return await super()._spawn()

    def child_env(self, env: dict) -> dict:
        env = dict(env)
        env.update(FORCED)                    # whatever the owner's shell says: approvals asked, nothing sent to DeepSeek's logs
        if not self.cfg.get("_workflow_ceo"):          # tasks use native login storage only (fence.auxiliary_environment)
            keyed = key_environment(env)
            env.update(keyed)
            self._key_mark = _mark(keyed)
        elif route(env) and not env.get(ROUTE_KEY_ENV):
            env[ROUTE_KEY_ENV] = NO_KEY                # a keyless local route still works for a task; never the card's key
        return env

    def login_hint(self) -> str:
        return login_text(getattr(self.host, "lang", "zh"), routed=bool(route()))

    # ---- the model option: values are '["provider","model"]'; the phone and config.json see the plain model id
    def _raw_values(self) -> list[dict]:
        return super()._values("model")

    def _values(self, cid: str) -> list[dict]:
        vals = super()._values(cid)
        if cid != "model":
            return vals
        out = []
        for v in vals:
            p = _plain(v.get("value"))
            if p and MODEL_RE.match(p) and all(p != o["value"] for o in out):
                out.append({**v, "value": p, "_raw": v["value"]})
        return out

    def cur_model(self) -> str | None:
        o = self._option("model") or {}
        m = self.cfg.get("model") or _plain(o.get("currentValue"))
        return m if isinstance(m, str) and m else None

    async def _set_option(self, cid: str, value: str) -> None:
        if cid == "model":
            raw = next((v["_raw"] for v in self._values("model") if v["value"] == value), None)
            if raw is None:
                raise ValueError("unknown model")
            await super()._set_option(cid, raw)
            return
        await super()._set_option(cid, value)

    async def _apply_choice(self) -> None:
        want, o = self.cfg.get("model"), self._option("model")
        if want and o and _plain(o.get("currentValue")) != want and want in {v["value"] for v in self._values("model")}:
            try:
                await self._set_option("model", want)
            except Exception:  # noqa: BLE001 — the harness's own default stays
                pass

    # ---- commands dsh does not offer over ACP
    async def cmd_compact(self, arg: str) -> Result:
        return Result(f"{self.LABEL} 在 ACP 里没有压缩命令：快到上下文上限时它自己压缩（/context 看用量）。", "info")

    async def cmd_usage(self, arg: str) -> Result:
        r = route()
        where = f"走 {r['host']} 的模型（本地 / 自建服务没有套餐额度）" if r else "DeepSeek API 按量计费（余额看 platform.deepseek.com）"
        return Result(f"套餐用量：{where}；本对话上下文用 /context。", "info")

    async def cmd_status(self, arg: str) -> Result:
        res = await super().cmd_status(arg)
        r = route()
        lines = ["审批：dsh 以只读预设运行，写文件 / 执行命令都先问手机（只回「这一次」）；危险清单生效；遥测关闭"
                 if ln.startswith("审批：") else ln for ln in res.text.split("\n")]
        if r:
            lines.append(f"路线：{r['host']} · {r['model']}（{tilde_route()}）")
        return Result("\n".join(lines), res.kind)


def config_text(why: str, lang: str = "zh") -> str:
    w = clean_line(why, 120)
    if lang == "en":
        return (f"DeepSeek Harness was not started: ~/.dsh would change its approvals or plugins ({w}). Agent J only starts dsh "
                "when every write / command still asks the phone. Move that file aside at the Agent J host (or rerun `agentj agent "
                "dsh --base-url … --model …`), then send the message again.")
    return (f"没有启动 DeepSeek Harness：~/.dsh 里的设置会改动它的审批或插件（{w}）。Agent J 只在每次写文件 / 执行命令都还会问手机时"
            "才启动 dsh。请在Agent J 主机上把那个文件挪走（或重新运行 `agentj agent dsh --base-url … --model …`），再发一次消息。")


def tilde_route() -> str:
    from .service import tilde
    return tilde(str(route_path()))


def probe(exe: str, env: dict, timeout: float = 60, model: str | None = None) -> str:
    """The switch gate: one tool-free greeting over ACP, every permission refused (agent_kimi.probe's loop) — in an empty
    temporary folder (so no project .env is read), the read-only preset, telemetry off, the route's key by name.
    → the reply text, or agent_kimi.ProbeError(code)."""
    from . import agent_kimi
    why = prepare(env)
    if why:
        raise agent_kimi.ProbeError("dsh_config_unsafe")
    env = dict(env)
    env.update(FORCED)
    keyed = key_environment(env, source=env)
    env.update(keyed)
    return agent_kimi.probe(exe, env, timeout, None, argv=argv_for(exe, env, keyed), key_env=False)
