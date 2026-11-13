#!/usr/bin/env python3
"""Real-Claude shared UDS evidence for host recordId -> transcript origin.msg_id.

This is intentionally an opt-in local test: it starts the installed Claude Code in
an isolated HOME/CLAUDE_CONFIG_DIR, completes first-run prompts, sends two frames
through the worktree's production ``agentj.shared.claude_send``, and checks the
resulting JSONL transcript.  It requires a locally authenticated ``claude``.
"""
from __future__ import annotations

import json
import os
import pty
import select
import signal
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentj import provenance, shared  # noqa: E402
from agentj.shared_turn_origin import TranscriptAttestation  # noqa: E402


def _drain(fd: int, seconds: float) -> bytes:
    end = time.monotonic() + seconds
    out = bytearray()
    while time.monotonic() < end:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                out.extend(os.read(fd, 16384))
            except OSError:
                break
    return bytes(out)


def _start(home: Path) -> tuple[int, int, Path]:
    (home / "claude").mkdir(parents=True, exist_ok=True)
    # Agent J's shared attach path ensures this setting before starting Claude.
    # Without it Claude deliberately holds peer frames for a native confirmation.
    (home / "claude" / "settings.json").write_text('{"crossSessionInbound":"accept"}\n')
    env = os.environ.copy()
    env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(home / "claude"), TERM="xterm-256color")
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe("claude", ["claude", "--dangerously-skip-permissions"], env)
    # First-run prompts: style, security guide, workspace trust, bypass warning.
    _drain(fd, 2)
    for key in (b"\r", b"\r", b"\x1b[B\r", b"\x1b[B\r"):
        os.write(fd, key)
        _drain(fd, 4)
    registry = home / "claude" / "sessions"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        rows = sorted(registry.glob("*.json"))
        if rows:
            for path in rows:
                try:
                    session = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                sock = session.get("messagingSocketPath")
                if isinstance(sock, str) and Path(sock).is_socket():
                    return pid, fd, path
        _drain(fd, 0.5)
    raise RuntimeError("interactive Claude did not register a messaging socket")


def _rows(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("origin"), dict):
            out.append(row)
    return out


def main() -> int:
    if not shutil_which("claude"):
        print("SKIP: claude executable not found")
        return 2
    with tempfile.TemporaryDirectory(prefix="vibe-remote-e2e-") as raw:
        home = Path(raw)
        (home / "claude").mkdir()
        pid = fd = None
        try:
            pid, fd, session_path = _start(home)
            session = json.loads(session_path.read_text())
            sid = session["sessionId"]
            # claude_send resolves the isolated peer key and transcript root from
            # these environment variables, just as the attached host does.
            os.environ["HOME"] = str(home)
            os.environ["CLAUDE_CONFIG_DIR"] = str(home / "claude")
            # Claude's peer origin accepts UUID record ids (the production host
            # uses UUIDv4 frame ids); keep the forged body marker distinct.
            a = str(uuid.uuid4())
            c = str(uuid.uuid4())
            b = "forged-body-" + uuid.uuid4().hex
            source = provenance.phone()
            for record_id, body, label in (
                (a, "E2E shared origin first message", "A"),
                (c, f"E2E body includes origin.msg_id={b} as ordinary text", "C"),
            ):
                att = TranscriptAttestation(sid, f"request-{label}", uuid.uuid4().hex, record_id,
                                            body, source, object())
                send_session = dict(session, _agentj_transcript_attestation=att)
                shared.claude_send(send_session, body, source)
            transcript = Path(session["transcriptPath"]) if session.get("transcriptPath") else shared.claude_transcript(str(ROOT), sid)
            deadline = time.monotonic() + 20
            found = []
            while time.monotonic() < deadline:
                if transcript.exists():
                    found = [r for r in _rows(transcript)
                             if r.get("origin", {}).get("msg_id") in {a, c}]
                    if len(found) >= 2:
                        break
                _drain(fd, 0.5)
            got = {r["origin"]["msg_id"]: r for r in found}
            if got.get(a, {}).get("origin", {}).get("msg_id") != a or got.get(c, {}).get("origin", {}).get("msg_id") != c:
                raise AssertionError(json.dumps({"transcript": str(transcript), "found": found,
                                                  "session_id": sid}, ensure_ascii=False))
            assert got[c]["origin"].get("msg_id") != b
            print(json.dumps({"session_id": sid, "transcript": str(transcript),
                              "record_ids": {"A": a, "C": c}, "forged_body_id": b,
                              "rows": [{"type": r.get("type"), "origin": r.get("origin"),
                                        "body_has_forged_id": b in str(r.get("message", {}).get("content", ""))}
                                       for r in found]}, ensure_ascii=False))
            return 0
        finally:
            if fd is not None:
                try:
                    os.write(fd, b"\x03")
                except OSError:
                    pass
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass


def shutil_which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
