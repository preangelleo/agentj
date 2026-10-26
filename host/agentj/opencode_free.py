"""P298: OpenCode Zen's free models as the phone's harness — no `opencode auth login`, no key, no payment.

Measured (Mac report 2026-10-14, re-measured here on 1.18.34 in an empty HOME): with zero credentials `opencode models
opencode` lists only the free models and they answer. Which ones changes within a day (a model appears, another is
deprecated), so the list is never kept here: a model is free when `opencode models opencode --verbose` shows it with zero
input and output cost and not deprecated. OpenCode 2.1's retired verbose CLI falls through to its isolated native price
catalogue (opencode_catalogue). Offline, a `-free` suffix remains an explicit unverified hint.

The free tier refuses a request that does not look like OpenCode's own client: a config-level `permission {"*": "deny"}` /
`tools {"*": false}` agent (the bots' native probe) and a session-level `*: deny` both get `403 FreeTierError`. The probe
for a free model therefore takes the formal chat path — `opencode serve` + a session carrying Agent J's own read-only
ruleset (agent_opencode.session_rules, research mode: every ask is a deny) — in an empty scratch folder. A freshly installed
OpenCode is sometimes refused for about a minute (measured ~70 s): a FreeTierError / 429 is retried with growing waits.

What the provider does with the conversation is not in the model list: the families below are from OpenCode Zen's own
model pages as quoted in the report (2026-10-14). Unknown free models get no line rather than a guess.
"""
from __future__ import annotations

from . import resource_policy

import base64
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

PROVIDER = "opencode"
LIST_TIMEOUT = 20
WARM_WAITS = (10, 20, 40)         # s between attempts after a FreeTierError / 429 (≈ the measured 70 s warm-up)
WARM_EXTRA = sum(WARM_WAITS) + 5  # s a free-model probe may take beyond the per-call bound
START_WAIT = 30                   # s for `opencode serve` to say it listens
PROMPT = "Hello. Reply with a short greeting only."
_MODEL = re.compile(r"opencode/[A-Za-z0-9._-]{1,80}")

# family → what happens to the conversation (one plain sentence each, zh / en)
DATA_TERMS = (
    (("mimo", "big-pickle"), "may_train"),
    (("muse-spark",), "trains"),
    (("nemotron",), "logged"),
)
TERMS_TEXT = {
    "zh": {"may_train": "免费期间的对话可能被拿去改进模型", "trains": "对话会被拿去训练模型",
           "logged": "对话会被记录（官方要求别发个人或机密信息）"},
    "en": {"may_train": "conversations may be used to improve the model during the free period",
           "trains": "conversations are used to train models",
           "logged": "usage is logged (the provider asks for no personal or confidential data)"},
}
# zero retention per the same pages — preferred when a free model has to be picked; never big-pickle first (its free tier
# refused `opencode run` in the report)
PREFERRED = ("deepseek-v4.1-flash-free", "glm-5.3-flash-free")

_cache: dict = {}


def _env(base=None) -> dict:
    env = dict(os.environ if base is None else base)
    env.update(OPENCODE_DISABLE_MODELS_FETCH="1", OPENCODE_DISABLE_AUTOUPDATE="1", OPENCODE_DISABLE_SHARE="1")
    return env


def parse_verbose(text: str) -> list[str]:
    """`opencode models opencode --verbose`: an `opencode/<id>` line, then that model's JSON. Free = zero input and output
    cost, status not deprecated."""
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        head = lines[i].strip()
        i += 1
        if not _MODEL.fullmatch(head):
            continue
        buf = []
        while i < len(lines) and not _MODEL.fullmatch(lines[i].strip()):
            buf.append(lines[i])
            i += 1
        try:
            info = json.loads("\n".join(buf))
        except ValueError:
            continue
        cost = info.get("cost") if isinstance(info, dict) else None
        if (isinstance(cost, dict) and cost.get("input") == 0 and cost.get("output") == 0
                and str(info.get("status") or "active") != "deprecated" and head not in out):
            out.append(head)
    return out


def listed(exe: str | None, timeout: float = LIST_TIMEOUT) -> list[str] | None:
    """The free models OpenCode offers right now (cached per executable for this process); None when it cannot tell."""
    if not exe or os.environ.get("AGENTJ_OC_FREE_LIST") == "off":      # tests: no stand-in binary is asked for its list
        return None
    if exe in _cache:
        return _cache[exe]
    try:
        r = subprocess.run([exe, "models", PROVIDER, "--verbose"], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=_env(), cwd=tempfile.gettempdir())
        from .opencode_catalogue import native_models, retired_models_cli
        got = native_models(exe, timeout) if retired_models_cli(r) else (parse_verbose(r.stdout) if r.returncode == 0 else None)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        got = None
    _cache[exe] = got
    return got


def status(model: str | None, exe: str | None) -> str | None:
    """"listed" (OpenCode lists it as free) · "name" (the list is unavailable; the name ends in -free) · None (not free)."""
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        return None
    free = listed(exe)
    if free is not None:
        return "listed" if model in free else None
    return "name" if model.endswith("-free") else None


def pick(models: list[str] | None) -> str | None:
    """The free model to suggest: zero retention first, then the rest in OpenCode's order (big-pickle last)."""
    models = [m for m in models or [] if _MODEL.fullmatch(m) and not m.split("/", 1)[1].startswith(("space-bunny", "longcat", "minimax-m3.1-flash"))]
    for fam in PREFERRED:
        for m in models:
            if m.split("/", 1)[1].startswith(fam):
                return m
    rest = [m for m in models if not m.endswith("/big-pickle")]
    return (rest or models or [None])[0]


def terms(model: str | None) -> str | None:
    """The data-terms class of a known free model family, else None."""
    if not isinstance(model, str) or not model.startswith(PROVIDER + "/"):
        return None
    mid = model.split("/", 1)[1]
    for fams, kind in DATA_TERMS:
        if any(mid.startswith(f) for f in fams):
            return kind
    return None


def privacy_note(model: str | None, lang: str = "zh") -> str | None:
    """One plain sentence for the settings page and the switch card when the chosen free model's provider keeps or trains
    on the conversation."""
    kind = terms(model)
    if not kind:
        return None
    what = TERMS_TEXT["en" if lang == "en" else "zh"][kind]
    if lang == "en":
        return (f"Note: {model} is an OpenCode free model — {what}. Keep personal or confidential things out of it, and "
                "switch to a model of your own for everyday use.")
    return f"提示：{model} 是 OpenCode 的免费模型，{what}。别在里面发个人或机密信息；长期用建议换成你自己的模型。"


# ------------------------------------------------------------------ the probe (formal chat path)
class ProbeError(Exception):
    """A fixed code: free_tier · native_failed · native_timeout · native_spawn_failed · empty_probe_reply."""


def classify(error) -> str:
    """An assistant message's `error` → free_tier (FreeTierError, or a 429 of the free tier) or native_failed. The text is
    read only to classify, never kept."""
    if not isinstance(error, dict):
        return "native_failed"
    data = error.get("data") if isinstance(error.get("data"), dict) else {}
    blob = (str(error.get("name") or "") + " " + str(data.get("message") or "") + " " + str(data.get("responseBody") or ""))
    if "FreeTierError" in blob or data.get("statusCode") == 429:
        return "free_tier"
    return "native_failed"


def _port() -> int:
    from .agent_opencode import free_port
    return free_port()


def _stop(proc) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
    if proc.stdout:
        proc.stdout.close()


def serve(exe: str, senv: dict, root: str, port: int, up_by: float, clock=time.monotonic):
    """`opencode serve` on 127.0.0.1:<port> in `root`, once it says it listens (by `up_by`); else ProbeError. P395: shared by
    the 2.x probe (agent_opencode2.probe) — the caller stops the process (_stop)."""
    try:
        proc = subprocess.Popen(resource_policy.work_argv([exe, "serve", "--hostname", "127.0.0.1", "--port", str(port)]), cwd=root, env=senv,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError:
        raise ProbeError("native_spawn_failed") from None
    import selectors
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout, selectors.EVENT_READ)
            buf = b""
            while b"listening on" not in buf:
                if clock() >= up_by or proc.poll() is not None:
                    raise ProbeError("native_spawn_failed")
                if sel.select(min(1.0, max(0.0, up_by - clock()))):
                    chunk = os.read(proc.stdout.fileno(), 4096)
                    if not chunk:
                        raise ProbeError("native_spawn_failed")
                    buf = (buf + chunk)[-4096:]
    except BaseException:
        _stop(proc)
        raise
    return proc


def probe(exe: str, model: str | None, env: dict, timeout: float, *, sleep=time.sleep, clock=time.monotonic,
          waits=None, prompt=None, diagnostic=False) -> str:
    """One greeting through `opencode serve` with Agent J's read-only session rules, in an empty scratch folder; the
    session is deleted afterwards. Returns the reply text; raises ProbeError(code). A FreeTierError / 429 is retried after
    each of `waits` (the warm-up) while the overall bound `timeout + sum(waits)` allows."""
    from .agent_opencode import opencode_env, session_rules, split_model, _rule
    waits = WARM_WAITS if waits is None else waits
    deadline = clock() + timeout + sum(waits)
    password = secrets.token_urlsafe(32)
    root = tempfile.mkdtemp(prefix="agentj-oc-probe-")
    proc = None
    port = _port()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # 127.0.0.1 never through a proxy
    auth = "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()

    def call(method, path, body=None, grace=0.0):
        left = deadline + grace - clock()
        if left <= 0:
            raise ProbeError("native_timeout")
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": auth, "Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=min(left, timeout)) as r:
                raw = r.read(4 * 1024 * 1024)
                return r.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as e:
            return e.code, None
        except (TimeoutError, OSError) as e:
            raise ProbeError("native_timeout" if "timed out" in str(e) or isinstance(e, TimeoutError) else "native_failed") \
                from None
        except ValueError:
            raise ProbeError("native_failed") from None

    try:
        proc = serve(exe, opencode_env(_env(env), password), root, port, min(deadline, clock() + START_WAIT), clock)
        rules = session_rules([], research=True) + [_rule("webfetch", "*", "deny")]
        if diagnostic:
            # Only scratch reads; no shell, skills, LSP, network or external files, even if the model asks.
            rules = [_rule("*", "*", "deny"), _rule("read", root + "/*", "allow")]
        st, ses = call("POST", "/session", {"title": "Agent J probe", "permission": rules})
        if st != 200 or not isinstance(ses, dict) or not isinstance(ses.get("id"), str):
            raise ProbeError("native_failed")
        sid = ses["id"]
        body = {"parts": [{"type": "text", "text": prompt if prompt is not None else PROMPT}]}
        m = split_model(model) if model else None
        if m:
            body["model"] = m
        try:
            for wait in (*waits, None):
                st, msg = call("POST", f"/session/{sid}/message", body)
                info = msg.get("info") if isinstance(msg, dict) and isinstance(msg.get("info"), dict) else {}
                code = classify(info["error"]) if info.get("error") else None
                if st != 200 and not code:
                    code = "free_tier" if st == 429 else "native_failed"
                if not code:
                    text = "\n".join(p.get("text") or "" for p in msg.get("parts") or []
                                     if isinstance(p, dict) and p.get("type") == "text").strip()
                    if not text:
                        raise ProbeError("empty_probe_reply")
                    return text
                if code != "free_tier" or wait is None or clock() + wait >= deadline:
                    raise ProbeError(code)
                sleep(wait)
            raise ProbeError("free_tier")
        finally:
            try:
                call("DELETE", f"/session/{sid}", grace=5.0)     # no probe session left in the owner's list
            except ProbeError:
                pass
    finally:
        _stop(proc)
        shutil.rmtree(root, ignore_errors=True)
