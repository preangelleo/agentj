"""Resolve harness executables without ever executing a mise/asdf shim.

Only known version-manager wrappers are bypassed. Explicit executable overrides
remain authoritative. Claude manager installs default to the highest executable version;
explicit manager selections remain authoritative. No subprocess is used during resolution.
"""
from pathlib import Path
import os
import re
import shutil
import tomllib

NAMES = ("claude", "codex", "opencode", "kimi", "qwen", "dsh")
ENV = {n: "AGENTJ_" + n.upper() + "_BIN" for n in (*NAMES, "gemini")}


def wrapper(path):
    try:
        p = Path(path)
        with p.open("rb") as f:
            data = f.read(8192)
        if data.startswith(b"#!") and re.search(rb"\b(?:mise|asdf)(?:[ /\\]|$)", data):
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


def version_sibling(a, b):
    """P282: the same install tree, another version — the paths differ in exactly one component and both differing
    components carry a digit (…/releases/0.156.0-aarch64-apple-darwin/bin/codex vs …/releases/0.162.1-…/bin/codex;
    …/claude/versions/2.1.283 vs …/2.1.290; …/mise/installs/codex/0.156.0/… vs …/0.162.1/…)."""
    pa, pb = Path(a).parts, Path(b).parts
    if len(pa) != len(pb):
        return False
    diff = [(x, y) for x, y in zip(pa, pb) if x != y]
    return len(diff) == 1 and all(re.search(r"\d", c) for c in diff[0])


def resolve(name, environ=None):
    """P282: a saved path (AGENTJ_<NAME>_BIN, e.g. the one `agentj service install` wrote) that a self-updating harness
    has left behind is not used: missing → reason "pin_missing", another version of the same install tree than the one
    PATH finds now → "pin_stale" (Mac seat 10-14: the service kept codex 0.156.0's release folder after codex updated
    itself to 0.162.1). Both fall back to what PATH resolves; `pinned` keeps the saved path for the report."""
    e = os.environ if environ is None else environ
    if e.get(ENV[name]) or e.get(ENV[name].replace("AGENTJ_", "AGENTJARVIS_")):
        r = _resolve(name, e)
        if r["reason"] in ("explicit", "missing"):
            fresh = _resolve(name, {k: v for k, v in e.items() if k not in (ENV[name], ENV[name].replace("AGENTJ_", "AGENTJARVIS_"))})
            pin = r["path"] or (str(Path(r["requested"]).absolute()) if r["requested"] else None)
            manager_pin = name == "claude" and fresh["wrapper"] and r["path"] is not None
            if not manager_pin and fresh["reason"] != "ambiguous_install" and fresh["path"] and pin and pin != fresh["path"] and version_sibling(pin, fresh["path"]):
                return {**fresh, "reason": "pin_missing" if r["path"] is None else "pin_stale", "pinned": r["requested"], "via": fresh["reason"]}
        return r
    return _resolve(name, e)


def _resolve(name, e):
    override = e.get(ENV[name]) or e.get(ENV[name].replace("AGENTJ_", "AGENTJARVIS_"))
    home = Path(e.get("HOME", str(Path.home())))
    original = (str(home / override[2:]) if override.startswith("~/") else override) if override else shutil.which(name, path=e.get("PATH", ""))
    # P454: the official v1/v2 installer writes ~/.opencode/bin. Its child shell
    # cannot refresh the caller/service PATH; still honour explicit and PATH selections.
    if not original and not override and name == "opencode":
        native = home / ".opencode/bin/opencode"
        if native.is_file() and os.access(native, os.X_OK):
            original = str(native)
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
        if name == "claude" and options:
            result.update(reason="ambiguous_install", hint="Set AGENTJ_CLAUDE_BIN to an executable, or select a version with mise / asdf / 用 AGENTJ_CLAUDE_BIN 或 mise / asdf 选定版本")
            # Numeric install-folder versions, never lexical (2.1.308 > 2.1.97).
            # Do not override a manager selection that names a missing version.
            ranked = []
            for path in chosen if not selected else []:
                parts = Path(path).parts
                version = next((parts[i + 2] for i in range(len(parts) - 2)
                                if parts[i:i + 2] == ("installs", name)), "")
                m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", version)
                if m:
                    ranked.append((tuple(map(int, m.groups())), path))
            if ranked:
                result["path"] = max(ranked)[1]
    return result
