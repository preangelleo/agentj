"""Two-layer privacy gate for install feedback (seat-setup CONTRACT §6; worker/FEEDBACK_API.md §privacy gate) and, plaza P2,
for Agent plaza posts / replies (`gate="plaza"`: plaza_criteria.md, state key plaza_draft — jarvis_host/plaza.py).

Layer 1 — `redact()` / `redact_draft()`: deterministic, pure, idempotent. Keys and tokens (the server scan's patterns),
emails (except @agentjarvis.net and RFC 2606 example domains), phone numbers (E.164-ish, CN mobile), home paths
(Linux / macOS / Windows / WSL → ~/), this machine's host and user name (→ <host> / <user>), private IPv4 addresses, our
aj?_ ids. Runs on this machine only.

Layer 2 — `layer2()`: Jev (`~typesafe/jev-latest`, TypeSafe decisions API on OpenRouter) judges the layer-1 output
against the criteria in `privacy_criteria.md` and returns a probability of privacy exposure; ≥ threshold (0.5) → blocked.
Only with the customer's own OPENROUTER_API_KEY from the environment; the key is never logged or printed and is sent
to ENDPOINT only (no override). No key / network / non-200 / odd answer → "unavailable": the caller falls back to
layer 1 + an explicit human confirmation. Jev never sees the raw draft.

`python3 -m jarvis_host.privacy --eval` runs the regression set (host/tests/privacy_cases.json) live — paid, opt-in.
Stdlib only.
"""
from __future__ import annotations

import argparse
import collections
import getpass
import json
import math
import os
import pathlib
import re
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = "~typesafe/jev-latest"
THRESHOLD = 0.5
CRITERIA_PATH = pathlib.Path(__file__).with_name("privacy_criteria.md")
CASES_PATH = pathlib.Path(__file__).resolve().parents[1] / "tests" / "privacy_cases.json"
# Agent plaza P2: a post is published to EVERY other customer, not sent to the vendor — its own, stricter criteria (state key
# `plaza_draft`), same model, same threshold, same failure → "unavailable" rule.
PLAZA_CRITERIA_PATH = pathlib.Path(__file__).with_name("plaza_criteria.md")
PLAZA_CASES_PATH = pathlib.Path(__file__).resolve().parents[1] / "tests" / "plaza_cases.json"
GATES = {"feedback": (CRITERIA_PATH, "feedback_draft", CASES_PATH), "plaza": (PLAZA_CRITERIA_PATH, "plaza_draft", PLAZA_CASES_PATH)}
KEY_ENV = "OPENROUTER_API_KEY"
UA = "agentjarvis-host (privacy gate)"

R = "<redacted>"


def _re(pattern: str, flags: int = 0) -> re.Pattern:
    """ASCII \\b \\d \\w, like the server's JavaScript regexes (a CJK letter is not a word character there)."""
    return re.compile(pattern, flags | re.ASCII)


# ------------------------------------------------------------------ layer 1
# Mirrors worker/src/scan.ts so a layer-1 draft never trips the server scan; replacements never match again (idempotent).
# Names whose value is a secret (case-insensitive): the server scan's list, plus (review SS-04) pass / pw / auth / credentials
# and any NAME ending in KEY / SECRET / TOKEN — `DB_PASS`, `MAILGUN_API_KEY`, `stripeSecret`, `x-auth-token` (a lower-case
# suffix needs a `_` / `-` before it, so `monkey` is not a name; upper or Capitalised suffixes may follow letters directly).
_CRED_NAMES = (r"(?:api[_-]?key|apikey|secret|token|password|passwd|pass|pwd|pw|auth|credentials?|access[_-]?key|client[_-]?secret"
               r"|private[_-]?key|[A-Za-z0-9_-]*[_-](?:key|secret|token|pass|passwd|pwd|pw)|[A-Za-z0-9]*(?-i:KEY|SECRET|TOKEN|Key|Secret|Token))")
_SECRETS: list[tuple[str, re.Pattern, str]] = [
    ("private_key", _re(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----(?:[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----|[\s\S]*)"), R),
    ("url_credentials", _re(r"\b([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@", re.I), r"\1" + R + "@"),
    ("bearer_token", _re(r"\b(Bearer\s+)[A-Za-z0-9._~+/-]{20,}=*"), r"\1" + R),
    ("jwt", _re(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), R),
    ("aws_access_key", _re(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), R),
    ("github_token", _re(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}"), R),
    ("gitlab_token", _re(r"\bglpat-[A-Za-z0-9_-]{20,}"), R),
    ("slack_token", _re(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), R),
    ("stripe_key", _re(r"\b(?:sk|rk|pk|whsec)_(?:live|test)_[A-Za-z0-9]{16,}"), R),
    ("google_api_key", _re(r"\bAIza[0-9A-Za-z_-]{35}"), R),
    ("anthropic_key", _re(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), R),
    ("openrouter_key", _re(r"\bsk-or-(?:v1-)?[A-Za-z0-9]{20,}"), R),
    ("openai_key", _re(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}"), R),
    ("replicate_token", _re(r"\br8_[A-Za-z0-9]{20,}"), R),
    ("huggingface_token", _re(r"\bhf_[A-Za-z0-9]{30,}"), R),
    ("telegram_bot_token", _re(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), R),
    ("agentjarvis_id", _re(r"\baj[aijmrst]_[A-Za-z0-9_-]{16,}"), R),   # ajm_ = the plaza admin token (P2)
    # review SS-04: more secret shapes the server scan does not know (layer 1 may be stricter than the server, never looser)
    ("mailgun_key", _re(r"\bkey-[0-9a-f]{32}\b"), R),
    ("authorization_header", _re(r"\b(Authorization\s*:\s*(?:Bearer|Basic|token)\s+)(?!<redacted>|\$\(|\$\{|\$[A-Z_])[^\s\"'<>]+", re.I),
     r"\1" + R),
    ("credential_assignment", _re(
        r"(?<![A-Za-z0-9])(" + _CRED_NAMES + r"[\"']?\s*[:=]\s*)([\"'])(?!<redacted>\2|\$\(|\$\{|\$[A-Z_])(?:(?!\2)[^\n]){6,}\2", re.I),
     r"\1\2" + R + r"\2"),                                                     # a quoted value, spaces allowed
    ("credential_assignment", _re(
        r"(?<![A-Za-z0-9])(" + _CRED_NAMES + r"[\"']?\s*[:=]\s*[\"']?)(?!<redacted>|\$\(|\$\{|\$[A-Z_])[^\s\"'<>]{8,}", re.I), r"\1" + R),
    ("labelled_hex", _re(
        r"(?<![A-Za-z0-9])((?:" + _CRED_NAMES + r"|key)[\"']?(?:\s*[:=]\s*|\s+(?:is\s+)?)[\"']?)[0-9a-fA-F]{32,}(?![0-9A-Za-z])", re.I), r"\1" + R),
]
_EMAIL = _re(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
_EMAIL_OK_DOMAIN = _re(r"(?:^|\.)(?:agentjarvis\.net|example\.(?:com|org|net))$", re.I)
_SEG = r"[^/\\\s\"'`<>:;,()\[\]{}|*?]+"   # one path segment (a user name)
_HOME_PATHS = [
    _re(r"\b[A-Za-z]:(?:\\\\|\\|/)Users(?:\\\\|\\|/)" + _SEG + r"(\\\\|\\|/)?", re.I),   # before the POSIX ones
    _re(r"(?<![\w.~:-])/mnt/[a-z]/User[s]/" + _SEG + r"(/)?", re.I),
    _re(r"(?<![\w.~:-])/(?:home|Users)/(?!Shared(?:/|$))" + _SEG + r"(/)?"),
]
_PHONES = [
    _re(r"(?<![\d+])\+\d{1,3}(?:[ .()-]{0,2}\d){6,14}(?!\d)"),            # E.164-ish, separators allowed
    _re(r"(?<![\d+])1[3-9]\d(?:[ -]?\d{4}){2}(?!\d)"),                   # CN mobile, 138 1234 5678 too
]
_PRIVATE_IP = _re(
    r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168|169\.254|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7]))"
    r"\.\d{1,3}\.\d{1,3}(?!\d|\.\d)")
_ENTROPIC = _re(r"[A-Za-z0-9+/_=-]{32,}")
# names never replaced as host / user: too generic to identify anyone, and replacing them would mangle ordinary text
# (and the placeholders themselves, which keeps redact() idempotent)
GENERIC_NAMES = frozenset("""localhost localdomain root admin administrator user users host home ubuntu debian fedora arch
archlinux centos rocky alma alpine kali mint manjaro pi raspberrypi ec2-user vagrant runner guest test jarvis mac macbook
macbook-pro macbook-air imac linux windows desktop server laptop pc workstation default redacted email phone private ip
private-ip none null codex claude agent""".split())


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in collections.Counter(s).values())


def _entropic(tok: str) -> bool:
    if re.fullmatch(r"[0-9a-fA-F-]+", tok):        # hashes, UUIDs, commit ids are fine (same as the server)
        return False
    if not (re.search(r"[A-Z]", tok) and re.search(r"[a-z]", tok) and re.search(r"[0-9]", tok)):
        return False
    return _entropy(tok) >= 4.3


def _names(name: str | None) -> list[str]:
    """Full name first, then the short host name; generic or < 3 chars never."""
    if not name:
        return []
    out = []
    for n in (name, name.split(".")[0]):
        n = n.strip()
        if len(n) >= 3 and n.lower() not in GENERIC_NAMES and n not in out:
            out.append(n)
    return out


def redact_with_report(text: str, host: str | None = None, user: str | None = None) -> tuple[str, collections.Counter]:
    """Layer 1. Pure: same input → same output; redact(redact(x)) == redact(x). Returns (text, Counter(kind → count)).
    One pass can open a boundary for an earlier rule ("10.0.0.1myhost" → "<private-ip>myhost"), so passes repeat until
    nothing changes; every replacement makes the text shorter in secrets, so this stops after a few passes."""
    hits: collections.Counter = collections.Counter()
    for _ in range(8):
        new = _one_pass(text, host, user, hits)
        if new == text:
            break
        text = new
    return text, hits


def _one_pass(text: str, host: str | None, user: str | None, hits: collections.Counter) -> str:
    def sub(kind, pat, repl, s):
        out, n = pat.subn(repl, s)
        if n:
            hits[kind] += n
        return out

    for kind, pat, repl in _SECRETS:
        text = sub(kind, pat, repl, text)

    def email(m):
        if _EMAIL_OK_DOMAIN.search(m.group(1)) or m.group(0).split("@")[0].lower() == "git":
            return m.group(0)
        hits["email"] += 1
        return "<email>"
    text = _EMAIL.sub(email, text)

    for pat in _HOME_PATHS:
        text = sub("home_path", pat, lambda m: "~/" if m.group(1) else "~", text)

    for kind, name, ph in (("hostname", host, "<host>"), ("username", user, "<user>")):
        for n in _names(name):
            text = sub(kind, re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(n) + r"(?![A-Za-z0-9_-])", re.I), ph, text)

    for pat in _PHONES:
        text = sub("phone", pat, "<phone>", text)
    text = sub("private_ip", _PRIVATE_IP, "<private-ip>", text)

    def entropic(m):
        if not _entropic(m.group(0)):
            return m.group(0)
        hits["high_entropy"] += 1
        return R
    return _ENTROPIC.sub(entropic, text)


def redact(text: str, host: str | None = None, user: str | None = None) -> str:
    return redact_with_report(text, host, user)[0]


def redact_draft(obj, host: str | None = None, user: str | None = None, hits: collections.Counter | None = None):
    """Every string value in a JSON draft (keys untouched; numbers / booleans as they are)."""
    hits = hits if hits is not None else collections.Counter()
    if isinstance(obj, str):
        out, h = redact_with_report(obj, host, user)
        hits.update(h)
        return out
    if isinstance(obj, list):
        return [redact_draft(x, host, user, hits) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_draft(v, host, user, hits) for k, v in obj.items()}
    return obj


def local_identity() -> tuple[str | None, str | None]:
    """This machine's host name and the current user name (what layer 1 replaces with <host> / <user>)."""
    try:
        host = socket.gethostname() or None
    except OSError:
        host = None
    try:
        user = getpass.getuser() or None
    except Exception:  # getpass raises OSError / KeyError when no name is resolvable
        user = None
    return host, user


# ------------------------------------------------------------------ layer 2
@dataclass
class Verdict:
    status: str                       # ok | blocked | unavailable
    probability: float | None = None  # Jev's probability that the draft still exposes private information
    kind: str | None = None           # the most likely kind (reason shown to the human); never decides alone
    reason: str = ""                  # one line, never contains the key
    model: str | None = None
    criteria_version: str | None = None


def load_criteria(path: pathlib.Path = CRITERIA_PATH) -> dict:
    """The first ```json block of privacy_criteria.md: {"version", "questions"}."""
    m = re.search(r"```json\n(.*?)\n```", path.read_text(encoding="utf-8"), re.S)
    if not m:
        raise ValueError(f"{path.name}: no json block")
    c = json.loads(m.group(1))
    if not isinstance(c.get("version"), str) or c.get("questions", {}).get("exposure", {}).get("type") != "noul":
        raise ValueError(f"{path.name}: needs version + questions.exposure (noul)")
    return c


def urllib_transport(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    """(status, raw body) — the only network seam of layer 2 (tests replace it)."""
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(1 << 20)
    except urllib.error.HTTPError as e:
        return e.code, b""


def layer2(draft, *, threshold: float = THRESHOLD, env=None, transport=None, timeout: float = 20.0,
           criteria: dict | None = None, gate: str = "feedback") -> Verdict:
    """Jev on the (already layer-1) draft. Never raises: every failure is Verdict(status="unavailable").
    `gate` = "feedback" (privacy_criteria.md, state key feedback_draft) or "plaza" (plaza_criteria.md, plaza_draft)."""
    crit_path, state_key, _ = GATES[gate]
    env = os.environ if env is None else env
    transport = transport or urllib_transport
    key = (env.get(KEY_ENV) or "").strip()
    if not key:
        return Verdict("unavailable", reason=f"no {KEY_ENV} in the environment")
    try:
        crit = criteria or load_criteria(crit_path)
    except (OSError, ValueError) as e:
        return Verdict("unavailable", reason=f"criteria unreadable ({type(e).__name__})")
    body = json.dumps({"model": MODEL, "state": {state_key: draft}, "questions": crit["questions"]},
                      ensure_ascii=False).encode()
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": UA}
    try:
        status, raw = transport(ENDPOINT, headers, body, timeout)
    except Exception as e:  # network, TLS, timeout — the class name only (messages could carry anything)
        return Verdict("unavailable", reason=f"network error ({type(e).__name__})", criteria_version=crit["version"])
    if status != 200:
        return Verdict("unavailable", reason=f"HTTP {status}", criteria_version=crit["version"])
    try:
        j = json.loads(raw)
        p = float(j["answers"]["exposure"]["noul"])
        if not 0.0 <= p <= 1.0 or math.isnan(p):
            raise ValueError
        kind = (j["answers"].get("kind") or {}).get("choice")
        kind = kind if isinstance(kind, str) and kind in crit["questions"].get("kind", {}).get("criteria", {}) else None
        model = j.get("model") if isinstance(j.get("model"), str) else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return Verdict("unavailable", reason="unexpected answer", criteria_version=crit["version"])
    if p >= threshold:
        return Verdict("blocked", p, kind, f"p={p:.2f} ≥ {threshold}", model, crit["version"])
    return Verdict("ok", p, kind, f"p={p:.2f} < {threshold}", model, crit["version"])


def kind_text(kind: str | None, criteria: dict | None = None, gate: str = "feedback") -> str:
    try:
        crit = criteria or load_criteria(GATES[gate][0])
        return crit["questions"]["kind"]["criteria"].get(kind or "", "") or "(no reason given)"
    except (OSError, ValueError, KeyError):
        return "(no reason given)"


# ------------------------------------------------------------------ live eval (opt-in, paid)
# Fake secrets the cases file refers to as {{fake:<name>}}; assembled here so no tracked
# file holds a secret-shaped literal (repo hygiene scan, public-export denylist).
FAKE_SECRETS = {
    "openrouter": "sk-" + "or-v1-" + "0123456789abcdef" * 2,
    "machome": "/Us" + "ers/",   # not a secret: a macOS home prefix, kept out of tracked text for the export denylist
}


def _expand_fakes(text: str) -> str:
    return re.sub(r"\{\{fake:([a-z]+)\}\}", lambda m: FAKE_SECRETS[m.group(1)], text)


def load_cases(path: pathlib.Path = CASES_PATH) -> list[dict]:
    cases = json.loads(_expand_fakes(path.read_text(encoding="utf-8")))["cases"]
    for c in cases:
        if c.get("expect") not in ("clean", "leaky") or "id" not in c or "draft" not in c:
            raise ValueError(f"bad case: {c.get('id')}")
    return cases


def run_eval(cases: list[dict], *, threshold: float = THRESHOLD, env=None, transport=None, out=None, gate: str = "feedback") -> int:
    """Each case: layer 1 (no machine identity, so results do not depend on who runs it), then layer 2 → PASS/FAIL.
    0 = all pass · 1 = some failed · 3 = layer 2 unavailable."""
    passed, out = 0, out or sys.stdout
    crit = load_criteria(GATES[gate][0])
    print(f"privacy eval ({gate}) · {MODEL} · criteria {crit['version']} · threshold {threshold} · {len(cases)} case(s)", file=out)
    for c in cases:
        v = layer2(redact_draft(c["draft"]), threshold=threshold, env=env, transport=transport, criteria=crit, gate=gate)
        if v.status == "unavailable":
            print(f"layer 2 unavailable: {v.reason}", file=out)
            return 3
        ok = (v.status == "blocked") == (c["expect"] == "leaky")
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {c['id']:<28} expect={c['expect']:<5} p={v.probability:.3f} kind={v.kind}", file=out)
    print(f"{passed}/{len(cases)} passed" + (f" · model {v.model}" if cases and v.model else ""), file=out)
    return 0 if passed == len(cases) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m jarvis_host.privacy", description=__doc__.split("\n\n")[0])
    ap.add_argument("--eval", action="store_true", help="run the regression set live against Jev (uses your OPENROUTER_API_KEY; paid)")
    ap.add_argument("--gate", choices=sorted(GATES), default="feedback", help="which criteria: feedback (default) or plaza (P2)")
    ap.add_argument("--cases", type=pathlib.Path, default=None, help="default: the gate's own case set")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    a = ap.parse_args(argv)
    if not a.eval:
        ap.print_help()
        return 0
    if not 0 < a.threshold <= 1:
        ap.error("--threshold must be in (0, 1]")
    return run_eval(load_cases(a.cases or GATES[a.gate][2]), threshold=a.threshold, gate=a.gate)


if __name__ == "__main__":
    sys.exit(main())
