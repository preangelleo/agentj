"""#562: value-free activity labels projected without changing the existing approval decision.

Presentation only. This module never grants authority or classifies a request for
execution. In particular a service launch is not a credential effect merely
because the old danger display category used ``cred.service``.
"""
from pathlib import PurePosixPath

_EFFECT = {
    "sensitive_storage": ("访问或修改密码、密钥", "Access or change passwords or keys", "credentials"),
    "sensitive_delivery": ("传出密码或密钥", "Transfer passwords or keys", "credentials"),
    "money": ("付款或交易", "Make a payment or trade", "spend"),
    "irreversible_destroy": ("不可恢复地删除或覆盖", "Delete or overwrite irrecoverably", "delete"),
    "approval_root": ("更改授权或配对设置", "Change approval or pairing settings", "hooks"),
}
_ORDINARY = {
    "redispatch": ("重新派出后台活", "Dispatch the background job again"),
    "dispatch": ("派出后台活", "Dispatch a background job"),
    "stop": ("停止后台活", "Stop a background job"),
    "dispatch_file": ("写派单文件", "Write a job brief"),
    "write": ("写文件", "Write files"),
    "read": ("读文件", "Read files"),
    "run": ("执行普通命令", "Run ordinary commands"),
    "step": ("执行已授权操作", "Run an authorized operation"),
}


def _brief(path):
    name = PurePosixPath(path).name
    return name.startswith(("PROMPT-", "RESUME-")) and name.endswith(".md")


def _systemctl_stop(args):
    # Presentation grammar only. Stop at the command, before any unit names;
    # unsupported options get the generic command label instead of a guess.
    flags = {"--user", "--system", "--no-block", "--quiet", "-q", "--wait", "--no-ask-password", "--no-pager", "--runtime", "--force", "-f"}
    values = {"--host", "-H", "--machine", "-M", "--root", "--image", "--property", "-p", "--type", "-t", "--state", "--job-mode", "--signal", "-s", "--kill-whom", "--preset-mode", "--output", "-o", "--lines", "-n"}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            return args[i + 1:i + 2] == ["stop"]
        if not arg.startswith("-"):
            return arg == "stop"
        key, eq, _ = arg.partition("=")
        if arg in flags:
            i += 1
        elif key in values:
            i += 1 if eq else 2
        else:
            return False
    return False


def presentation(receipt, tool, inp, *, lang="zh"):
    """Return only fixed human labels, categories and closed effects; no operands."""
    index = 1 if lang == "en" else 0
    effects = [effect for effect in receipt.get("effects", ()) if effect in _EFFECT]
    if effects:
        return {"summary": " · ".join(_EFFECT[e][index] for e in effects),
                "cats": list(dict.fromkeys(_EFFECT[e][2] for e in effects)), "effects": effects}
    from . import danger
    operation = tool.split("__")[-1] if isinstance(tool, str) else ""
    labels = []
    if operation in ("Bash", "exec_command", "shell_command", "execute_command", "bash", "Monitor"):
        text = inp.get("command", inp.get("cmd", ""))
        launch, stop, brief, write, read = False, False, False, False, False
        if isinstance(text, str):
            for command in danger.lex(danger.executable_text(text)):
                write = write or bool(command.outs)
                brief = brief or any(_brief(path) for path in command.outs)
                for simple in danger.unwrap(command):
                    launch = launch or simple.name == "systemd-run"
                    stop = stop or (simple.name == "systemctl" and _systemctl_stop(simple.args))
                    read = read or simple.name in ("cat", "head", "tail", "grep", "sed", "rg")
                    if simple.name == "tee":
                        write = True
                        brief = brief or any(_brief(path) for path in simple.args if not path.startswith("-"))
        if launch:
            labels.append("redispatch" if stop else "dispatch")
        elif stop:
            labels.append("stop")
        if brief or write:
            labels.append("dispatch_file" if brief else "write")
        if not labels:
            labels.append("read" if read else "run")
    elif operation in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        labels.append("dispatch_file" if _brief(str(inp.get("file_path", inp.get("path", "")))) else "write")
    elif operation in ("Read", "Glob", "Grep"):
        labels.append("read")
    elif operation in ("Agent", "Task", "task"):
        labels.append("dispatch")
    else:
        labels.append("step")
    return {"summary": " · ".join(_ORDINARY[label][index] for label in labels), "cats": [], "effects": []}
