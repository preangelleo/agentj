"""Two-layer privacy gate for install feedback (seat-setup CONTRACT §6; worker/FEEDBACK_API.md §privacy gate) and, plaza P2,
for Agent plaza posts / replies (`gate="plaza"`: plaza_criteria.md, state key plaza_draft — agentj/plaza.py).

Layer 1 — `redact()` / `redact_draft()`: deterministic, pure, idempotent. Keys and tokens (the server scan's patterns),
emails (except @agentj.app, @agentjarvis.net and RFC 2606 example domains), phone numbers (E.164-ish, CN mobile), home paths
(Linux / macOS / Windows / WSL → ~/), this machine's host and user name (→ <host> / <user>), private IPv4 addresses, our
aj?_ ids. Runs on this machine only.

Layer 2 — `layer2()`: Jev (`~typesafe/jev-latest`, TypeSafe decisions API on OpenRouter) judges the layer-1 output
against the criteria in `privacy_criteria.md` and returns a probability of privacy exposure; ≥ threshold (0.5) → blocked.
Only with the customer's own OPENROUTER_API_KEY from the environment; the key is never logged or printed and is sent
to ENDPOINT only (no override). No key / network / non-200 / odd answer → "unavailable": the caller falls back to
layer 1 + an explicit human confirmation. Jev never sees the raw draft.

`python3 -m agentj.privacy --eval` runs the regression set (host/tests/privacy_cases.json) live — paid, opt-in.
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
UA = "agentj (privacy gate)"

R = "<redacted>"


def _re(pattern: str, flags: int = 0) -> re.Pattern:
    """ASCII \\b \\d \\w, like the server's JavaScript regexes (a CJK letter is not a word character there)."""
    return re.compile(pattern, flags | re.ASCII)


# ------------------------------------------------------------------ layer 1
# Mirrors worker/src/scan.ts so a layer-1 draft never trips the server scan; replacements never match again (idempotent).
# Names whose value is a secret (case-insensitive): the server scan's list, plus (review SS-04) pass / pw / auth / credentials
# and any NAME ending in KEY / SECRET / TOKEN — `DB_PASS`, `MAILGUN_API_KEY`, `stripeSecret`, `x-auth-token` (a lower-case
# suffix needs a `_` / `-` before it, so `monkey` is not a name; upper or Capitalised suffixes may follow letters directly).
_CRED_NAMES = (r"(?:api[ _-]?key|apikey|x[_-]api|secret|token|password|passwd|pass|pwd|pw|auth|credentials?|access[ _-]?key|client[ _-]?secret"
               r"|private[ _-]?key|secret[ _-]?key|私钥|密钥|密码|口令|令牌|秘密|访问令牌|助记词|恢复短语|mnemonic|(?:seed|recovery|secret recovery)[ _-]?phrase|[A-Za-z0-9_-]*[_-](?:key|secret|token|pass|passwd|pwd|pw)|[A-Za-z0-9]*(?-i:KEY|SECRET|TOKEN|Key|Secret|Token))")
_SECRETS: list[tuple[str, re.Pattern, str]] = [
    ("private_key", _re(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----(?:[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----|[\s\S]*)"), R),
    # P326: a scheme is at most 32 characters — unbounded, a long "a.b.c…" run was scanned once per position (quadratic)
    ("url_credentials", _re(r"\b([a-z][a-z0-9+.-]{0,31}://)[^\s/:@]+:[^\s/@]+@", re.I), r"\1" + R + "@"),
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
    ("agentjarvis_id", _re(r"\baj[ahijmrst]_[A-Za-z0-9_-]{16,}"), R),   # ajm_ = the plaza admin token (P2)
    ("agentj_access_code", _re(r"\bAJV-[A-Z0-9]{4}-[A-Z0-9]{4}\b", re.I), R),
    ("agentj_access_code", _re(r"\bAJUP-[A-Z0-9]{5}(?:-[A-Z0-9]{5}){3}\b", re.I), R),
    # review SS-04: more secret shapes the server scan does not know (layer 1 may be stricter than the server, never looser)
    ("mailgun_key", _re(r"\bkey-[0-9a-f]{32}\b"), R),
    ("authorization_header", _re(r"\b(Authorization\s*:\s*(?:Bearer|Basic|token)\s+)(?!<redacted>|\$\(|\$\{|\$[A-Z_])[^\s\"'<>\u3000-\u303f\uff00-\uffef]+", re.I),
     r"\1" + R),
    ("credential_assignment", _re(
        r"(?<![A-Za-z0-9])(" + _CRED_NAMES + r"[\"']?\s*[:：=]\s*)([\"'`])(?!<[^<>\r\n]+>\2|\$\(|\$\{|\$[A-Z_])(?:(?!\2)[^\n`\u3000-\u303f\u3400-\u9fff\uff00-\uffef]){6,}\2", re.I),
     r"\1\2" + R + r"\2"),                                                     # a quoted value, spaces allowed
    # P573 (#386): a value never runs into full-width / CJK punctuation (「密钥：CredSweeper（MIT，ONNX」 was one 24-byte "value")
    ("credential_assignment", _re(
        r"(?<![A-Za-z0-9])(" + _CRED_NAMES + r"[\"']?\s*[:：=]\s*[\"']?)(?!<redacted>|\$\(|\$\{|\$[A-Z_])[^\s\"'<>`\u3000-\u303f\u3400-\u9fff\uff00-\uffef]{8,}", re.I), r"\1" + R),
    # Public wallet shapes must not bypass an explicit private/secret-key label.
    ("credential_assignment", _re(r"((?:private[ _-]?key|secret[ _-]?key|私钥|密钥)\s*[:：=]\s*[\"']?)[^\s\"'<>`\u3000-\u303f\u3400-\u9fff\uff00-\uffef]{8,}", re.I), r"\1" + R),
    ("labelled_hex", _re(
        r"(?<![A-Za-z0-9])((?:" + _CRED_NAMES + r"|key)[\"']?(?:\s*[:=]\s*|\s+(?:is\s+)?)[\"']?)[0-9a-fA-F]{32,}(?![0-9A-Za-z])", re.I), r"\1" + R),
]
# Public IDs are PII, not credentials.
_PERSONAL_IDS = [("agentj_public_id", _re(r"\bAJ-[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}\b", re.I), R)]
_EMAIL = _re(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
_EMAIL_OK_DOMAIN = _re(r"(?:^|\.)(?:agentj\.app|agentjarvis\.net|example\.(?:com|org|net))$", re.I)
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
archlinux centos rocky alma alpine kali mint manjaro pi raspberrypi ec2-user vagrant runner guest test agentj jarvis mac macbook
macbook-pro macbook-air imac linux windows desktop server laptop pc workstation default redacted email phone private ip
private-ip none null codex claude agent""".split())


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in collections.Counter(s).values())


def _sluglike(tok: str) -> bool:
    """Recognize title slugs using at least four ordinary-cased words.

    Mixed-case product names may appear alongside those words. Mixed-case random
    chunks alone must not exempt a base64url credential from the entropy scan.
    """
    parts = tok.split("-")
    return (len(parts) >= 6 and all(p and (p.isalpha() or p.isdigit() or len(p) <= 4) for p in parts)
            and sum(bool(re.fullmatch(r"(?:[a-z]{3,}|[A-Z][a-z]{2,}|[A-Z]{3,})", p)) for p in parts) >= 4)



_PUBLIC_CHAIN = re.compile(r"(?:[1-9A-HJ-NP-Za-km-z]{32,44}|[1-9A-HJ-NP-Za-km-z]{87,88}|0x(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64}))")


def _public_chain(tok: str) -> bool:
    return _PUBLIC_CHAIN.fullmatch(tok) is not None


def _entropic(tok: str) -> bool:
    if re.fullmatch(r"[0-9a-fA-F-]+", tok):        # hashes, UUIDs, commit ids are fine (same as the server)
        return False
    segs = [x for x in tok.split("/") if x]      # a link's tail `net/Asus-…-review`: lower-case words around one slug
    if any(_sluglike(x) for x in segs) and all(_sluglike(x) or not re.search(r"[A-Z]", x) for x in segs):
        return False
    if not (re.search(r"[A-Z]", tok) and re.search(r"[a-z]", tok) and re.search(r"[0-9]", tok)):
        return False
    return _entropy(tok) >= 4.3


_NAME_CHUNK = re.compile(r"[A-Z][a-z]{2,}|[a-z]{3,}|[A-Z]{2,}(?![a-z])|\d+|[a-z]\d+|[A-Z]\d+")
_NAME_PART = re.compile(r"(?:" + _NAME_CHUNK.pattern + r")+|[A-Za-z]{1,2}")
_VOWEL = re.compile(r"[aeiouyAEIOUY]")


def _namelike(tok: str) -> bool:
    parts = [p for p in re.split(r"[_\-./]", tok) if p]
    if len(parts) < 2 or not all(_NAME_PART.fullmatch(p) and len(re.findall(r"\d+", p)) <= 2 for p in parts):
        return False
    words = [c for p in parts for c in _NAME_CHUNK.findall(p) if c.isalpha()] + [p for p in parts if len(p) <= 2 and p.isalpha()]
    novowel = [w for w in words if len(w) >= 3 and not _VOWEL.search(w)]
    if len(novowel) > 1 or any(not (w.isupper() and len(w) <= 4) for w in novowel):
        return False
    alpha = sum(len(w) for w in words)
    return alpha > 0 and sum(len(w) for w in words if len(w) >= 4) >= 0.6 * alpha


_ENV_NAME = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
_REF_VALUE = re.compile(r"\$\(|\$\{|(?:\$(?:env:)?[A-Za-z_]\w*|(?-i:%[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+%))(?![^\s\"'`;|&)\]},])", re.I)
_CODE_ARG = r"(?:\"[A-Za-z_][\w.-]*\"|'[A-Za-z_][\w.-]*'|\$\{?[A-Za-z_]\w*\}?|[a-z_][a-z0-9_]{0,23}|" + _ENV_NAME.pattern + r")"
_CODE_VALUE = re.compile(r"([A-Za-z_][\w.]*)(?:\((?:" + _CODE_ARG + r"(?:\s*,\s*" + _CODE_ARG + r")*)?\)|\[" + _CODE_ARG
                         + r"\])+(?=$|[\s,;)\]}])")
_CODE_CALLEES = frozenset("getenv env environ secret secrets config settings vault keyring getpass input get".split())
_GREP_TAIL = re.compile(r"[ \t]+(?:[~/.$]|\|)")
_KNOWN_ENV: set[str] = set()
KNOWN_ENV_MAX = 4096


def remember_env_names(names) -> None:
    """Key NAMES (never values) of the env files the SecretIndex read: `KEY=OTHER_ENV_NAME` toward others needs one of these."""
    for n in names:
        if len(_KNOWN_ENV) >= KNOWN_ENV_MAX:
            return
        if isinstance(n, str) and _ENV_NAME.fullmatch(n):
            _KNOWN_ENV.add(n)


def _known_env_name(n: str) -> bool:
    return n in _KNOWN_ENV or n in os.environ


def _code_value(v: str) -> bool:
    m = _CODE_VALUE.match(v)
    if not m:
        return False
    callee = m.group(1)
    return "." in callee or "_" in callee or callee.split(".")[-1] in _CODE_CALLEES


def _not_value(match: re.Match, repl: str, owner: bool = False) -> bool:
    """Recognize whole shell/code/name references; owner channels also accept name-shaped environment identifiers."""
    rendered = match.expand(repl)
    pre = rendered.partition(R)[0]
    rest = match.string[match.start() + len(pre):]
    quoted = bool(pre) and pre[-1] in "\"'"
    if quoted and rest[:1] in (" ", "\t"):
        return bool(_GREP_TAIL.match(rest)) and pre[-1] in rest.partition("\n")[0]
    v = rest.lstrip("\"'")
    if _REF_VALUE.match(v) or (not quoted and v == rest and _code_value(v)):
        return True
    token = re.match(r"[^\s\"'<>]+", v)
    if not (token and _ENV_NAME.fullmatch(token.group(0))):
        return False
    name = token.group(0)
    return _known_env_name(name) or (owner and _namelike(name))


VALUE_KINDS = frozenset(("credential_assignment", "authorization_header", "bearer_token"))
_PW_LABEL = re.compile(r"(?:password|passwd|pass|pwd|pw|密码|口令|助记词|恢复短语|mnemonic|(?:seed|recovery)[ _-]?phrase)[\"'`\s:：=]*$", re.I)
# (seed words keep their strength too: a mnemonic is lower-case words, never "digit or symbol")
_PLACEHOLDER_VALUES = frozenset(("true", "false", "null", "none", "undefined", "password", "changeme", "your_api_key", "your_token",
                                 "replace_me", "placeholder", "redacted", "example", "secret", "not_set"))
_PLACEHOLDER_WORD = re.compile(r"(?:example|sample|dummy|fake|placeholder|redacted|changeme|change[_-]me|"
                               r"(?:your|example|placeholder|replace|insert)[_-](?:api[_-]?key|key|token|secret|password)"
                               r"(?:[_-]here)?)", re.I)
_BARE_PREFIX = re.compile(r"(?:sk-(?:or-(?:v1-)?|ant-(?:api\d\d-)?|proj-|svcacct-|admin-)?|sk_(?:live|test)_|ghp_|gho_|github_pat_|"
                          r"glpat-|xox[abposr]-|AKIA|ASIA|AIza|hf_|r8_|aj[ahijmrst]_|whsec_|Bearer)[xX*.…_-]*")
_NAME_VALUE = re.compile(r"[A-Z][A-Z0-9_]*_(?:KEY|TOKEN|SECRET|PASSWORD|PASS|CREDENTIALS?|VAR|NAME)", re.I)


def _label_value(match: re.Match, repl: str) -> tuple[str, str, bool]:
    """(label part, value part, quoted?) of a labelled match, as the replacement splits it around R."""
    rendered = match.expand(repl)
    pre, _, suffix = rendered.partition(R)
    whole = match.group(0)
    return pre, whole[len(pre):len(whole) - len(suffix) if suffix else None], bool(suffix)


_HEADER_LABEL = re.compile(r"(?:\bBearer\s+|\bAuthorization\s*:\s*(?:Bearer|Basic|token)\s+)$", re.I)


def is_secret(value: str, password: bool = False, quoted: bool = False, header: bool = False) -> bool:
    """P573: does this labelled value look like a real credential (see VALUE_KINDS above)? Never logs or returns the value."""
    v = value.lstrip("`")
    if not quoted and not password:            # unquoted: judge its longest key-character run (`x(<random>)` is a value)
        runs = re.findall(r"[A-Za-z0-9+/_=.!@#$%^&*?~-]+", v)
        v = max(runs, key=len) if runs else ""
    v = v.rstrip(",;|`)]}.") if not password else v.rstrip(",;|`]}.")
    if not v.strip():
        return False
    low = v.lower()
    if low in _PLACEHOLDER_VALUES or _PLACEHOLDER_WORD.fullmatch(v) or re.fullmatch(r"[*xX•.…_-]+", v):
        return False
    if "..." in v or "…" in v or "${" in v or "$HOME" in v or v.startswith(("<", "~/", "./")):   # `$x` / `%X%`: _not_value
        return False
    path = re.match(r"[A-Za-z0-9+/_=-]+", v)
    if path and "/" in path.group(0) and _pathlike(path):
        return False                           # owner-path syntax, not a blanket slash exception
    if re.fullmatch(_CRED_NAMES, v, re.I) or _NAME_VALUE.fullmatch(v):
        return False                           # APIFY_TOKEN, OPENAI_API_KEY … are names
    if _BARE_PREFIX.fullmatch(v):
        return False                           # `sk-or-`, `sk-ant-` alone: a prefix being talked about
    if password:
        return True                            # P573: a password label keeps its strength
    if header and len(v) >= 20:
        return True                            # `Authorization: Bearer <20+ chars>` is a token, never prose (as before P573)
    if len(v) < 8 or not re.fullmatch(r"[A-Za-z0-9+/_=.!@#$%^&*?~ -]+", v):
        return False
    if re.fullmatch(r"(?:0x)?[0-9a-fA-F]{16,}", v):
        return True                            # a hex key (an EVM private key is 0x + 64 hex)
    if _namelike(v):
        return False                           # words joined by _ - . / : an identifier, a slug
    if re.fullmatch(r"[A-Za-z]{1,19}", v):
        return False                           # 「CredSweeper」: letters only and short — a word, not a key
    classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9 ]"))
    entropy = _entropy(v)
    return (classes >= 2 and entropy >= 2.5) or (len(v) >= 32 and entropy >= 4.0)


def credential_value(match: re.Match, repl: str) -> bool:
    """Shared owner-channel verdict: reject non-value references, then apply is_secret to the labelled value."""
    if _not_value(match, repl, True):
        return False
    pre, value, quoted = _label_value(match, repl)
    return is_secret(value, bool(_PW_LABEL.search(pre)), quoted, bool(_HEADER_LABEL.search(pre)))


_PATH_HEAD = re.compile(r"[A-Za-z0-9][a-z0-9._-]*")
_PATH_EXT = re.compile(r"\.[A-Za-z0-9]{1,8}(?![A-Za-z0-9])")
SEGMENT_MIN = 24


def _pathlike(m: re.Match) -> bool:
    tok, s = m.group(0), m.string
    if "/" not in tok or "+" in tok or "=" in tok or len([x for x in tok.split("/") if x]) < 2:
        return False
    if tok.startswith("/"):
        return bool(_PATH_HEAD.fullmatch(tok.split("/")[1]))
    return (m.start() > 0 and s[m.start() - 1] in "~.") or bool(_PATH_EXT.match(s, m.end()))


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


def redact_with_report(text: str, host: str | None = None, user: str | None = None,
                       paths: bool = False, owner: bool = False) -> tuple[str, collections.Counter]:
    """Idempotent layer-1 redaction → (text, kind counts). Strict outbound by default.
    Owner mode preserves contact details but masks credentials; paths mode judges path segments separately."""
    hits: collections.Counter = collections.Counter()
    paths = paths or owner
    for _ in range(8):
        new = _owner_pass(text, host, user, hits) if owner else _one_pass(text, host, user, hits, paths, owner)
        if new == text:
            break
        text = new
    return text, hits


_LABELLED = frozenset(("credential_assignment", "authorization_header"))
_LINK = _re(r"\b[a-z][a-z0-9+.-]{0,31}://[^\s<>\"'`]+", re.I)     # P326: bounded scheme (see url_credentials)
_LINK_HARD = frozenset(("private_key", "url_credentials", "aws_access_key", "github_token", "gitlab_token", "slack_token",
                        "stripe_key", "google_api_key", "anthropic_key", "openrouter_key", "openai_key", "replicate_token",
                        "huggingface_token", "telegram_bot_token", "agentjarvis_id", "mailgun_key"))
_MNEMONIC = _re(r"(?i)((?:mnemonic|seed\s+phrase|recovery\s+phrase|secret\s+recovery\s+phrase|助记词|恢复短语)[^\n:：]{0,12}[:：]?\s*)"
                r"((?:[a-z]{3,8}[ ,]+){11,23}[a-z]{3,8})\b")


def _owner_pass(text: str, host: str | None, user: str | None, hits: collections.Counter) -> str:
    out, at = [], 0
    for m in _LINK.finditer(text):
        out.append(_one_pass(text[at:m.start()], host, user, hits, True, True))
        link = m.group(0)
        for kind, pat, repl in _SECRETS + _PERSONAL_IDS:
            if kind in _LINK_HARD or kind == "agentj_public_id":
                link, n = pat.subn(repl, link)
                if n:
                    hits[kind] += n
        out.append(link)
        at = m.end()
    out.append(_one_pass(text[at:], host, user, hits, True, True))
    return "".join(out)


def credentials(text: str, entropy: bool = True) -> list[tuple[str, str, int, int]]:
    """P194: the real credentials in an owner-bound text — (kind, value, start, end), in order, not overlapping. The same
    rules as owner mode: access codes and links are not credentials."""
    spans: list[tuple[int, int, str]] = []

    def add(seg_start, m, repl, kind):
        rep = m.expand(repl) if isinstance(repl, str) else R
        i = rep.find(R)
        pre, suf = (rep[:i], rep[i + len(R):]) if i >= 0 else ("", "")
        s, e = seg_start + m.start() + len(pre), seg_start + m.end() - len(suf)
        if e > s:
            spans.append((s, e, kind))

    def scan(seg: str, base: int, link: bool) -> None:
        for kind, pat, repl in _SECRETS:
            if kind == "agentj_access_code":
                continue  # P194 owner transfer
            if link and kind not in _LINK_HARD:
                continue
            for m in pat.finditer(seg):
                if R in m.group(0) or (kind in VALUE_KINDS and not credential_value(m, repl)):
                    continue
                add(base, m, repl, kind)
        if link:
            return
        for m in _MNEMONIC.finditer(seg):
            spans.append((base + m.start(2), base + m.end(2), "mnemonic"))
        for m in _ENTROPIC.finditer(seg) if entropy else ():
            if _public_chain(m.group(0)) or _pathlike(m) or _namelike(m.group(0)):   # P194 / P424: same as the owner mode — a path / name is no credential
                continue
            if _entropic(m.group(0)):
                spans.append((base + m.start(), base + m.end(), "high_entropy"))
    at = 0
    for m in _LINK.finditer(text):
        scan(text[at:m.start()], at, False)
        scan(m.group(0), m.start(), True)
        at = m.end()
    scan(text[at:], at, False)
    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    out: list[tuple[str, str, int, int]] = []
    end = -1
    for s, e, kind in spans:
        if s < end:                       # inside one already taken (a key inside a credential assignment …)
            continue
        out.append((kind, text[s:e], s, e))
        end = e
    return out


def secret_at(text: str, kinds=None) -> tuple[str, int, int] | None:
    """Earliest relaxed-rule secret span (kind, start, end), or None; location hints never grant authority."""
    spans: list[tuple[int, int, str]] = []
    for kind, pat, repl in _SECRETS:
        if kinds is not None and kind not in kinds:
            continue
        for m in pat.finditer(text):
            if R in m.group(0) or (kind in _LABELLED and _not_value(m, repl)):
                continue
            rep = m.expand(repl)
            i = rep.find(R)
            pre, suf = (rep[:i], rep[i + len(R):]) if i >= 0 else ("", "")
            if m.end() - len(suf) > m.start() + len(pre):
                spans.append((m.start() + len(pre), m.end() - len(suf), kind))
    if kinds is None or "high_entropy" in kinds:
        for m in _ENTROPIC.finditer(text):
            tok = m.group(0)
            if _namelike(tok):
                continue
            if _pathlike(m):
                at = m.start()
                for seg in tok.split("/"):
                    if len(seg) >= SEGMENT_MIN and _entropic(seg) and not _namelike(seg):
                        spans.append((at, at + len(seg), "high_entropy"))
                    at += len(seg) + 1
            elif _entropic(tok):
                spans.append((m.start(), m.end(), "high_entropy"))
    if not spans:
        return None
    s, e, kind = min(spans)
    return kind, s, e


def where(text: str, span: tuple[str, int, int] | None) -> dict | None:
    """(kind, start, end) → {"kind", "line", "col", "len", "hint"}: 1-based line / column and the fragment masked to its first
    two characters + "…" — enough to find it, never enough to use it."""
    if not span:
        return None
    kind, s, e = span
    line = text.count("\n", 0, s) + 1
    col = s - (text.rfind("\n", 0, s) + 1) + 1
    return {"kind": kind, "line": line, "col": col, "len": e - s, "hint": text[s:s + 2] + "…"}


def _one_pass(text: str, host: str | None, user: str | None, hits: collections.Counter, paths: bool = False,
              owner: bool = False) -> str:
    def sub(kind, pat, repl, s):
        out, n = pat.subn(repl, s)
        if n:
            hits[kind] += n
        return out

    # Seed phrases must be consumed whole before a generic labelled-token rule.
    text = sub("mnemonic", _MNEMONIC, r"\1" + R, text)
    relaxed = paths or owner
    for kind, pat, repl in _SECRETS + _PERSONAL_IDS:
        if owner and kind == "agentj_access_code":
            continue
        if (relaxed and kind in _LABELLED) or (owner and kind in VALUE_KINDS):
            def keep(m, repl=repl, kind=kind):
                if _not_value(m, repl, owner) or (owner and not credential_value(m, repl)):   # P573: one owner verdict
                    return m.group(0)
                hits[kind] += 1
                return m.expand(repl)
            text = pat.sub(keep, text)
            continue
        text = sub(kind, pat, repl, text)
    text = sub("mnemonic", _MNEMONIC, r"\1" + R, text)          # P194: a wallet's seed words

    def email(m):
        if _EMAIL_OK_DOMAIN.search(m.group(1)) or m.group(0).split("@")[0].lower() == "git":
            return m.group(0)
        hits["email"] += 1
        return "<email>"
    if not owner:
        text = _EMAIL.sub(email, text)

    for pat in _HOME_PATHS:
        text = sub("home_path", pat, lambda m: "~/" if m.group(1) else "~", text)

    for kind, name, ph in (("hostname", host, "<host>"), ("username", user, "<user>")):
        for n in _names(name):
            text = sub(kind, re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(n) + r"(?![A-Za-z0-9_-])", re.I), ph, text)

    for pat in () if owner else _PHONES:
        text = sub("phone", pat, "<phone>", text)
    if not owner:                               # P194: the owner's own LAN addresses are theirs to copy
        text = sub("private_ip", _PRIVATE_IP, "<private-ip>", text)

    def entropic(m):
        if owner and _public_chain(m.group(0)):
            return m.group(0)
        if owner and _pathlike(m):          # P194: a local path to the owner's own channel stays whole — the media scan and
            return m.group(0)               # the owner both need it (named key shapes inside it were already taken above)
        if paths and _namelike(m.group(0)):  # P424: a path / file name / identifier / env-var name made of words
            return m.group(0)
        if paths and _pathlike(m):
            segs = [R if len(x) >= SEGMENT_MIN and _entropic(x) and not _namelike(x) else x for x in m.group(0).split("/")]
            if segs.count(R):                # P424: never a zero count — `k in hits` is how callers ask "any secret?"
                hits["high_entropy"] += segs.count(R)
            return "/".join(segs)
        if not _entropic(m.group(0)):
            return m.group(0)
        hits["high_entropy"] += 1
        return R
    return _ENTROPIC.sub(entropic, text)


def redact(text: str, host: str | None = None, user: str | None = None, paths: bool = False, owner: bool = False) -> str:
    return redact_with_report(text, host, user, paths, owner)[0]


def owner_text(text: str, *, mask_access_codes: bool = False) -> str:
    """P194 owner delivery: keep contact details, paths, links and access codes; hide credentials.
    Terminals set mask_access_codes to also hide transferable codes."""
    if mask_access_codes:
        for kind, pat, repl in _SECRETS:
            if kind == "agentj_access_code":
                text = pat.sub(repl, text)
    return redact(text, owner=True)


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
    from . import jev
    route = jev.resolve(env=env)
    if route.mode == "off":
        return Verdict("ok", reason="Jev off: owner confirmed; local redaction only")
    key = route.key
    if not key:
        return Verdict("unavailable", reason=f"no {KEY_ENV} in the environment")
    try:
        crit = criteria or load_criteria(crit_path)
    except (OSError, ValueError) as e:
        return Verdict("unavailable", reason=f"criteria unreadable ({type(e).__name__})")
    body = json.dumps({"model": route.model, "state": {state_key: draft}, "questions": crit["questions"]},
                      ensure_ascii=False).encode()
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": UA}
    try:
        status, raw = transport(route.endpoint, headers, body, timeout)
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
    "machome": "/Us" + "ers/",
    # P127 layer-1 cases (privacy_cases.json "layer1")
    "keyseg": "".join(('Zq8xV2', 'mK9pL4', 'rT7wY1', 'nB6cD3', 'fG5hJ0', 'sAeU')),
    "awssecret": "".join(('wJalrX', 'UtnFEM', 'I/K7MD', 'ENG/bP', 'xRfiCY', 'zQ8Lm3', 'Nq2W')),
    "slashkey": "".join(('aB3+dE', '5/fG7h', 'J9kL1m', 'N2pQ4r', 'S6tU8v', 'W0xY+z', 'A1bC/d', 'E3fG==')),   # not a secret: a macOS home prefix, kept out of tracked text for the export denylist
}


def _expand_fakes(text: str) -> str:
    return re.sub(r"\{\{fake:([a-z]+)\}\}", lambda m: FAKE_SECRETS[m.group(1)], text)


def load_layer1_cases(path: pathlib.Path = CASES_PATH) -> list[dict]:
    return json.loads(_expand_fakes(path.read_text(encoding="utf-8")))["layer1"]["cases"]


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
    ap = argparse.ArgumentParser(prog="python3 -m agentj.privacy", description=__doc__.split("\n\n")[0])
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
