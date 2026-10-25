"""Bounded straight-line shell expansion for classification, never execution.

Track literal assignment statements in order; expand every later variable use.
Scopes, control flow and substitutions stay opaque. No ambient environment reads.
"""
import re
import shlex

_NAMES = ('PY', 'PYTHON')
_INTERPRETER = re.compile(r'(?:/[A-Za-z0-9_.+-]+)*/(?:python(?:[23](?:\.[0-9]+)?)?|pytest)|python(?:[23](?:\.[0-9]+)?)?|pytest')
_VAR = re.compile(r'\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))')
_CONTROL = frozenset(('if', 'then', 'elif', 'else', 'fi', 'for', 'while', 'until',
                      'do', 'done', 'case', 'esac', 'select', 'function', '{', '}'))


def interpreter_env(env):
    """Copy exact nonsecret interpreter names, after launch filtering, never config/input."""
    return {k: env[k] for k in _NAMES if isinstance(env.get(k), str) and _INTERPRETER.fullmatch(env[k])}


def _statements(command):
    """Quote-aware shell tokens with source spans, operators and redirection roles.

    Never evaluate shell syntax. Unsupported scopes/substitutions return no facts.
    """
    statements, words, pos, target = [], [], 0, False
    while pos < len(command):
        c = command[pos]
        if c in ' \t\r':
            pos += 1
            continue
        if c == '#' and not target:
            end = command.find('\n', pos)
            pos = len(command) if end < 0 else end
            continue
        if c == '\n' and not words and not target:
            pos += 1
            continue
        if c in ';\n|&' and not (c == '&' and command[pos:pos + 2] in ('&>',)):
            op = command[pos:pos + 2]
            if op not in ('&&', '||', '|&'):
                op = c
            if target or op == '|&':
                return None
            statements.append((words, op))
            words, pos = [], pos + len(op)
            continue
        if c in '<>' or command[pos:pos + 2] == '&>':
            m = re.match(r'(?:&>>?|<>|<&|>&|>>?|<)', command[pos:])
            op = m[0]
            if target or command[pos:pos + 2] in ('<<', '<(', '>('):
                return None
            if words and words[-1][1] == pos and words[-1][2].isdigit():
                words.pop()  # adjacent IO_NUMBER, not an executable/argument
            target = True
            pos += len(op)
            continue
        start, quote = pos, ''
        while pos < len(command):
            c = command[pos]
            if c == "'" and quote != '"':
                quote = '' if quote else "'"
            elif c == '"' and quote != "'":
                quote = '' if quote else '"'
            elif c == '\\' and quote != "'":
                if pos + 1 == len(command):
                    return None
                pos += 2
                continue
            elif quote != "'" and (c == '`' or command[pos:pos + 2] == '$(' or
                                    (command[pos:pos + 2] == '${' and not _VAR.match(command, pos))):
                return None
            elif not quote and (c.isspace() or c in ';|&<>'):
                break
            elif not quote and c in '()':
                return None
            pos += 1
        if quote or start == pos:
            return None
        words.append((start, pos, command[start:pos], target))
        target = False
    if target:
        return None
    return statements + [(words, '')]


def _continuations(command):
    """Remove shell line continuations except inside single quotes."""
    out, pos, quote = [], 0, ''
    while pos < len(command):
        c = command[pos]
        if c == '\\' and quote != "'":
            if command[pos:pos + 2] != '\\\n':
                out.append(command[pos:pos + 2])
            pos += 2
            continue
        if c in "'\"" and (not quote or quote == c):
            quote = '' if quote else c
        out.append(c)
        pos += 1
    return ''.join(out)


def _literal(raw):
    """Decode a literal assignment word; reject active expansions, never evaluate."""
    value, pos, quote = raw.split('=', 1)[1], 0, ''
    while pos < len(value):
        c = value[pos]
        if c == '\\' and quote != "'":
            pos += 2
            continue
        if c in "'\"" and (not quote or quote == c):
            quote = '' if quote else c
        elif quote != "'" and c in '$`':
            return None
        elif not quote and c in '*?[~{}':
            return None
        pos += 1
    parts = shlex.split(value)
    return parts[0] if parts else ''


def _expand(word, known):
    out, pos, quote = [], 0, ''
    while pos < len(word):
        c = word[pos]
        if c == '\\' and quote != "'":
            out.append(word[pos:pos + 2])
            pos += 2
            continue
        if c in "'\"" and (not quote or quote == c):
            quote = '' if quote else c
        m = _VAR.match(word, pos) if quote != "'" else None
        value = known.get(m[1] or m[2]) if m else None
        if value is not None and (quote == '"' or re.fullmatch(r'[A-Za-z0-9_./:+,=@%-]+', value)):
            # Literal values must not turn into shell operators or another expansion.
            out.append(value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`')
                       if quote == '"' else shlex.quote(value))
            pos = m.end()
        else:
            out.append(c)
            pos += 1
    return ''.join(out)


def resolve(command, env=None):
    """Return analysis text only. Original executable request/digest is untouched."""
    if len(command) > 65536:
        return command
    original = command
    command = _continuations(command)
    statements = _statements(command)
    if statements is None or any(
            any(re.match(r'IFS(?:\+)?=', w[2]) for w in tokens) or
            next((w[2] for w in tokens if not w[3]), '') in _CONTROL
            for tokens, _ in statements):
        return original
    known, edits = interpreter_env(env or {}), []
    # An async AND/OR list runs in a separate shell. None of its writes
    # establish facts for the parent, even before the final '&'.
    background, group = set(), []
    for i, (_, op) in enumerate(statements):
        group.append(i)
        if op in (';', '\n', '&', ''):
            if op == '&':
                background.update(group)
            group = []
    for i, (tokens, op) in enumerate(statements):
        words = [w for w in tokens if not w[3]]
        prior = statements[i - 1][1] if i else ''
        uncertain = i in background or prior in ('&&', '||', '|') or op == '|'
        if not words:
            continue
        assignments = words[1:] if words[0][2] == 'export' else words
        if assignments and all(re.match(r'[A-Za-z_]\w*=', w[2]) for w in assignments):
            # Redirection expansion happens before assignment side effects.
            for start, end, raw, redirection in tokens:
                if redirection:
                    value = _expand(raw, known)
                    if value != raw:
                        edits.append((start, end, value))
            for _, _, raw, _ in assignments:
                value = _literal(raw)
                name = raw.split('=', 1)[0]
                if value is not None and not uncertain and not any(w[3] for w in tokens):
                    known[name] = value
                else:
                    known.pop(name, None)
            continue
        # Append/array assignments and builtin wrappers are outside this grammar.
        if re.match(r'[A-Za-z_]\w*(?:\+|\[).*=', words[0][2]):
            known.clear()
        expanded = []
        for start, end, raw, redirection in tokens:
            value = _expand(raw, known)
            if not redirection:
                expanded.append(value)
            if value != raw:
                edits.append((start, end, value))
        # These builtins may replace variables in this shell, without literal facts.
        executable = next((w for w in expanded if not re.match(r'[A-Za-z_]\w*=', w)), '')
        head = shlex.split(executable)  # token quotes/escapes only; never evaluates code
        if head and head[0] in ('unset', 'read', 'source', '.', 'eval', 'declare', 'typeset', 'export',
                           'mapfile', 'readarray', 'local', 'readonly', 'let',
                           'getopts', 'set', 'shift', 'command', 'builtin', 'trap'):
            known.clear()
        elif head and head[0] == 'printf':
            args = [shlex.split(w)[0] for w in expanded[expanded.index(executable) + 1:]]
            # Ordinary output does not mutate shell variables. Bash printf -v
            # (including -vNAME) and the %n conversion do; unknown formats may too.
            fmt = args[1] if args[:1] == ['--'] and len(args) > 1 else (args[0] if args else '')
            if (fmt.startswith('-v') or '$' in fmt or
                    re.search(r'%(?:[-+ #0]*[0-9*]*(?:\.[0-9*]+)?)n', fmt)):
                known.clear()
    out, pos = [], 0
    for start, end, value in edits:
        out.extend((command[pos:start], value))
        pos = end
    return ''.join(out) + command[pos:]


def main(argv=None):
    """Read-only classifier probe; never executes input or reads launch environment."""
    import argparse
    import json
    from . import approval_jev, danger
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('--classify', required=True, metavar='COMMAND')
    args = parser.parse_args(argv)
    verdict = danger.classify('Bash', {'command': args.classify})
    print(json.dumps({'resolved': resolve(args.classify), 'danger': verdict.danger,
                      'gray': approval_jev.gray(verdict), 'rules': verdict.rules},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
