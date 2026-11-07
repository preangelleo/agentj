"""Narrow display adapter: control-plane labels are not secret output dataflow.

This never authorizes a tool. The existing approval classifier/verifier remains
responsible for every operation; uncertain display paths retain the old route.
"""
import ast
import os
import re
import shlex
from . import approval_review, danger

_FILTERS = frozenset(('grep','egrep','fgrep'))
_CONTROL = frozenset(('daemon-reload','enable','disable','start','stop','restart','reload','try-restart',
                      'reload-or-restart','reset-failed','set-environment','unset-environment','list-timers'))


def _literal_unit_writer(command, cwd):
    """Closed no-output shape, not a general Python/file-effect allowance."""
    from . import uv_effect
    words = command.words
    if len(words) != 3 or words[1] != '-c':
        return False
    proof = uv_effect._python_file_program(shlex.join(words), cwd)
    if proof is None or not set(proof.effects) <= {'approval_root'}:
        return False
    try:
        tree = ast.parse(words[2])
    except SyntaxError:
        return False
    values = {}
    home_units = os.path.expanduser('~/.config/systemd/user')
    def literal(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return values.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = literal(node.left), literal(node.right)
            return left + right if left is not None and right is not None else None
        return None
    wrote = False
    for node in tree.body:
        if isinstance(node, ast.Import) and len(node.names) == 1 and node.names[0].name == 'os' and node.names[0].asname is None:
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = node.value
            if not isinstance(value, ast.Call) or ast.unparse(value.func) != 'os.path.expanduser' or value.keywords or len(value.args) != 1:
                return False
            raw = literal(value.args[0])
            if raw != '~/.config/systemd/user':
                return False
            values[node.targets[0].id] = home_units
            continue
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            return False
        call = node.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr != 'write' or call.keywords or len(call.args) != 1 or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
            return False
        opened = call.func.value
        if not isinstance(opened, ast.Call) or not isinstance(opened.func, ast.Name) or opened.func.id != 'open' or opened.keywords or len(opened.args) != 2 or literal(opened.args[1]) != 'w':
            return False
        target = literal(opened.args[0])
        if target is None or os.path.dirname(target) != home_units or not re.fullmatch(r'[A-Za-z0-9_.@-]+\.(?:service|timer)', os.path.basename(target)):
            return False
        wrote = True
    return wrote


def reveals_to_session(tool, inp, cwd=None, env=None):
    legacy = approval_review.reveals_to_session(tool, inp, cwd, env)
    if not legacy:
        return False
    if isinstance(inp, dict) and (inp.get('env') or inp.get('shell')):
        return True
    verdict = danger.classify_shared(tool, inp, cwd=cwd, env=env)
    labels = {r for r in verdict.rules if r.startswith('cred.') or r == 'extra.credentials'}
    if not labels or not labels <= {'cred.service','cred.hooks'} or not isinstance(inp, dict):
        return True
    raw = inp.get('command', inp.get('cmd', inp.get('code', '')))
    # No expansion, substitutions, heredocs or partial/truncated analysis may
    # acquire the exception, even if their final output argument looks constant.
    if not isinstance(raw, str) or len(raw) > danger.MAX_CMD or re.search(r'[$`]|<<|[<>]\(', raw):
        return True
    commands = danger.lex(raw)
    safe_pipeline = set()
    found = False
    for index, command in enumerate(commands):
        parts = danger.unwrap(command)
        if len(parts) != 1:
            return True
        part = parts[0]
        if part.flags - {'piped'} or command.ins or command.docs or command.subst or not command.words or command.words[0] not in (part.name, '/usr/bin/'+part.name, '/bin/'+part.name):
            return True
        if part.name == 'systemctl' and not command.piped:
            # Only this fixed listing can supply a benign grep stream. `show`,
            # `cat`, show-environment and arbitrary service output are excluded.
            args = part.args
            verbs = [a for a in args if not a.startswith('-')]
            if not verbs or verbs[0] not in _CONTROL:
                return True
            if args and all(a in ('--user','list-timers','--all','--no-pager','--no-legend') for a in args) and args.count('list-timers') == 1:
                safe_pipeline.add(index)
        elif part.name in ('echo','printf'):
            if command.piped or command.ins or any(danger.cred_path(a) for a in part.args):
                return True
            found = True
        elif part.name in _FILTERS:
            # One literal pattern, flag-only options, no credential/file/stdin
            # operand. Its producer must be that exact list-timers pipeline.
            args = part.args
            if not command.piped or index - 1 not in safe_pipeline or commands[index-1].owner is not command.owner:
                return True
            operands = [a for a in args if not a.startswith('-')]
            if len(operands) != 1 or any(not re.fullmatch(r'-[EFiInvxw]+', a) for a in args if a.startswith('-')):
                return True
            safe_pipeline.add(index); found = True
        elif part.name == 'cut':
            if not command.piped or index - 1 not in safe_pipeline or commands[index-1].owner is not command.owner:
                return True
            if len(part.args) != 1 or not re.fullmatch(r'-c[0-9,-]+', part.args[0]):
                return True
            safe_pipeline.add(index)
        elif part.name in ('python','python3') and not command.piped:
            if not _literal_unit_writer(command, cwd):
                return True
        elif part.name in ('true',':') and not command.piped:
            if part.args:
                return True
        else:
            return True
    return not found
