"""Owner-local agentj://pair handler. A public channel id can select a host, never authorize a device.

The cloud receives no loopback token, pairing material or local URL. All admission and approval remain AdminServer's.
"""
from __future__ import annotations
import os
import plistlib
import shlex
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path


def validate_url(url: str, channel: str) -> None:
    u = urllib.parse.urlsplit(url)
    if u.scheme != 'agentj' or u.netloc != 'pair' or u.path not in ('', '/') or u.fragment or u.username or u.password:
        raise ValueError('invalid_pair_url')
    values = urllib.parse.parse_qs(u.query, strict_parsing=True, keep_blank_values=True)
    if set(values) - {'channel'} or ('channel' in values and values['channel'] != [channel]):
        raise ValueError('wrong_computer')


def install() -> dict:
    """Register for this user; only known owned files are written. No privilege or key is involved."""
    executable = str(Path(sys.executable).resolve())
    if sys.platform.startswith('linux'):
        root = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
        path = root / 'applications/agentj-pair.desktop'
        path.parent.mkdir(parents=True, exist_ok=True)
        # Desktop Exec uses its own quoting rules, not shell quoting. Percent expansion is escaped in the executable path.
        quoted = '"' + executable.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
        path.write_text('[Desktop Entry]\nType=Application\nName=Agent J pairing\nNoDisplay=true\nTerminal=false\n'
                        f'Exec={quoted} -m agentj protocol open %u\nMimeType=x-scheme-handler/agentj;\n')
        os.chmod(path, 0o644)
        subprocess.run(['xdg-mime', 'default', path.name, 'x-scheme-handler/agentj'], check=True, timeout=15, capture_output=True)
        return {'kind': 'desktop', 'path': str(path)}
    if sys.platform == 'darwin':
        app = Path.home() / 'Applications/Agent J Pair.app'
        app.parent.mkdir(parents=True, exist_ok=True)
        # macOS delivers URLs through the open location Apple event, not process argv.
        cmd = shlex.join([executable, '-m', 'agentj', 'protocol', 'open'])
        literal = cmd.replace('\\', '\\\\').replace('"', '\\"')
        script = f'on open location theURL\n do shell script "{literal} " & quoted form of theURL & " >/dev/null 2>&1 &"\nend open location\n'
        subprocess.run(['osacompile', '-o', str(app), '-'], input=script, text=True, check=True, timeout=30, capture_output=True)
        info = app / 'Contents/Info.plist'
        data = plistlib.loads(info.read_bytes())
        data.update(CFBundleIdentifier='app.agentj.pair', CFBundleURLTypes=[{'CFBundleURLName': 'Agent J', 'CFBundleURLSchemes': ['agentj']}])
        info.write_bytes(plistlib.dumps(data))
        subprocess.run(['/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister', '-f', str(app)], check=True, timeout=15, capture_output=True)
        return {'kind': 'application', 'path': str(app)}
    raise ValueError('unsupported_os')


def _wrong_computer(opener):
    # Static local explanation, no token, host data or cloud request.
    html='<meta charset="utf-8"><title>Agent J</title><p>请在装了这个 Agent 的电脑上打开账号页，再点「添加遥控器」。</p><p>Open the account page on the computer running this Agent, then choose Add a remote.</p>'
    opener('data:text/html;charset=utf-8,' + urllib.parse.quote(html))


def open_pair(url: str, *, stop=None, opener=webbrowser.open, lifetime=600) -> None:
    from .state import State
    from .admin import AdminServer
    st = State()
    if not st.exists():
        validate_url(url, '')
        _wrong_computer(opener)
        return
    try:
        validate_url(url, st.config()['channel'])
    except ValueError as e:
        if str(e) != 'wrong_computer':
            raise
        _wrong_computer(opener)
        return
    # No protocol input reaches the admin URL. The one-use fragment is minted on this host only.
    server = AdminServer(st, port=0).start()
    try:
        if not opener(server.new_link().replace("/#", "/?pair=1#")):
            raise ValueError('browser_unavailable')
        (stop or threading.Event()).wait(lifetime)
    finally:
        server.stop()


def main(args) -> None:
    if args.mode == 'install':
        result = install()
        print('Agent J pairing link registered: ' + result['kind'])
    else:
        open_pair(args.url)
