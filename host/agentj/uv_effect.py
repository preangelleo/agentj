"""#537: one closed UV effect assessment shared by normal and goal execution.

Adapters describe executable operands, not card labels. This is a static admission
check, not a claim to observe arbitrary programs at runtime. Provenance, signatures,
emergency stop and explicit host passkey requests remain the caller's boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re

# Adding an effect requires chairman approval via Jarvis (CONSTITUTION #537).
EFFECTS = ("sensitive_storage", "sensitive_delivery", "money", "irreversible_destroy", "approval_root")
_DATA_OPERATIONS = frozenset({"cd", "pwd", "cat", "head", "tail", "cut", "wc", "grep", "egrep", "fgrep", "echo", "printf", "true", "false", "test", "[", ":"})


@dataclass(frozen=True)
class Assessment:
    reason: str | None = None
    effects: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


def _result(effect, reason, evidence):
    assert effect in EFFECTS
    return Assessment(reason, (effect,), tuple(evidence))


def _proposal(path, cwd=None):
    """A goal proposal is input data, never the running approval state."""
    if not isinstance(path, str) or any(c in path for c in "$`*?[]{\n"):
        return False
    p = os.path.realpath(os.path.join(cwd or os.getcwd(), os.path.expanduser(path)))
    return (os.path.dirname(p) == os.path.realpath(os.path.expanduser("~/coding/.agentj"))
            and re.fullmatch(r"goal-[A-Za-z0-9_.-]+\.json", os.path.basename(p)) is not None)


def _state_write(tool, inp, text, cwd):
    from . import approval_policy, danger
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Delete"):
        paths = [inp.get(k) for k in ("file_path", "path", "notebook_path") if inp.get(k)]
        return any(not _proposal(p, cwd) and approval_policy.aj_state_write("echo x > " + str(p)) for p in paths)
    if tool != "Bash":
        return False
    # A literal data writer has named output operands. Validate every output;
    # quoted data and heredoc text never become potential approval filenames.
    commands = danger.lex(text)
    literal = commands and all(c.words and danger._base(c.words[0]) in ("cat", "printf", "echo", "tee")
                               and not c.subst for c in commands)
    if literal:
        targets = [p for c in commands for p in c.outs]
        for c in commands:
            if danger._base(c.words[0]) == "tee":
                targets.extend(danger._positionals(c.words[1:]))
        return any(not _proposal(p, cwd) and approval_policy.aj_state_write("echo x > " + str(p)) for p in targets)
    return approval_policy.aj_state_write(text)


def _expanding_text(text):
    """Only proven data builtins may treat single-quoted operands as literals.

    Any interpreter/shell/program keeps its entire executable program operand,
    regardless of options before -c/-e. No interpreter flag grammar is guessed.
    """
    from . import danger
    commands = danger.lex(text)
    if commands and all(c.words and danger._base(c.words[0]) in ("echo", "printf")
                        and not c.subst for c in commands):
        return re.sub(r"'[^']*'", "''", text)
    return text


def _ssh_material(path):
    """Public SSH materials are not private-key stores; resolve local aliases."""
    if not isinstance(path, str) or any(c in path for c in "$`*?["):
        return None
    # Remote operands preserve their named role; local symlinks use real paths.
    p = path.split(":", 1)[1] if ":" in path and not path.startswith("/") else path
    p = os.path.realpath(os.path.expanduser(p))
    if "/.ssh/" not in p:
        return None
    base = os.path.basename(p)
    if base in ("authorized_keys", "authorized_keys2", "known_hosts", "config"):
        return "authority"
    return "public" if base.endswith(".pub") else None


def _curl_file_operands(args):
    """The HTTP client's actual file-read/write option roles, never URL words."""
    reads, writes, eligible = [], [], True
    upload = {"-d", "--data", "--data-binary", "--data-raw", "--data-urlencode", "--json", "-F", "--form", "-T", "--upload-file"}
    inputs = {"--key", "--cert", "--cacert", "--cookie", "-b"}
    outputs = {"-o", "--output", "--cookie-jar", "-c", "--dump-header", "-D"}
    # Configuration and authentication files may add further requests/credentials.
    config = {"-K", "--config", "--netrc", "--netrc-file", "--netrc-optional"}
    i = 0
    while i < len(args):
        arg = args[i]
        option, equals, value = arg.partition("=") if arg.startswith("--") else (arg, "", "")
        if not arg.startswith("--") and len(arg) > 2 and arg[:2] in upload | inputs | outputs | config:
            option, equals, value = arg[:2], "=", arg[2:]
        if option in config:
            eligible = False
        if option in upload | inputs | outputs | config and not equals and i + 1 < len(args):
            i += 1
            value = args[i]
        if option in upload:
            file = value[1:] if value.startswith("@") else value.split("=@", 1)[1].split(";", 1)[0] if "=@" in value and option in ("-F", "--form") else value.split("@", 1)[1] if "@" in value and option == "--data-urlencode" else None
            if option in ("-T", "--upload-file"):
                file = value
            if file and file != "-":
                reads.append(file)
        elif option in inputs | config and value:
            reads.append(value)
        elif option in outputs and value:
            writes.append(value)
        elif "@" in arg:
            # An unparsed argument with file-read syntax cannot be washed out by
            # a separately proven public upload. Preserve its operand evidence.
            reads.append(arg.split("@", 1)[1])
        i += 1
    return reads, writes, eligible


def _ssh_operands(tool, inp, text):
    from . import danger
    writes, operands, eligible = [], [], True
    if tool == "Bash":
        commands = danger.lex(text)
        eligible = len(commands) == 1 and not commands[0].subst if commands else False
        for command in commands:
            writes.extend(command.outs)
            operands.extend(command.ins)
            for c in danger.unwrap(command):
                if c.name == "curl":
                    reads, outputs, adapted = _curl_file_operands(c.args)
                    operands.extend(reads)
                    writes.extend(outputs)
                    eligible = eligible and adapted
                else:
                    eligible = eligible and c.name in {"cat", "head", "tail", "less", "more", "cp", "scp", "rsync", "mv", "install", "tee", "sed", "ssh", "echo", "printf", "ls", "stat", "wc", "sha256sum", "shasum"}
                    operands.extend(c.args)
                operands.extend(c.ins or [])
                # unwrap exposes nested ssh/sh command redirections on Simple,
                # not on the outer Cmd. Input redirects are reads, not writes.
                writes.extend(p for p in c.redirs if p not in (c.ins or []))
                if c.name == "tee":
                    writes.extend(danger._positionals(c.args))
                elif c.name in ("cp", "scp", "rsync", "mv", "install"):
                    pos = danger._positionals(c.args)
                    writes.extend(pos[-1:])
                elif c.name == "sed" and any(a == "--in-place" or a.startswith("-i") for a in c.args):
                    writes.extend(danger._positionals(c.args)[1:])
        operands.extend(writes)
    else:
        operands = [inp.get(k) for k in ("file_path", "path", "notebook_path", "source", "destination") if inp.get(k)]
        if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Delete"):
            writes.extend(operands)
    named = [p for p in operands if isinstance(p, str) and danger.cred_path(p)]
    authority_write = any(_ssh_material(p) == "authority" for p in writes)
    public_only = eligible and bool(named) and all(_ssh_material(p) in ("authority", "public") for p in named)
    return authority_write, public_only


def _local_operand(path, cwd=None):
    """Resolve the actual local file named by a literal operand, including links."""
    if not isinstance(path, str) or not path or any(c in path for c in "$`*?["):
        return None
    # A remote scp operand belongs to another filesystem, never this host's cwd.
    if ":" in path and not path.startswith("/"):
        return None
    expanded = os.path.expanduser(path)
    return os.path.realpath(expanded if os.path.isabs(expanded) else os.path.join(cwd or os.getcwd(), expanded))


def _file_operand_effect(tool, inp, text, cwd):
    """Admit actual file read/write operands before legacy display classification.

    Redirections, file-tool targets and HTTP file options use resolved storage
    identity. Mentioning a filename in ordinary data never creates an operand.
    """
    from . import approval_level, approval_policy, danger
    writes, reads = [], []
    if tool == "Bash":
        for command in danger.lex(text):
            writes.extend(command.outs)
            reads.extend((path, False) for path in command.ins)
            for c in danger.unwrap(command):
                writes.extend(path for path in c.redirs if path not in (c.ins or []))
                reads.extend((path, False) for path in c.ins or [])
                if c.name == "curl":
                    incoming, outgoing, _ = _curl_file_operands(c.args)
                    reads.extend((path, True) for path in incoming)
                    writes.extend(outgoing)
                elif c.name == "tee":
                    writes.extend(danger._positionals(c.args))
                elif c.name in ("source", ".", "bash", "sh", "zsh", "dash"):
                    files = danger._positionals(c.args)
                    if files and not any(a in ("-c", "-lc") for a in c.args):
                        reads.append((files[0], False))
                elif c.name == "sed":
                    files = danger._positionals(c.args, {"-e", "--expression", "-f", "--file"})
                    has_program_option = any(a in ("-e", "-f", "--expression", "--file") or a.startswith(("--expression=", "--file=")) or len(a)>2 and a.startswith(("-e", "-f")) for a in c.args)
                    if not has_program_option:
                        files = files[1:]
                    reads.extend((path, False) for path in files)
                    reads.extend((c.args[i+1], False) for i,a in enumerate(c.args[:-1]) if a in ("-f", "--file"))
                    reads.extend((a.split("=",1)[1], False) for a in c.args if a.startswith("--file="))
                    if any(a == "--in-place" or a.startswith(("--in-place=", "-i")) for a in c.args):
                        writes.extend(files)
                elif c.name in ("cp", "scp", "rsync", "mv", "install"):
                    pos = danger._positionals(c.args)
                    writes.extend(pos[-1:])
                    reads.extend((path, c.name in ("scp", "rsync")) for path in pos[:-1])
                elif c.name in ("cat", "head", "tail", "cut", "wc", "grep", "egrep", "fgrep"):
                    option_values = ({"-n", "-c", "--lines", "--bytes", "--pid", "-s", "--sleep-interval"}
                                     if c.name in ("head", "tail") else
                                     {"-b", "-c", "-f", "-d", "--bytes", "--characters", "--fields", "--delimiter", "--output-delimiter"}
                                     if c.name == "cut" else
                                     {"-e", "--regexp", "-f", "--file", "-m", "--max-count", "-A", "-B", "-C", "--after-context", "--before-context", "--context", "--include", "--exclude", "--exclude-dir"}
                                     if c.name in ("grep", "egrep", "fgrep") else set())
                    files = danger._positionals(c.args, option_values)
                    if c.name in ("grep", "egrep", "fgrep"):
                        explicit_pattern = any(a in ("-e", "--regexp", "-f", "--file") or a.startswith(("--regexp=", "--file=")) or a.startswith(("-e", "-f")) and len(a) > 2 for a in c.args)
                        if not explicit_pattern:
                            files = files[1:]
                        files.extend(c.args[i+1] for i,a in enumerate(c.args[:-1]) if a in ("-f", "--file"))
                        files.extend(a.split("=",1)[1] for a in c.args if a.startswith("--file="))
                        files.extend(a[2:] for a in c.args if a.startswith("-f") and len(a)>2)
                    reads.extend((path, False) for path in files)
    elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Delete"):
        writes.extend(inp.get(k) for k in ("file_path", "path", "notebook_path") if inp.get(k))
    elif tool == "apply_patch":
        writes.extend(re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", str(inp.get("command", inp.get("patch", inp.get("input", "")))), re.M))
    for path in writes:
        actual = _local_operand(path, cwd)
        if actual is None:
            continue
        if _ssh_material(actual) == "authority" or danger.hook_path(actual) or danger.service_path(actual) or approval_level._own_code("Write", {"file_path":actual}):
            return _result("approval_root", "settings", ("resolved_authority_write_operand",))
        if danger.cred_path(actual) and _ssh_material(actual) != "public":
            return _result("sensitive_storage", "credentials", ("resolved_sensitive_write_operand",))
        if not _proposal(actual, cwd) and approval_policy.aj_state_write("echo x > " + actual):
            return _result("approval_root", "settings", ("resolved_state_write_operand",))
    for path, delivery in reads:
        actual = _local_operand(path, cwd)
        if actual and danger.cred_path(actual) and _ssh_material(actual) not in ("public", "authority"):
            return _result("sensitive_delivery" if delivery else "sensitive_storage", "secret_out" if delivery else "credentials", ("resolved_sensitive_delivery_operand" if delivery else "resolved_sensitive_read_operand",))
    return None


def _shell_parameter_read(text):
    """Track shell quote boundaries, independently of interpreter string syntax."""
    from . import approval_policy, danger
    command_text, docs = danger.heredocs(text)
    active, single, double, i = [], False, False, 0
    while i < len(command_text):
        c = command_text[i]
        if c == "\\" and not single:
            i += 2
            continue
        if c == "'" and not double:
            single = not single
        elif c == '"' and not single:
            double = not double
        elif not single:
            active.append(c)
        i += 1
    expanded = ["".join(active)] + [body for _kind, body, expands in docs if expands]
    return any(approval_policy.secret_name(n) for part in expanded for n in approval_policy._KEYREF.findall(part)) or any("${!" in part for part in expanded)


def _sed_operand_view(text, depth=0):
    """Separate a literal nonexecuting substitution program from its file args."""
    import shlex
    from . import danger, goal
    commands = danger.lex(text)
    if len(commands) != 1 or commands[0].subst or commands[0].docs:
        return None
    command = commands[0]
    if depth < 4 and command.words and danger._base(command.words[0]) == "ssh":
        words, k = list(command.words), 1
        while k < len(words) and words[k].startswith("-"):
            k += 2 if words[k] in danger._SSH_ARG else 1
        if k + 1 >= len(words):
            return None
        remote = " ".join(words[k+1:])
        remote_nodes = danger.lex(remote)
        # Remote output stays data only when every consumer is nonexecuting.
        # Do not erase a producer feeding xargs, a shell, or another code sink.
        if any(c.subst or c.docs or not c.words or danger._base(c.words[0]) not in _DATA_OPERATIONS | {"sed"} for c in remote_nodes):
            return None
        changed = False
        for piece in goal.pieces(remote) or []:
            node = danger.lex(piece)
            if len(node) == 1 and node[0].words and danger._base(node[0].words[0]) == "sed":
                view = _sed_operand_view(piece, depth+1)
                if isinstance(view, Assessment):
                    return view
                if view is None:
                    return None
                remote = remote.replace(piece, view, 1)
                changed = True
        if not changed:
            return None
        view = shlex.join(words[:k+1] + [remote])
        view += "".join(" < " + shlex.quote(p) for p in command.ins)
        view += "".join(" > " + shlex.quote(p) for p in command.redirs if p not in command.ins)
        return view
    if not command.words or danger._base(command.words[0]) != "sed":
        return None
    words = list(command.words)
    k = 1
    while k < len(words) and words[k] in ("-E", "-r", "-n", "--regexp-extended", "--quiet", "--silent", "-i"):
        k += 1
    if k < len(words) and words[k] == "--":
        k += 1
    if k >= len(words):
        return None
    script = words[k]
    if len(script) < 4 or script[0] != "s" or script[1].isalnum() or script[1].isspace() or re.search(r"\$(?:\{|[A-Za-z_])|`|\n", script):
        return None
    delimiter, sections, i = script[1], 0, 2
    while i < len(script) and sections < 2:
        if script[i] == "\\":
            i += 2
            continue
        if script[i] == delimiter:
            sections += 1
        i += 1
    if sections == 2 and re.match(r"[gIp0-9]*e[gIpe0-9]*(?=\s|;|$)", script[i:]):
        return _result("approval_root", "hooks", ("sed_executes_input_data",))
    if sections != 2 or not re.fullmatch(r"[gIp0-9]*", script[i:]):
        return None  # e/w commands or extra statements remain executable code
    words[k] = "s/DATA/DATA/" + script[i:]
    view = shlex.join(words)
    view += "".join(" < " + shlex.quote(p) for p in command.ins)
    view += "".join(" > " + shlex.quote(p) for p in command.redirs if p not in command.ins)
    return view


def _data_code_boundary(text, depth=0):
    """Actual xargs replacement data used as a shell program, including SSH."""
    from . import danger
    if depth >= 8:
        return False
    for command in danger.lex(text):
        words = command.words
        if not words:
            continue
        name = danger._base(words[0])
        if name == "ssh":
            k = 1
            while k < len(words) and words[k].startswith("-"):
                k += 2 if words[k] in danger._SSH_ARG else 1
            if k + 1 < len(words) and _data_code_boundary(" ".join(words[k+1:]), depth+1):
                return True
        if name != "xargs":
            continue
        binder, k = None, 1
        long_values = {"--arg-file", "--delimiter", "--eof", "--max-lines", "--max-args", "--max-procs", "--max-chars", "--process-slot-var"}
        while k < len(words) and words[k].startswith("-"):
            arg = words[k]
            if arg == "--":
                k += 1
                break
            if arg == "--replace":
                binder = "{}"  # GNU optional argument is attached with '='
            elif arg.startswith("--replace="):
                binder = arg.split("=", 1)[1]
            elif arg in long_values:
                k += 1
            elif not arg.startswith("--"):
                letters, j = arg[1:], 0
                while j < len(letters):
                    option = letters[j]
                    if option in "Ii":
                        if letters[j+1:]:
                            binder = letters[j+1:]
                        elif option == "i":
                            binder = "{}"
                        elif k+1 < len(words):
                            k += 1
                            binder = words[k]
                        break
                    if option in "adELnPs":
                        if not letters[j+1:]:
                            k += 1
                        break
                    if option in "el":  # historical attached optional args
                        break
                    j += 1
            k += 1
        # Only the actual executable is a program. Later argv words may be
        # literal printf/grep data that merely happen to say 'sh -c {}'.
        if k >= len(words) or danger._base(words[k]) not in ("sh", "bash", "zsh", "dash"):
            continue
        j = k+1
        while j < len(words) and words[j].startswith("-"):
            if words[j].startswith("-") and not words[j].startswith("--") and "c" in words[j][1:]:
                if j+1 == len(words) or binder and binder in words[j+1]:
                    return True
                break
            if words[j] == "--":
                break
            if words[j] in ("--rcfile", "--init-file", "-o", "-O"):
                j += 1
            j += 1
    return False


def _python_file_program(text, cwd):
    """Prove a literal local file/text transformation from its AST, or abstain.

    String data never supplies a storage target. Only literal/assigned paths at
    open/Path call sites do. Unsupported syntax/calls abstain to normal analysis;
    this evaluator never executes code, imports a module, or reads file contents.
    """
    import ast
    from . import danger
    commands = danger.lex(text)
    if len(commands) != 1:
        return None
    command = commands[0]
    if command.subst or command.ins or command.outs or not command.words:
        return None
    words = command.words
    if not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", danger._base(words[0])):
        return None
    script_args=[]
    if len(words) >= 3 and words[1] == "-c":
        program = words[2]
        script_args=words[3:]
    elif (len(words) == 1 or len(words) >= 2 and words[1] == "-") and len(command.docs) == 1:
        script_args=words[2:]
        program = command.docs[0][2]
        if not command.docs[0][1] and any(c in program for c in "$`"):
            return None  # shell expands unquoted stdin before Python parses it
    else:
        return None
    if any(any(c in arg for c in "$`*?[]{\n") for arg in script_args):
        return None
    values, effects = {}, []
    stdin_used=[]
    storage_used=[]
    execution_used=[]
    class Unknown(Exception):
        pass
    def storage(path, write=False):
        storage_used.append(True)
        if not isinstance(path, str):
            raise Unknown()
        actual = _local_operand(path, cwd)
        if actual is None:
            raise Unknown()
        if write:
            effect = _file_operand_effect("Write", {"file_path": actual}, "", cwd)
        else:
            effect = (_result("sensitive_storage", "credentials", ("python_file_read_operand",))
                      if danger.cred_path(actual) and _ssh_material(actual) not in ("public", "authority") else None)
        if effect:
            effects.append(effect)
    def expr(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, bool, type(None))):
            return ("text", node.value) if isinstance(node.value, str) else ("scalar", node.value)
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, ast.Attribute):
            receiver=expr(node.value)
            if receiver == ("module", "os") and node.attr == "path":
                return ("module", "os.path")
            if receiver == ("module", "sys") and node.attr == "stdin":
                stdin_used.append(True)
                return ("stdin",None)
            if receiver == ("module", "sys") and node.attr == "argv":
                return ("argv", ["-", *script_args])
            if receiver == ("module", "datetime") and node.attr in ("datetime", "timezone"):
                return ("module", "datetime."+node.attr)
            if receiver == ("module", "datetime.timezone") and node.attr == "utc":
                return ("scalar", None)
            raise Unknown()
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    expr(value.value)
                    if value.format_spec is not None: expr(value.format_spec)
                else: expr(value)
            return ("text", None)
        if isinstance(node, ast.IfExp):
            expr(node.test);expr(node.body);expr(node.orelse)
            return ("data", None)
        if isinstance(node, ast.Name) and node.id in ("str", "int", "float", "dict", "list", "tuple", "bool"):
            return ("builtin_type", node.id)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for value in node.elts: expr(value)
            return ("data", None)
        if isinstance(node, ast.Dict):
            for value in [*node.keys, *node.values]:
                if value is not None: expr(value)
            return ("data", None)
        if isinstance(node, ast.Slice):
            for part in (node.lower,node.upper,node.step):
                if part is not None: expr(part)
            return ("scalar", None)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            saved=dict(values)
            try:
                for generator in node.generators:
                    if generator.is_async: raise Unknown()
                    iterable=expr(generator.iter)
                    if iterable[0] not in ("data", "text"): raise Unknown()
                    bind(generator.target, ("data", None))
                    for condition in generator.ifs: expr(condition)
                if isinstance(node,ast.DictComp):
                    expr(node.key);expr(node.value)
                else:expr(node.elt)
            finally:
                values.clear(); values.update(saved)
            return ("data", None)
        if isinstance(node, ast.Subscript):
            receiver=expr(node.value)
            if receiver[0] == "argv":
                if not isinstance(node.slice, ast.Constant) or type(node.slice.value) is not int or not 0 <= node.slice.value < len(receiver[1]): raise Unknown()
                return ("text", receiver[1][node.slice.value])
            if receiver[0] in ("data", "text", "counter"):
                expr(node.slice)
                return ("data", None)
            raise Unknown()
        if isinstance(node, ast.Compare):
            expr(node.left)
            for other in node.comparators: expr(other)
            return ("scalar", None)
        if isinstance(node, ast.BoolOp):
            for value in node.values: expr(value)
            return ("data", None)
        if isinstance(node, ast.UnaryOp):
            expr(node.operand)
            return ("scalar", None)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = expr(node.left), expr(node.right)
            if left[0] == right[0] == "text":
                return ("text", left[1] + right[1] if left[1] is not None and right[1] is not None else None)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = expr(node.left), expr(node.right)
            if left[0] == "path" and right[0] == "text" and right[1] is not None:
                return ("path", os.path.join(left[1], right[1]))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sorted":
            # Only a structural tuple/dict-key selector; no callable body can
            # hide reads, writes, imports or callbacks in this diagnostic sort.
            if len(node.args) != 1 or len(node.keywords) > 1: raise Unknown()
            if expr(node.args[0])[0] not in ("data", "text"): raise Unknown()
            if node.keywords:
                keyword = node.keywords[0]
                key = keyword.value
                if keyword.arg != "key" or not isinstance(key, ast.Lambda): raise Unknown()
                args = key.args
                if (len(args.args) != 1 or args.posonlyargs or args.kwonlyargs or args.defaults
                        or args.kw_defaults or args.vararg or args.kwarg): raise Unknown()
                selector = key.body
                while isinstance(selector, ast.Subscript):
                    index = selector.slice
                    if not isinstance(index, ast.Constant) or type(index.value) not in (str, int): raise Unknown()
                    selector = selector.value
                if not isinstance(selector, ast.Name) or selector.id != args.args[0].arg: raise Unknown()
            return ("data", None)
        if isinstance(node, ast.Call):
            # Fixed formatting options only. Callbacks, custom JSON classes and
            # arbitrary opener functions never enter this data-only adapter.
            allowed=("encoding", "errors", "newline")
            if node.keywords and isinstance(node.func, ast.Attribute) and expr(node.func.value) == ("module", "json"):
                allowed=("ensure_ascii", "sort_keys", "indent", "allow_nan", "skipkeys", "strict")
            if any(k.arg not in allowed or not isinstance(k.value, ast.Constant)
                   or not isinstance(k.value.value, (str, int, bool, type(None))) for k in node.keywords):
                raise Unknown()
            args = [expr(a) for a in node.args]
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                return ("scalar", None)
            if isinstance(node.func, ast.Name) and node.func.id in ("len", "int", "float", "bool", "isinstance"):
                return ("scalar", None)
            if isinstance(node.func, ast.Name) and node.func.id == "str" and len(args) == 1:
                return ("text", args[0][1] if args[0][0] == "text" else None)
            if isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval") and len(args) == 1 and args[0][0] == "text" and args[0][1] is not None:
                execution_used.append(True)
                import shlex
                nested = _python_file_program("python3 -c " + shlex.quote(args[0][1]), cwd)
                if nested is None:
                    raise Unknown()
                if nested.reason:
                    effects.append(nested)
                return ("scalar", None)
            if isinstance(node.func, ast.Name) and node.func.id in ("open", "Path"):
                if not args or args[0][0] != "text" or args[0][1] is None:
                    raise Unknown()
                path = args[0][1]
                if node.func.id == "Path" and len(args) == 1:
                    return ("path", path)
                mode = args[1][1] if len(args) == 2 and args[1][0] == "text" else "r" if len(args) == 1 else None
                if mode not in ("r", "w", "a", "rb", "wb", "ab"):
                    raise Unknown()
                storage(path, write=mode[0] in "wa")
                return ("file", (path, mode))
            if isinstance(node.func, ast.Attribute):
                method = node.func.attr
                if (method == "home" and not args and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "Path"):
                    return ("path", os.path.expanduser("~"))
                if (method == "expanduser" and len(args) == 1 and args[0][0] == "text" and args[0][1] is not None
                        and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "path"
                        and isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == "os"):
                    return ("text", os.path.expanduser(args[0][1]))
                receiver = expr(node.func.value)
                if receiver == ("module", "os.path") and method == "exists" and len(args)==1 and args[0][0] in ("text", "path") and args[0][1] is not None:
                    return ("scalar", None)
                if receiver == ("module", "collections") and method == "Counter" and not args and not node.keywords:
                    return ("counter", None)
                if receiver == ("module", "time") and not node.keywords:
                    if method == "strptime" and len(args) == 2 and all(a[0] == "text" for a in args):
                        return ("time_tuple", None)
                    if method == "mktime" and len(args) == 1 and args[0][0] == "time_tuple":
                        return ("scalar", None)
                    if method == "localtime" and len(args) == 1 and args[0][0] in ("scalar", "data"):
                        return ("time_tuple", None)
                    if method == "strftime" and len(args) == 2 and args[0][0] == "text" and args[1][0] == "time_tuple":
                        return ("text", None)
                if receiver == ("module", "datetime.datetime") and method == "fromtimestamp" and 1 <= len(args) <= 2:
                    return ("datetime", None)
                if receiver[0] == "datetime" and method == "strftime" and len(args) == 1 and args[0][0] == "text":
                    return ("text", None)
                if receiver == ("module", "json") and method in ("dump", "dumps"):
                    if method == "dumps" and len(args) == 1: return ("text", None)
                    if method == "dump" and len(args) == 2 and args[1][0] == "file" and args[1][1][1][0] in "wa":
                        storage(args[1][1][0], write=True)
                        return ("scalar", None)
                    raise Unknown()
                if receiver == ("module", "json") and method in ("load", "loads") and len(args) == 1:
                    if method == "load":
                        if args[0][0]=="stdin":pass
                        elif args[0][0] == "file" and args[0][1][1].startswith("r"):storage(args[0][1][0])
                        else:raise Unknown()
                    elif args[0][0] != "text": raise Unknown()
                    return ("data", None)
                if receiver[0] == "counter" and method in ("items", "keys", "values") and not args and not node.keywords:
                    return ("data", None)
                if receiver[0] == "data" and method in ("get", "items", "keys", "values"):
                    return ("data", None)
                if receiver[0] == "file" and method in ("read", "write", "close"):
                    path, mode = receiver[1]
                    if method == "read" and not args and mode.startswith("r"):
                        storage(path)
                        return ("text", None)
                    if method == "write" and len(args) == 1 and args[0][0] == "text" and mode[0] in "wa":
                        storage(path, write=True)
                        return ("scalar", None)
                    if method == "close" and not args:
                        return ("scalar", None)
                if receiver[0] == "path":
                    if method == "expanduser" and not args:
                        return ("path", os.path.expanduser(receiver[1]))
                    if method in ("read_text", "read_bytes") and not args:
                        storage(receiver[1])
                        return ("text", None)
                    if method in ("write_text", "write_bytes") and len(args) == 1 and args[0][0] == "text":
                        storage(receiver[1], write=True)
                        return ("scalar", None)
                if receiver[0] == "text" and method in ("replace", "strip", "lstrip", "rstrip") and all(a[0] in ("text", "scalar") for a in args):
                    return ("text", None)
        raise Unknown()
    def bind(target, value):
        if isinstance(target, (ast.Tuple, ast.List)) and value[0] in ("data", "text"):
            for item in target.elts: bind(item, ("data", None))
            return
        if not isinstance(target, ast.Name): raise Unknown()
        values[target.id] = value
    def merge_states(states):
        keys=set().union(*(state.keys() for state in states))
        values.clear()
        for key in keys:
            options=[state.get(key) for state in states]
            values[key]=options[0] if all(option==options[0] for option in options) and options[0] is not None else ("data", None)
    def statements(body):
        for statement in body:
            if isinstance(statement, ast.Import) and all(n.name in ("os", "json", "sys", "datetime", "time", "collections") and not n.asname for n in statement.names):
                for item in statement.names: values[item.name] = ("module", item.name)
                continue
            if isinstance(statement, ast.ImportFrom) and statement.module == "pathlib" and len(statement.names) == 1 and statement.names[0].name == "Path" and not statement.names[0].asname:
                continue
            if isinstance(statement, ast.Assign) and all(isinstance(t, ast.Name) for t in statement.targets):
                value = expr(statement.value)
                for target in statement.targets: bind(target, value)
            elif isinstance(statement, ast.AugAssign):
                target = statement.target
                if (not isinstance(statement.op, ast.Add) or not isinstance(target, ast.Subscript)
                        or expr(target.value)[0] != "counter" or not isinstance(statement.value, ast.Constant)
                        or type(statement.value.value) is not int): raise Unknown()
                expr(target.slice)
            elif isinstance(statement, ast.Expr): expr(statement.value)
            elif isinstance(statement, ast.For):
                iterable = expr(statement.iter)
                if iterable[0] not in ("file", "data", "text"): raise Unknown()
                if iterable[0] == "file" and not iterable[1][1].startswith("r"): raise Unknown()
                before=dict(values)
                bind(statement.target, ("text", None) if iterable[0] == "file" else ("data", None))
                statements(statement.body); statements(statement.orelse)
                merge_states([before,dict(values)])
            elif isinstance(statement, ast.If):
                expr(statement.test)
                before=dict(values)
                statements(statement.body); yes=dict(values)
                values.clear(); values.update(before)
                statements(statement.orelse)
                merge_states([yes,dict(values)])
            elif isinstance(statement, ast.Try):
                before=dict(values)
                statements(statement.body); statements(statement.orelse)
                branches=[dict(values)]
                for handler in statement.handlers:
                    values.clear(); values.update(before)
                    if handler.type is not None and not (isinstance(handler.type, ast.Name) and handler.type.id in ("Exception", "ValueError", "TypeError", "KeyError", "OSError") or isinstance(handler.type, ast.Attribute) and handler.type.attr == "JSONDecodeError" and isinstance(handler.type.value, ast.Name) and values.get(handler.type.value.id) == ("module", "json")):
                        raise Unknown()
                    if handler.name: values[handler.name] = ("data", None)
                    statements(handler.body)
                    branches.append(dict(values))
                merge_states(branches)
                statements(statement.finalbody)
            elif isinstance(statement, (ast.Continue, ast.Break, ast.Pass)): continue
            else: raise Unknown()
    try:
        tree = ast.parse(program)
        statements(tree.body)
    except (Unknown, SyntaxError, ValueError, TypeError, RecursionError):
        return None
    return effects[0] if effects else Assessment(evidence=("python_stdin_data_transform" if stdin_used and not storage_used and not execution_used else "python_literal_file_transform",))


def assess(tool, tool_input=None, *, env=None, cwd=None, registered_dispatch=False):
    """Return the single UV reason, or None for ordinary automatic execution.

    registered_dispatch is an internal proof result from peer_origin.check_batch,
    never an input field or a command-prefix allowlist. That proof covers the
    complete command (including every adjacent statement and redirection).
    """
    from . import approval_level, approval_policy, confirm_tier, danger, goal, policy_registry
    inp = tool_input if isinstance(tool_input, dict) else {}
    operation = tool.split("__")[-1] if isinstance(tool, str) else ""
    shell = operation in ("Bash", "exec_command", "shell_command", "execute_command", "bash", "Monitor") and any(k in inp for k in ("command", "cmd"))
    if operation == "write_stdin":
        # Native terminal input executes shell statements, not a text-tool body.
        shell = True
        inp = {**inp, "command": inp.get("chars", inp.get("input", ""))}
    canonical = "Bash" if shell else tool
    text = goal.action_text(canonical, inp) if not shell else inp.get("command", inp.get("cmd", ""))
    if isinstance(text, list):
        import shlex
        text = " ".join(shlex.quote(str(w)) for w in text)
    if shell:
        if _data_code_boundary(text or ""):
            return _result("approval_root", "hooks", ("data_becomes_shell_program",))
        if _shell_parameter_read(text or ""):
            return _result("sensitive_storage", "credentials", ("shell_sensitive_parameter_read",))
        for command in danger.lex(text or ""):
            for _delimiter, quoted, body, _start, _end in command.docs:
                if not quoted:
                    for child in danger._substs(body):
                        effect = assess("Bash", {"command":child}, env=env, cwd=cwd)
                        if effect.reason is not None:
                            return effect
        proven = _python_file_program(text or "", cwd)
        if proven is not None:
            return proven
        # Keep language boundaries through composition. A proven data-only
        # interpreter statement contributes no effect; retain every surrounding
        # shell operator/statement for the independent transport/attack analysis.
        original = text or ""
        leading = [cwd]
        context_known = not any(c.subst or c.piped and (not c.words or danger._base(c.words[0]) not in _DATA_OPERATIONS) for c in danger.lex(original))
        for piece in (goal.pieces(original) or []) if context_known else []:
            nodes = danger.lex(piece)
            if len(nodes) == 1 and nodes[0].words[:1] == ["cd"] and len(nodes[0].words) == 2:
                # cd may fail or be skipped by &&/||. Preserve all possible
                # prior directories rather than treating lexical order as fact.
                moved = [_local_operand(nodes[0].words[1], path) for path in leading]
                if any(path is None for path in moved):
                    context_known = False
                leading = list(dict.fromkeys(leading + moved))
            elif any(s.name in ("cd", "pushd", "popd", "source", ".", "eval") for c in nodes for s in danger.unwrap(c)):
                context_known = False
            proofs = [_python_file_program(piece, path) for path in leading] if context_known and len(leading) <= 8 else []
            if not proofs:
                candidate=_python_file_program(piece,cwd)
                if candidate is not None and candidate.evidence==("python_stdin_data_transform",):proofs=[candidate]
            if proofs and all(proof is not None for proof in proofs):
                affected = next((proof for proof in proofs if proof.reason is not None), None)
                if affected is not None:
                    return affected
                text = text.replace(piece, "true", 1)
            elif context_known:
                view = _sed_operand_view(piece)
                if isinstance(view, Assessment):
                    return view
                if view is not None:
                    text = text.replace(piece, view, 1)
    text = danger.executable_text(text or "")
    normalized = {**inp, "command": text} if shell else inp
    # A complete host proof is the registered effect adapter for CEO dispatch.
    if registered_dispatch:
        return Assessment(evidence=("host_verified_ceo_dispatch",))
    if operation in ("js_repl", "PowerShell"):
        text = "\n".join(inp.get(k, "") for k in ("command", "cmd", "code", "chars", "input") if isinstance(inp.get(k), str))
    parts = goal.pieces(text) if shell else None
    secret_text = "\n".join(_expanding_text(piece) for piece in parts) if parts else text
    if approval_policy.shows_secret(secret_text if shell else text):
        return _result("sensitive_storage", "credentials", ("secret_output_operand",))
    operand_effect = _file_operand_effect(canonical, normalized, text, cwd)
    if operand_effect is not None:
        return operand_effect
    shared = danger.classify_shared(canonical, normalized, cwd=cwd, env=env)
    authority_write, public_only = _ssh_operands(canonical, normalized, text)
    if authority_write:
        return _result("approval_root", "settings", ("ssh_authority_write",))
    # Only credential operation evidence counts; service dispatch is not a password.
    secret_rules = [r for r in shared.rules if (r.startswith("cred.") and r not in ("cred.service", "cred.hooks"))
                    or r.startswith("shared.credential") or r == "extra.credentials"]
    # File reads are an independent effect. The legacy category classifier skips
    # its read adapter whenever any credential-change tag exists (including a
    # service transport tag), so consuming that category alone can erase a read.
    if shell:
        code = danger.code_text(text[:danger.MAX_CMD])
        shown = danger._shown_creds(code) or danger._shown_creds(danger._expand_assign(code))
        read_effect = ("shared.credential_show",) if shown else danger._shared_cred_read(text, cwd)
        if read_effect and read_effect[0] not in secret_rules:
            secret_rules.append(read_effect[0])
    # A public-file read does not erase another statement's credential effect.
    # Compose statement assessments, while still retaining the original pipeline's
    # root/exfil/deletion assessment below (splitting a pipe cannot erase | sh).
    public_composition = False
    pipeline = danger.lex(text) if shell else []
    separable = not any(c.piped or c.subst for c in pipeline) or all(
        s.name in _DATA_OPERATIONS | {"ssh", "scp", "rsync", "curl"} for c in pipeline for s in danger.unwrap(c))
    if shell and secret_rules and not public_only and parts and len(parts) > 1 and separable:
        children = [assess("Bash", {"command": piece}, env=env, cwd=cwd) for piece in parts]
        affected = next((child for child in children if child.reason is not None), None)
        if affected is not None:
            return affected
        public_composition = True
    if secret_rules and not public_only and not public_composition:
        return _result("sensitive_storage", "credentials", secret_rules)
    if shell:
        # These parsed operations cannot execute their literal operands. Storage
        # reads, expansions, substitutions and output targets were assessed above.
        # A multiline search pattern must never be re-lexed as shell commands.
        nodes = [s for c in danger.lex(text) for s in danger.unwrap(c)]
        if nodes and all(s.name in _DATA_OPERATIONS for s in nodes):
            return Assessment(evidence=("parsed_nonexecuting_operands",))
    if "cred.hooks" in shared.rules or approval_level._own_code(canonical, normalized) or _state_write(canonical, normalized, text, cwd):
        return _result("approval_root", "settings", ("protected_write_operand",))
    if canonical == "agentj config" or policy_registry.tool_action(tool) in ("device.pair", "device.revoke"):
        return _result("approval_root", "settings", ("registered_authority_mutation",))
    # Known attack forms are writes to the execution/approval boundary. Ordinary
    # opaque commands do not invent a sixth fingerprint category.
    suspect = goal.suspect(canonical, normalized, shared)
    if suspect in ("hooks", "injection") or approval_level.download_and_run(text):
        return _result("approval_root", "hooks", ("execution_boundary_mutation",))
    if "cred.service" in shared.rules:
        # A service manager is transport, not itself an approval-root effect.
        # Persisting a unit or launching a harness that explicitly removes its
        # approval boundary does alter that root. Routine service control does not.
        cmds = [s for c in danger.lex(text) for s in danger.unwrap(c)] if shell else []
        bypass = any(s.name in ("codex", "claude", "opencode") and any(a in (
            "--dangerously-bypass-approvals-and-sandbox", "--dangerously-skip-permissions", "--yolo") for a in s.args) for s in cmds)
        outputs = [p for c in danger.lex(text) for p in c.outs] if shell else [inp.get("file_path") or inp.get("path")]
        if bypass or any(danger.service_path(p) for p in outputs if isinstance(p, str)):
            return _result("approval_root", "hooks", ("approval_bypass_launch" if bypass else "persistent_unit_write",))
    if shell:
        # Evaluate destruction independently. The legacy tier chooser prioritizes
        # a network+credential *label* before deletion; discarding that unsupported
        # label must not discard an actual adjacent rm/find/remote deletion.
        operations = danger._Hits()
        danger._bash(text, operations, lines=False)
        if confirm_tier._owner_delete(text, operations.items, cwd):
            return _result("irreversible_destroy", "delete", ("parsed_destructive_operation",))
    keep = confirm_tier.harness_keeps(canonical, normalized, env=env, workdir=cwd)
    if keep in ("hooks", "settings", "credentials"):
        return _result("approval_root" if keep != "credentials" else "sensitive_storage", keep, ("protected_operand",))
    if keep == "spend":
        return _result("money", "spend", ("payment_or_price_operand",))
    if keep == "delete":
        return _result("irreversible_destroy", "delete", ("unrecoverable_delete_operand",))
    if keep == "seat":
        return _result("approval_root", "seat", ("seat_authority_mutation",))
    if keep == "secret_out" and not public_only and not public_composition and (
            "cred.exfil" in shared.rules or any(s.name == "agentj" and s.args[:1] in (["secret"], ["secrets"])
                                               for c in danger.lex(text) for s in danger.unwrap(c))):
        return _result("sensitive_delivery", "secret_out", ("secret_delivery_operand",))
    return Assessment()


# Fixed vocabulary only: never retain user-supplied program names or operands.
_SHAPE_PROGRAMS = frozenset({"cat", "echo", "printf", "tee", "systemd-run", "systemctl", "agentj", "codex", "claude", "opencode",
    "sh", "bash", "python", "python3", "node", "curl", "wget", "git", "gh", "trash-put", "trash", "gio", "rm", "rmdir", "unlink", "stripe", "sed", "head", "tail", "ls"})


def _operand_role(value, cwd=None):
    from . import danger
    if not isinstance(value, str):
        return "argument"
    if _proposal(value, cwd):
        return "goal_proposal"
    if _ssh_material(value) == "authority" or danger.hook_path(value) or danger.service_path(value):
        return "approval_root"
    if _ssh_material(value) == "public":
        return "public_material"
    if danger.cred_path(value):
        return "sensitive_store"
    if re.match(r"https?://", value):
        return "ordinary_destination"
    if ".jarvis/" in value:
        return "notes"
    if value.startswith("-"):
        return "option"
    return "ordinary_file" if "/" in value or re.search(r"\.[A-Za-z0-9]{1,8}$", value) else "argument"


_SHAPE_OPTIONS = frozenset({"-r", "-R", "-f", "-rf", "-fr", "--recursive", "--force", "--delete", "--empty",
                           "-d", "-e", "-X", "--data", "--data-binary", "-c", "--user", "--collect", "--yolo",
                           "--dangerously-bypass-approvals-and-sandbox", "--dangerously-skip-permissions"})


def receipt(tool, tool_input=None, *, env=None, cwd=None, registered_dispatch=False):
    """Versioned, value-free semantic/AST receipt for future regression collection.

    It preserves every command node (not the first 300 characters). It does not
    reconstruct executable parameters: raw command replay and semantic-shape
    replay are distinct evidence. Unknown names become `program`; arguments,
    filenames, URLs, heredoc bodies, environment names and values are not stored.
    """
    from . import danger, policy_registry
    inp = tool_input if isinstance(tool_input, dict) else {}
    effect = assess(tool, inp, env=env, cwd=cwd, registered_dispatch=registered_dispatch)
    shape = []
    operation = tool.split("__")[-1] if isinstance(tool, str) else "unknown"
    if operation in ("Bash", "exec_command", "shell_command", "execute_command", "bash", "Monitor"):
        raw = inp.get("command", inp.get("cmd", ""))
        if isinstance(raw, str):
            for command in danger.lex(danger.executable_text(raw)):
                for simple in danger.unwrap(command):
                    shape.append({"program": simple.name if simple.name in _SHAPE_PROGRAMS else "program",
                                  "argc": len(simple.args), "inputs": len(simple.ins or []),
                                  "outputs": len(command.outs or []), "substitution": bool(command.subst),
                                  "operands": ["literal_data" if simple.name in ("echo", "printf") else _operand_role(a, cwd) for a in simple.args],
                                  "input_roles": [_operand_role(a, cwd) for a in (simple.ins or [])],
                                  "output_roles": [_operand_role(a, cwd) for a in (command.outs or [])],
                                  "options": [a for a in simple.args if a in _SHAPE_OPTIONS]})
    action = policy_registry.tool_action(tool)
    return {"schema": 1, "action": action if action in policy_registry.REGISTRY else "unknown",
            "effects": list(effect.effects), "reason": effect.reason,
            "evidence": list(effect.evidence), "commands": shape,
            "parameters_retained": False}
