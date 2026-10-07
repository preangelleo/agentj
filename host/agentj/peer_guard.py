"""Agent friends: the deterministic gates around the peer session (PROTOCOL §17.6 steps 3, 4 and 6; ADR-0.16 §6.1, §8).

- `inbound(text)`  — before the model: tg_guard's normalisation and its "asks for a credential" rules, plus credential *shapes*
  (private-key headers, vendor key / token patterns, labelled secrets, high-entropy strings from privacy.py's layer 1). A hit is
  not given to the model; the owner is told. (The optional Jev second layer is 0.16.1.)
- `outbound(text, index, never_tell_extra)` — before a reply leaves: fragments of this host's own secrets (SecretIndex), the
  same credential shapes, private-key headers, and the owner's own 「绝不外说」 additions as keywords. The fixed never-tell list
  is a rule for the model (peer_session's system prompt), not a keyword filter — matching 「密码」 would only block harmless
  sentences. Verdict.rule says why in a few words and never contains the matched text.
- `wrap(friend_id, name, text)` — the friend's words as data: one JSON object, `"untrusted": true`.

SecretIndex keeps no secret: only salted 64-bit hashes of every 16-character window (whole value for 12–15 characters) of each
secret-looking value, with a random per-process salt.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass

from . import privacy, tg_guard

WINDOW = 16
MIN_SECRET = 12
MAX_FILE = 1024 * 1024
MAX_VALUES = 5000
SECRET_NAME = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASS|CREDENTIAL|AUTH|COOKIE|PRIVATE|SESSION)")
PRIVATE_KEY = re.compile(r"-----\s*BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?\s*-----")
# privacy.py layer-1 kinds that mean "a credential" (not e-mail / phone / path / host / IP, which are ordinary in a chat)
CRED_KINDS = frozenset({"private_key", "url_credentials", "bearer_token", "jwt", "aws_access_key", "github_token",
                        "gitlab_token", "slack_token", "stripe_key", "google_api_key", "anthropic_key", "openrouter_key",
                        "openai_key", "replicate_token", "huggingface_token", "telegram_bot_token", "agentjarvis_id",
                        "mailgun_key", "authorization_header", "credential_assignment", "labelled_hex", "high_entropy"})
# kinds that are safe to try on the space-squeezed spelling too ("sk-ant- abc…" split by a space); the label / entropy rules
# would fuse ordinary words there
VENDOR_KINDS = frozenset({"private_key", "jwt", "aws_access_key", "github_token", "gitlab_token", "slack_token", "stripe_key",
                          "google_api_key", "anthropic_key", "openrouter_key", "openai_key", "replicate_token",
                          "huggingface_token", "telegram_bot_token", "agentjarvis_id", "mailgun_key"})
_VENDOR = [(k, p) for k, p, _ in privacy._SECRETS if k in VENDOR_KINDS]


@dataclass
class Verdict:
    ok: bool
    rule: str | None = None


def _forms(text: str) -> tuple[str, str]:
    """tg_guard's two spellings (NFKC, format characters removed; spaced / squeezed)."""
    return tg_guard.rule_forms(text or "")


def credential_shape(text: str) -> str | None:
    """The first credential kind found in `text`, or None."""
    spaced, squeezed = _forms(text)
    raw = unicodedata.normalize("NFKC", text or "")
    for t in (raw, spaced, squeezed):
        if PRIVATE_KEY.search(t):
            return "private_key"
    for t in (raw, spaced):
        _, hits = privacy.redact_with_report(t)
        kinds = sorted(k for k in hits if k in CRED_KINDS)
        if kinds:
            return kinds[0]
    for kind, pat in _VENDOR:
        if pat.search(squeezed):
            return kind
    return None


def inbound(text: str) -> Verdict:
    """§17.6 step 3. ok=False → do not give it to the model; tell the owner (the rule names the reason, not the content)."""
    hit = tg_guard.rule_hit(text or "", "proxy")
    if hit:
        return Verdict(False, f"asks_secret:{hit}")
    kind = credential_shape(text)
    if kind:
        return Verdict(False, f"credential:{kind}")
    return Verdict(True)


# ------------------------------------------------------------------ the host's own secrets

def _parse_env(body: str) -> list[tuple[str, str]]:
    out = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].lstrip()
        k, sep, v = s.partition("=")
        if not sep:
            continue
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        out.append((k, v))
    return out


def _tokenish(v: str) -> bool:
    """A value from an env file that is worth indexing although its name does not say "secret": long, no spaces, letters and
    digits, not a plain URL or path."""
    if len(v) < WINDOW or any(c.isspace() for c in v):
        return False
    if v.startswith(("/", "~/", "./")) or (re.match(r"(?i)https?://", v) and "@" not in v):
        return False
    return bool(re.search(r"[A-Za-z]", v) and re.search(r"[0-9]", v))


class SecretIndex:
    """Hashes of windows of this host's secret values. `hits(text)` → does any window of a secret appear in text?"""

    def __init__(self, values=()):
        self._salt = secrets.token_bytes(16)
        self._set: set[bytes] = set()
        self._lens: set[int] = set()
        self.count = 0
        for v in values:
            self.add(v)

    def _h(self, s: str) -> bytes:
        return hashlib.blake2b(s.encode("utf-8", "surrogatepass"), digest_size=8, key=self._salt).digest()

    def add(self, value: str) -> None:
        if not isinstance(value, str) or self.count >= MAX_VALUES:
            return
        v = "".join(value.split())
        if len(v) < MIN_SECRET:
            return
        self.count += 1
        if len(v) < WINDOW:
            self._lens.add(len(v))
            self._set.add(self._h(v))
            return
        self._lens.add(WINDOW)
        for i in range(len(v) - WINDOW + 1):
            self._set.add(self._h(v[i:i + WINDOW]))

    def hits(self, text: str) -> bool:
        if not self._set or not text:
            return False
        for t in {text, "".join(text.split()), unicodedata.normalize("NFKC", "".join(text.split()))}:
            for n in self._lens:
                for i in range(len(t) - n + 1):
                    if self._h(t[i:i + n]) in self._set:
                        return True
        return False

    def __len__(self) -> int:
        return self.count

    @classmethod
    def build(cls, environ=None, files=(), workdir: str | None = None, home: str | None = None,
              service_env: bool = True) -> "SecretIndex":
        """Default sources: this process's environment (names like KEY / TOKEN / SECRET / PASS / CREDENTIAL …), `~/.env`,
        the Agent working folder's `.env*`, the agentj service environment file, and `files` (the owner's extra list).
        From files: every value whose name looks secret, plus long token-like values under any name."""
        env = os.environ if environ is None else environ
        idx = cls()
        for k, v in env.items():
            if SECRET_NAME.search(k):
                idx.add(v)
        home = home or os.path.expanduser("~")
        paths = [os.path.join(home, ".env")]
        if workdir:
            paths += sorted(glob.glob(os.path.join(glob.escape(workdir), ".env*")))
        if service_env:
            try:
                from .provider_runtime import env_path
                paths.append(str(env_path()))
            except Exception:      # noqa: BLE001 — an optional source; the service name may not resolve in tests
                pass
        paths += [os.path.expanduser(p) for p in files or () if isinstance(p, str)]
        seen = set()
        for p in paths:
            try:
                rp = os.path.realpath(p)
                if rp in seen or not os.path.isfile(rp) or os.path.getsize(rp) > MAX_FILE:
                    continue
                seen.add(rp)
                with open(rp, encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
            except OSError:
                continue
            for k, v in _parse_env(body):
                if SECRET_NAME.search(k) or _tokenish(v):
                    idx.add(v)
        return idx


def _kw_norm(s: str) -> str:
    return tg_guard.rule_forms(s)[1].casefold()


def outbound(text: str, index: SecretIndex | None, never_tell_extra=()) -> Verdict:
    """§17.6 step 6. ok=False → not sent; a peer_question card tells the owner why (rule), never what matched."""
    if index is not None and index.hits(text or ""):
        return Verdict(False, "secret_fragment")
    kind = credential_shape(text)
    if kind:
        return Verdict(False, "private_key" if kind == "private_key" else f"credential:{kind}")
    squeezed = _kw_norm(text or "")
    for i, kw in enumerate(never_tell_extra or ()):
        if not isinstance(kw, str):
            continue
        k = _kw_norm(kw)
        if len(k) >= 2 and k in squeezed:
            return Verdict(False, f"never_tell:{i + 1}")
    return Verdict(True)


# ------------------------------------------------------------------ wrapping (§17.6 step 4)

def _escape_invisible(s: str) -> str:
    """json.dumps(ensure_ascii=False) escapes only C0 controls: also escape format characters (bidi overrides, zero-width)
    and the line / paragraph separators, so what the model reads is exactly what the JSON says."""
    def esc(c: str) -> str:
        n = ord(c)
        if n > 0xFFFF:                      # astral: a UTF-16 surrogate pair, as JSON requires
            n -= 0x10000
            return f"\\u{0xD800 + (n >> 10):04x}\\u{0xDC00 + (n & 0x3FF):04x}"
        return f"\\u{n:04x}"
    return "".join(esc(c) if unicodedata.category(c) in ("Cf", "Zl", "Zp", "Cc", "Cs", "Co") else c for c in s)


def wrap(friend_id: str, name: str, text: str) -> str:
    """The friend's message as untrusted data: one line of JSON {"from_friend":{"id","name"},"untrusted":true,"text"}."""
    body = json.dumps({"from_friend": {"id": str(friend_id), "name": str(name or "")}, "untrusted": True,
                       "text": str(text or "")}, ensure_ascii=False, separators=(",", ":"))
    return _escape_invisible(body)
