"""P406 #173 (PROTOCOL §11.2): a running sudo card leaves the phone's screen as soon as the command really started.

`Live` is the bridge between `collect_command` (a worker thread) and the event loop for ONE approved sudo card:
- **started**: sudo forked the command (a child of the sudo process exists across two looks, or the command printed on
  stdout) and did not ask a second time. sudo only forks after it accepted the password, so this is the moment the card can
  close. It is a hint for the page only: a later second prompt still ends as `bad_password` → the card is shown again.
- **output**: what the command prints after it started, decoded and masked with the web terminal's streaming masker
  (`term_mask.Masker`: escapes dropped, key-like runs held / hidden, PEM blocks hidden, `privacy.owner_text`) plus, for this
  stream only, `key=value` / `key: value` lines whose name looks like a credential (psk, passphrase, secret, key, token,
  pass*, pwd, auth*, credential*) masked to the end of the line, and NO idle flush (a held run leaves only with a delimiter
  or at the end — a key prefix never shows). One masker per stream. Everything on stderr before the start is dropped (sudo's
  prompt markers, its lecture — only counted); a marker cut across two reads is removed too. Only the masked text is kept
  (a tail of TAIL chars); it reaches a phone only when that phone asked for it with a signed `elev_watch` (elevate.py).
The password never passes through here: it goes to sudo's stdin inside collect_command and is wiped there.
"""
from __future__ import annotations

import codecs
import json
import os
import re
import subprocess
import threading
import time

from .term_mask import Masker

_KV = re.compile(r"(?i)(\b[\w.-]*(?:psk|passphrase|secret|key|token|pass|pwd|auth|credential)[\w.-]*\s*[=:]\s*)(\S[^\n]*)")


def mask(text: str) -> str:
    """privacy.owner_text, then a credential-looking `name=value` / `name: value` keeps only its name (case-insensitive)."""
    from .privacy import owner_text
    return _KV.sub(lambda m: m.group(1) + "<redacted>", owner_text(text))

TAIL = 4000            # masked chars kept (replay on ready, the result's tail)
SEND_MAX = 12000       # bytes of JSON-escaped text in one elev_out (a 16 KiB legacy frame keeps room for the rest)
DONE_TAIL = 2000       # chars of the masked tail in the result message
SCAN_EVERY = .2        # s between looks for sudo's child


def has_child(pid: int) -> bool:
    """Does `pid` have a child process? Linux: /proc children (world-readable even for root's sudo), else a /proc scan;
    other systems: `pgrep -P`. Any error → False (the page then waits for stdout or the result)."""
    try:
        with open(f"/proc/{pid}/task/{pid}/children", "rb") as f:
            return bool(f.read(64).strip())
    except FileNotFoundError:
        pass
    except OSError:
        return False
    if os.path.isdir("/proc/self"):
        try:
            names = [d for d in os.listdir("/proc") if d.isdigit()]
        except OSError:
            return False
        for d in names:
            try:
                with open(f"/proc/{d}/stat", "rb") as f:
                    stat = f.read(512)
                if int(stat[stat.rindex(b")") + 2:].split()[1]) == pid:
                    return True
            except (OSError, ValueError, IndexError):
                continue
        return False
    try:
        return subprocess.run(["pgrep", "-P", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=2).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def fit(text: str, limit: int = SEND_MAX) -> str:
    """The newest part of `text` whose JSON form is ≤ limit bytes (cut at a line start when one is near)."""
    while text and len(json.dumps(text)) > limit:
        text = text[max(1, len(text) // 4):]
        i = text.find("\n")
        if 0 <= i < 200:
            text = text[i + 1:]
    return text


class Live:
    """One approved sudo card while it runs; `data` / `tick` are called from collect_command's thread."""

    def __init__(self, wake=None, started: bool = False):
        self.lock = threading.Lock()
        self.wake = wake or (lambda: None)   # loop.call_soon_threadsafe(event.set), set by the Elevator
        self.gate = b""                     # sudo's prompt marker (run_sudo sets it)
        self.started, self.bad, self.prompts = started, False, 0
        self.held = b""                     # stderr bytes that may be the start of a marker (released with the next read)
        self.dec = [codecs.getincrementaldecoder("utf-8")("replace") for _ in range(2)]
        self.mask = [Masker(mask), Masker(mask)]
        self.new, self.tail = "", ""
        self.seen, self.next_look = False, 0.0

    # ---------------------------------------------------------------- worker thread
    def data(self, index: int, chunk: bytes) -> None:
        with self.lock:
            if index == 1 and self.gate:
                chunk = self._unmark(chunk)
                if self.prompts >= 2 and not self.started:
                    self.bad = True             # sudo asked again: the password was wrong
            if not self.started:
                if index == 0 and not self.bad:
                    self._start()
                else:
                    return                      # stderr before the start: prompts / lecture, never shown
            self._emit(index, chunk)

    def _unmark(self, chunk: bytes) -> bytes:
        """Count sudo's prompt markers on stderr and cut every one out — also one split across reads: an end of the read
        that could be the start of a marker is held until the next read (or the end) decides."""
        g = self.gate
        buf = self.held + chunk
        while g in buf:
            self.prompts += buf.count(g)
            buf = buf.replace(g, b"")
        hold = next((n for n in range(min(len(g) - 1, len(buf)), 0, -1) if buf.endswith(g[:n])), 0)
        self.held = buf[len(buf) - hold:] if hold else b""
        return buf[:len(buf) - hold]

    def tick(self, pid: int) -> None:
        now = time.monotonic()
        with self.lock:
            if self.started or self.bad or now < self.next_look:
                return                          # no idle flush: a held run could be a key's first characters
            self.next_look = now + SCAN_EVERY
        child = has_child(pid)
        with self.lock:
            if self.started or self.bad:
                return
            if child and self.seen and self.prompts <= 1:
                self._start()
            self.seen = child

    def _start(self) -> None:
        self.started = True
        self.wake()

    def _emit(self, index: int, chunk: bytes) -> None:
        self._out(self.mask[index].feed(self.dec[index].decode(chunk)))

    def _out(self, text: str) -> None:
        if text:
            self.new += text
            self.tail = (self.tail + text)[-TAIL:]
            self.wake()

    # ---------------------------------------------------------------- event loop
    def take(self) -> tuple[str, str]:
        """→ (the masked text since the last take, the masked tail now) — one snapshot, so a new watcher's tail and the
        next take neither overlap nor leave a gap."""
        with self.lock:
            text, self.new = self.new, ""
            return fit(text), fit(self.tail)

    def close(self) -> None:
        with self.lock:
            if self.started:
                held, self.held = self.held, b""
                if held:
                    self._emit(1, held)
                for i in (0, 1):
                    self._out(self.mask[i].feed(self.dec[i].decode(b"", True)) + self.mask[i].close())
