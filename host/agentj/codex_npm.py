"""Resolve the native payload of a standard npm Codex install without running Node."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys


def _manifest(root):
    path = root / 'package.json'
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError('package manifest')
    with path.open('rb') as src:
        raw = src.read(65537)
    if len(raw) > 65536:
        raise ValueError('package manifest size')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('package manifest shape')
    return value


# Published npm entrypoint bytes. Customized scripts retain their ordinary launch path.
LAUNCHERS = {
    'baefc109b871e73a7bab298ee19b8bf73c8b647c4f8649a9794fc5db01db17b9',  # 0.130.0
    '134063e133f0b4244fa3b251acf973d4fe4b4aeeacbdc135211bf480f59f1477',  # 0.145.0 / 0.150.0
    '61b0194f3bb6534439c8d26a3ed57d0805f84b884588b761795323eeb92fcf70',  # 0.155.0 to 0.165.0
    'e14fc83739c13c1074b12bd1acce4010c893c7f6fbfc1593b4a31d74cabb57af',  # 0.166.0 / 0.167.0
}


def launch(path, environ=None):
    """Return (native executable, npm environment), or keep the selected launcher."""
    env = os.environ if environ is None else environ
    if Path(path).name not in ('codex', 'codex.js') or sys.platform not in ('linux', 'darwin'):
        return None
    if (env.get('NODE_OPTIONS') or env.get('NODE_PATH') or 'bun/' in env.get('npm_config_user_agent', '')
            or 'bun' in env.get('npm_execpath', '')):
        return None
    try:
        entry = Path(path).resolve(strict=True)
        root = entry.parent.parent
        if (entry.parts[-5:] != ('node_modules', '@openai', 'codex', 'bin', 'codex.js')
                or any(part in ('.pnpm', '.bun') for part in root.parts)):
            return None
        if any((p / '.modules.yaml').is_file() for p in (root.parent.parent, root / 'node_modules')):
            return None
        if any((p / 'packages' / '@openai' / 'codex.json').is_file() for p in (root, *root.parents)):
            return None
        if not entry.is_file() or not os.access(entry, os.X_OK):
            return None
        with entry.open('rb') as src:
            launcher_bytes = src.read(16385)
        if len(launcher_bytes) > 16384 or hashlib.sha256(launcher_bytes).hexdigest() not in LAUNCHERS:
            return None
        arch = {'x86_64': ('x64', 'x86_64'), 'amd64': ('x64', 'x86_64'),
                'arm64': ('arm64', 'aarch64'), 'aarch64': ('arm64', 'aarch64')}.get(platform.machine().lower())
        if arch is None:
            return None
        os_arch = f'{sys.platform}-{arch[0]}'
        triple = arch[1] + ('-unknown-linux-musl' if sys.platform == 'linux' else '-apple-darwin')
        name = 'codex-' + os_arch
        launcher = _manifest(root)
        version = launcher.get('version')
        if (launcher.get('name') != '@openai/codex' or launcher.get('bin') != {'codex': 'bin/codex.js'}
                or not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', version)):
            return None
        native_version = f'{version}-{os_arch}'
        aliases = launcher.get('optionalDependencies')
        if not isinstance(aliases, dict) or aliases.get('@openai/' + name) != f'npm:@openai/codex@{native_version}':
            return None
        nested = root / 'node_modules' / '@openai' / name
        package = nested if os.path.lexists(nested) else root.with_name(name)
        nearer = (entry.parent / 'node_modules' / '@openai' / name,)
        if package != nested:
            nearer += (root.parent / 'node_modules' / '@openai' / name,)
        if any(os.path.lexists(p) for p in nearer) or package.resolve(strict=True) != package:
            return None
        native = _manifest(package)
        if (native.get('name') != '@openai/codex' or native.get('version') != native_version
                or native.get('os') != [sys.platform] or native.get('cpu') != [arch[0]]):
            return None
        binary = package / 'vendor' / triple / 'bin' / 'codex'
        if not binary.is_file() or binary.resolve(strict=True) != binary or not os.access(binary, os.X_OK):
            return None
        with binary.open('rb') as src:
            magic = src.read(4)
        if sys.platform == 'linux' and magic != b'\x7fELF':
            return None
        if sys.platform == 'darwin' and magic not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
            return None
        return str(binary), {'CODEX_MANAGED_BY_NPM': '1', 'CODEX_MANAGED_PACKAGE_ROOT': str(root)}
    except (OSError, ValueError, RuntimeError):
        return None
