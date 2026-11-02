"""See ARCHITECTURE."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import re
import json
import os
from pathlib import Path
import subprocess

# Reviewed web-page-report installation, 2026-11-03. Includes scripts, registry,
# schemas, examples and assets, not merely a same-directory self-reported hash.
TRUSTED_SHA256 = '3a9b062cfdcd889fef5990bce4d55993e4e76eaf93e034ae4ca51388b623c590'


def digest(root: Path) -> str:
    rows = []
    for f in sorted(root.rglob('*')):
        if '__pycache__' in f.parts:
            continue
        if f.is_symlink():
            raise ValueError('report package symlink refused')
        if f.is_file():
            rows.append([f.relative_to(root).as_posix(), hashlib.sha256(f.read_bytes()).hexdigest()])
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()


def safe_path(value: str) -> tuple[Path, Path]:
    """See ARCHITECTURE."""
    path = Path(os.path.abspath(os.path.expanduser(value)))
    home = Path.home()
    roots = [Path.cwd(), Path(os.environ.get('AGENT_WORKSPACE') or '~/agent-workspace').expanduser(), Path('/tmp')]
    # cwd/env are selectors, not authority to export into /etc, HOME or hidden
    # credential/configuration trees. Permit ordinary workspace paths and /tmp.
    if any(p.startswith('.') or p.lower() in ('credentials', 'secrets') for p in path.parts[1:]):
        raise ValueError('report local sensitive path refused')
    for root in sorted(set(roots), key=lambda x: len(str(x)), reverse=True):
        root = Path(os.path.abspath(root))
        if root != Path('/tmp') and not (home in root.parents or Path('/tmp') in root.parents):
            continue
        if path == root or root not in path.parents:
            continue
        for part in (root, *reversed(tuple(path.parents)[:tuple(path.parents).index(root)]), path):
            if part.is_symlink():
                raise ValueError('report local path symlink refused')
        # Check root ancestors too: a symlinked workspace must not confer access.
        if any(p.is_symlink() for p in root.parents):
            raise ValueError('report local root symlink refused')
        return path, root
    raise ValueError('report local path is outside workspace or /tmp')


def export_email(value: str, data: bytes) -> Path:
    """See ARCHITECTURE."""
    path, root = safe_path(value)
    if path.suffix.lower() not in ('.html', '.htm'):
        path = path / 'email.html'
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    # Traverse from / rather than reopening an already-checked mutable root path.
    fd = os.open('/', flags)
    try:
        for part in path.parts[1:-1]:
            try:
                nxt = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, mode=0o700, dir_fd=fd)
                nxt = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = nxt
        # No overwrites: an existing file may be a hardlink or another job's file.
        out = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, 'wb') as stream:
            stream.write(data)
    finally:
        os.close(fd)
    return path



def check_email(data: bytes, *, allow_https_images=False) -> None:
    """See ARCHITECTURE."""
    def css(value):
        if re.search(r'@import|url\s*\(|expression\s*\(|javascript\s*:|\\', value, re.I):
            raise ValueError('report local external CSS refused')
    class Check(HTMLParser):
        style = False
        def handle_starttag(self, tag, attrs):
            if tag.lower() in ('script', 'iframe', 'object', 'embed', 'link', 'base', 'form', 'svg', 'math', 'frame', 'portal'):
                raise ValueError('report local active HTML refused')
            if tag.lower() == 'style':
                self.style = True
            for name, value in attrs:
                name, value = name.lower(), (value or '').strip()
                if name.startswith('on') or name in ('srcdoc', 'srcset') or name == 'http-equiv':
                    raise ValueError('report local active HTML refused')
                if name in ('src', 'poster', 'background', 'data', 'xlink:href') and not (allow_https_images and tag.lower() == 'img' and name == 'src' and value.startswith('https://')) and not re.fullmatch(r'data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+', value, re.I):
                    raise ValueError('report local external resource refused')
                if name == 'href' and not (value.startswith(('https://', 'mailto:', '#')) or not value):
                    raise ValueError('report local unsafe link refused')
                if name == 'style':
                    css(value)
        handle_startendtag = handle_starttag
        def handle_endtag(self, tag):
            if tag.lower() == 'style':
                self.style = False
        def handle_data(self, value):
            if self.style:
                css(value)
    Check(convert_charrefs=True).feed(data.decode('utf-8'))


def run(engine: Path, original: list[str], args: list[str], root: Path, *, capture=False):
    """See ARCHITECTURE."""
    if not args or args[0] not in ('templates', 'schema', 'example', 'validate', 'render'):
        return None
    if args[0] == 'render' and '--email' not in args:
        return None
    def system_helper(name):
        path = Path('/usr/bin') / name
        real = path.resolve()
        if Path('/usr') not in real.parents or not real.is_file():
            return False
        info = real.stat()
        return info.st_uid == 0 and not info.st_mode & 0o022
    if digest(engine) != TRUSTED_SHA256 or not all(system_helper(n) for n in ('bwrap', 'python3')):
        return None
    action = args[0]
    # Only exact argv emitted by report._engine_args/send. No extra switches,
    # duplicate flags or positional flags can reach a different package action.
    final = list(args)
    expected = {'templates': [], 'schema': ['value'], 'example': ['value'], 'validate': ['value'], 'render': ['value']}[action]
    if expected and (len(final) < 2 or final[1].startswith('-')):
        raise ValueError('report local arguments refused')
    at = 1 + len(expected)
    seen = set()
    while at < len(final):
        flag = final[at]
        if flag in seen or flag not in ('--json', '--template', '--email', '--out'):
            raise ValueError('report local arguments refused')
        if flag in ('--email', '--out') and action != 'render' or flag == '--template' and action not in ('validate', 'render'):
            raise ValueError('report local arguments refused')
        seen.add(flag)
        at += 2 if flag in ('--template', '--out') else 1
    if at != len(final) or action == 'render' and '--out' not in seen:
        raise ValueError('report local arguments refused')
    if action in ('validate', 'render'):
        safe_path(original[1])
    # Every custom input was frozen by report._run; guard its original root too.
    ti = original.index('--template') + 1 if '--template' in original else (1 if action in ('schema', 'example') else None)
    if ti is not None and str(root / 'input-template') in final[ti]:
        raw = original[ti]
        path = Path(raw).expanduser()
        if '/' not in raw and not path.is_dir():
            path = Path(os.environ.get('AGENT_WORKSPACE') or '~/agent-workspace').expanduser() / 'report-templates' / raw
        safe_path(str(path))
    dest = None
    if action == 'render':
        oi = final.index('--out') + 1
        dest = original[oi]
        safe_path(dest)
        final[oi] = '/out/email.html'
    final = [v.replace(str(root) + '/', '/job/', 1) if v.startswith(str(root) + '/') else v for v in final]
    output = root / 'local-output'
    output.mkdir()
    argv = ['/usr/bin/bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--cap-drop', 'ALL']
    for source in ('/usr', '/lib', '/lib64'):
        if Path(source).exists():
            argv += ['--ro-bind', source, source]
    argv += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/workspace', '--ro-bind', str(root), '/job',
             '--bind', str(output), '/out', '--chdir', '/workspace', '/usr/bin/python3', '-I', '-B',
             '/job/engine/scripts/web_report.py', *final]
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/nonexistent', 'AGENT_WORKSPACE': '/workspace', 'TMPDIR': '/tmp'}
    try:
        result = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('report local execution timed out') from exc
    if result.returncode == 0 and dest is not None:
        file = output / 'email.html'
        if file.is_symlink() or not file.is_file() or file.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('report local output refused')
        data = file.read_bytes()
        custom = any(v.startswith('/job/input-template/') for v in final)
        check_email(data, allow_https_images=not custom)
        target = export_email(dest, data)
        result.stdout = f'Rendered local email → {target}\n'
    if not capture:
        print(result.stdout, end='')
        if result.stderr:
            import sys
            print(result.stderr, end='', file=sys.stderr)
    return result
