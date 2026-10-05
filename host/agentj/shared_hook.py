"""Local Claude shared hook transport. No key, tool input or reply is persisted.

PermissionRequest is the only allow-returning boundary: it answers a native
request through Host.ask. PreToolUse may only return ask, never allow.

Unavailable approval channel (host gone, socket error/timeout, ADR-A146): the
fallback never allows. Claude high-risk/unclassifiable PreToolUse returns ask
(the native dialog reaches the person at this computer) and PermissionRequest
returns nothing (native dialog). Codex 0.159 treats PreToolUse ask as an
unsupported output and runs the tool, and its native policies auto-run some
high-risk commands, so Codex keeps deny; OpenCode's tool.execute.before cannot
ask, so it keeps deny too. Unknown channels fail closed (deny).
"""
from __future__ import annotations
import asyncio
import contextlib
import json
import os
from pathlib import Path
import shlex
import socket
import struct
import sys

EVENTS = ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PostToolUseFailure',
          'SubagentStart', 'SubagentStop', 'Stop', 'PreCompact', 'PostCompact', 'SessionEnd', 'PermissionRequest')
MAX_FRAME = 2 * 1024 * 1024
# Host-generated channel names; the fallback derives the harness from the
# channel argument already baked into every installed hook command.
CHANNELS = {'claude': 'shared-claude.sock', 'codex': 'shared-codex.sock', 'opencode': 'shared-opencode.sock'}
UNAVAILABLE = 'Agent J phone not connected — confirm on this computer.'
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
        try:
            sock = writer.get_extra_info('socket')
            if hasattr(socket, 'SO_PEERCRED'):
                _, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    return
            line = await asyncio.wait_for(reader.readline(), 5)
            if len(line) > MAX_FRAME:
                return
            event = json.loads(line)
            answer = await self.adapter.hook_event(event)
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


def fallback(name, family, event, valid_execution):
    """Decision when the approval channel is unavailable. Never returns allow.

    None means "no decision": native rules and the native dialog decide.
    """
    if name not in ('PreToolUse', 'PermissionRequest'):
        return None  # telemetry never blocks ordinary desktop use or exit
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from agentj.danger import classify_shared
        if valid_execution and event.get('hook_event_name') == name and \
                not classify_shared(event['tool_name'], event['tool_input']).danger:
            return None  # routine work keeps native authority
    except Exception:
        pass  # unclassifiable: treated as high risk below
    if family == 'claude':
        if name == 'PermissionRequest':
            return None  # Claude shows its own dialog to the person at the desktop
        if isinstance(event, dict) and event.get('hook_event_name') == name and not valid_execution:
            # Parsed, but the call itself is malformed (no string tool_name /
            # object tool_input): nothing well-formed to show a person.
            return {'permissionDecision': 'deny', 'permissionDecisionReason': BLOCKED}
        # ask forces the native dialog even over an owner allow rule.
        return {'permissionDecision': 'ask', 'permissionDecisionReason': UNAVAILABLE}
    if name == 'PermissionRequest':
        return {'decision': {'behavior': 'deny', 'message': BLOCKED}}
    return {'permissionDecision': 'deny', 'permissionDecisionReason': BLOCKED}


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
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(170 if event.get('hook_event_name') in ('PermissionRequest', 'PreToolUse') else 10)
            s.connect(sys.argv[1])
            s.sendall(json.dumps(event).encode() + b'\n')
            with s.makefile('rb') as f:
                answer = json.loads(f.readline(MAX_FRAME))
        if answer:
            print(json.dumps(answer))
        return 0
    except Exception:
        # Host gone / socket error / timeout / bad event (ADR-A146). A detached
        # phone must not disable desktop work: routine operations keep native
        # rules; high-risk ones go to the native ask where that provably reaches
        # a person (Claude), otherwise deny. Never return an allow decision.
        expected = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] in EVENTS else None
        name = expected or (event.get('hook_event_name') if isinstance(event, dict) else None) or 'PreToolUse'
        family = family_of(sys.argv[1]) if len(sys.argv) > 1 else None
        result = fallback(name, family, event if isinstance(event, dict) else {}, valid_execution)
        if result is not None:
            print(json.dumps({'hookSpecificOutput': {'hookEventName': name, **result}}))  # ASCII-safe under any locale
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
