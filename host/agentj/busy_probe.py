"""Local, bounded process evidence for the display-only busy board. Never return argv/env/auth bodies."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path
import re
import tomllib

# Only model identifiers, never arbitrary command fragments, URLs, addresses or credentials.
def identifier(value):
    from .privacy import redact
    if not isinstance(value, str) or len(value) > 100 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:+-]*', value):
        return None
    if '@' in value or '://' in value or redact(value) != value or re.search(r'(?:sk-|token|secret|password)', value, re.I):
        return None
    return value


def document(path, toml=False):
    try:
        p = Path(path)
        if p.stat().st_size > 256 * 1024:
            return {}
        v = tomllib.loads(p.read_text()) if toml else json.loads(p.read_text())
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def start_id(pid):
    try:
        return (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        if not Path('/proc').exists():
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                r = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True, text=True, timeout=2)
                return ' '.join(r.stdout.split()) or None
        return None


class Processes:
    """One sampling pass. Raw process data is held locally and is never serialized."""
    def __init__(self, root=Path('/proc')):
        self.root = Path(root)
        self.rows = {}
        self.cache = {}
        with contextlib.suppress(OSError):
            for d in self.root.iterdir():
                if not d.name.isdigit():
                    continue
                try:
                    s = (d / 'stat').read_text().rsplit(')', 1)[1].split()
                    if s[0] in ('Z', 'X'):
                        continue
                    self.rows[int(d.name)] = {'ppid': int(s[1]), 'start': s[19]}
                except (OSError, ValueError, IndexError):
                    continue

        if not self.root.exists():
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                raw = subprocess.run(['ps', '-axo', 'pid=,ppid=,lstart=,comm='], capture_output=True, text=True, timeout=2)
                for line in raw.stdout.splitlines():
                    parts = line.split(None, 7)
                    if len(parts) == 8 and parts[0].isdigit():
                        self.rows[int(parts[0])] = {'ppid': int(parts[1]), 'start': ' '.join(parts[2:7]), 'exe': parts[7]}

    def namespace_pid(self, local_pid, ns):
        if not ns:
            return None
        for pid in self.rows:
            d = self.root / str(pid)
            try:
                if os.readlink(d / 'ns/pid') != ns:
                    continue
                line = next(l for l in (d / 'status').read_text().splitlines() if l.startswith('NSpid:'))
                if int(line.split()[-1]) == local_pid:
                    return pid
            except (OSError, ValueError, StopIteration):
                continue
        return None

    def identity(self, pid):
        row = self.rows.get(pid)
        return row['start'] if row else None

    def descendants(self, pid):
        found = {pid} if pid in self.rows else set()
        for _ in range(32):
            more = {p for p, r in self.rows.items() if r['ppid'] in found} - found
            if not more:
                break
            found |= more
        return sorted(found)[:128]

    def inspect(self, pid):
        if pid not in self.cache:
            self.cache[pid] = self._inspect(pid)
        return self.cache[pid]

    def _inspect(self, pid):
        d = self.root / str(pid)
        try:
            argv = (d / 'cmdline').read_bytes()[:65536].decode('utf-8', 'replace').split('\0')
            env = dict(x.split('=', 1) for x in (d / 'environ').read_bytes()[:262144].decode('utf-8', 'replace').split('\0') if '=' in x)
            exe = Path(os.readlink(d / 'exe')).name
        except OSError:
            row = self.rows.get(pid, {})
            if not row.get('exe'):
                return {}
            try:
                raw = subprocess.run(['ps', '-p', str(pid), '-o', 'args='], capture_output=True, text=True, timeout=2)
                argv, env, exe = shlex.split(raw.stdout), {}, Path(row['exe']).name
            except (OSError, ValueError, subprocess.SubprocessError):
                return {}
        # Only executable positions. A prompt containing "codex" is not a harness.
        names = [exe] + ([Path(argv[1]).name] if exe in ('node', 'bun') and len(argv) > 1 else [])
        h = next((h for h in ('claude', 'codex', 'opencode') if any(n == h or n in (h + '.js', h + '.mjs') for n in names)), None)
        if not h and exe in ('node', 'bun') and len(argv) > 1:
            script = argv[1].replace('\\', '/')
            if '/@anthropic-ai/claude-code/' in script and Path(script).name == 'cli.js':
                h = 'claude'
        if not h:
            return {}
        home = Path(env.get('HOME') or str(Path.home()))
        confhome = Path(env.get('CODEX_HOME') or home / '.codex') if h == 'codex' else Path(env.get('CLAUDE_CONFIG_DIR') or home / '.claude')
        cfg = document(confhome / 'config.toml', True) if h == 'codex' else document(confhome / 'settings.json') if h == 'claude' else document(Path(env.get('XDG_CONFIG_HOME') or home / '.config') / 'opencode' / 'opencode.json')
        def flag(*names):
            for i, a in reversed(list(enumerate(argv))):
                for n in names:
                    if a.startswith(n + '='):
                        return a[len(n) + 1:]
                    if a == n and i + 1 < len(argv):
                        return argv[i + 1]
            return None
        profile = flag('-p', '--profile') if h == 'codex' else None
        if not env:  # no verified environment: custom config homes cannot be inferred
            cfg = {}
        if profile:
            profiles = cfg.get('profiles') or {}
            selected = profiles.get(profile) if isinstance(profiles, dict) else None
            if isinstance(selected, dict):
                cfg = {**cfg, **selected}
        if h == 'opencode' and env:
            from .harness import _jsonc
            config_dir = Path(env.get('XDG_CONFIG_HOME') or home / '.config') / 'opencode'
            for name in ('config.json', 'opencode.jsonc', 'opencode.json'):
                try:
                    p = config_dir / name
                    if p.stat().st_size <= 256 * 1024:
                        parsed = _jsonc(p.read_text())
                        if isinstance(parsed, dict):
                            cfg.update(parsed)
                except (OSError, ValueError):
                    pass
            try:
                inline = _jsonc(env.get('OPENCODE_CONFIG_CONTENT') or '{}')
                if isinstance(inline, dict):
                    cfg.update(inline)
            except ValueError:
                pass
        if h == 'claude' and flag('--settings'):
            override = flag('--settings')
            try:
                extra = json.loads(override) if override.startswith('{') else document(override)
                if isinstance(extra, dict):
                    cfg.update(extra)
            except ValueError:
                pass
        model = flag('-m', '--model') or (env.get('ANTHROPIC_MODEL') if h == 'claude' else None) or cfg.get('model')
        effort = cfg.get('model_reasoning_effort') if h == 'codex' else env.get('CLAUDE_CODE_EFFORT_LEVEL') if h == 'claude' else None
        if h == 'codex':
            for i, arg in enumerate(argv):
                override = argv[i + 1] if arg in ('-c', '--config') and i + 1 < len(argv) else arg.split('=', 1)[1] if arg.startswith('--config=') else ''
                key, _, value = override.partition('=')
                if key.strip() == 'model':
                    model = value.strip().strip('"\'')
                elif key.strip() == 'model_reasoning_effort':
                    effort = value.strip().strip('"\'')
        model = flag('-m', '--model') or model
        if h == 'claude':
            effort = flag('--effort') or effort
        # Read only declared auth metadata. Never decode a token or export an address.
        account, auth = None, None
        if h == 'codex' and env:
            metadata = document(confhome / 'auth.json')
            mode = metadata.get('auth_mode')
            auth = mode if mode in ('chatgpt', 'apikey') else None
            tokens = metadata.get('tokens') if isinstance(metadata.get('tokens'), dict) else {}
            aid = tokens.get('account_id')
            if isinstance(aid, str) and aid:
                account = 'acct:' + hashlib.sha256(aid.encode()).hexdigest()[:8]
        elif h == 'claude' and env:
            metadata = document(confhome / '.credentials.json')
            oauth = metadata.get('claudeAiOauth') if isinstance(metadata.get('claudeAiOauth'), dict) else {}
            subscription = oauth.get('subscriptionType')
            auth = subscription if subscription in ('max', 'pro', 'team', 'enterprise') else None
        if any(env.get(k) for k in ({'claude': ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'), 'codex': ('OPENAI_API_KEY', 'CODEX_API_KEY'), 'opencode': ('OPENCODE_API_KEY',)}[h])):
            account = None
            auth = 'api' if auth in (None, 'apikey') else None
        return {'harness': h, 'model': identifier(model), 'effort': identifier(effort), 'account': account,
                'subscription': auth, 'pid': pid, 'start': self.identity(pid), 'model_source': 'argv/config' if identifier(model) else None}

    def probe(self, pids):
        candidates = []
        for p in pids:
            info = self.inspect(p)
            if info:
                candidates.append(info)
        # Multiple independent harnesses under one wrapper cannot honestly be called one actual model.
        # A JS launcher and its native binary are one harness; select the deepest harness descendant.
        candidates = [c for c in candidates if not any(c['pid'] != other['pid'] and other['pid'] in self.descendants(c['pid']) for other in candidates)]
        if len(candidates) == 1:
            return candidates[0]
        return {'harness': None, 'model': None, 'effort': None, 'account': None, 'subscription': None}


def fields(it, processes):
    """Projection + kernel-verified source. Missing evidence is None, never a guessed model."""
    pid = it.get('pid')
    if it.get('ns_other'):
        pid = processes.namespace_pid(pid, it.get('source_ns'))
    token = processes.identity(pid)
    if not token or (it.get('proc_start') and it['proc_start'] != token):
        return None
    pids = processes.descendants(pid)
    detected = processes.probe(pids)
    native = it.get('native_id')
    if it.get('native_child') or native:
        # A child's model cannot be inferred from its hosting process.
        detected['model'] = identifier(it.get('model'))
        detected['effort'] = identifier(it.get('effort'))
        detected['model_source'] = 'native_event' if detected['model'] else None
    elif it.get('native'):
        # Accepted workflow selection outranks argv, but absence is not an observation.
        for key in ('model', 'effort'):
            if value := identifier(it.get(key)):
                detected[key] = value
                if key == 'model':
                    detected['model_source'] = 'native_event'
    reported = {k: identifier(it.get(k)) for k in ('harness', 'model') if identifier(it.get(k))} if it.get('self_reported') else {}
    conflict = any(detected.get(k) and detected[k] != v for k, v in reported.items())
    return {'detected': detected, 'reported': reported, 'conflict': conflict,
            'source': {'kind': 'native_thread' if native else 'pid', 'pid': pid, 'start': token,
                       **({'thread_id': native} if native else {})},
            'live_pids': sorted({p for p in pids if processes.inspect(p)} or {pid}),
            'harness': detected.get('harness'), 'model': detected.get('model')}
