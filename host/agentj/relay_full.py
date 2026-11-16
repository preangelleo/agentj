"""0.30.29 R-0006 — `agentj relay claude|codex --full`: the Agent J host runs ONE headless, full-permission Claude Code /
Codex job through the owner's Agents Relay key, after ONE Face ID card for exactly that request.

Security boundary (ADR-0329-relay-full, closes the six 0304 review findings):
1. Recipient pinned: ORIGINS are constants; no flag, preference, environment variable or file moves them. The key goes only
   into the child's environment (never argv, logs, ledger or the CLI) and to `probe` with redirects refused.
2. No replay: a child that started is never re-run; failure / timeout / unknown are reported as such (`replay_allowed`
   is always false). Lost IPC after the request was sent is `outcome_unknown`; the CLI never resends it.
3. Host-bound authority: the CLI sends semantic fields only (control.sock, or elevate.sock inside the fence). serve asks
   `Host.ask(TOOL, …, grant_info, always_ask, require_passkey)` — TOOL is in uv_list.COMMANDS, so no source or level
   skips the card — and spawns only after `_verified`: a paired device's signed allow with fresh UV, the approvals-log record
   for that rid binding the exact tool_input (which carries request_sha256 over channel / cwd / task / model / minutes),
   consumed once per serve.
4. Key names: KEYS == the settings catalog's Agents Relay row (keys.CATALOG); values read by name from keys.read_env().
5. Output: only the child's stdout tail, through redact_output (complete local SecretIndex + this key + every stored key);
   an incomplete scan withholds it. stderr is reduced to a fixed category.
6. Private state <state>/agentsrelay (ledger, run-homes/) is opened by O_NOFOLLOW dir fds, owner-only and refused when a
   symlink, foreign owner or group/other permission is found. Each job gets a FRESH CLAUDE_CONFIG_DIR / CODEX_HOME
   (run-homes/<id>, created by mkdir — never reused) that is removed when the job ends: a job cannot leave settings,
   hooks or instructions for a later one (R-0006 r1 M1).
7. Task and folder carry only visible text (R-0006 r1 H2, r2 N4): control / format / private-use / unassigned /
   line-separator characters and the blank-rendering marks in _BLANK are refused before any card. And the card's own
   display projection (card_display: display_normalizer over the same complete SecretIndex, built for the main Agent's
   folder exactly like list_approval, r3 N1) must leave task and folder
   unchanged apart from showing this computer's home as `~`: anything it would mask (credential-shaped JSON / pair / form /
   base64, known fragments) is refused `task_not_displayable` (r2 N1), so the card shows exactly what the child receives;
   re-checked on fresh indexes after the allow, before spawn (agent folder moved → `agent_folder_changed`). And the
   summary the passkey signed (grant_info, host-held) must equal the card text composed from the raw request
   (signed_texts) — a value present only while Host.ask built its index cannot mask the signed task (r4 N1).
8. Stop-everything and serve stopping end every running job's own process group (TERM, KILL after STOP_GRACE; one more
   group KILL once the leader is gone, for TERM-ignoring members) and record it `stopped` once (r1 H1 / L4, r2 N2 / N3).
   A leader that exits on its own is followed by one group KILL and a bounded pipe drain (r3 N2b).
   serve start removes any run-homes/ an earlier serve left behind (r2 N5).
Known limit: the child runs with the owner's full rights and holds the key in its environment, so a task (or content it
reads) can still copy the key or act anywhere; the card says so. Pinning stops silent re-routing, not a hostile task.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import stat
import sys
import time
import unicodedata
import uuid

TOOL = "agentj relay"
KEYS = {"claude": "AGENTSRELAY_CLAUDE_KEY", "codex": "AGENTSRELAY_OPENAI_KEY"}
ORIGINS = {"claude": "https://api.agentsrelay.net", "codex": "https://api.agentsrelay.net/v1"}
LABELS = {"claude": "Claude Code", "codex": "Codex"}
MODELS = {"claude": "claude-opus-5-5", "codex": "gpt-6.1-sol"}
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,99}")
MAX_TASK = 8000                 # characters; the whole task is on the card
MAX_OUT = 48 * 1024             # characters of redacted stdout returned
CAPTURE = 256 * 1024            # bytes of stdout kept while the child runs (tail)
PER_CHANNEL = 4                 # Agents Relay allows 5 per account; one stays free for the owner
MINUTES, MINUTES_MAX = 60, 240
KEEPALIVE = 30                  # s between progress lines (each one resets the CLI's socket timeout)
RESULTS_MAX = 32
DIR = "agentsrelay"
HOMES = "run-homes"
STOP_GRACE = 3                  # s between TERM and KILL on stop-everything / serve stopping
PIPE_GRACE = 2                  # s to drain stdout / stderr once the job's group is gone (r3 N2b)
_HIDDEN = frozenset(("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"))
# r2 N4: visible categories that render blank (variation selectors, CGJ, Hangul fillers, braille blank, Khmer AQ / AA)
_BLANK = frozenset((*map(chr, range(0xFE00, 0xFE10)), *map(chr, range(0xE0100, 0xE01F0)),
                    "\u034f", "\u115f", "\u1160", "\u3164", "\uffa0", "\u2800",
                    "\u17b4", "\u17b5"))        # r3 N4: Khmer inherent vowels AQ / AA (Mn, render blank)
_D = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
FIELDS = {"channel", "cwd", "task", "model", "minutes", "request_sha256"}
WITHHELD = "（输出已隐藏：本机密钥扫描不完整 / output withheld: local secret scan incomplete）"
WHY = (("auth", re.compile(r"\b40[13]\b|unauthori[sz]ed|forbidden|invalid (?:api )?key|authentication", re.I)),
       ("quota", re.compile(r"quota|usage limit|insufficient|LIMIT_EXCEEDED|SUBSCRIPTION_|额度", re.I)),
       ("rate_limit", re.compile(r"\b429\b|too many requests|rate[ _-]?limit|concurren", re.I)),
       ("model", re.compile(r"\b404\b|model.{0,40}(?:not found|not exist|unsupported)", re.I)),
       ("network", re.compile(r"ECONN|ENOTFOUND|EAI_AGAIN|timed? ?out|network|connection|certificate|\b5\d\d\b", re.I)))


class Refused(Exception):
    def __init__(self, why: str, detail: str | None = None):
        super().__init__(why)
        self.why, self.detail = why, detail


def request_sha(spec: dict) -> str:
    body = {"v": 1, **{k: spec[k] for k in ("channel", "cwd", "task", "model", "minutes")}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _visible(text: str, keep: str = "") -> bool:
    """No character the card could hide or reorder (r1 H2): approval_review.display_text drops Cc / Cf / Cs before the card
    is shown and signed, while the child would get the full string — so such a request is refused, never shown."""
    return not any((unicodedata.category(c) in _HIDDEN or c in _BLANK) and c not in keep for c in text)


def check(req: dict) -> dict:
    """The request's semantic fields only (Refused('bad_request') otherwise); cwd must be canonical and a directory;
    task and cwd hold visible text only (task may keep newline and tab)."""
    if not isinstance(req, dict):
        raise Refused("bad_request")
    extra = set(req) - FIELDS - {"cmd", "t", "early"}
    spec = {k: req.get(k) for k in FIELDS}
    ok = (not extra and spec["channel"] in KEYS
          and isinstance(spec["cwd"], str) and spec["cwd"].startswith("/") and 1 < len(spec["cwd"]) <= 4096
          and _visible(spec["cwd"])
          and isinstance(spec["task"], str) and 0 < len(spec["task"]) <= MAX_TASK and _visible(spec["task"], "\n\t")
          and spec["task"].strip() != ""
          and isinstance(spec["model"], str) and MODEL_RE.fullmatch(spec["model"]) is not None
          and type(spec["minutes"]) is int and 1 <= spec["minutes"] <= MINUTES_MAX
          and isinstance(spec["request_sha256"], str))
    if not ok:
        raise Refused("bad_request")
    sha = spec.pop("request_sha256")
    if sha != request_sha(spec):
        raise Refused("request_mismatch")
    try:
        if os.path.realpath(spec["cwd"]) != spec["cwd"] or not stat.S_ISDIR(os.stat(spec["cwd"]).st_mode):
            raise Refused("cwd_invalid")
    except OSError:
        raise Refused("cwd_invalid") from None
    return spec


# ------------------------------------------------------------------ private state (no-follow fds)
def _owned(fd: int, kind) -> None:
    s = os.fstat(fd)
    if not kind(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077:
        raise Refused("state_unsafe")


def _subdir(parent: int, name: str) -> int:
    try:
        fd = os.open(name, _D, dir_fd=parent)
    except FileNotFoundError:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        except OSError:
            raise Refused("state_unsafe") from None
        try:
            fd = os.open(name, _D, dir_fd=parent)
        except OSError:
            raise Refused("state_unsafe") from None
    except OSError:                         # ELOOP / ENOTDIR: a symlink or a file planted at the name
        raise Refused("state_unsafe") from None
    try:
        _owned(fd, stat.S_ISDIR)
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextlib.contextmanager
def _private(st):
    try:
        root = os.open(str(st.root), _D)
    except OSError:
        raise Refused("state_unsafe") from None
    try:
        fd = _subdir(root, DIR)
    finally:
        os.close(root)
    try:
        yield fd
    finally:
        os.close(fd)


def private_path(st) -> str:
    return os.path.join(str(st.root), DIR)


def homes_ready(st) -> None:
    """Before the card: <state>/agentsrelay/run-homes passes the no-follow / owner-only check."""
    with _private(st) as d:
        os.close(_subdir(d, HOMES))


def run_home(st, name: str) -> str:
    """A FRESH owner-only CLAUDE_CONFIG_DIR / CODEX_HOME for one job: mkdir (an existing name refuses), so nothing a
    previous job wrote can be there. Never the owner's official one."""
    if not re.fullmatch(r"[0-9a-f]{16}", name):
        raise Refused("state_unsafe")
    with _private(st) as d:
        h = _subdir(d, HOMES)
        try:
            os.mkdir(name, 0o700, dir_fd=h)
            fd = os.open(name, _D, dir_fd=h)
        except OSError:
            raise Refused("state_unsafe") from None
        finally:
            os.close(h)
        try:
            _owned(fd, stat.S_ISDIR)
        finally:
            os.close(fd)
    return os.path.join(private_path(st), HOMES, name)


def sweep_homes(st) -> int:
    """serve start (r2 N5): remove every run-homes/<16 hex> an earlier serve left (crash, kill -9), with the same
    no-follow fd-based rmtree as drop_home. Creates nothing; a symlinked / foreign agentsrelay or run-homes is left
    alone. Returns how many were removed; never raises."""
    import shutil
    n = 0
    with contextlib.suppress(Refused, OSError):
        root = os.open(str(st.root), _D)
        try:
            d = os.open(DIR, _D, dir_fd=root)
        finally:
            os.close(root)
        try:
            _owned(d, stat.S_ISDIR)
            h = os.open(HOMES, _D, dir_fd=d)
            try:
                _owned(h, stat.S_ISDIR)
                for name in os.listdir(h):
                    if re.fullmatch(r"[0-9a-f]{16}", name):
                        with contextlib.suppress(OSError):
                            shutil.rmtree(name, dir_fd=h)
                            n += 1
            finally:
                os.close(h)
        finally:
            os.close(d)
    return n


def drop_home(st, name: str) -> None:
    """Remove one job's home (fd-based rmtree under the no-follow run-homes fd: links inside are removed, not followed;
    a symlink put in its place is left alone). Best effort, never raises."""
    import shutil
    with contextlib.suppress(Refused, OSError):
        with _private(st) as d:
            h = _subdir(d, HOMES)
            try:
                shutil.rmtree(name, dir_fd=h)
            finally:
                os.close(h)


def ledger_append(st, row: dict) -> None:
    """One metadata line (no task, output, cwd or key) under an flock on the ledger itself."""
    line = (json.dumps({"ts": round(time.time(), 3), **row}, sort_keys=True, ensure_ascii=False) + "\n").encode()
    with _private(st) as d:
        try:
            fd = os.open("ledger.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
                         | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=d)
        except OSError:
            raise Refused("state_unsafe") from None
        try:
            _owned(fd, stat.S_ISREG)
            if os.fstat(fd).st_nlink != 1:
                raise Refused("state_unsafe")
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line)
        finally:
            os.close(fd)


# ------------------------------------------------------------------ key, child, output
def key_value(channel: str) -> str | None:
    """By name only: the managed service env file (no-follow), then serve's own environment. Never logged or returned."""
    from . import keys
    name = KEYS[channel]
    v = keys.read_env().get(name) or os.environ.get(name)
    if not isinstance(v, str) or not v or len(v) > 8192 or any(ord(c) < 33 or ord(c) == 127 for c in v):
        return None
    return v


def child_command(channel: str, model: str, cwd: str, key: str, home, *, base=None, binary=None) -> tuple[list, dict]:
    """argv + environment for the child (`home` = this job's fresh run_home). Every route-bearing variable is removed and
    set by us; the key is only in env."""
    from .peer_session import clean_env
    env = clean_env(channel, os.environ if base is None else base)
    for k in list(env):
        if (k.startswith(("ANTHROPIC_", "OPENAI_", "CODEX_", "CLAUDE", "AGENTSRELAY_", "AGENTJ", "OPENCODE"))
                or k in ("NODE_OPTIONS",)):
            env.pop(k)
    home = str(home)
    if binary is None:
        from . import binaries
        binary = binaries.resolve(channel).get("path")
    if channel == "claude":
        env.update(CLAUDE_CONFIG_DIR=home, ANTHROPIC_BASE_URL=ORIGINS["claude"], ANTHROPIC_AUTH_TOKEN=key,
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1")
        # Only the isolated (empty) user settings: a project's .claude/settings.json cannot set env, hooks or a key helper.
        argv = [binary, "-p", "--dangerously-skip-permissions", "--setting-sources", "user", "--strict-mcp-config",
                "--no-session-persistence", "--output-format", "text", "--model", model]
    else:
        from .agentsrelay_dispatch import _RelayProfile, provider_options
        profile = _RelayProfile("agentj_relay_" + uuid.uuid4().hex, ORIGINS["codex"], model)
        env.update(CODEX_HOME=home, AGENTSRELAY_OPENAI_KEY=key)
        # A random provider id + --ignore-user-config: no config file can pre-define headers / URL for this provider.
        argv = [binary, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                "--sandbox", "danger-full-access", "-c", 'approval_policy="never"', "--color", "never",
                *provider_options(profile), "-C", cwd, "-"]
    return argv, env


def redact_output(text: str, cwd: str, extra=()) -> str:
    """Complete local SecretIndex (env, .env files up to home, Codex auth, SSH keys, the service env file) + `extra` + every
    stored key value → DisplayIndex.display. Incomplete → WITHHELD."""
    from types import SimpleNamespace
    from . import display_privacy, keys
    try:
        index = display_privacy.index(SimpleNamespace(agent_cfg={"dir": cwd}))
        for v in (*extra, *keys.read_env().values()):
            index.add(v)
        index.require_complete()
        return index.display(text)
    except Exception:  # noqa: BLE001 — never fall back to the raw text
        return WITHHELD


def why_of(stderr_tail: str) -> str:
    for name, rx in WHY:
        if rx.search(stderr_tail or ""):
            return name
    return "exit_nonzero"


def probe(channel: str, key: str) -> str:
    """One GET <origin>/v1/models (codex: <origin>/models) with the key; redirects refused, nothing else sent."""
    import http.client
    import urllib.error
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    url = ORIGINS[channel] + ("/models" if channel == "codex" else "/v1/models")
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": "Bearer " + key, "User-Agent": "agentj-relay/1", "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=8) as r:
            r.read(65536)
            return "key_accepted" if r.status == 200 else "provider_error"
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        return ("redirect_refused" if 300 <= code < 400 else "key_rejected" if code in (401, 403) else
                "rate_limited" if code == 429 else "reachable" if code == 404 else "provider_error")
    except (OSError, ValueError, http.client.HTTPException):
        return "unreachable"


# ------------------------------------------------------------------ serve side
def _rt(host) -> dict:
    # procs: run_id → (proc, wait task, began) while the child lives · stops: run_id → why (estop / serve_stop)
    # · ended: run_ids whose ledger `end` line is written (exactly one, whoever ends it first)
    return host.__dict__.setdefault("_relay_full", {"running": {"claude": 0, "codex": 0}, "used": set(), "results": {},
                                                    "active": set(), "procs": {}, "stops": {}, "ended": set()})


def _refused(host, why: str, detail: str | None = None, **log) -> dict:
    with contextlib.suppress(Exception):
        host.st.log("relay_full", result=why, **log)
    return {"ok": False, "status": "refused", "error": why, "started": False, "replay_allowed": False,
            **({"detail": detail} if detail else {})}


def card_input(spec: dict) -> dict:
    ch = spec["channel"]
    return {
        "action": ("Run a background AI coding agent with FULL permissions on this Agent J host: no sandbox and no step "
                   "confirmations. It can read, change and delete anything your account can. / 在这台 Agent J 主机上以完全权限运行"
                   "后台 AI 编程工具：无沙箱、不逐步确认，你的账户能读、改、删的它都能。"),
        "tool": LABELS[ch],
        "via": f"Agents Relay {ORIGINS[ch]} ({KEYS[ch]})",
        "working_directory": spec["cwd"],
        "model": spec["model"],
        "time_limit_minutes": spec["minutes"],
        "task": spec["task"],
        "data_notice": ("The task, files it reads and the replies pass through Agents Relay (a third party). The job "
                        "holds this key while it runs. It also follows this folder's own instructions automatically "
                        "(CLAUDE.md, AGENTS.md, .claude/, .codex/ — here and in parent folders), which are not shown on this card. / "
                        "任务、它读到的文件和回复会经过第三方 Agents Relay；运行期间它持有这把密钥。它还会自动遵循这个文件夹"
                        "（及上级文件夹）里的 CLAUDE.md、AGENTS.md、.claude/、.codex/ 指令，这些内容不在本卡上。"),
        "request_sha256": request_sha(spec),
    }


def _verified(host, answer: dict, tool_input: dict, spec: dict) -> str | None:
    """None = this exact request was approved by a paired device with fresh UV, not used before; else the reason."""
    from . import approvals
    if answer.get("behavior") != "allow":
        r = answer.get("reason")
        return "denied" if r == "device" else (r if isinstance(r, str) and re.fullmatch(r"[a-z_]{1,40}", r) else "not_approved")
    proof = answer.get("grant_info") or {}
    rid, device, pub = proof.get("rid"), proof.get("device"), proof.get("sign_pub")
    if not (rid and device and pub and proof.get("uv") is True and host.st.sign_key(device) == pub):
        return "fresh_owner_uv_required"
    if tool_input.get("request_sha256") != request_sha(spec):
        return "approval_not_bound"
    rows = [r for r in approvals.read_log(host.st) if r.get("id") == rid]
    if not (len(rows) == 1 and rows[0].get("tool") == TOOL and rows[0].get("decision") == "allow"
            and rows[0].get("reason") == "device" and rows[0].get("device") == device):
        return "approval_not_bound"
    if rows[0].get("input_sha256") != approvals.input_digest(tool_input):
        return "approval_not_bound"
    if approvals.check_record(host.st, rows[0]) != "ok":
        return "approval_not_bound"
    used = _rt(host)["used"]
    if rid in used:
        return "approval_used"
    used.add(rid)
    return None


async def handle(host, req: dict, emit=None) -> dict:
    """→ {ok, status: completed|failed|timeout|refused|unknown, id?, exit_code?, why?, output?, started, replay_allowed: false}.
    emit(ev): {"stage": "card"} (again every KEEPALIVE s while it waits) · {"stage": "started", "id"} ·
    {"stage": "running", "id", "seconds"}. Always answers: an unexpected error is `host_error`, `unknown` once started."""
    mark = {"started": False}
    try:
        return await _handle(host, req, emit, mark)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — the caller gets a fixed answer, never a traceback or a value
        with contextlib.suppress(Exception):
            host.st.log("relay_full", result="host_error", error=type(e).__name__)
        return {"ok": False, "status": "unknown" if mark["started"] else "refused", "error": "host_error",
                "started": mark["started"], "replay_allowed": False}


def task_refusal(task: str, cwd: str, extra=()) -> str | None:
    """The task goes to a third party: refuse one that carries a known local secret (complete index, the same
    sources as redact_output + every stored key), or when that cannot be established."""
    from types import SimpleNamespace
    from . import display_privacy, keys
    try:
        index = display_privacy.index(SimpleNamespace(agent_cfg={"dir": cwd}))
        for v in (*extra, *keys.read_env().values()):
            index.add(v)
        index.require_complete()
        return "task_contains_secret" if index.hits(task) else None
    except Exception:  # noqa: BLE001
        return "local_secret_scan_incomplete"


NOT_DISPLAYABLE = ("任务或文件夹里有像密钥 / 密码字段的写法（例如 \"password\": …、token: …、key=…，或这类内容的 base64），"
                   "确认卡上会被遮住，不能运行。请去掉这些字段名、用普通文字改写后重新提交 / The task or folder contains "
                   "credential-like fields (\"password\": …, token: …, key=…, or base64 of such) that the approval card "
                   "would mask, so it cannot run. Please rephrase it in plain words without credential-like field names "
                   "and submit again.")


def card_workdir(host) -> str | None:
    """The folder the real card's SecretIndex is built for: list_approval.ask_device takes
    `(host.agent_cfg or {}).get('dir')` (no native context for this host-owned tool) — the MAIN Agent's folder, not the
    job folder and not serve's own working directory (r3 N1)."""
    return (getattr(host, "agent_cfg", None) or {}).get("dir")


def card_display(lang: str, workdir: str | None):
    """Exactly the card's projection (list_approval.ask_device): SecretIndex.build(workdir=cwd or os.getcwd(),
    fragment_size=8), require_complete, display_normalizer(lang=host.lang, workdir=cwd, index=…). Built fresh each call."""
    from . import action_policy, peer_guard
    index = peer_guard.SecretIndex.build(workdir=workdir or os.getcwd(), fragment_size=8)
    index.require_complete()
    return action_policy.display_normalizer(lang=lang, workdir=workdir, index=index)


def display_refusal(spec: dict, lang: str = "en", workdir: str | None = None) -> str | None:
    """r2 N1 / r3 N1: run the card's own projection (card_display: the same complete SecretIndex, for the same folder,
    that Host.ask builds) over the exact card input. The owner must see exactly what the child receives: refuse when any
    sensitive kind (home_path aside) is found in task / folder, or when the shown task / folder / card text differs from
    the raw values other than this computer's home shown as `~`. Never rewrites the request."""
    from collections import Counter
    from . import approval_review, privacy
    try:
        display = card_display(lang, workdir)
        raw = {"task": spec["task"], "working_directory": spec["cwd"]}
        if display.sensitive_kinds(raw) - {"home_path"}:
            return "task_not_displayable"
        inp = card_input(spec)
        want = {**inp, **{k: privacy._owner_homes(v, Counter()) for k, v in raw.items()}}
        safe = display(inp)
        if safe != want:
            return "task_not_displayable"
        head = TOOL + "\n"
        shown = approval_review.display_text(head + approval_review.canonical(safe), display)
        if not shown.startswith(head) or json.loads(shown[len(head):]) != want:
            return "task_not_displayable"
        return None
    except OSError as e:
        return "local_secret_scan_incomplete" if str(e) == "local_secret_scan_incomplete" else "task_not_displayable"
    except Exception:  # noqa: BLE001 — cannot be shown faithfully → never a card
        return "task_not_displayable"


def signed_texts(tool_input: dict, spec: dict) -> tuple[str, str]:
    """r4 N1: the exact card text a faithful card carries for this raw request, composed from the raw values only (never
    from what any index masked): tool_input with only this computer's home shown as `~` in task / folder. list_approval.ask
    (no native context) composes `TOOL + "\n" + canonical(display(inp))`; display_text then runs the normalizer over that
    text, which re-serializes the embedded object with json.dumps(…, ensure_ascii=False) — keys stay sorted, default
    separators. → (shown form, canonical form); the signed summary must be exactly one of them."""
    from collections import Counter
    from . import approval_review, privacy
    want = {**tool_input, "task": privacy._owner_homes(spec["task"], Counter()),
            "working_directory": privacy._owner_homes(spec["cwd"], Counter())}
    head = TOOL + "\n"
    return (head + json.dumps(want, sort_keys=True, ensure_ascii=False, allow_nan=False),
            head + approval_review.canonical(want))


def signed_refusal(proof, tool_input: dict, spec: dict) -> str | None:
    """r4 N1: bind what the owner signed to the raw request. `proof` is Host.ask's grant_info — list_approval.proof()
    copies `summary` (and, for v2 answers, `envelope`) from the host-held Ask the passkey signed, never from the phone's
    reply. Anything other than signed_texts (a task the card's own index masked while it was built, a missing summary)
    → task_not_displayable: nothing runs."""
    try:
        expect = signed_texts(tool_input, spec)
        if not isinstance(proof, dict) or not isinstance(proof.get("summary"), str) or proof["summary"] not in expect:
            return "task_not_displayable"
        if "envelope" in proof:
            env = proof["envelope"]
            if not isinstance(env, dict) or not isinstance(env.get("details"), str) or env["details"] not in expect:
                return "task_not_displayable"
        return None
    except Exception:  # noqa: BLE001 — cannot be compared → never runs
        return "task_not_displayable"


async def _card(host, tool_input: dict, say) -> dict:
    """Host.ask for the one Face ID card; the CLI hears {"stage": "card"} until it is answered."""
    ask = asyncio.ensure_future(host.ask(TOOL, tool_input, batch=False, grant_info=True, always_ask=True,
                                         require_passkey=True))
    try:
        while True:
            done, _ = await asyncio.wait({ask}, timeout=KEEPALIVE)
            if done:
                return ask.result()
            say({"stage": "card", "waiting": True})
    finally:
        if not ask.done():
            ask.cancel()


async def _handle(host, req: dict, emit, mark: dict) -> dict:
    from . import application_ingress, binaries, plaza_perms

    def say(ev):
        if emit is not None:
            with contextlib.suppress(Exception):
                emit(ev)
    try:
        spec = await asyncio.to_thread(check, req)
    except Refused as e:
        return _refused(host, e.why)
    ch = spec["channel"]
    if host.stopped():
        return _refused(host, "estop")
    if host.stopping.is_set():
        return _refused(host, "serve_stop")
    gate = plaza_perms.turn_gate(host)        # the asker's source when the request arrives: group / OTG never get a card
    if gate["gated"]:
        return _refused(host, gate["why"])
    if not application_ingress.allowed(host):
        return _refused(host, "application_ingress_unverified")
    if await asyncio.to_thread(key_value, ch) is None:
        return _refused(host, "key_missing", f"{KEYS[ch]} 未设置：在手机「设置 → 模型和 API Key → Agents Relay」填写 / "
                        f"{KEYS[ch]} is not set: add it on the phone in Settings → Models & API keys → Agents Relay")
    why = await asyncio.to_thread(task_refusal, spec["task"], spec["cwd"])
    if why:
        return _refused(host, why)
    agent_dir = card_workdir(host)            # r3 N1: the folder the card's index is built for, recorded now
    why = await asyncio.to_thread(display_refusal, spec, host.lang, agent_dir)
    if why:
        return _refused(host, why, NOT_DISPLAYABLE if why == "task_not_displayable" else None)
    binary = binaries.resolve(ch).get("path")
    if not binary:
        return _refused(host, "harness_missing", f"{LABELS[ch]} 没装在这台 Agent J 主机上 / {LABELS[ch]} is not installed on this Agent J host")
    rt = _rt(host)
    if rt["running"][ch] >= PER_CHANNEL:
        return _refused(host, "busy", f"已有 {PER_CHANNEL} 个 {LABELS[ch]} 任务在跑 / {PER_CHANNEL} {LABELS[ch]} jobs are running")
    try:
        await asyncio.to_thread(homes_ready, host.st)
        ident = await asyncio.to_thread(lambda: (lambda s: (s.st_dev, s.st_ino))(os.stat(spec["cwd"])))
    except Refused as e:
        return _refused(host, e.why)
    except OSError:
        return _refused(host, "cwd_invalid")
    tool_input = card_input(spec)
    say({"stage": "card"})
    answer = await _card(host, tool_input, say)
    why = await asyncio.to_thread(_verified, host, answer, tool_input, spec)
    if why:
        return _refused(host, why, channel=ch)
    rid = answer["grant_info"]["rid"]
    # r4 N1: the signed card must be the raw request itself — Host.ask builds its own index, and a value present only
    # while it ran (planted, then removed) masks the task on the card yet passes every check before and after.
    why = signed_refusal(answer["grant_info"], tool_input, spec)
    if why:
        return _refused(host, why, NOT_DISPLAYABLE, rid=rid)
    if host.stopped() or host.stopping.is_set():
        return _refused(host, "estop" if host.stopped() else "serve_stop", rid=rid)
    # r3 N1: what the owner approved must still be what the card would show now — same agent folder, and a fresh
    # index (an .env edited while the card was open) leaves task and folder unmasked; else nothing runs.
    if card_workdir(host) != agent_dir:
        return _refused(host, "agent_folder_changed", rid=rid)
    why = (await asyncio.to_thread(task_refusal, spec["task"], spec["cwd"])
           or await asyncio.to_thread(display_refusal, spec, host.lang, agent_dir))
    if why:
        return _refused(host, why, NOT_DISPLAYABLE if why == "task_not_displayable" else None, rid=rid)
    key = await asyncio.to_thread(key_value, ch)              # fresh: a key removed meanwhile is not used
    if key is None:
        return _refused(host, "key_missing", rid=rid)
    try:
        st_now = await asyncio.to_thread(os.stat, spec["cwd"])
        if (st_now.st_dev, st_now.st_ino) != ident or os.path.realpath(spec["cwd"]) != spec["cwd"]:
            return _refused(host, "cwd_changed", rid=rid)
    except OSError:
        return _refused(host, "cwd_changed", rid=rid)
    if rt["running"][ch] >= PER_CHANNEL:
        return _refused(host, "busy", rid=rid)
    name = secrets.token_hex(8)
    run_id = "relay:" + name
    rt["running"][ch] += 1                 # reserved before any await: concurrent approvals cannot exceed PER_CHANNEL
    rt["active"].add(run_id)
    try:
        try:
            home = await asyncio.to_thread(run_home, host.st, name)
        except Refused as e:
            return _refused(host, e.why, rid=rid)
        try:
            try:
                await asyncio.to_thread(ledger_append, host.st, {"event": "start", "id": run_id, "channel": ch,
                                                                 "model": spec["model"], "rid": rid,
                                                                 "request_sha256": tool_input["request_sha256"]})
            except Refused as e:
                return _refused(host, e.why, rid=rid)
            mark["started"] = True
            result = await _run(host, spec, key, binary, run_id, home, say)
        finally:
            await asyncio.to_thread(drop_home, host.st, name)
    finally:
        rt["running"][ch] -= 1
        rt["active"].discard(run_id)
        rt["stops"].pop(run_id, None)
        rt["ended"].discard(run_id)
    results = rt["results"]
    results[run_id] = result
    while len(results) > RESULTS_MAX:
        results.pop(next(iter(results)))
    with contextlib.suppress(Exception):
        host.agent_notice(f"Agents Relay {LABELS[ch]} {run_id}："
                          + {"completed": "已完成 / completed", "timeout": "超时已停止 / stopped at its time limit",
                             "stopped": "已被停下（不会自动重跑）/ stopped (not re-run automatically)"}.get(
                              result["status"], "失败（不会自动重跑）/ failed (not re-run automatically)"))
    return result


async def _run(host, spec: dict, key: str, binary: str, run_id: str, home: str, say) -> dict:
    ch, began = spec["channel"], time.monotonic()
    rt = _rt(host)
    argv, env = child_command(ch, spec["model"], spec["cwd"], key, home, binary=binary)

    async def end(status, rc=None, why=None, output=""):
        await _ledger_end(host, run_id, status, rc, why, began)
        return {"ok": status == "completed", "id": run_id, "channel": ch, "status": status, "exit_code": rc,
                **({"why": why} if why else {}), "output": output, "started": status != "not_started",
                "replay_allowed": False}
    try:
        proc = await asyncio.create_subprocess_exec(*argv, cwd=spec["cwd"], env=env, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    start_new_session=True)
    except OSError:
        return await end("not_started", None, "spawn_failed")
    wait_t = asyncio.ensure_future(proc.wait())
    rt["procs"][run_id] = (proc, wait_t, began)        # registered before any await: stop_all always sees it
    try:
        return await _watch(host, spec, key, run_id, proc, wait_t, began, end, say)
    finally:
        rt["procs"].pop(run_id, None)


async def _watch(host, spec, key, run_id, proc, wait_t, began, end, say) -> dict:
    rt = _rt(host)
    if host.stopped() or host.stopping.is_set():       # stop-everything / stopping landed between approval and spawn
        rt["stops"].setdefault(run_id, "estop" if host.stopped() else "serve_stop")
        await _stop(proc, wait_t, STOP_GRACE)
    say({"stage": "started", "id": run_id})

    async def pump(stream, buf, cap):        # buf is kept by the caller: what arrived is used even if cut short
        while chunk := await stream.read(65536):
            buf += chunk
            if len(buf) > cap:
                del buf[:len(buf) - cap]

    async def feed():
        with contextlib.suppress(OSError, ConnectionError):
            proc.stdin.write(spec["task"].encode())
            await proc.stdin.drain()
        with contextlib.suppress(OSError, ConnectionError):
            proc.stdin.close()
    out_b, err_b = bytearray(), bytearray()
    out_t = asyncio.ensure_future(pump(proc.stdout, out_b, CAPTURE))
    err_t = asyncio.ensure_future(pump(proc.stderr, err_b, 16384))
    feed_t = asyncio.ensure_future(feed())
    deadline = time.monotonic() + spec["minutes"] * 60
    timed_out = False
    try:
        while not wait_t.done():
            left = deadline - time.monotonic()
            if left <= 0:
                timed_out = True
                break
            await asyncio.wait({wait_t}, timeout=min(KEEPALIVE, left))
            if not wait_t.done():
                say({"stage": "running", "id": run_id, "seconds": int(time.monotonic() - began)})
        if timed_out:
            await _stop(proc, wait_t)
        else:
            _group_kill(proc)                 # r3 N2b: the leader ended on its own — so does the rest of its group
        await _drain((out_t, err_t))          # bounded: a pipe held outside the group cannot keep the job / slot
    except asyncio.CancelledError:            # serve stopping: stop OUR child's process group, record it, re-raise
        await _stop(proc, wait_t, STOP_GRACE)
        for t in (out_t, err_t, feed_t):
            t.cancel()
        await end("stopped" if run_id in rt["stops"] else "cancelled", proc.returncode, rt["stops"].get(run_id))
        raise
    rc = proc.returncode
    out, err = bytes(out_b), bytes(err_b)
    feed_t.cancel()
    text = out.decode("utf-8", "replace")[-MAX_OUT:]
    shown = await asyncio.to_thread(redact_output, text, spec["cwd"], [key])
    if run_id in rt["stops"]:
        return await end("stopped", rc, rt["stops"][run_id], shown)
    if timed_out:
        return await end("timeout", rc, "time_limit", shown)
    if rc == 0:
        return await end("completed", 0, None, shown)
    return await end("failed", rc, why_of(err.decode("utf-8", "replace")[-4096:]), shown)


async def _ledger_end(host, run_id: str, status: str, rc, why, began: float) -> None:
    """Exactly one `end` line per job: the first of _run / stop_all to get here writes it (claimed on the loop)."""
    ended = _rt(host)["ended"]
    if run_id in ended:
        return
    ended.add(run_id)
    row = {"event": "end", "id": run_id, "status": status, "exit_code": rc, **({"why": why} if why else {}),
           "seconds": round(time.monotonic() - began, 1)}
    with contextlib.suppress(Refused, OSError):
        await asyncio.to_thread(ledger_append, host.st, row)


def _group_kill(proc) -> None:
    """One SIGKILL to the job's own process group once its leader is gone (an empty group is ESRCH, ignored). Sent at
    most once per job (r4 I1): a later stop_all never signals a group id that may since belong to someone else."""
    if getattr(proc, "_aj_group_killed", False):
        return
    proc._aj_group_killed = True
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


async def _drain(tasks, grace: float = PIPE_GRACE) -> None:
    """Wait up to `grace` s for the pipe readers to reach EOF, then cancel what is left (its bytes so far are kept)."""
    _, pending = await asyncio.wait(set(tasks), timeout=grace)
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _stop(proc, wait_t, grace: float = 5) -> None:
    """SIGTERM then SIGKILL to the child's own process group (it was started in a new session: pgid == its pid), sent
    while our child (the group leader) is still running. r2 N2: when the leader exits during the stop, one more group
    SIGKILL ends members that ignored TERM — the group id cannot be reused while any member is alive; an empty group is
    ESRCH (ignored). r3 N2b: that final group KILL is sent also when the leader was already gone before any signal."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if wait_t.done() or getattr(proc, "_aj_group_killed", False):
            break
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, sig)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(wait_t), grace)
    if wait_t.done():
        _group_kill(proc)


async def stop_all(host, why: str) -> int:
    """Stop-everything (`do_estop`) and serve stopping: every running relay job's own process group gets TERM, then KILL
    after STOP_GRACE s; each is recorded `end status=stopped why=<why>` and its fresh home removed. Returns how many."""
    rt = _rt(host)
    jobs = list(rt["procs"].items())
    for run_id, _ in jobs:
        rt["stops"].setdefault(run_id, why)
    await asyncio.gather(*(_stop(proc, wait_t, STOP_GRACE) for _, (proc, wait_t, _b) in jobs), return_exceptions=True)
    for run_id, (proc, _w, began) in jobs:
        if run_id not in rt["active"]:      # r2 N3: its own _handle already wrote the end and forgot it
            continue
        await _ledger_end(host, run_id, "stopped", proc.returncode, rt["stops"].get(run_id, why), began)
        await asyncio.to_thread(drop_home, host.st, run_id.split(":", 1)[1])
    if jobs:
        with contextlib.suppress(Exception):
            host.st.log("relay_full", result="stopped", why=why, jobs=len(jobs))
    return len(jobs)


async def query(host, req: dict) -> dict:
    """op keys → names + set (+ probe when check); op result → a finished job's redacted receipt (this serve only).
    Same caller gate as `handle` (r1 L1): a group / OTG turn or a caller without a live application-ingress frame gets
    nothing — no probe with the key, no job output."""
    from . import application_ingress, plaza_perms
    gate = plaza_perms.turn_gate(host)
    if gate["gated"]:
        return {"ok": False, "error": gate["why"]}
    if not application_ingress.allowed(host):
        return {"ok": False, "error": "application_ingress_unverified"}
    op = req.get("op") if isinstance(req, dict) else None
    if op == "keys":
        present = {ch: await asyncio.to_thread(key_value, ch) for ch in KEYS}
        out = {"ok": True, "keys": {ch: {"name": KEYS[ch], "set": present[ch] is not None} for ch in KEYS}}
        if req.get("check") is True:
            out["check"] = {ch: (await asyncio.to_thread(probe, ch, present[ch]) if present[ch] else "key_missing")
                            for ch in KEYS}
        return out
    rid = req.get("id") if op == "result" else None
    if isinstance(rid, str) and re.fullmatch(r"relay:[0-9a-f]{16}", rid):
        row = _rt(host)["results"].get(rid)
        if row is not None:
            return dict(row)
        return {"ok": False, "id": rid, "status": "running" if rid in _rt(host)["active"] else "unknown",
                "replay_allowed": False, "scope": "current_host_process"}
    return {"ok": False, "error": "bad_request"}


# ------------------------------------------------------------------ CLI side
def _serve_call(st, req: dict, progress, timeout: float) -> tuple[str, dict | None]:
    """('ok', answer) · ('not_running', None): no socket accepted the request · ('unknown', None): sent, no final answer.
    Never resends: the elevate.sock fallback is tried only when control.sock was certainly not reached."""
    from . import fence, names
    cmd = req["cmd"]
    paths = [] if fence.inside() else [(st.sock_path, req)]
    paths.append((names.elevate_sock(st), {**{k: v for k, v in req.items() if k != "cmd"}, "t": cmd}))
    for path, body in paths:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            try:
                s.connect(str(path))
            except OSError:
                continue
            try:
                s.sendall((json.dumps(body, ensure_ascii=False) + "\n").encode())
                buf = b""
                while True:
                    while b"\n" not in buf:
                        chunk = s.recv(65536)
                        if not chunk:
                            return "unknown", None
                        buf += chunk
                        if len(buf) > 512 * 1024:
                            return "unknown", None
                    line, buf = buf.split(b"\n", 1)
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        return "unknown", None
                    if obj.get("sent") is True or obj.get("t") == "card":
                        progress(obj)
                        continue
                    return "ok", obj
            except (OSError, ValueError):
                return "unknown", None
        finally:
            s.close()
    return "not_running", None


def _progress(ev: dict) -> None:
    stage = ev.get("stage")
    if stage == "card" and ev.get("waiting"):
        line = "确认卡仍在等主人处理 / the card is still waiting for the owner"
    elif stage == "card":
        line = "确认卡已发到你的遥控器，等主人 Face ID / card sent to the owner's remote; waiting for Face ID"
    elif stage == "started":
        line = f"已开始 {ev.get('id')}；中断这条命令不会停止任务，用 agentj relay status {ev.get('id')} 取结果 / started; " \
               f"if this command is cut short the job keeps running: agentj relay status {ev.get('id')}"
    elif stage == "running":
        line = f"仍在运行 / still running {ev.get('id')} {ev.get('seconds')} s"
    else:
        return
    print(line, file=sys.stderr, flush=True)


def _emit(obj: dict) -> int:
    print(json.dumps(obj, ensure_ascii=False))
    if obj.get("ok"):
        return 0
    if obj.get("error") == "outcome_unknown":
        return 4
    if obj.get("error") == "host_unreachable":
        return 1
    return 3 if obj.get("started") else 2


def command(args) -> int:
    from .state import State
    if any(getattr(args, k, None) for k in ("sandbox", "base_url", "report")):
        return _emit({"ok": False, "status": "refused", "error": "full_excludes_profile_options", "started": False,
                      "replay_allowed": False})
    task = args.prompt
    if task == "-":
        task = sys.stdin.read(MAX_TASK + 1)
    cwd = os.path.realpath(getattr(args, "cwd", None) or os.getcwd())
    spec = {"channel": args.channel, "cwd": cwd, "task": task, "model": args.model or MODELS[args.channel],
            "minutes": getattr(args, "minutes", None) or MINUTES}
    if not (isinstance(task, str) and 0 < len(task) <= MAX_TASK and task.strip()):
        return _emit({"ok": False, "status": "refused", "error": "bad_task", "started": False, "replay_allowed": False,
                      "detail": f"1–{MAX_TASK} characters"})
    req = {"cmd": "agentsrelay_full", **spec, "request_sha256": request_sha(spec)}
    how, answer = _serve_call(State(), req, _progress, KEEPALIVE * 4)
    if how == "not_running":
        return _emit({"ok": False, "status": "refused", "error": "host_unreachable", "started": False,
                      "replay_allowed": False})
    if how == "unknown":
        return _emit({"ok": False, "status": "unknown", "error": "outcome_unknown", "replay_allowed": False,
                      "detail": "请求已送达但没有收到结果；不要重发，先用 agentj relay status 查看 / sent but no answer; "
                                "do not resend — check agentj relay status first"})
    safe = {k: answer[k] for k in ("ok", "id", "channel", "status", "exit_code", "why", "error", "detail", "output",
                                   "started", "replay_allowed") if k in answer}
    safe["replay_allowed"] = False
    return _emit(safe)


def status_command(args) -> int:
    from .state import State
    rid = getattr(args, "key", None)
    req = ({"cmd": "agentsrelay_full_status", "op": "result", "id": rid} if rid else
           {"cmd": "agentsrelay_full_status", "op": "keys", "check": bool(getattr(args, "check", False))})
    how, answer = _serve_call(State(), req, lambda ev: None, 30)
    if how != "ok":
        print(json.dumps({"ok": False, "error": "host_unreachable" if how == "not_running" else "outcome_unknown"}))
        return 1
    print(json.dumps(answer, ensure_ascii=False))
    return 0 if answer.get("ok") else 1
