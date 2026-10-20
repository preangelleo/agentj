"""P408 (ADR-P408-terminal H5, PROTOCOL §38): the web terminal's streaming output masker — a pure class, no I/O.

Text arrives in arbitrary chunks (a pipe read can cut a token, an escape sequence or a PEM header anywhere). `feed(text)`
returns only what is safe to show now:
  - ANSI CSI / OSC sequences and C0 control bytes (except \\n and \\t) are dropped; an escape cut at the chunk end is held.
  - a trailing run of token-like characters ([A-Za-z0-9_+/=.~-]) is HELD until a delimiter arrives — a key split across two
    reads is masked as one; `flush()` (the caller's idle timer) releases a held run only when it is too short to be a key
    (< FLUSH_MAX: at worst a vendor prefix like `ghp_`, the rest is then masked through the carried context); `close()`
    releases everything, a run ≥ HOLD_MIN masked whole.
  - `-----BEGIN … PRIVATE KEY-----` hides everything until its END line (one 「[私钥已隐藏 …]」 line; an END that never comes
    keeps hiding until close).
  - what is released goes through `privacy.owner_text` together with up to CARRY (4 KiB) of what was released before, so a
    rule that needs the left context (`Authorization: Bearer ` then the token in the next read) still fires; only the part
    after the context is emitted.
"""
from __future__ import annotations

import re

CARRY = 4096          # chars of released text kept as left context for owner_text
HOLD_MIN = 16         # a held run this long may be a key: masked whole at close
FLUSH_MAX = 8         # an idle flush releases a held run only when it is shorter (a counter, `y/N`): at most a key's prefix
HOLD_MAX = 8192       # a run longer than this (no delimiter at all) is released masked
PEM_NOTE = "[私钥已隐藏 / private key hidden]\n"
LONG_NOTE = "[已隐藏 / hidden]"
_ESC = re.compile(r"\x1b\[[0-9;?<>=!]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]|[\x00-\x08\x0b-\x1f\x7f]")
_ESC_PREFIX = re.compile(r"\x1b(?:\[[0-9;?<>=!]*[ -/]*|\][^\x07\x1b]*\x1b?)?")
_TOKEN_TAIL = re.compile(r"[A-Za-z0-9_+/=.~-]+\Z")
_TOKEN_HEAD = re.compile(r"[A-Za-z0-9_+/=.~-]*")
_PEM_BEGIN = re.compile(r"-----BEGIN[A-Z0-9 ]*PRIVATE KEY-----")
_PEM_END = re.compile(r"-----END[A-Z0-9 ]*PRIVATE KEY-----[^\n]*\n?")
_PEM_PART = re.compile(r"-----BEGIN[A-Z0-9 ]*-{0,4}\Z")


def _owner(text: str) -> str:
    from .privacy import owner_text
    return owner_text(text)


class Masker:
    def __init__(self, mask=None):
        self.mask = mask or _owner
        self.esc = ""        # an escape sequence cut at the end of the last chunk
        self.held = ""       # stripped text not released yet (a token run / a partial PEM header)
        self.ctx = ""        # released raw text (left context, ≤ CARRY)
        self.pem = False     # inside a private key block
        self.run = ""        # R3 (M-2): a token run an idle flush already released — still open until a delimiter

    def _release(self, raw: str) -> str:
        if not raw:
            return ""
        before = self.mask(self.ctx) if self.ctx else ""
        both = self.mask(self.ctx + raw)
        if both.startswith(before):
            out = both[len(before):]
        else:                                  # a rule spans the boundary: emit from where the masked texts part
            i = 0
            while i < min(len(before), len(both)) and before[i] == both[i]:
                i += 1
            out = both[i:]
        self.ctx = (self.ctx + raw)[-CARRY:]
        return out

    def _strip(self, text: str) -> str:
        text = self.esc + text
        self.esc = ""
        i = text.rfind("\x1b")
        if i >= 0 and len(text) - i < 4096 and _ESC_PREFIX.fullmatch(text, i):
            text, self.esc = text[:i], text[i:]          # an escape cut by the read: wait for its end
        return _ESC.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))

    def _scan(self, text: str, final: bool) -> str:
        out = []
        while text:
            if self.pem:
                m = _PEM_END.search(text)
                if not m:
                    self.held = "" if final else text[-80:]      # keep enough to see an END split across reads
                    return "".join(out)
                text, self.pem = text[m.end():], False
                continue
            m = _PEM_BEGIN.search(text)
            if m:
                out.append(self._release(text[:m.start()]))
                out.append(PEM_NOTE)
                self.ctx = ""
                self.pem, text = True, text[m.end():]
                continue
            hold = ""
            if not final:
                p = _PEM_PART.search(text[-64:])
                t = _TOKEN_TAIL.search(text)
                starts = [x for x in (len(text) - len(text[-64:]) + p.start() if p else None, t.start() if t else None)
                          if x is not None]
                if starts:
                    cut = min(starts)
                    hold, text = text[cut:], text[:cut]
                if len(hold) > HOLD_MAX:
                    out.append(self._release(text) + LONG_NOTE)
                    self.ctx = ""
                    return "".join(out)
            out.append(self._release(text))
            self.held = hold
            return "".join(out)
        self.held = "" if final else self.held
        return "".join(out)

    def _join(self, text: str, final: bool) -> str:
        """R3 (M-2): continue a run an idle flush released. Its further token characters are held until a delimiter (or the
        close); the whole run ≥ HOLD_MIN → LONG_NOTE instead of the rest (at most a FLUSH_MAX prefix was ever shown)."""
        lead = _TOKEN_HEAD.match(text).group()
        if len(lead) == len(text) and not final and len(self.run) + len(lead) <= HOLD_MAX:
            self.held = text
            return ""
        joined, self.run = self.run + lead, ""
        if len(joined) >= HOLD_MIN:
            self.ctx = ""
            first = LONG_NOTE
        else:
            first = self._release(lead)
        return first + self._scan(text[len(lead):], final)

    def feed(self, text: str) -> str:
        """→ the part that may be shown now (may be "")."""
        held, self.held = self.held, ""
        text = held + self._strip(text)
        if self.run and not self.pem:
            return self._join(text, False)
        return self._scan(text, False)

    def flush(self) -> str:
        """Idle: release a held run only when it is too short to be a key (a prompt, `50%`, `y/N`)."""
        if self.pem or self.run or not self.held or len(self.held) >= FLUSH_MAX or "-----" in self.held:
            return ""
        held, self.held = self.held, ""
        t = _TOKEN_TAIL.search(held)
        self.run = t.group() if t else ""
        return self._release(held)

    def close(self) -> str:
        """The session ends: everything left, a long token run masked whole."""
        held, self.held, self.esc = self.held, "", ""
        if self.pem:
            return ""
        if self.run:
            return self._join(held, True)
        t = _TOKEN_TAIL.search(held)
        if t and len(t.group()) >= HOLD_MIN:
            return self._scan(held[:t.start()], True) + LONG_NOTE
        return self._scan(held, True)
