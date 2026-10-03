"""The task contract `workflows/<id>/task.json` (ARCHITECTURE ADR-A61). One reader for every component.

Written by `agentj wizard add-template` (from a template package) and by the workflow design wizard; read by the task
scheduler and the stop-everything switch (later rounds). Shape (exact key set — unknown keys are refused):

    {"v": 1, "id": "<template id>", "title": {"zh": "…", "en": "…"}, "schedule": "<cron, 5 fields>",
     "tz": "<IANA name or 'local'>", "prompt_file": "RUN.md", "dry_run_prompt_file": "DRYRUN.md",
     "mode": "normal" | "research", "enabled": false, "needs": ["<which service, never a key>"], "outputs": ["reports/…"]}

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
    extra = [k for k in obj if k not in KEYS]
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
    outs = obj.get("outputs")
    if "outputs" in obj and not (isinstance(outs, list) and 0 < len(outs) <= 10 and all(
            isinstance(x, str) and _OUT_RE.fullmatch(x) and ".." not in x.split("/") for x in outs)):
        out.append("outputs must be 1–10 relative paths under reports/")
    return out


def secret_like(text: str) -> bool:
    """Layer 1 of the host privacy gate, secret kinds only (keys, tokens, credential assignments, high-entropy strings)."""
    from .privacy import redact_with_report
    _, hits = redact_with_report(text)
    return any(k in SECRET_KINDS for k in hits)


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
