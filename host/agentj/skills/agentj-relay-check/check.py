#!/usr/bin/env python3
"""Standalone, read-only Agents Relay route check. Do not run without owner approval for a call."""
import argparse
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import time
import signal
import subprocess
import tempfile
import tomllib
from urllib.parse import urlsplit

HOSTS = frozenset(('agentsrelay.net', 'api.agentsrelay.net'))
KINDS = ('claude', 'codex', 'opencode')
MAX_BYTES = 65536


def is_relay(value):
    if not isinstance(value, str):
        return False
    try:
        u = urlsplit(value)
        return u.scheme == 'https' and u.hostname in HOSTS and not (u.username or u.password or u.query or u.fragment)
    except ValueError:
        return False


def load(path, kind):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            return {}
        with path.open('rb') as f:
            return tomllib.load(f) if kind == 'toml' else json.load(f)
    except (OSError, ValueError, UnicodeError, TypeError):
        return {}


def configured(kind, home, env):
    if kind == 'claude':
        if 'ANTHROPIC_BASE_URL' in env:
            return is_relay(env['ANTHROPIC_BASE_URL'])
        cfg = load(home / '.claude/settings.json', 'json')
        options = cfg.get('env') if isinstance(cfg, dict) else None
        return isinstance(options, dict) and is_relay(options.get('ANTHROPIC_BASE_URL'))
    if kind == 'codex':
        cfg = load(Path(env.get('CODEX_HOME', str(home / '.codex'))) / 'config.toml', 'toml')
        if not isinstance(cfg, dict):
            return False
        provider = cfg.get('model_provider')
        providers = cfg.get('model_providers', {})
        return isinstance(provider, str) and isinstance(providers, dict) and is_relay(
            providers.get(provider, {}).get('base_url') if isinstance(providers.get(provider), dict) else None)
    # Only a selected provider counts, not an unrelated account present in config.
    cfg_home = Path(env.get('XDG_CONFIG_HOME', str(home / '.config'))) / 'opencode'
    cfg = load(cfg_home / 'opencode.json', 'json')
    if not isinstance(cfg, dict):
        return False
    providers = cfg.get('provider', {})
    if not isinstance(providers, dict):
        return False
    model = env.get('OPENCODE_MODEL') or cfg.get('model', '')
    selected = model.split('/')[0] if isinstance(model, str) and '/' in model else None
    if selected is None and len(providers) == 1:
        selected = next(iter(providers))
    entry = providers.get(selected)
    return isinstance(entry, dict) and isinstance(entry.get('options'), dict) and is_relay(entry['options'].get('baseURL'))


def classify(data, returncode):
    """Classify a bounded local response; never return any substring of it."""
    text = data.decode('utf-8', 'replace').lower()
    if re.search(r'(?<!\d)401(?!\d)|unauthorized|authentication', text):
        return 'auth'
    if re.search(r'(?<!\d)403(?!\d)|forbidden', text):
        return 'forbidden'
    if re.search(r'(?<!\d)404(?!\d)|not.found', text):
        return 'model_or_endpoint'
    if re.search(r'(?<!\d)429(?!\d)|quota|rate.limit|subscription.expired', text):
        return 'quota'
    if returncode or '"type": "error"' in text or '"type":"error"' in text or '"is_error":true' in text:
        return 'call_failed'
    if not text.strip():
        return 'empty_reply'
    return 'ok'


def invoke(kind, exe, env):
    with tempfile.TemporaryDirectory(prefix='agentj-relay-check-') as folder:
        root = Path(folder)
        (root / 'system.md').write_text('Reply with one short greeting. Do not use tools or read files.')
        if kind == 'claude':
            env.pop('CLAUDECODE', None)
            cmd = [exe, '-p', '--safe-mode', '--tools', '', '--disable-slash-commands', '--strict-mcp-config',
                   '--mcp-config', '{"mcpServers":{}}', '--no-chrome', '--no-session-persistence',
                   '--output-format', 'json', '--permission-mode', 'dontAsk']
        elif kind == 'codex':
            cmd = [exe, 'exec', '--ignore-rules', '--ephemeral', '--skip-git-repo-check',
                   '--sandbox', 'read-only', '--json',
                   '-c', 'web_search="disabled"', '-c', 'features.shell_tool=false',
                   '-c', 'features.multi_agent=false', '-c', 'features.apps=false',
                   '-c', 'features.browser_use=false', '-c', 'features.skill_search=false', '-']
        else:
            # OpenCode's config stays on disk and its own auth/route is inherited.
            env['OPENCODE_CONFIG_CONTENT'] = json.dumps({'permission': {'*': 'deny'}, 'tools': {'*': False},
                'instructions': [], 'plugin': [], 'mcp': {}, 'agent': {'relay-check': {
                    'mode': 'primary', 'permission': {'*': 'deny'}, 'tools': {'*': False},
                    'prompt': 'Reply with one short greeting. Do not use tools.'}}})
            env['OPENCODE_DISABLE_AUTOUPDATE'] = 'true'
            cmd = [exe, 'run', '--pure', '--agent', 'relay-check', '--format', 'json']
        proc = None
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    cwd=root, env=env, start_new_session=True)
            proc.stdin.write(b'Hello. Reply briefly; do not use tools.')
            proc.stdin.close()
            output = bytearray()
            deadline = time.monotonic() + 60
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return 'timeout'
                    for key, _ in selector.select(min(1, remaining)):
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            output.extend(chunk)
                            if len(output) > MAX_BYTES:
                                return 'call_failed'
            try:
                proc.wait(timeout=max(.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                return 'timeout'
            return classify(output, proc.returncode)
        except OSError:
            return 'call_failed'
        finally:
            if proc is not None:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()


def check(env=None):
    env = dict(os.environ if env is None else env)
    home = Path(env.get('HOME', str(Path.home())))
    result = {}
    for kind in KINDS:
        exe = shutil.which(kind, path=env.get('PATH', ''))
        if not exe:
            result[kind] = 'not_installed'
        elif not configured(kind, home, env):
            result[kind] = 'not_relay'
        else:
            result[kind] = invoke(kind, exe, env.copy())
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Read-only Agents Relay background-account check')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    result = check()
    print(json.dumps(result, sort_keys=True) if args.json else '\n'.join(f'{k}: {v}' for k, v in result.items()))
