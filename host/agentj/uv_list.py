"""Explicit Agent J command UV list. No shell/effect heuristics or source grants.

Each command executor calls Host.ask with its semantic command name. Native tools
(including Bash strings mentioning these commands) follow the harness; the actual
Agent J executor owns the fresh UV check.
"""
from types import MappingProxyType

CREDENTIAL_READ_REASON = 'credential_file_read_forbidden'
CREDENTIAL_READ_HINT = ('凭据文件不能由工具直接读取（包括计数）。请走既有凭据入口按名称使用；'
                        '给主人交付秘密请用 agentj secret send，它会单独核验。 / '
                        'Tools cannot directly read credential files, including counts. Use the configured credential '
                        'entry by name; owner secret delivery uses agentj secret send with its own verification.')


def credential_paths(host=None, *, env=None):
    """Explicit metadata inventory only; never discovers or reads secret values."""
    import os
    from . import keys_found, provider_runtime
    env = host_environment(host) if env is None else env
    home = env.get('HOME') or os.path.expanduser('~')
    paths = [os.path.join(home, 'coding', '.env'), provider_runtime.env_path()]
    paths += keys_found.extra_files(getattr(host, 'preferences', None))
    return [p for p in (_credential_path(p, home, home) for p in paths) if p]


def _credential_path(path, cwd, home):
    import os
    if not isinstance(path, str) or not path:
        return None
    for prefix in ('${HOME}/', '$HOME/', '~/'):
        if path.startswith(prefix):
            path = os.path.join(home, path[len(prefix):])
            break
    if any(c in path for c in '$`*?[') or path.startswith('~'):
        return None
    return os.path.abspath(path if os.path.isabs(path) else os.path.join(cwd or os.getcwd(), path))


def credential_reads(tool, inp, *, host=None, cwd=None, env=None):
    """Known raw-read operands only, not confinement of arbitrary runtime code."""
    import os
    from . import danger, policy_registry, static_shell
    env = host_environment(host) if env is None else env
    home = env.get('HOME') or os.path.expanduser('~')
    protected = credential_paths(host, env=env)
    canonical = {os.path.realpath(p) for p in protected}
    identities = set()
    for path in protected:
        try:
            row = os.stat(path); identities.add((row.st_dev, row.st_ino))
        except OSError:
            pass
    def matches(path, directory):
        actual = _credential_path(path, directory, home)
        if actual is None:
            return False
        if actual in protected or os.path.realpath(actual) in canonical:
            return True
        try:
            row = os.stat(actual)
            return (row.st_dev, row.st_ino) in identities
        except OSError:
            return False
    inp = inp if isinstance(inp, dict) else {}
    if not isinstance(tool, str):
        return False
    operation = policy_registry.harness_tool(tool).rsplit('__', 1)[-1].rsplit('.', 1)[-1].lower()
    if operation in ('read', 'read_file', 'file_read', 'view_image'):
        return any(matches(inp.get(key), cwd) for key in ('file_path', 'path', 'filename', 'absolute_path'))
    if operation not in ('bash', 'exec_command', 'shell_command', 'execute_command'):
        return False
    directory = _credential_path(inp.get('workdir'), cwd, home) if inp.get('workdir') else cwd
    text = inp.get('command', inp.get('cmd', ''))
    if not isinstance(text, str):
        return False
    import shlex
    text = static_shell.resolve('HOME=' + shlex.quote(home) + ';\n' + text)
    statements = static_shell._statements(text)
    # Unsupported control/program syntax retains native permissions. Literal
    # absolute inputs are still checked; no opaque program is called safe.
    parts = [(text, '', '')] if statements is None else [
        (text[tokens[0][0]:tokens[-1][1]], op, statements[i-1][1] if i else '')
        for i, (tokens, op) in enumerate(statements) if tokens]
    directories = {directory}
    readers = {'cat', 'head', 'tail', 'wc', 'grep', 'egrep', 'fgrep', 'rg', 'awk', 'gawk', 'sed',
               'less', 'more', 'strings', 'base64', 'od', 'xxd', 'cut', 'sort', 'uniq', 'cmp', 'diff',
               'sha256sum', 'sha512sum', 'md5sum', 'cksum'}
    for part, following, prior in parts:
        for command in danger.lex(part):
            for call in danger.unwrap(command):
                if 'remote' in call.flags:
                    continue
                paths = list(call.ins)
                if call.name in readers:
                    options = {'-e', '--regexp', '-f', '--file', '-m', '--max-count', '-A', '-B', '-C', '-g', '--glob'} if call.name in ('grep', 'egrep', 'fgrep', 'rg') else (
                        {'-n', '--lines', '-c', '--bytes'} if call.name in ('head', 'tail') else
                        {'-F', '-v', '-f'} if call.name in ('awk', 'gawk') else {'-e', '-f', '--expression', '--file'} if call.name == 'sed' else set())
                    values = danger._positionals(call.args, options)
                    expression = any(a in ('-e', '--regexp', '-f', '--file', '--expression') or a.startswith(('-e', '-f', '--regexp=', '--file=', '--expression=')) for a in call.args)
                    if call.name in ('grep', 'egrep', 'fgrep', 'rg', 'awk', 'gawk', 'sed') and not expression:
                        values = values[1:]
                    paths += values
                    paths += [call.args[i+1] for i, arg in enumerate(call.args[:-1]) if arg in ('-f', '--file')]
                    paths += [arg.split('=', 1)[1] for arg in call.args if arg.startswith('--file=')]
                    paths += [arg[2:] for arg in call.args if arg.startswith('-f') and len(arg) > 2]
                # resolve() already expanded real HOME references with shell
                # quotation intact. A remaining dollar is literal or unknown.
                if any(matches(path, current) for path in paths if '$' not in path for current in directories):
                    return True
                if call.name == 'cd' and len(call.args) == 1 and not command.subst:
                    selected = {_credential_path(call.args[0], current, home) for current in directories}
                    selected.discard(None)
                    if prior in ('&&', '||', '|') or following in ('|', '&'):
                        directories |= selected
                    elif selected and (following == '&&' or all(os.path.isdir(p) for p in selected)):
                        directories = selected
                    else:
                        directories |= selected
    return False

COMMANDS = MappingProxyType({
    'agentj mail workflow-grant': 'Create a durable delegation allowing a workflow to send mail to the owner.',
    'agentj automation grant': 'Create a durable grant letting one webhook / scheduled task mail listed third-party recipients.',
    'AuthRescue': 'Insert or replace AI account login credentials in the host settings.',
    'owner_hosts_set': 'Change cross-host trust and secret-routing peers.',
    'security_begin': 'Begin a paired-device or credential trust-root rotation.',
    'security_verify': 'Verify replacement device/key authority during rotation.',
    'security_retire': 'Retire an old trusted device/key during rotation.',
    'fr_set [op=owner-confirm-v3]': 'Bind another host as the same owner (scope v3 and v4 rounds; the version is inside the signed challenge).',
    'pk_peer_answer': 'Sponsor registration or replacement of another remote verification key.',
    'native_scheduler_control [scope!=user]': 'Authorize system-level scheduled-job control.',
    'host_write [kind=mail_set|mail_test, except mail_role_downgrade]': 'Insert or change mail credentials and forwarding routes; narrowing a role stays a signed tap.',
    'assist_grant': 'Grant a remote support agent temporary control of this host.',
    'assist_term_grant': 'Grant remote support access to an interactive terminal.',
    'seat_transfer': 'Move the trusted account seat to another host.',
    'keys.copy': 'Display a stored secret on the confirming remote (keys UI).',
    'keys.delete': 'Remove a stored credential (keys UI).',
    'terminal.enable': 'Grant a remote interactive terminal access (terminal security configuration).',
    'terminal.allow': 'Grant another remote terminal access (terminal security configuration).',
    'terminal.open': 'Unlock a stored-login terminal session for this remote.',
    'vault.share': 'Share a saved host login password with another remote.',
    'budget.set': 'Increase a spending safety limit (lowering a limit remains a signed tap).',
    'approval.set': 'Change the approval security policy.',
    'device.revoke': 'Remove a trusted remote from the paired device roster.',
    'passkey.reset': 'Replace the owner verification trust root.',
    'agentj secret send': 'Expose a secret to the confirming remote or an external destination.',
    'agentj secret request': 'Replace or insert a secret; prevent a stolen remote changing credentials.',
    'agentj secret grant': 'Authorize another host to handle owner secrets.',
    'pair_device': 'Add a remote with control over this host.',
    'agentj revoke': 'Remove a trusted remote.',
    'agentj unlink': 'Remove account/device trust.',
    'passphrase_reset': 'Replace the pairing password used to admit remotes.',
    'agentj config set <listed-security-key>': 'Change an explicitly enumerated trust, permission, credential-routing or installation setting.',
    'agentj goal grant': 'Change approval preauthorization for a task.',
    'agentj fn add': 'Register or replace an owner-selected high-risk function.',
    'agentj fn run': 'Execute an explicitly registered high-risk function.',
    'agentj fn remove': 'Remove a function from the owner-selected UV list.',
})


# Explicit semantic configuration keys, not command/path/effect heuristics.
CONFIG_KEYS = MappingProxyType({
    **dict.fromkeys(('security.e2e','security.signed_approvals'), 'Change encrypted transport or signed-approval protection.'),
    **dict.fromkeys(('human.devices','human.remote_unbind'), 'Change paired-device trust or remote account control.'),
    **dict.fromkeys(('human.relay','human.web','human.fence'), 'Change the trusted relay/front-end endpoint or isolation boundary.'),
    **dict.fromkeys(('agent.working_root','agent.private_instructions_file','agent.session_mode','agent.isolation','agent.allow_docker'), 'Change agent filesystem/instruction authority, session ownership or sandbox scope.'),
    **dict.fromkeys(('channels.items','telegram.groups','telegram.private_domains','telegram.forward','telegram.owner_commands'), 'Change inbound identity/routing or who can issue owner commands.'),
    **dict.fromkeys(('proxy.https_env','proxy.http_env','proxy.no_proxy_env','proxy.https','proxy.http','proxy.no_proxy'), 'Redirect outbound authenticated service traffic or bypass its configured route.'),
    **dict.fromkeys(('updates.mode','updates.auto_install','updates.skill_categories'), 'Change permission for installing executable host or skill updates.'),
})


def config_keys(changes):
    return [key for key in changes if isinstance(key,str) and (key.startswith('approval.') or key in CONFIG_KEYS)]


def entries():
    return ([{'command': command, 'reason': reason} for command, reason in COMMANDS.items()]
        + [{'command':'agentj config set approval.*','reason':'Change approval policy, expiry or preauthorization.'}]
        + [{'command':'agentj config set '+key,'reason':reason} for key,reason in CONFIG_KEYS.items()])


def required(tool, tool_input=None, *, cwd=None, env=None):
    """Semantic names only. Model text, command shape and origin never grant UV."""
    if protected_targets(tool, tool_input, cwd=cwd, env=env):
        return True
    if tool in COMMANDS:
        return True
    if tool == 'agentj config' and isinstance(tool_input, dict):
        changes = tool_input.get('changes') or {}
        return isinstance(changes,dict) and bool(config_keys(changes))
    if tool == 'agentj mail send' and isinstance(tool_input, dict):
        return bool(tool_input.get('friend_handoff'))     # ADR-0306-friend-handoff-mail: set host-side by agent_send only
    return False


# Closed control-file inventory. Templates, not arbitrary secret or service paths.
# Device records contain the WebAuthn public keys and counters (state.py F20).
STATE_FILES = (
    'config.json', 'settings.json', 'devices.json', 'removed.json', 'approver.json',
    'host_x25519.key', 'host_ed25519.key', 'cloud.json', 'estop.json',
    'owner-hosts.json', 'owner-hosts.revision', 'functions.json',
    'approval-operation-once.sqlite3', 'approval-operation-once.sqlite3-wal',
    'approval-operation-once.sqlite3-shm',
)
HARNESS_FILES = (
    ('claude', 'settings.json'), ('claude', 'settings.local.json'),
    ('codex', 'hooks.json'), ('codex', 'config.toml'),
    ('opencode', 'opencode.json'), ('opencode', 'opencode.jsonc'),
)


def host_environment(host):
    """Use the running controller's actual State, not a caller-selected root."""
    import os
    env = dict(os.environ)
    state = getattr(getattr(host, 'st', None), 'root', None)
    if state is not None:
        env['AGENTJ_STATE_DIR'] = str(state)
    return env


def protected_paths(*, cwd=None, env=None):
    """Concrete inventory owned by the host environment, never the tool input."""
    import os
    from pathlib import Path
    from .state_paths import state_dir
    env = os.environ if env is None else env
    home = Path(env.get('HOME') or Path.home())
    root = Path(env.get('AGENTJ_STATE_DIR') or state_dir())
    config = Path(env.get('XDG_CONFIG_HOME') or home / '.config')
    rows = [{'path': str(root / name), 'reason': 'Agent J trust and approval state'} for name in STATE_FILES]
    from .instance import current
    rows.append({'path': str(config / ('agentj' if current() == 1 else 'agentj-' + str(current())) / 'config.json5'),
                 'reason': 'Agent J settings and approval configuration'})
    for family, name in HARNESS_FILES:
        base = (Path(env.get('CLAUDE_CONFIG_DIR') or home / '.claude') if family == 'claude' else
                Path(env.get('CODEX_HOME') or home / '.codex') if family == 'codex' else config / 'opencode')
        rows.append({'path': str(base / name), 'reason': 'Harness hooks and permissions configuration'})
        if cwd:
            directory = Path(cwd).resolve()
            for ancestor in (directory, *directory.parents):
                rows.append({'path': str(ancestor / ('.' + family) / name),
                             'reason': 'Inherited project harness hooks and permissions configuration'})
                if family == 'opencode':
                    rows.append({'path': str(ancestor / name),
                                 'reason': 'Inherited OpenCode project permissions configuration'})
    rows.append({'path': str(config / 'opencode/plugins/agentj-dispatch'), 'recursive': True,
                 'reason': 'Agent J generated OpenCode pre-tool plugin package'})
    rows.append({'path': str(config / 'opencode/plugins/agentj-shared.js'),
                 'reason': 'Agent J generated OpenCode pre-tool hook'})
    if cwd:
        directory = Path(cwd).resolve()
        for ancestor in (directory, *directory.parents):
            rows.append({'path': str(ancestor / '.opencode/plugins/agentj-dispatch'), 'recursive': True,
                         'reason': 'Agent J generated OpenCode pre-tool plugin package'})
            rows.append({'path': str(ancestor / '.opencode/plugins/agentj-shared.js'),
                         'reason': 'Agent J generated OpenCode pre-tool hook'})
    rows.append({'path': str(Path(__file__).resolve().parent), 'recursive': True,
                 'reason': 'Running Agent J package: installed hooks, control and verification implementation'})
    return rows


def _resolved(path, cwd):
    import os
    if not isinstance(path, str) or not path or any(c in path for c in '$`*?['):
        return None
    path = os.path.expanduser(path)
    return os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd or os.getcwd(), path))


def write_targets(tool, inp, *, cwd=None):
    """Extract filesystem operands, never infer danger from command names.

    Unknown programs/expansions add no targets and retain native policy. This is
    an operand adapter, not confinement of arbitrary runtime code.
    """
    from . import danger, static_shell, policy_registry
    import os
    import re
    inp = inp if isinstance(inp, dict) else {}
    tool = policy_registry.harness_tool(tool)
    operation = tool.rsplit('__', 1)[-1].rsplit('.', 1)[-1]
    if operation in ('exec_command', 'shell_command', 'execute_command') and isinstance(inp.get('workdir'), str):
        cwd = _resolved(inp['workdir'], cwd) or cwd
    tool = {'exec_command':'Bash', 'shell_command':'Bash', 'execute_command':'Bash', 'bash':'Bash',
            'write':'Write', 'edit':'Edit', 'apply_patch':'apply_patch'}.get(operation, tool)
    paths = []
    if tool in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Delete'):
        paths += [inp[k] for k in ('file_path', 'path', 'notebook_path') if isinstance(inp.get(k), str)]
    elif tool in ('apply_patch', 'ApplyPatch'):
        patch = inp.get('patch', inp.get('input', inp.get('command', '')))
        paths += re.findall(r'^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$', str(patch), re.M)
    elif tool == 'Bash':
        text = inp.get('command', inp.get('cmd', ''))
        if isinstance(text, list):
            text = ' '.join(text)
        if not isinstance(text, str):
            return []
        # Only the current host HOME is a known environment binding.
        import shlex
        text = static_shell.resolve('HOME=' + shlex.quote(os.path.expanduser('~')) + ';\n' + text)
        directory = cwd
        for command in danger.lex(text):
            paths += [(p, directory) for p in command.outs]
            for c in danger.unwrap(command):
                if 'remote' in c.flags:
                    continue
                paths += [(p, directory) for p in c.redirs if p not in c.ins]
                args = c.args
                pos = danger._positionals(args, {'-t', '--target-directory'})
                if c.name == 'cd' and len(pos) == 1:
                    directory = _resolved(pos[0], directory) or directory
                elif c.name in ('rm', 'unlink', 'rmdir', 'touch', 'truncate', 'tee', 'chmod', 'chown'):
                    paths += [(p, directory) for p in pos]
                elif c.name in ('cp', 'mv', 'ln', 'install'):
                    dest = next((args[i+1] for i, a in enumerate(args[:-1]) if a in ('-t', '--target-directory')), None)
                    dest = next((a.split('=', 1)[1] for a in args if a.startswith('--target-directory=')), dest)
                    if c.name in ('mv', 'ln'):
                        paths += [(p, directory) for p in (pos if dest else pos[:-1])]
                    target = dest or (pos[-1] if pos else None)
                    if target:
                        actual = _resolved(target, directory)
                        if actual and os.path.isdir(actual) and not any(a in ('-T', '--no-target-directory') for a in args):
                            paths += [(os.path.join(actual, os.path.basename(p)), directory) for p in (pos if dest else pos[:-1])]
                        else:
                            paths.append((target, directory))
                elif c.name in ('python', 'python3', 'python2') and '-c' in args:
                    index = args.index('-c')
                    if index + 1 < len(args):
                        paths += [(p, directory) for p in _python_targets(args[index + 1])]
                elif c.name in ('sed', 'perl') and any(a == '--in-place' or a.startswith(('-i', '--in-place=')) for a in args):
                    files = danger._positionals(args, {'-e', '-f', '--expression', '--file'})
                    if not any(a in ('-e', '-f', '--expression', '--file') or a.startswith(('-e', '-f', '--expression=', '--file=')) for a in args):
                        files = files[1:]
                    paths += [(p, directory) for p in files]
    resolved = []
    import glob
    for item in paths:
        p, directory = item if isinstance(item, tuple) else (item, cwd)
        if not isinstance(p, str):
            continue
        if any(c in p for c in '*?[') and not any(c in p for c in '$`'):
            pattern = os.path.expanduser(p)
            if not os.path.isabs(pattern):
                pattern = os.path.join(directory or os.getcwd(), pattern)
            resolved.extend(os.path.realpath(match) for match in glob.iglob(pattern))
        else:
            resolved.append(_resolved(p, directory))
    return resolved


def protected_targets(tool, inp=None, *, cwd=None, env=None):
    """Resolved identity, ancestor replacement and hardlink aliases count equally."""
    import os
    rows = protected_paths(cwd=cwd, env=env)
    protected = [os.path.realpath(r['path']) for r in rows]
    trees = [os.path.realpath(r['path']) for r in rows if r.get('recursive')]
    inodes = set()
    for p in protected:
        try:
            st = os.stat(p)
            inodes.add((st.st_dev, st.st_ino))
        except OSError:
            pass
    hits = []
    for actual in write_targets(tool, inp, cwd=cwd):
        if not actual:
            continue
        identity = False
        try:
            st = os.stat(actual)
            identity = (st.st_dev, st.st_ino) in inodes
            if not identity and st.st_nlink > 1:
                for tree in trees:
                    for directory, _, files in os.walk(tree, followlinks=False):
                        for filename in files:
                            try:
                                member = os.stat(os.path.join(directory, filename))
                                if (member.st_dev, member.st_ino) == (st.st_dev, st.st_ino):
                                    identity = True
                                    break
                            except OSError:
                                pass
                        if identity:
                            break
                    if identity:
                        break
        except OSError:
            pass
        if identity or any(actual == p or p.startswith(actual.rstrip(os.sep) + os.sep) for p in protected) or any(actual.startswith(p + os.sep) for p in trees):
            hits.append(actual)
    return sorted(set(hits))


def _python_targets(program):
    """Literal Python filesystem operands only; no evaluation or opaque fallback."""
    import ast
    try:
        tree = ast.parse(program)
    except (SyntaxError, ValueError):
        return []
    def literal(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            if name == 'Path' and len(node.args) == 1:
                return literal(node.args[0])
        return None
    paths = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
        args = [literal(a) for a in node.args]
        if name == 'open':
            receiver = literal(node.func.value) if isinstance(node.func, ast.Attribute) else None
            filename = receiver or (args[0] if args else next((literal(k.value) for k in node.keywords if k.arg == 'file'), None))
            modes = args if receiver else args[1:]
            mode = modes[0] if modes else next((literal(k.value) for k in node.keywords if k.arg == 'mode'), 'r')
            if isinstance(mode, str) and any(c in mode for c in 'wax+'):
                paths.append(filename)
        elif isinstance(node.func, ast.Attribute):
            receiver = literal(node.func.value)
            if receiver and name in ('write_text', 'write_bytes', 'unlink', 'rmdir', 'touch', 'chmod', 'rename', 'replace', 'symlink_to', 'hardlink_to'):
                paths.append(receiver)
                if name in ('rename', 'replace'):
                    paths += args[:1]
            elif isinstance(node.func.value, ast.Name) and node.func.value.id in ('os', 'shutil'):
                if name in ('remove', 'unlink', 'rmdir', 'rmtree', 'chmod'):
                    paths += args[:1]
                elif name in ('rename', 'replace', 'move'):
                    paths += args[:2]
                elif name in ('symlink', 'link', 'copy', 'copy2', 'copyfile', 'copytree'):
                    paths += args[1:2]
    return [p for p in paths if p is not None]
