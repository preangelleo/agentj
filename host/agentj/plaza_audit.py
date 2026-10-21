"""Static audit of a plaza package (P426 #181-2) — deterministic rules, zero model.

One rule table, `plaza_audit_rules.json` next to this file, shared with the Dashboard (dashboard/src/plaza_audit.ts has its own
matcher over the same JSON). Every file of the package and every manifest string is matched line by line; a file that is not
valid UTF-8 is still scanned (bytes that do not decode become U+FFFD — one stray 0xFF must not hide a whole script, security
review S2), and a program-like file (under scripts/ cli/ tools/ bin/, a script extension, or starting with #!) that does not
decode is itself a block (`undecodable-script`, built in here and in plaza_audit.ts, not in the table). Two levels:
  block — refused: `agentj plaza publish` does not send it, the Dashboard upload rejects it, the operator does not certify it,
          `agentj plaza install` does not install it (Jarvis ruling 3, P426: a valid Agent J signature does not exempt it — the
          owner may still allow that one install with a signed phone card that lists every block finding);
  warn  — shown in the publish receipt and the install preview; the owner decides.
What it catches: remote code run straight from the network (curl … | sh, iex(irm …)), decode-and-run, reading SSH keys / cloud
credentials / the keychain / browser cookies, sending all environment variables or local files out, wiping the home folder,
and words written to steer the AI (中英). It reads text, never runs anything; a determined author can still hide behaviour in
code it cannot read — the audit raises the bar, the owner's yes and the sandboxed install.verify stay.

Standard library only (the operator tools load this file by path).
"""
from __future__ import annotations

import json
import pathlib
import re

RULES_FILE = pathlib.Path(__file__).resolve().with_name("plaza_audit_rules.json")
MAX_FINDINGS = 200
EXCERPT = 80
_rules = None
# security review S2: a program the reviewer cannot read is refused (mirrors plaza_perms.EXEC_DIRS / SCRIPT_EXT; this file loads
# by path, so no imports from the package)
EXEC_DIRS = frozenset(("scripts", "cli", "tools", "bin"))
SCRIPT_EXT = (".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1", ".php", ".lua")
UNDECODABLE = {"rule": "undecodable-script", "level": "block",
               "zh": "这是一个会被运行的脚本 / 程序，但它不是正常的 UTF-8 文本，人和审计都读不全它，所以不收",
               "en": "a script / program that would be run, but it is not valid UTF-8 text, so neither a person nor the audit can "
                     "read all of it: refused"}


def program_like(path: str, data: bytes) -> bool:
    """A file meant to be run: under scripts/ cli/ tools/ bin/ (top level or scaffold/<dir>/), a script extension, or #!."""
    segs = path.split("/")
    return (segs[0] in EXEC_DIRS or (segs[0] == "scaffold" and len(segs) > 2 and segs[1] in EXEC_DIRS)
            or path.lower().endswith(SCRIPT_EXT) or data[:2] == b"#!")


def load_rules(path=None) -> list[dict]:
    """[{id, level, zh, en, rx: [compiled]}] in table order (cached for the default file)."""
    global _rules
    if path is None and _rules is not None:
        return _rules
    d = json.loads(pathlib.Path(path or RULES_FILE).read_text(encoding="utf-8"))
    out = []
    for r in d["rules"]:
        flags = re.I if "i" in r.get("flags", "") else 0
        out.append({"id": r["id"], "level": r["level"], "zh": r["zh"], "en": r["en"],
                    "rx": [re.compile(p, flags) for p in r["patterns"]]})
    if path is None:
        _rules = out
    return out


def cases(path=None) -> list[dict]:
    return json.loads(pathlib.Path(path or RULES_FILE).read_text(encoding="utf-8"))["cases"]


def first_hit(text: str, rules=None) -> str | None:
    """The id of the first rule (table order) any line of `text` matches, or None (the vector semantics)."""
    for r in rules or load_rules():
        for line in text.split("\n"):
            if any(rx.search(line) for rx in r["rx"]):
                return r["id"]
    return None


def _manifest_strings(m, path="manifest.json"):
    if isinstance(m, str):
        yield path, m
    elif isinstance(m, list):
        for i, x in enumerate(m):
            yield from _manifest_strings(x, f"{path}[{i}]")
    elif isinstance(m, dict):
        for k, v in m.items():
            if path == "manifest.json" and k == "files":
                continue
            yield from _manifest_strings(v, f"{path}.{k}" if path != "manifest.json" else f"manifest.json:{k}")


def _scan(where: str, text: str, rules, out: list) -> None:
    for n, line in enumerate(text.split("\n"), 1):
        for r in rules:
            m = next((m for rx in r["rx"] for m in [rx.search(line)] if m), None)
            if m is not None:
                a = max(0, m.start() - 20)
                out.append({"rule": r["id"], "level": r["level"], "path": where, "line": n, "zh": r["zh"], "en": r["en"],
                            "excerpt": line[a:a + EXCERPT].strip()})


def audit(files: dict, manifest: dict | None = None, rules=None) -> list[dict]:
    """Every finding: {rule, level, path, line, zh, en, excerpt} — block first, then by path / line; ≤ MAX_FINDINGS."""
    rules = rules or load_rules()
    out: list = []
    for where, text in _manifest_strings(manifest or {}):
        _scan(where, text, rules, out)
    for p in sorted(files, key=lambda x: x.encode()):
        data = files[p]
        if not isinstance(data, (bytes, bytearray)):
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            if program_like(p, bytes(data)):
                out.append({**UNDECODABLE, "path": p, "line": data.count(b"\n", 0, e.start) + 1, "excerpt": ""})
            text = data.decode("utf-8", "replace")
        _scan(p, text, rules, out)
    out.sort(key=lambda f: (f["level"] != "block", f["path"].encode(), f["line"]))
    return out[:MAX_FINDINGS]


def rule_text(rule_id: str):
    """(zh, en) of a rule id from the table (or the built-in undecodable one); None = not a rule we know."""
    if rule_id == UNDECODABLE["rule"]:
        return UNDECODABLE["zh"], UNDECODABLE["en"]
    r = next((r for r in load_rules() if r["id"] == rule_id), None)
    return (r["zh"], r["en"]) if r else None


def blocked(findings: list) -> list:
    return [f for f in findings if f["level"] == "block"]


def lines(findings: list, limit: int = 30) -> list[str]:
    """One plain line per finding, author text kept out of the sentence (the excerpt is printed by the caller, fenced)."""
    out = []
    for f in findings[:limit]:
        tag = "拒绝 BLOCK" if f["level"] == "block" else "注意 WARN"
        out.append(f"[{tag}] {f['path']} 第 {f['line']} 行 line {f['line']}：{f['zh']} / {f['en']}")
    if len(findings) > limit:
        out.append(f"……还有 {len(findings) - limit} 处 / {len(findings) - limit} more")
    return out
