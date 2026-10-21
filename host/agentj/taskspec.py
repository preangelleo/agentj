"""The task contract `workflows/<id>/task.json` (ARCHITECTURE ADR-A61). One reader for every component.

Written by `agentj wizard add-template` (from a template package) and by the workflow design wizard; read by the task
scheduler and the stop-everything switch (later rounds). Shape (exact key set — unknown keys are refused):

    {"v": 1, "id": "<template id>", "title": {"zh": "…", "en": "…"}, "schedule": "<cron, 5 fields>",
     "tz": "<IANA name or 'local'>", "prompt_file": "RUN.md", "dry_run_prompt_file": "DRYRUN.md",
     "mode": "normal" | "research", "enabled": false, "needs": ["<which service, never a key>"], "outputs": ["reports/…"]}
    optional (P254): "harness": "claude" | "codex" | "opencode" | [one or more of them] — the only harnesses this workflow may
    run on; the quota steward never moves it elsewhere. Absent = any (normally the main Agent's own).
    optional (P365): "notify_webapp": true | false — decided when the task is made, default false: true = the files this run
    writes under reports/ go to the phone as file cards on its result page (notify.py; the phone's settings can override).
    optional (P426 #181-5): "steps": 2–12 nodes [{"id", "prompt_file", optional "title" {zh,en}, "outputs" [reports/…],
    "idempotent" (default false), "retry" 0–3 (only with idempotent or mode research)}] — each node is its own harness run,
    checkpointed by wfrun.py; a failed run continues from the failed node (`agentj automation resume`).

- `enabled` is turned on only by a human (the scheduler round); the wizard and `add-template` always write `false`.
- `mode: "research"` = the read-only run profile (reports only). `normal` workflows may draft, but every step that spends
  money, sends anything outside, changes a price / listing or deletes is written in RUN.md as "ask on the phone first".
- `needs` names services ("Amazon SP-API or an ERP export"), never a credential; a value that looks like one is refused.
- Paths (`prompt_file`, `dry_run_prompt_file`, `outputs`) are relative to the workflow folder `workflows/<id>/`.
Stdlib only; pure functions (no file writes).
"""
from __future__ import annotations

import json
import re

KEYS = ("v", "id", "title", "schedule", "tz", "prompt_file", "dry_run_prompt_file", "mode", "enabled", "needs", "outputs")
OPTIONAL = ("harness", "notify_webapp", "steps")   # P365: notify_webapp, see notify.py; P426: steps, see wfrun.py
STEP_KEYS = ("id", "prompt_file")
STEP_OPTIONAL = ("title", "outputs", "idempotent", "retry")
MAX_STEPS = 12
MAX_RETRY = 3
HARNESS_NAMES = ("claude", "codex", "opencode", "kimi", "qwen", "dsh")   # P305: + Kimi Code (ACP, tasks.py)
MODES = ("normal", "research")
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_FILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.md")
_OUT_RE = re.compile(r"reports/[A-Za-z0-9._<>{}/-]{1,120}")
_TZ_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+){0,2}")
# one cron field: numbers, *, ranges, steps and lists only (no names, no @macros): what every scheduler reads the same way
_CRON_FIELD = re.compile(r"(?:\*|\d{1,2}(?:-\d{1,2})?)(?:/\d{1,2})?(?:,(?:\*|\d{1,2}(?:-\d{1,2})?)(?:/\d{1,2})?)*")
_CRON_MAX = (59, 23, 31, 12, 7)
_CRON_MIN = (0, 0, 1, 1, 0)


def cron_problem(expr: object) -> str | None:
    if not isinstance(expr, str):
        return "schedule must be a string"
    parts = expr.split()
    if len(parts) != 5 or " ".join(parts) != expr:
        return "schedule must be 5 cron fields separated by single spaces"
    for i, f in enumerate(parts):
        if not _CRON_FIELD.fullmatch(f):
            return f"cron field {i + 1} is not numeric cron syntax"
        for n in (int(x) for x in re.findall(r"\d+", f.split("/")[0])):
            if not _CRON_MIN[i] <= n <= _CRON_MAX[i]:
                return f"cron field {i + 1} out of range"
    return None


def tz_problem(tz: object) -> str | None:
    if tz == "local":
        return None
    if not isinstance(tz, str) or not _TZ_RE.fullmatch(tz):
        return "tz must be 'local' or an IANA zone name"
    try:
        import zoneinfo
        zoneinfo.ZoneInfo(tz)
    except Exception:                       # unknown zone, or no tz database on this machine
        return "unknown IANA zone"
    return None


def _short_text(v: object, n: int) -> bool:
    return isinstance(v, str) and 0 < len(v) <= n and not re.search(r"[\x00-\x1f\x7f]", v)


def problems(obj: object, expect_id: str | None = None) -> list[str]:
    """Every way `obj` breaks the contract (empty list = valid). `expect_id` = the folder name it must match."""
    if not isinstance(obj, dict):
        return ["task.json must be a JSON object"]
    out: list[str] = []
    missing = [k for k in KEYS if k not in obj]
    extra = [k for k in obj if k not in KEYS and k not in OPTIONAL]
    if missing:
        out.append("missing keys: " + ", ".join(missing))
    if extra:
        out.append("unknown keys: " + ", ".join(sorted(extra)))
    if obj.get("v") != 1:
        out.append("v must be 1")
    tid = obj.get("id")
    if not (isinstance(tid, str) and ID_RE.fullmatch(tid)):
        out.append("id must match [a-z0-9][a-z0-9-]{0,39}")
    elif expect_id is not None and tid != expect_id:
        out.append("id does not match its folder")
    t = obj.get("title")
    if not (isinstance(t, dict) and set(t) == {"zh", "en"} and all(_short_text(t[k], 80) for k in ("zh", "en"))):
        out.append("title must be {zh, en}, each 1–80 characters")
    if "schedule" in obj and (p := cron_problem(obj["schedule"])):
        out.append(p)
    if "tz" in obj and (p := tz_problem(obj["tz"])):
        out.append(p)
    for k in ("prompt_file", "dry_run_prompt_file"):
        if k in obj and not (isinstance(obj[k], str) and _FILE_RE.fullmatch(obj[k])):
            out.append(f"{k} must be a plain .md file name in the workflow folder")
    if "mode" in obj and obj["mode"] not in MODES:
        out.append("mode must be normal or research")
    if "enabled" in obj and not isinstance(obj["enabled"], bool):
        out.append("enabled must be true or false")
    needs = obj.get("needs")
    if "needs" in obj and not (isinstance(needs, list) and len(needs) <= 20 and all(_short_text(x, 120) for x in needs)):
        out.append("needs must be ≤ 20 strings of 1–120 characters")
    elif isinstance(needs, list) and secret_like(" \n".join(x for x in needs if isinstance(x, str))):
        out.append("needs looks like it holds a credential: name the service only")
    h = obj.get("harness")
    if "harness" in obj and not (h in HARNESS_NAMES or (isinstance(h, list) and 0 < len(h) <= len(HARNESS_NAMES) and all(x in HARNESS_NAMES for x in h))):
        out.append("harness must be claude, codex, opencode, kimi, qwen or dsh, or a list of them")
    if "notify_webapp" in obj and type(obj["notify_webapp"]) is not bool:
        out.append("notify_webapp must be true or false")
    outs = obj.get("outputs")
    if "outputs" in obj and not (isinstance(outs, list) and 0 < len(outs) <= 10 and all(
            isinstance(x, str) and _OUT_RE.fullmatch(x) and ".." not in x.split("/") for x in outs)):
        out.append("outputs must be 1–10 relative paths under reports/")
    if "steps" in obj and (p := steps_problem(obj["steps"], obj.get("mode"))):
        out.append(p)
    return out


def _outputs_ok(outs: object, least: int = 1) -> bool:
    return isinstance(outs, list) and least <= len(outs) <= 10 and all(
        isinstance(x, str) and _OUT_RE.fullmatch(x) and ".." not in x.split("/") for x in outs)


def steps_problem(steps: object, mode: object = None) -> str | None:
    """P426 #181-5: the optional node list of a multi-step automation (wfrun.py runs and checkpoints it)."""
    if not (isinstance(steps, list) and 2 <= len(steps) <= MAX_STEPS):
        return f"steps must be a list of 2–{MAX_STEPS} nodes"
    seen = set()
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict) or any(k not in s for k in STEP_KEYS) or any(k not in STEP_KEYS + STEP_OPTIONAL for k in s):
            return f"step {i} must have id and prompt_file (optional: title, outputs, idempotent, retry) and nothing else"
        if not (isinstance(s["id"], str) and ID_RE.fullmatch(s["id"])) or s["id"] in seen:
            return f"step {i}: id must match [a-z0-9][a-z0-9-]{{0,39}} and be unique"
        seen.add(s["id"])
        if not (isinstance(s["prompt_file"], str) and _FILE_RE.fullmatch(s["prompt_file"])):
            return f"step {i}: prompt_file must be a plain .md file name in the workflow folder"
        t = s.get("title")
        if "title" in s and not (isinstance(t, dict) and set(t) == {"zh", "en"} and all(_short_text(t[k], 80) for k in t)):
            return f"step {i}: title must be {{zh, en}}, each 1–80 characters"
        if "outputs" in s and not _outputs_ok(s["outputs"], 0):
            return f"step {i}: outputs must be ≤ 10 relative paths under reports/"
        if "idempotent" in s and type(s["idempotent"]) is not bool:
            return f"step {i}: idempotent must be true or false"
        r = s.get("retry", 0)
        if type(r) is not int or not 0 <= r <= MAX_RETRY:
            return f"step {i}: retry must be a whole number 0–{MAX_RETRY}"
        if r and s.get("idempotent") is not True and mode != "research":
            return f"step {i}: retry needs \"idempotent\": true (a step with side effects is never repeated by itself)"
    return None


def secret_like(text: str, owner: bool = False) -> bool:
    """Layer 1 of the host privacy gate, secret kinds only (keys, tokens, credential assignments, high-entropy strings)."""
    from .privacy import redact_with_report
    if owner:
        from .privacy import _SECRETS
        for kind, pat, repl in _SECRETS:
            for match in pat.finditer(text):
                if kind in {"credential_assignment", "authorization_header", "bearer_token"} and _not_owner_value(match, repl):
                    continue
                return True
        return False                     # P211: no unlabelled entropy guess for owner's files
    _, hits = redact_with_report(text, paths=True)   # P424 #142/#147: paths / names / `$VAR` references are no value
    return any(k in SECRET_KINDS for k in hits)


def secret_where(text: str, owner: bool = False) -> dict | None:
    """P424 #142: where `secret_like(text, owner)` found its secret — privacy.where's {"kind","line","col","len","hint"} of
    the earliest fragment that rule would refuse, or None. Only for telling the owner where to look."""
    from .privacy import _SECRETS, R, secret_at, where
    if not owner:
        return where(text, secret_at(text, SECRET_KINDS))
    spans = []
    for kind, pat, repl in _SECRETS:
        for match in pat.finditer(text):
            if kind in {"credential_assignment", "authorization_header", "bearer_token"} and _not_owner_value(match, repl):
                continue
            rep = match.expand(repl)
            i = rep.find(R)
            pre, suf = (rep[:i], rep[i + len(R):]) if i >= 0 else ("", "")
            spans.append((match.start() + len(pre), max(match.start() + len(pre) + 1, match.end() - len(suf)), kind))
    if not spans:
        return None
    s, e, kind = min(spans)
    return where(text, (kind, s, e))


def _not_owner_value(match, replacement: str) -> bool:
    """P270 + P424: an OWNER file's labelled match that is documentation / shell / code, not a credential value."""
    from .privacy import _not_value
    return _not_value(match, replacement, True) or not _credential_value(match, replacement)


def _credential_value(match, replacement: str) -> bool:
    """P270: an OWNER file's labelled value must resemble a credential, not documentation.

    Provider prefixes/private keys are checked independently, even inside an example.
    Strict outbound/privacy gates retain their existing rules.
    """
    from .privacy import R, _entropy, _pathlike, _CRED_NAMES
    rendered = match.expand(replacement)
    pre, _, suffix = rendered.partition(R)
    value = match.group(0)[len(pre):len(match.group(0)) - len(suffix) if suffix else None]
    value = value.lstrip("`")
    if not suffix:                         # an unquoted value ends before prose / Markdown punctuation
        token = re.match(r"[A-Za-z0-9+/_=.!@#$%^&*?~-]+", value)
        value = token.group(0) if token else ""
    value = value.rstrip(",;|`)]}.")
    if len(value) < 8 or not re.fullmatch(r"[A-Za-z0-9+/_=.!@#$%^&*?~ -]+", value):
        return False
    if value.lower() in {"true", "false", "null", "none", "undefined", "password", "changeme", "your_api_key",
                         "your_token", "replace_me", "placeholder", "redacted", "example", "secret", "not_set"}:
        return False
    if re.fullmatch(r"(?:your|example|placeholder|replace|insert)[_-].*", value, re.I):
        return False
    if "..." in value or "${" in value or "$HOME" in value or value.startswith(("$", "<", "~/", "./")):
        return False
    path = re.match(r"[A-Za-z0-9+/_=-]+", value)
    if path and _pathlike(path):
        return False                     # reuse owner-path syntax, not a blanket slash exception
    if (re.fullmatch(_CRED_NAMES, value, re.I) or
            re.fullmatch(r"[A-Z][A-Z0-9_]*_(?:KEY|TOKEN|SECRET|PASSWORD|PASS|CREDENTIALS?|VAR|NAME)", value, re.I)):
        return False                     # APIFY_TOKEN, OPENAI_API_KEY, etc. are names
    if re.fullmatch(r"[0-9a-fA-F]{16,}", value):
        return True
    classes = sum(bool(re.search(pattern, value)) for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9 ]"))
    entropy = _entropy(value)
    return (classes >= 2 and entropy >= 2.5) or (len(value) >= 32 and entropy >= 4.0)


SECRET_KINDS = frozenset({"private_key", "url_credentials", "bearer_token", "jwt", "aws_access_key", "github_token",
                          "gitlab_token", "slack_token", "stripe_key", "google_api_key", "anthropic_key", "openrouter_key",
                          "openai_key", "replicate_token", "huggingface_token", "telegram_bot_token", "agentjarvis_id",
                          "mailgun_key", "authorization_header", "credential_assignment", "labelled_hex", "high_entropy"})


def load(path, expect_id: str | None = None) -> tuple[dict | None, list[str]]:
    """(task, problems). A file that does not parse → (None, [why])."""
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except FileNotFoundError:
        return None, ["task.json is missing"]
    except (OSError, ValueError, UnicodeDecodeError):
        return None, ["task.json is not valid UTF-8 JSON"]
    return obj, problems(obj, expect_id)
