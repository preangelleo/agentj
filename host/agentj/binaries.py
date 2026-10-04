"""Resolve harness executables without ever executing a mise/asdf shim.

Only known version-manager wrappers are bypassed. Explicit executable overrides
remain authoritative. Ambiguous manager installs fail closed instead of picking
an unrelated version. No subprocess is used during resolution.
"""
from pathlib import Path
import os
import re
import shutil
import tomllib

NAMES = ("claude", "codex", "opencode")
ENV = {n: "AGENTJ_" + n.upper() + "_BIN" for n in NAMES}


def wrapper(path):
    try:
        p = Path(path)
        with p.open("rb") as f:
            data = f.read(8192)
        if data.startswith(b"#!") and re.search(rb"(?:mise|asdf)(?:[ /\\]|$)", data):
            return True
        return any(x in p.parts for x in ("shims",)) and any(x in p.parts for x in ("mise", "asdf"))
    except OSError:
        return False


def installations(name, environ=None):
    e = os.environ if environ is None else environ
    home = Path(e.get("HOME", str(Path.home())))
    paths = [Path(d) / name for d in e.get("PATH", "").split(os.pathsep) if d]
    paths += [home / ".opencode/bin" / name] if name == "opencode" else []
    for manager in (Path(e.get("MISE_DATA_DIR", str(home / ".local/share/mise"))),
                    Path(e.get("ASDF_DATA_DIR", str(home / ".asdf")))):
        folder = manager / "installs" / name
        if folder.is_dir():
            for ver in sorted(folder.iterdir()):
                paths += [ver / name, ver / "bin" / name]
    explicit = e.get(ENV[name]) or e.get(ENV[name].replace("AGENTJ_", "AGENTJARVIS_"))
    if explicit:
        paths.insert(0, Path(str(home / explicit[2:]) if explicit.startswith("~/") else explicit))
    out = []
    for p in paths:
        if p.is_file() and os.access(p, os.X_OK):
            s = str(p.absolute())
            if s not in out:
                out.append(s)
    return out


def _selected(name, e):
    home = Path(e.get("HOME", str(Path.home())))
    try:
        cfg = Path(e.get("MISE_CONFIG_FILE", str(Path(e.get("XDG_CONFIG_HOME", str(home / ".config"))) / "mise/config.toml")))
        value = tomllib.loads(cfg.read_text()).get("tools", {}).get(name)
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and isinstance(value.get("version"), str):
            return value["version"]
    except (OSError, ValueError):
        pass
    try:
        for line in (home / ".tool-versions").read_text().splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0] == name:
                return fields[1]
    except OSError:
        pass
    return None


def resolve(name, environ=None):
    e = os.environ if environ is None else environ
    override = e.get(ENV[name]) or e.get(ENV[name].replace("AGENTJ_", "AGENTJARVIS_"))
    home = Path(e.get("HOME", str(Path.home())))
    original = (str(home / override[2:]) if override.startswith("~/") else override) if override else shutil.which(name, path=e.get("PATH", ""))
    result = {"requested": original, "path": None, "wrapper": False, "reason": "missing"}
    if not original or not Path(original).is_file() or not os.access(original, os.X_OK):
        return result
    if not wrapper(original):
        result.update(path=str(Path(original).resolve()), reason="explicit" if override else "path")
        return result
    result["wrapper"] = True
    options = [p for p in installations(name, e)
               if not wrapper(p) and any(x in Path(p).parts for x in ("mise", ".asdf"))]
    selected = _selected(name, e)
    matches = [p for p in options if selected and selected in Path(p).parts]
    chosen = matches if matches else options if not selected else []
    chosen = list(dict.fromkeys(str(Path(p).resolve()) for p in chosen))
    if len(chosen) == 1:
        result.update(path=chosen[0], reason="wrapper_bypassed")
    else:
        result["reason"] = "wrapper_ambiguous" if options else "wrapper_no_install"
    return result
