"""Local Claude shared hook transport. No key, tool input or reply is persisted.

PermissionRequest is the only allow-returning boundary: it answers a native
request through Host.ask. PreToolUse may only return ask, never allow.

P139 (0.18.3) — the phone always has a card:
- Another PermissionRequest hook no longer makes Agent J yield. Known observers
  (relay_hook.py taps) never answer; known/unknown answerers race the phone card
  — first answer wins, and when Claude drops our hook process the card is
  withdrawn (EOF on this channel → `gone`).
- Every hook event carries the served session's one-time token (A14), found
  through the hook's own process ancestry (`<state>/shared-hook/<pid>.token`).
  The host also checks, by SO_PEERCRED, that the sender descends from the served
  Claude process; a forged session_id from anywhere else is refused.
- Unavailable approval channel (host gone, socket error/timeout): the fallback
  never allows. Claude: a high-risk or hook-file PreToolUse returns ask (it is
  never silently handed back to native rules), and PermissionRequest keeps
  re-trying the host for RECONNECT_S while the native dialog is on screen, so a
  recovered host still puts the card on the phone; whatever stayed native is
  spooled (tool name only) and reported to the phone when the host is back.
  Codex treats PreToolUse ask as unsupported output and OpenCode cannot ask, so
  their unavailable path keeps native rules (no decision).
"""
from __future__ import annotations
import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import struct
import sys

EVENTS = ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PostToolUseFailure',
          'SubagentStart', 'SubagentStop', 'Stop', 'PreCompact', 'PostCompact', 'SessionEnd', 'PermissionRequest',
          'Notification')   # P116 (B5): the state machine's waiting signal (permission_prompt / idle_prompt)
MAX_FRAME = 2 * 1024 * 1024
# Host-generated channel names; the fallback derives the harness from the
# channel argument already baked into every installed hook command.
CHANNELS = {'claude': 'shared-claude.sock', 'codex': 'shared-codex.sock', 'opencode': 'shared-opencode.sock'}
UNAVAILABLE = 'Agent J phone not connected — confirm on this computer.'
TOKEN_DIR = 'shared-hook'        # <state>/shared-hook/<claude pid>.token (0600): the served session's hook token (A14)
SPOOL = 'shared-missed.jsonl'     # requests that stayed with the native dialog while the host was unreachable
RECONNECT_S = 150                 # PermissionRequest: keep trying a host that is restarting (< the 180 s hook timeout)
# P147: a new / resumed / cleared / compacted Claude Code session whose Agent J is not answering learns how to reconnect.
RECONNECT = ("[Agent J] The owner's phone link (Agent J) is not answering on this computer, so the phone/web page may show "
             "grey or disconnected. Run `agentj status --json`; if agent_status is not idle or working, run "
             "`agentj doctor --reconnect` here (it repairs the service, the relay and attaches the session that runs it) and "
             "tell the owner its one-line result. If it still fails, follow the agentj-manual support steps.")
BLOCKED = 'Agent J phone not connected; high-risk action blocked (this harness cannot hand it to a native prompt).'


def channel_path(root, family):
    return Path(root) / CHANNELS[family]


def family_of(channel):
    name = Path(str(channel)).name
    return next((family for family, known in CHANNELS.items() if known == name), None)


def settings(path, events=EVENTS):
    cmd = (f'{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve()))} '
           f'{shlex.quote(str(path))}')
    return {'hooks': {name: [{'hooks': [{'type': 'command', 'command': cmd + ' ' + shlex.quote(name),
                       'timeout': 180 if name in ('PermissionRequest', 'PreToolUse') else 15}]}] for name in events}}


def install(directory, path, family="claude"):
    """Merge only our hook entries; preserve every native permission and owner hook.

    Owner local settings must be valid and not symlinks. Shared hooks are durable
    so `claude --resume` loads them too; disconnected high-risk requests fall back
    to the native ask (Claude) or deny (Codex), see main().
    """
    dest = Path(directory) / ('.claude' if family == 'claude' else '.codex') / ('settings.local.json' if family == 'claude' else 'hooks.json')
    if dest.is_symlink() or dest.parent.is_symlink():
        raise ValueError('shared hook settings symlink refused')
    original = dest.read_bytes() if dest.exists() else None
    doc = json.loads(original) if original else {}
    if not isinstance(doc, dict) or not isinstance(doc.get('hooks', {}), dict):
        raise ValueError('invalid native Claude hooks')
    hooks = doc.setdefault('hooks', {})
    for event, entries in settings(path, EVENTS if family == 'claude' else ('UserPromptSubmit','PreToolUse'))['hooks'].items():
        old = hooks.get(event, [])
        if not isinstance(old, list):
            raise ValueError('invalid native Claude hook entries')
        # Replace only entries exclusively owned by this module, keep mixed ones.
        kept = []
        for entry in old:
            if not isinstance(entry, dict) or not isinstance(entry.get('hooks'), list):
                raise ValueError('invalid native Claude hook entry')
            copy = dict(entry)
            copy['hooks'] = [h for h in entry['hooks'] if not
                (isinstance(h, dict) and '/agentj/shared_hook.py ' in str(h.get('command', '')))]
            if copy['hooks']:
                kept.append(copy)
        hooks[event] = kept + entries
    data = (json.dumps(doc, ensure_ascii=False, indent=2) + '\n').encode()
    if data != original:
        dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + '.agentj-tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, dest)
    return dest


def _matches(matcher, tool):
    import re
    if not matcher or matcher == '*':
        return True
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return matcher == tool


def hook_role(command, tool):
    """P139: what another PermissionRequest hook command does for `tool`.
    None = ours or a known observer (never prints a decision): relay_hook.py taps. "answerer" = a known answerer for this
    tool (relay_hook.py ask → AskUserQuestion, approve → every other tool). "unknown" = anything else (may answer)."""
    command = str(command or '')
    if '/agentj/shared_hook.py ' in command:
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    at = next((i for i, w in enumerate(words) if Path(w).name == 'relay_hook.py'), None)
    if at is None:
        return 'unknown'
    mode = words[at + 1] if at + 1 < len(words) else ''
    if mode == 'ask':
        return 'answerer' if tool == 'AskUserQuestion' else None
    if mode == 'approve':
        return 'answerer' if tool != 'AskUserQuestion' else None
    if mode.startswith('-') or mode in ('', 'selftest'):
        return None
    return None if re.fullmatch(r'[A-Za-z]+', mode) else 'unknown'


def foreign_answerers(directory, tool):
    """Settings layers (user / project / local) holding another PermissionRequest hook that may answer `tool`.
    P139: diagnostic only — Agent J never yields to them any more (the phone card races them, first answer wins).
    Known observers (relay_hook.py taps) are not answerers. Read-only."""
    root = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    d = Path(directory) / '.claude'
    found = []
    for name, p in (('user', root / 'settings.json'), ('project', d / 'settings.json'), ('local', d / 'settings.local.json')):
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        entries = ((doc.get('hooks') or {}) if isinstance(doc, dict) else {}).get('PermissionRequest') or []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not _matches(entry.get('matcher'), tool):
                continue
            if any(isinstance(h, dict) and hook_role(h.get('command'), tool) for h in entry.get('hooks') or []):
                found.append(name)
                break
    return found


def missing(directory, path, family='claude'):
    """A5 (P139): events whose Agent J hook entry (pointing at this channel) is not in the settings file install() writes.
    [] = all in place. Read-only; an unreadable / invalid file counts as all missing."""
    dest = Path(directory) / ('.claude' if family == 'claude' else '.codex') / ('settings.local.json' if family == 'claude' else 'hooks.json')
    want = settings(path, EVENTS if family == 'claude' else ('UserPromptSubmit', 'PreToolUse'))['hooks']
    try:
        doc = json.loads(dest.read_text()) if not dest.is_symlink() else None
    except (OSError, ValueError):
        doc = None
    hooks = doc.get('hooks') if isinstance(doc, dict) else None
    out = []
    for event, entries in want.items():
        cmd = entries[0]['hooks'][0]['command']
        have = hooks.get(event) if isinstance(hooks, dict) else None
        if not any(isinstance(e, dict) and any(isinstance(h, dict) and h.get('command') == cmd for h in e.get('hooks') or [])
                   for e in (have if isinstance(have, list) else [])):
            out.append(event)
    return out


def token_path(root, pid):
    return Path(root) / TOKEN_DIR / f'{int(pid)}.token'


def write_token(root, pid, token):
    d = Path(root) / TOKEN_DIR
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    p = token_path(root, pid)
    tmp = p.with_name(p.name + '.tmp')
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(token)
    os.replace(tmp, p)
    return p


_LIBPROC = None


def _ppid_libproc(pid):
    """macOS: libproc proc_pidinfo(PROC_PIDTBSDINFO) — no fork. P291: `ps` per ancestry level forked inside serve's event
    loop and, on a loaded Mac, took 5–8 s (timeout 2 s) → the chain broke and the served session's own hooks were refused
    ("process"). Same-user processes only (a root-owned one answers None: the served Claude is never above it)."""
    global _LIBPROC
    if sys.platform != 'darwin':
        return None
    try:
        import ctypes
        if _LIBPROC is None:
            _LIBPROC = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        buf = ctypes.create_string_buffer(136)            # sizeof(struct proc_bsdinfo)
        n = _LIBPROC.proc_pidinfo(ctypes.c_int(pid), ctypes.c_int(3), ctypes.c_uint64(0), buf, ctypes.c_int(136))
        if n != 136:
            return None
        pp = struct.unpack_from('I', buf.raw, 16)[0]     # pbi_ppid
        return pp if pp > 0 else None
    except (OSError, AttributeError, ValueError, struct.error):
        return None


def _ppid(pid):
    try:
        with open(f'/proc/{pid}/stat') as f:
            return int(f.read().rsplit(')', 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        pass
    pp = _ppid_libproc(pid)
    if pp is not None:
        return pp
    try:
        import subprocess
        out = subprocess.run(['ps', '-o', 'ppid=', '-p', str(pid)], capture_output=True, text=True, timeout=2).stdout
        return int(out.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def ancestors(pid, depth=32, until=None):
    """pid, its parent, … (bounded; stops once `until` is reached). Linux /proc; macOS libproc; elsewhere `ps`."""
    out = []
    while isinstance(pid, int) and pid > 1 and len(out) < depth:
        out.append(pid)
        if pid == until:
            break
        pid = _ppid(pid)
    return out


def find_token(root):
    """The hook's own ancestry decides which served session's token it may present (A14)."""
    for pid in ancestors(os.getppid()):
        try:
            return token_path(root, pid).read_text().strip()
        except OSError:
            continue
    return None


def peer_pid(sock):
    with contextlib.suppress(OSError, AttributeError, struct.error):
        if hasattr(socket, 'SO_PEERCRED'):
            pid, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            return pid, uid
    with contextlib.suppress(OSError, AttributeError, struct.error):
        return struct.unpack('i', sock.getsockopt(0, 2, 4))[0], None     # macOS SOL_LOCAL / LOCAL_PEERPID
    return None, None


class Channel:
    def __init__(self, adapter, path):
        self.adapter, self.path = adapter, Path(path)
        self.server = None
        self.clients = set()

    async def start(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Never unlink a live listener from a second host.
        if self.path.exists():
            with socket.socket(socket.AF_UNIX) as s:
                try:
                    s.connect(str(self.path))
                except (ConnectionRefusedError, FileNotFoundError):
                    self.path.unlink()
                else:
                    raise ValueError('shared Claude channel already owned')
        self.server = await asyncio.start_unix_server(self.receive, str(self.path), limit=MAX_FRAME)
        self.path.chmod(0o600)

    async def receive(self, reader, writer):
        self.clients.add(writer)
        task = None
        try:
            sock = writer.get_extra_info('socket')
            pid, uid = peer_pid(sock)
            if uid is not None and uid != os.getuid():
                return
            line = await asyncio.wait_for(reader.readline(), 5)
            if len(line) > MAX_FRAME:
                return
            event = json.loads(line)
            if not isinstance(event, dict):
                return
            for k in [k for k in event if str(k).startswith('_agentj')]:
                event.pop(k)                      # host-only keys are never taken from the wire
            event['_agentj_peer'] = pid
            event['_agentj_token'] = event.pop('agentj_token', None)
            if isinstance(pid, int):                # P291: the ancestry walk never runs on the event loop
                served = getattr(getattr(self.adapter, 'session', None), 'get', lambda k: None)('pid')
                event['_agentj_chain'] = await asyncio.to_thread(ancestors, pid, 32, served)
            check = getattr(self.adapter, 'authentic', None)
            if check and not check(event):
                writer.write(b'{}\n')
                await writer.drain()
                return
            for k in ('_agentj_peer', '_agentj_token', '_agentj_chain'):
                event.pop(k, None)
            # P139: the hook process going away (Claude took another answer / the dialog) withdraws the phone card
            gone = asyncio.get_running_loop().create_future()
            event['_agentj_gone'] = gone
            task = asyncio.ensure_future(self.adapter.hook_event(event))
            eof = asyncio.ensure_future(reader.read(1))
            try:
                done, _ = await asyncio.wait({task, eof}, return_when=asyncio.FIRST_COMPLETED)
                if task not in done:
                    if not gone.done():
                        gone.set_result(True)
                    answer = await task
                    return                         # nobody left to read it
            finally:
                eof.cancel()
            answer = task.result()
            writer.write((json.dumps(answer) + '\n').encode())
            await writer.drain()
        except (ValueError, OSError, ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            self.clients.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def stop(self):
        for writer in tuple(self.clients):
            writer.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.path.unlink(missing_ok=True)


def _risk(event):
    """Local classification for the unavailable path: (why, verdict) when the call is high-risk or rewrites hooks."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agentj import danger
    v = danger.classify_shared(event.get('tool_name'), event.get('tool_input'))
    return v.why if v.cats else None


def fallback(name, family, event, valid_execution):
    """Decision when the approval channel is unavailable. Never returns allow.

    None means "no decision": native rules and the native dialog decide. P139 (A5): Claude's high-risk / hook-file
    PreToolUse is never silently handed back to native rules (which may auto-allow it) — it returns ask, so a person
    confirms it at this computer. Codex/OpenCode cannot ask from PreToolUse: native rules, as before. P147: SessionStart of a Claude session only
    adds context (how to reconnect) — never a decision.
    """
    if name == 'SessionStart' and family == 'claude' and not event.get('agent_id'):
        return {'additionalContext': RECONNECT}
    if family == 'claude' and name == 'PreToolUse' and valid_execution:
        try:
            why = _risk(event)
        except Exception:
            why = 'Agent J could not check this call'
        if why:
            return {'permissionDecision': 'ask', 'permissionDecisionReason': 'Agent J offline: ' + str(why)[:200]}
    # F14: no Agent J decision. Native hooks/rules and dialogs remain authoritative.
    # Crucially this is neither allow nor an amendment to the session's permissions.
    return None


def _spool(channel, name, event):
    """Tool name + time only: what stayed with the native dialog while the host was unreachable (reported on recovery)."""
    with contextlib.suppress(Exception):
        p = Path(channel).parent / SPOOL
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a') as f:
            import time
            f.write(json.dumps({'at': int(time.time()), 'event': name,
                                'tool': str(event.get('tool_name') or '?')[:64]}) + '\n')


def _exchange(channel, event, timeout):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout)
        s.connect(channel)
        s.sendall(json.dumps(event).encode() + b'\n')
        with s.makefile('rb') as f:
            line = f.readline(MAX_FRAME)
    if not line:
        raise ConnectionError('host closed')
    return json.loads(line)


def main():
    event = {}
    valid_execution = False
    try:
        line = sys.stdin.buffer.read(MAX_FRAME + 1)
        if len(line) > MAX_FRAME:
            raise ValueError('oversized event')
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError('invalid hook event')
        if len(sys.argv) > 2 and event.get('hook_event_name') != sys.argv[2]:
            raise ValueError('hook event mismatch')
        if event.get('hook_event_name') in ('PreToolUse','PermissionRequest'):
            if not isinstance(event.get('tool_name'),str) or not isinstance(event.get('tool_input'),dict):
                raise ValueError('invalid execution payload')
            valid_execution = True
        token = find_token(Path(sys.argv[1]).parent)
        wire = dict(event, agentj_token=token) if token else event
        blocking = event.get('hook_event_name') in ('PermissionRequest', 'PreToolUse')
        if event.get('hook_event_name') == 'PermissionRequest' and family_of(sys.argv[1]) == 'claude':
            # P139: a restarting host (update, crash) still gets this request onto the phone; the native dialog is on
            # screen meanwhile, so the person at the computer is never blocked by the wait.
            import time
            wait = float(os.environ.get('AGENTJ_HOOK_RECONNECT_S', RECONNECT_S))
            deadline = time.monotonic() + wait
            while True:
                try:
                    answer = _exchange(sys.argv[1], wire, 170)
                    break
                except (OSError, ValueError):
                    if time.monotonic() + 1 > deadline:
                        _spool(sys.argv[1], 'PermissionRequest', event)
                        raise
                    time.sleep(1)
        else:
            answer = _exchange(sys.argv[1], wire, 170 if blocking else 10)
        if answer:
            print(json.dumps(answer))
        return 0
    except Exception:
        # Host gone / socket error / timeout / bad event. Never return an allow decision (see fallback()).
        expected = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] in EVENTS else None
        name = expected or (event.get('hook_event_name') if isinstance(event, dict) else None) or 'PreToolUse'
        family = family_of(sys.argv[1]) if len(sys.argv) > 1 else None
        result = fallback(name, family, event if isinstance(event, dict) else {}, valid_execution)
        if result is not None:
            print(json.dumps({'hookSpecificOutput': {'hookEventName': name, **result}}))  # ASCII-safe under any locale
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
