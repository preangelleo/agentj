"""Read and validate workflows/<id>/task.json (ADR-A61).
The shared task contract defines inputs, outputs, checks and delivery, never grants authority.
Secret checks delegate to privacy; owner files omit only the unlabelled entropy guess.
See agentjarvis/ARCHITECTURE.md for schema and workflow lifecycle."""
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
    """Strict privacy secret check; owner files use shared credentials verdict without unlabelled entropy guesses."""
    from .privacy import credentials, redact_with_report
    if owner:
        return bool(credentials(text, entropy=False))   # P211: no unlabelled entropy guess for owner's files
    _, hits = redact_with_report(text, paths=True)   # P424 #142/#147: paths / names / `$VAR` references are no value
    return any(k in SECRET_KINDS for k in hits)


def secret_where(text: str, owner: bool = False) -> dict | None:
    """Location-only hint for the earliest secret_like hit; never affects authorization."""
    from .privacy import credentials, secret_at, where
    if not owner:
        return where(text, secret_at(text, SECRET_KINDS))
    found = credentials(text, entropy=False)
    if not found:
        return None
    kind, _value, s, e = found[0]
    return where(text, (kind, s, e))


SECRET_KINDS = frozenset({"private_key", "url_credentials", "bearer_token", "jwt", "aws_access_key", "github_token",
                          "gitlab_token", "slack_token", "stripe_key", "google_api_key", "anthropic_key", "openrouter_key",
                          "openai_key", "replicate_token", "huggingface_token", "telegram_bot_token", "agentjarvis_id", "agentj_access_code",
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
