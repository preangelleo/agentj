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
    # Security r2 H2: nothing under the trashbin's entries/ is read through a tool (a payload may be a former
    # credential or control file; `agentj trash list|review` show the metadata) — the passwd home's and env HOME's.
    from .state_paths import account_home, trash_path
    from . import trash as _trash
    binned = {os.path.realpath(os.path.join(trash_path(h), _trash.ENTRIES)) for h in {account_home(), home} if h}
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
        real = os.path.realpath(actual)
        if any(real == b or real.startswith(b + os.sep) for b in binned):
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


# #537② conservative list, Jarvis 2026-11-11 pending chairman; delete an entry to relax.
# Third-party CLI shapes that really move money (charge, pay, transfer, refund, broadcast a transaction). Matched on the
# parsed argv (danger.lex/unwrap: wrappers, `sh -c`, substitutions expanded) — program basename + the exact subcommand
# words, never a pattern over the command line. Only an owner / host turn's off-list Bash that names one of these gets
# the fingerprint card (list_approval.ask); every other source keeps the ADR-0307-offlist refusal. A speed bump against
# the model's own mistakes, not a boundary: a script, a raw HTTP call or a variable-built argv is not recognised.
CHARGE_COMMANDS = MappingProxyType({
    'stripe': (('charges', 'create'), ('charges', 'capture'),
               ('payment_intents', 'create'), ('payment_intents', 'confirm'), ('payment_intents', 'capture'),
               ('invoices', 'pay'), ('subscriptions', 'create'), ('payouts', 'create'), ('transfers', 'create'),
               ('topups', 'create'), ('refunds', 'create')),
    'cast': (('send',), ('publish',)),                                    # Foundry: sign + broadcast a transaction
    'solana': (('transfer',),),
    'spl-token': (('transfer',),),
    'bitcoin-cli': (('send',), ('sendall',), ('sendtoaddress',), ('sendmany',), ('sendrawtransaction',)),
    'lncli': (('sendpayment',), ('payinvoice',), ('sendcoins',), ('sendmany',)),
    'aptos': (('account', 'transfer'),),
    'sui': (('client', 'pay'), ('client', 'pay-sui'), ('client', 'pay-all-sui'), ('client', 'transfer-sui'),
            ('client', 'transfer')),
})
# `stripe post <path>`: the same Stripe effects through the generic API verb (exact /v1 resource paths only).
STRIPE_POST_CREATE = frozenset({'charges', 'payment_intents', 'subscriptions', 'payouts', 'transfers', 'topups', 'refunds'})
STRIPE_POST_ACTION = frozenset({('charges', 'capture'), ('payment_intents', 'confirm'), ('payment_intents', 'capture'),
                                ('invoices', 'pay')})
_SHELL_OPS = ('bash', 'exec_command', 'shell_command', 'execute_command', 'shell')


def _stripe_post(words):
    if len(words) < 2 or words[0] != 'post':
        return False
    segments = words[1].split('?', 1)[0].strip('/').split('/')
    if not segments or segments[0] != 'v1':
        return False
    return ((len(segments) == 2 and segments[1] in STRIPE_POST_CREATE)
            or (len(segments) == 4 and (segments[1], segments[3]) in STRIPE_POST_ACTION))


def _charge_call(name, args):
    import os
    program = os.path.basename(name or '')
    shapes = CHARGE_COMMANDS.get(program)
    if not shapes:
        return None
    end = args.index('--') if '--' in args else len(args)
    if '--help' in args[:end] or '-h' in args[:end]:      # help only as an option; after `--` it is an operand
        return None
    # Positional words in order; `skippable` = may be the value of the bare flag right before it
    # (`stripe --api-key K charges create`, `stripe charges --api-key K create`, `sui client --client.config c pay-sui`).
    # A shape word may skip only such values, never an ordinary positional (`bitcoin-cli help sendtoaddress`).
    positional = []
    for i, a in enumerate(args):
        if i == end:
            continue
        if i > end:
            positional.append((a, False))
        elif not a.startswith('-'):
            prev = args[i - 1] if i else ''
            positional.append((a, prev.startswith('-') and '=' not in prev and prev != '--'))

    def match(shape, k=0):
        """Index after `shape` matched from positional k (skipping flag values only), or None."""
        for word in shape:
            while k < len(positional) and positional[k][0] != word:
                if not positional[k][1]:
                    return None
                k += 1
            if k >= len(positional):
                return None
            k += 1
        return k

    for shape in shapes:
        if match(shape) is not None:
            return program + ' ' + ' '.join(shape)
    if program == 'stripe':
        k = match(('post',))
        while k is not None and k < len(positional) and positional[k][1] and not positional[k][0].startswith('/'):
            k += 1
        if k is not None and k < len(positional) and _stripe_post(['post', positional[k][0]]):
            return 'stripe post ' + positional[k][0].split('?', 1)[0]
    return None


def charge_command(tool, tool_input):
    """The CHARGE_COMMANDS shape a shell tool call runs ('stripe charges create'), or None. Never reads model text other
    than the command itself; an unparseable command is not guessed at (it stays the off-list decision)."""
    from . import danger, policy_registry
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        return None
    operation = policy_registry.harness_tool(tool).rsplit('__', 1)[-1].rsplit('.', 1)[-1].lower()
    if operation not in _SHELL_OPS:
        return None
    text = tool_input.get('command', tool_input.get('cmd', ''))
    if isinstance(text, list):
        import shlex
        text = shlex.join(str(w) for w in text)
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        for command in danger.lex(text):
            for call in danger.unwrap(command):
                hit = _charge_call(call.name, list(call.args))
                if hit:
                    return hit
    except Exception:  # noqa: BLE001 — a parser failure is not a charge claim; the off-list rules still apply
        return None
    return None


# ADR-0308-fingerprint-scope — 董事长 11-11 定（不可逆清单批准）: the main-thread fingerprint card also covers IRREVERSIBLE
# destruction a trashbin cannot undo (core data / documents: DROP/TRUNCATE, disk format writes, credential overwrite …).
# Ordinary file deletion is NOT here (main-thread rm goes to `agentj delete`, delete_redirect.py). Same contract as CHARGE_COMMANDS: parsed argv (danger.lex/unwrap), closed shapes, never a pattern that turns "default card"
# back on (rm -rf <project dir>, find -delete, wrangler d1 execute --local, fdisk -l stay card-free). Speed bump, not a
# boundary. Delete an entry to relax.
IRREVERSIBLE = MappingProxyType({
    'rm -r <home|/|/home>': 'Recursively delete the home directory, the filesystem root or /home (no trashbin holds it).',
    'dd of=/dev/…': 'Overwrite a block device.',
    'mkfs': 'Format a filesystem.',
    'wipefs -a': 'Erase filesystem / partition signatures.',
    'fdisk': 'Edit a partition table (listing excluded).',
    'parted': 'Edit a partition table (print / list excluded).',
    'SQL DROP/TRUNCATE': 'Drop a database / table or truncate it (sqlite3 psql mysql … or a non-local wrangler d1 execute).',
    'git push --force': 'Rewrite remote history.',
    'overwrite credential file': 'Replace or truncate a listed credential file (credential_paths) with Write / cp / mv / '
                                 'install / truncate / shred.',
})
_IR_SQL_CLIENTS = frozenset({'sqlite3', 'psql', 'mysql', 'mariadb', 'duckdb', 'clickhouse-client'})
_IR_DEV_SAFE = ('/dev/null', '/dev/zero', '/dev/stdout', '/dev/stderr', '/dev/tty', '/dev/fd/')
_IR_GIT_VALUE_OPTS = frozenset({'-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path'})
_IR_SQL = None


def _ir_sql(text):
    global _IR_SQL
    if _IR_SQL is None:
        import re
        _IR_SQL = re.compile(r'\b(?:drop\s+(?:table|database|schema)|truncate(?:\s+table)?\s+[A-Za-z_"`\[])', re.I)
    return isinstance(text, str) and _IR_SQL.search(text) is not None


def _ir_short(args, letters):
    """A short-option cluster (before `--`) holding any of `letters`."""
    end = args.index('--') if '--' in args else len(args)
    return any(len(a) > 1 and a[0] == '-' and a[1] != '-' and a[1:].isalpha() and any(ch in a[1:] for ch in letters)
               for a in args[:end])


def _ir_home(word, home):
    for prefix in ('${HOME}', '$HOME', '~'):
        if word == prefix or word.startswith(prefix + '/'):
            return home + word[len(prefix):]
    return word


def _ir_call(c, home):
    """IRREVERSIBLE label of one unwrapped call, or None."""
    import os
    from . import danger
    name, args = os.path.basename(c.name or ''), list(c.args)
    end = args.index('--') if '--' in args else len(args)
    head = args[:end]
    if 'remote' in c.flags:
        return None
    if name == 'rm' and (_ir_short(args, 'rR') or '--recursive' in head):
        roots = {'/', '/home', os.path.normpath(home)} if home else {'/', '/home'}
        for t in danger._positionals(args):
            t = _ir_home(t, home)
            if t in ('/', '/*'):
                return 'rm -r <home|/|/home>'
            t = t[:-2] if t.endswith('/*') else t
            if t and os.path.normpath(t) in roots:
                return 'rm -r <home|/|/home>'
        return None
    if name == 'git':
        k = 0
        while k < len(args) and args[k].startswith('-'):
            k += 2 if args[k] in _IR_GIT_VALUE_OPTS else 1
        rest = args[k:]
        if rest[:1] == ['push']:
            a = rest[1:]
            if '--dry-run' in a or '-n' in a:
                return None
            if any(x.startswith('--force') or (x.startswith('+') and len(x) > 1)
                   or (len(x) > 1 and x[0] == '-' and x[1] != '-' and 'f' in x[1:]) for x in a):
                return 'git push --force'
        return None
    if name == 'dd':
        return 'dd of=/dev/…' if any(a.startswith('of=/dev/') and not a[3:].startswith(_IR_DEV_SAFE) for a in args) else None
    if name == 'mkfs' or name.startswith('mkfs.'):
        return 'mkfs' if danger._positionals(args) and not any(a in ('-V', '--version', '-h', '--help') for a in head) else None
    if name == 'wipefs':
        if '-n' in head or '--no-act' in head or _ir_short(args, 'n'):
            return None
        if _ir_short(args, 'ao') or any(a in ('--all', '--offset') or a.startswith('--offset=') for a in head):
            return 'wipefs -a'
        return None
    if name == 'fdisk':
        if (_ir_short(args, 'lsxhV') or any(a in ('--list', '--getsz', '--list-details', '--help', '--version')
                                            for a in head)):
            return None
        return 'fdisk' if danger._positionals(args, {'-b', '-c', '-C', '-H', '-S', '-u', '-L', '-w', '-W', '-t'}) else None
    if name == 'parted':
        if _ir_short(args, 'lhv') or any(a in ('--list', '--help', '--version') for a in head):
            return None
        pos = danger._positionals(args, {'-a', '--align'})
        read = {'print', 'p', 'unit', 's', 'b', 'mb', 'gb', 'mib', 'gib', 'free', 'list', 'all', 'help', 'version'}
        if len(pos) >= 2 and all(w.lower() in read for w in pos[1:]):
            return None
        return 'parted' if len(pos) >= 2 else None          # `parted /dev/sda` alone is interactive: the harness decides
    if name in _IR_SQL_CLIENTS:
        return 'SQL DROP/TRUNCATE' if any(_ir_sql(a) for a in args) else None
    if name in ('wrangler', 'npx', 'bunx', 'pnpx'):
        pos = danger._positionals(args)
        if 'd1' in pos and 'execute' in pos and '--local' not in head and any(_ir_sql(a) for a in args):
            return 'SQL DROP/TRUNCATE'
        return None
    return None


def _ir_credential_overwrite(tool, inp, operation, cwd, env, host=None):
    """Write / cp / mv / install destination, truncate / shred operand = a listed credential file (literal paths)."""
    import os
    from . import danger
    home = (env or {}).get('HOME') or os.path.expanduser('~')
    try:
        listed = {os.path.realpath(p) for p in credential_paths(host, env=env or dict(os.environ))}
    except Exception:  # noqa: BLE001 — an inventory failure adds nothing (the off-list rules still apply)
        return None
    if not listed:
        return None

    def hit(word, here):
        if not isinstance(word, str) or not word:
            return False
        p = _ir_home(word, home)
        if any(ch in p for ch in '$`*?['):
            return False
        p = p if os.path.isabs(p) else os.path.join(here or os.getcwd(), p)
        return os.path.realpath(p) in listed
    if operation in ('write', 'write_file', 'create_file'):
        return 'overwrite credential file' if any(hit(inp.get(k), cwd) for k in ('file_path', 'filePath', 'path')) else None
    if operation not in _SHELL_OPS:
        return None
    text = inp.get('command', inp.get('cmd', ''))
    if isinstance(text, list):
        import shlex
        text = shlex.join(str(w) for w in text)
    if not isinstance(text, str):
        return None
    here = _resolved(inp['workdir'], cwd) if isinstance(inp.get('workdir'), str) else cwd
    for command in danger.lex(text):
        for c in danger.unwrap(command):
            if 'remote' in c.flags:
                continue
            name, args = os.path.basename(c.name or ''), list(c.args)
            pos = danger._positionals(args, {'-t', '--target-directory', '-m', '--mode', '-o', '--owner', '-g',
                                             '--group', '-s', '--size', '-n', '--iterations'})
            if name == 'cd' and len(pos) == 1:
                here = _resolved(_ir_home(pos[0], home), here) or here
            elif name in ('cp', 'mv', 'install') and len(pos) >= 2 and not any(
                    a in ('-t', '--target-directory') or a.startswith('--target-directory=') for a in args):
                if hit(pos[-1], here):
                    return 'overwrite credential file'
            elif name in ('truncate', 'shred') and any(hit(w, here) for w in pos):
                return 'overwrite credential file'
    return None


def irreversible_command(tool, tool_input, *, cwd=None, env=None, host=None):
    """The IRREVERSIBLE label a tool call runs, or None. Parsed argv only; an unparseable command is not guessed at."""
    import os
    from . import danger, policy_registry
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        return None
    operation = policy_registry.harness_tool(tool).rsplit('__', 1)[-1].rsplit('.', 1)[-1].lower()
    env = dict(os.environ) if env is None else env
    try:
        if operation not in _SHELL_OPS:
            return _ir_credential_overwrite(tool, tool_input, operation, cwd, env, host)
        text = tool_input.get('command', tool_input.get('cmd', ''))
        if isinstance(text, list):
            import shlex
            text = shlex.join(str(w) for w in text)
        if not isinstance(text, str) or not text.strip():
            return None
        home = env.get('HOME') or os.path.expanduser('~')
        for command in danger.lex(text):
            for call in danger.unwrap(command):
                hit = _ir_call(call, home)
                if hit:
                    return hit
                if (os.path.basename(call.name or '') in _IR_SQL_CLIENTS and 'remote' not in call.flags
                        and any(_ir_sql(d[2]) for d in command.docs)):
                    return 'SQL DROP/TRUNCATE'
        return _ir_credential_overwrite(tool, tool_input, operation, cwd, env, host)
    except Exception:  # noqa: BLE001 — a parser failure is not a claim; the off-list rules still apply
        return None


def fingerprint_command(tool, tool_input, *, cwd=None, env=None, host=None):
    """CHARGE_COMMANDS or IRREVERSIBLE hit of an off-list call, or None (ADR-0308-fingerprint-scope)."""
    return charge_command(tool, tool_input) or irreversible_command(tool, tool_input, cwd=cwd, env=env, host=host)


# ADR-0308-fingerprint-scope: the fingerprint card for a fingerprint_command() hit is for the MAIN thread only — the
# owner's own turns (approval_offlist.source types). Host heartbeat and all host-held background work run without
# runtime fingerprint cards or refusals, regardless of dispatch provenance; other off-list safety rules still apply.
FINGERPRINT_SOURCES = frozenset({'owner_remote', 'owner_terminal', 'owner_channel'})


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
    n = current(env)          # security r2 H1: the instance of `env`, not of this process
    rows.append({'path': str(config / ('agentj' if n == 1 else 'agentj-' + str(n)) / 'config.json5'),
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


def write_targets(tool, inp, *, cwd=None, agentj_only=False):
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
    paths, agentj = [], []
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
                elif _agentj_args(c.name, args) is not None:
                    agentj += [(p, directory) for p in _agentj_targets(_agentj_args(c.name, args))]
                elif c.name in ('python', 'python3', 'python2') and '-c' in args:
                    index = args.index('-c')
                    if index + 1 < len(args):
                        paths += [(p, directory) for p in _python_targets(args[index + 1])]
                elif c.name in ('sed', 'perl') and any(a == '--in-place' or a.startswith(('-i', '--in-place=')) for a in args):
                    files = danger._positionals(args, {'-e', '-f', '--expression', '--file'})
                    if not any(a in ('-e', '-f', '--expression', '--file') or a.startswith(('-e', '-f', '--expression=', '--file=')) for a in args):
                        files = files[1:]
                    paths += [(p, directory) for p in files]
    paths = agentj if agentj_only else paths + agentj
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
    return protected_match(write_targets(tool, inp, cwd=cwd), cwd=cwd, env=env)


def protected_match(actuals, *, cwd=None, env=None):
    """The resolved paths in `actuals` that hit the protected inventory (also used by trash.put / trash.restore)."""
    return match_rows(actuals, protected_paths(cwd=cwd, env=env))


def match_rows(actuals, rows):
    """protected_match against given protected_paths rows (trash.inventory passes the union over environments)."""
    import os
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
    for actual in actuals:
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


_PY_FLAGS = r'[bBdEiIOqsSuvRP]'


def _agentj_args(name, args):
    """The argv after the program for `agentj …` / `python[3] [flags] -m agentj[.cli] …` (closed shapes), else None.
    Security r2 H1/L2: interpreter flags before `-m` (-I -B -E -s -S -u -O -OO, -X opt, -W arg, grouped like -IB / -Im)
    and `--instance N` / `--instance=N` anywhere before a bare `--` (instance.split_argv semantics, without its number
    check: a bad number makes the CLI itself exit) are taken out, so the verb is the verb the CLI will run."""
    import re
    if name == 'agentj':
        rest = list(args)
    elif re.fullmatch(r'python[0-9.]*', name or ''):
        rest, i = None, 0
        while i < len(args):
            a = args[i]
            if a in ('-X', '-W'):
                i += 2
                continue
            if a.startswith(('-X', '-W')):
                i += 1
                continue
            m = re.fullmatch('-(' + _PY_FLAGS + '*)m(.*)', a)
            if m:
                mod, j = (m.group(2), i + 1) if m.group(2) else ((args[i + 1] if i + 1 < len(args) else ''), i + 2)
                if mod in ('agentj', 'agentj.cli'):
                    rest = list(args[j:])
                break
            if re.fullmatch('-' + _PY_FLAGS + '+', a):
                i += 1
                continue
            break
        if rest is None:
            return None
    else:
        return None
    out, i = [], 0
    while i < len(rest):
        x = rest[i]
        if x == '--':
            out += rest[i:]
            break
        if x == '--instance':
            i += 2
            continue
        if x.startswith('--instance='):
            i += 1
            continue
        out.append(x)
        i += 1
    return out


AGENTJ_DELETE_REASON = 'agentj_delete_protected'
AGENTJ_DELETE_HINT = ('Agent J：这是受保护的控制 / 凭据文件，Agent J 不会移动它；确实要删，由主人在本机自己删除。这次没有执行。 / '
                      'Agent J: protected control/credential file: Agent J will not move it; the owner removes it '
                      'locally. Not run.')


def agentj_delete_refusal(tool, inp, *, cwd=None):
    """Security r2 H1/H2/L3: the fixed deny text when an `agentj delete|rm|trash … <paths>` / `agentj trash restore <id>`
    operand hits what trash.guard refuses (protected / credential inventory, an Agent J state root, HOME) — the hook
    denies it outright (no card: put()/restore() would refuse it after an approval anyway). None otherwise. The
    inventory is trash.inventory(): the passwd-home environment of every instance plus the caller's own."""
    targets = [t for t in write_targets(tool, inp, cwd=cwd, agentj_only=True) if t]
    if not targets:
        return None
    from . import trash
    from .state_paths import trash_path
    base = trash_path()
    for t in targets:
        try:
            trash.guard(t, base, cwd=cwd)
        except trash.Refused as r:
            if str(r) in ('protected', 'credential', 'state_dir', 'home'):
                return AGENTJ_DELETE_HINT
    return None


def _agentj_targets(argv):
    """0.30.8 security r1 H1: `agentj delete|rm <paths>`, `agentj trash [--] <paths>` and `agentj trash restore <id>`
    (the entry's recorded original path; trash.restore re-validates it itself) are filesystem writes like `rm`/`mv`."""
    words = [a for a in argv if a != '--json']
    if not words:
        return []
    verb, rest = words[0], words[1:]
    if verb in ('delete', 'rm'):
        if '--' in rest:
            i = rest.index('--')
            return [a for a in rest[:i] if not a.startswith('-')] + rest[i + 1:]
        return [a for a in rest if not a.startswith('-')]
    if verb != 'trash' or not rest:
        return []
    from . import trash
    if rest[0] == '--':
        return rest[1:]
    if rest[0] == 'restore':
        return [p for p in (trash.original_path(e) for e in rest[1:]) if p]
    if rest[0] in trash.ACTIONS:
        return []
    return rest


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
