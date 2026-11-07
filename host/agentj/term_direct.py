"""H1's closed literal-output profile, not a general read-only classifier.

Only valid in a pristine, no-rc bash line shell. No paths are opened, no external
program is selected, and no shell expansion, formatting assignment or script is
accepted. Unknown syntax retains the existing confirmation path.
"""
import re
import shlex


def literal_output(line: str) -> bool:
    if not isinstance(line, str) or not line or len(line) > 4096:
        return False
    # Deliberately reject even quoted expansion/operators; this is an allow subset.
    if any(c in line for c in '$`<>|&(){}*?[]~#\r\n'):
        return False
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=';')
        lex.whitespace_split, lex.commenters = True, ''
        tokens = list(lex)
    except ValueError:
        return False
    commands, current = [], []
    for token in tokens:
        if token == ';':
            if not current:
                return False
            commands.append(current)
            current = []
        elif token and all(c == ';' for c in token):
            return False
        else:
            current.append(token)
    if current:
        commands.append(current)
    if not commands:
        return False
    for words in commands:
        name, args = words[0], words[1:]
        if name == 'pwd':
            if args not in ([], ['-L'], ['-P']):
                return False
        elif name == 'echo':
            # No option-dependent interpretation; all arguments are literal output.
            if args and args[0].startswith('-'):
                return False
        elif name == 'printf':
            if not args or args[0].startswith('-'):
                return False
            fmt = args[0]
            # Only %s/%% and newline/tab escapes: no %n assignment, %b or octal/control escapes.
            if re.search(r'%(?!s|%)|\\(?!n|t|\\)', fmt):
                return False
        else:
            return False
    return True


def pristine_env(env: dict) -> bool:
    """Metadata-only check; never inspect or log environment values."""
    return not any(k.startswith('BASH_') or k in {
        'SHELLOPTS', 'BASHOPTS', 'PROMPT_COMMAND', 'PS0', 'PS1', 'PS2', 'PS4', 'ENV'
    } for k in env)
