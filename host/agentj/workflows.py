"""P244 (PROTOCOL §30): the five pages' read-only picture of the Agent J host — the working root's workflow folders, skills,
memory files and the Agent J constitution layer. Nothing here writes a file except the colour table <state>/workflows.json.

Safety: a name matching CRED is never listed, never counted by name and never readable (only an anonymous `hidden` count);
wf_doc / skill_get serve only (folder, name) / id pairs present in the last scan, re-resolved and confirmed inside their
folder (symlinks leaving it refused); bodies pass privacy.redact and are cut at 64 KiB.

P276: the scan is bounded (one level, SCAN_BUDGET seconds, a few entries per folder) and a WDB folder missing from the CEO
roster (the working root's documentation/ROLES.md — Agent J's CEO registry) is added once, append-only, by `register`:
the only other file this module ever writes. A folder it registered once is never re-added (the owner may delete the row)."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import threading
import time
from pathlib import Path

CRED = re.compile(r"(credential|secret|token|password|passwd|private|\.env|\.pem|\.key|id_rsa|id_ed25519)", re.I)
SKIP = frozenset((".worktrees", "node_modules", "credentials", "__pycache__", "venv", "site-packages"))
SCAN_BUDGET = 5.0          # P276: seconds for one scan; folders past it keep their cached card or wait for the next scan
PEEK = 64                  # P276: entries looked at per child folder (the only listing; a huge folder costs no more)
ENTRY_FILES = ("CLAUDE.md", "AGENTS.md")
_CELL = re.compile(r"[^|\n\r`]{1,120}")
DOC_MAX = 64 * 1024
COLORS = 16
RECLAIM = 30 * 86400
MAX_FOLDERS = 400
MAX_AGENTS, MAX_DOCS, MAX_SKILL_IDS = 60, 60, 80
W_MAX = 10 * 1024          # one W in JSON bytes; longer lists are cut (`cut: true`)
CHUNK = 11 * 1024          # = serve.CHUNK
HEAD_READ = 8192
SKILLS = Path(__file__).with_name("skills")
CORE = Path(__file__).with_name("identity")
MEM_KINDS = {"MEMORY.md": "memory", "SOUL.md": "soul", "NEXT_SESSION.md": "next"}
_CJK = re.compile(r"[㐀-鿿]")


# ------------------------------------------------------------------ small readers
def _mt(p) -> int:
    try:
        return os.stat(p).st_mtime_ns
    except OSError:
        return 0


def _head(p, n: int = HEAD_READ) -> str:
    try:
        if not stat.S_ISREG(os.stat(p).st_mode):
            return ""
        with open(p, "rb") as f:
            return f.read(n).decode("utf-8", "replace")
    except OSError:
        return ""


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _safe_file(folder: str, rel: str) -> str | None:
    """The real path of folder/rel when it is a regular file that stays inside `folder` (a symlink is followed only if its
    target is inside too); else None."""
    real_folder = os.path.realpath(folder)
    p = os.path.realpath(os.path.join(real_folder, rel))
    if not _inside(p, real_folder):
        return None
    try:
        return p if stat.S_ISREG(os.stat(p).st_mode) else None
    except OSError:
        return None


def front(text: str) -> tuple[dict, str]:
    """(simple YAML front matter: `key: value` and `aliases:` as [a, b] or `- a` lines, body)."""
    meta: dict = {}
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return meta, text
    for j in range(1, min(len(lines), 200)):
        if lines[j].strip() in ("---", "..."):
            key = None
            for ln in lines[1:j]:
                m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", ln)
                if m:
                    key, v = m.group(1), m.group(2).strip()
                    if v.startswith("[") and v.endswith("]"):
                        meta[key] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
                    elif v:
                        meta[key] = v.strip("'\"")
                    else:
                        meta[key] = []
                elif key and isinstance(meta.get(key), list) and re.match(r"^\s+-\s+", ln):
                    meta[key].append(ln.split("-", 1)[1].strip().strip("'\""))
            return meta, "\n".join(lines[j + 1:]).lstrip("\n")
    return meta, text


def sentence(s, n: int) -> str:
    from .privacy import redact
    s = re.sub(r"\s+", " ", redact(str(s or ""))).strip()
    m = re.search(r"(?<=[。！？!?])|(?<=\.)\s", s)
    if m and m.start() > 0:
        s = s[:m.start()].strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


_META: dict = {}


def _meta(p: str) -> dict:
    """Front matter of a SKILL.md, cached by (path, mtime, size): a rescan reads only what changed."""
    try:
        s = os.stat(p)
    except OSError:
        return {}
    key = (p, s.st_mtime_ns, s.st_size)
    if key not in _META:
        if len(_META) > 4096:
            _META.clear()
        _META[key] = front(_head(p))[0]
    return _META[key]


def _title(text: str, n: int = 60) -> str:
    _, body = front(text)
    for ln in body.split("\n"):
        if ln.startswith("# "):
            return sentence(ln[2:], n)
    return ""


def _plain(v: str) -> str:
    return re.sub(r"[*`_]", "", v).strip(" ：:")


def pinyin_index(*names) -> str | None:
    words = [x for x in names if isinstance(x, str) and _CJK.search(x)]
    if not words:
        return None
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        return None
    out = []
    for w in words:
        full = lazy_pinyin(w, errors=lambda s: list(s.lower()))
        ini = lazy_pinyin(w, style=Style.FIRST_LETTER, errors=lambda s: list(s.lower()))
        out += ["".join(full).replace(" ", "").lower(), "".join(ini).replace(" ", "").lower()]
    return " ".join(dict.fromkeys(out))[:200]


# ------------------------------------------------------------------ one workflow folder
def is_wdb(d: str) -> bool:
    """A Workflow Design Bible folder: CONSTITUTION + ROLES; or a STRUCTURE.json with bible_version; or (P276) a thin entry
    file + documentation/CONSTITUTION.md + one more boot document / STRUCTURE.json. Only fixed names are stat'ed."""
    con = _safe_file(d, "documentation/CONSTITUTION.md")
    if con and _safe_file(d, "documentation/ROLES.md"):
        return True
    if con and any(_safe_file(d, e) for e in ENTRY_FILES) and any(
            _safe_file(d, "documentation/" + x) for x in ("STRUCTURE.json", "IDENTITY.md", "WORKFLOW.md", "NEXT_SESSION.md")):
        return True
    try:
        sp = _safe_file(d, "documentation/STRUCTURE.json")
        return "bible_version" in json.loads((_head(sp, 65536) if sp else "") or "{}")
    except (ValueError, TypeError):
        return False


def identity(d: str) -> dict:
    out: dict = {}
    doc = os.path.join(d, "documentation")
    ip = _safe_file(d, "documentation/IDENTITY.md")
    _, body = front(_head(ip) if ip else "")
    first_para = heading = None
    for ln in body.split("\n"):
        m = re.match(r"^\s*[-*]\s+\*\*(Name|Role|Mission|Company)\*\*\s*[:：]?\s*(.*)$", ln)
        if m:
            k = {"Name": "ceo", "Mission": "one", "Company": "company", "Role": "role"}[m.group(1)]
            v = _plain(m.group(2))
            if k == "ceo":
                v = re.split(r"[。.（(——,，;；]", v, 1)[0].strip()
            out.setdefault(k, v)
        elif ln.startswith("# ") and heading is None:
            heading = _plain(re.sub(r"^#\s*(IDENTITY|身份)?\s*[—–:：-]*\s*", "", ln, flags=re.I))
        elif first_para is None and ln.strip() and not ln.startswith(("#", ">", "-", "*", "|")):
            first_para = ln.strip()
    if "ceo" not in out and heading and len(heading) <= 40:
        out["ceo"] = heading
    if "one" not in out and first_para:
        out["one"] = first_para
    try:
        sp = _safe_file(d, "documentation/STRUCTURE.json")
        sj = json.loads((_head(sp, 65536) if sp else "") or "{}")
    except (ValueError, TypeError):
        sj = {}
    if isinstance(sj, dict):
        for k in ("ceo", "company", "one"):
            if isinstance(sj.get(k), str) and sj[k].strip():
                out[k] = sj[k].strip()
    out.pop("role", None)
    return {k: sentence(v, 40 if k != "one" else 120) for k, v in out.items() if v}


def roster(root: str) -> list[list[str]]:
    """The working root's ROLES.md table rows (cells, markdown stripped)."""
    rows = []
    for ln in _head(os.path.join(root, "documentation", "ROLES.md"), 65536).split("\n"):
        if ln.strip().startswith("|") and not re.match(r"^\s*\|[\s:|-]+\|\s*$", ln):
            rows.append([_plain(c) for c in ln.strip().strip("|").split("|")])
    return rows


def _from_roster(rows, folder: str) -> dict:
    pat = re.compile(r"(^|[/\s`])" + re.escape(folder) + r"($|[/\s`])")
    for r in rows:
        if len(r) >= 2 and any(pat.search(c) for c in r[1:]) and r[0] and "CEO" not in r[0] and r[0] != folder:
            return {"ceo": sentence(r[0], 40), **({"company": sentence(r[1], 40)} if not pat.search(r[1]) else {})}
    return {}


STAFF_TITLE = ("title", "name_zh", "display_name")     # P308: the owner-language name shown instead of the slug
_TOML_KEY = re.compile(r'^(name|description|title|name_zh|display_name)\s*=\s*"((?:[^"\\\n]|\\.)*)"', re.M)


def _toml_meta(text: str) -> dict:
    """The few top-level `key = "…"` strings of a Codex agent .toml (no full parse: only the head of the file is read)."""
    out = {}
    for k, v in _TOML_KEY.findall(text):
        try:
            out.setdefault(k, json.loads(f'"{v}"'))
        except ValueError:
            out.setdefault(k, v)
    return out


def _agents(d: str) -> tuple[list, int]:
    """P308: staff = `.claude/agents/*.md` with a front-matter `name` and `.codex/agents/*.toml` with a `name` (a README or
    a note in that folder is not an employee); `title` (or name_zh / display_name) is shown instead of the slug."""
    out, hidden = {}, 0
    for sub, ext in ((".claude/agents", ".md"), (".codex/agents", ".toml")):
        base = os.path.join(d, sub)
        try:
            names = sorted(n for n in os.listdir(base) if n.endswith(ext) and not n.startswith("."))
        except OSError:
            continue
        for n in names:
            stem = n[:-len(ext)]
            if CRED.search(n):
                hidden += 1
                continue
            p = _safe_file(d, os.path.join(sub, n))
            if not p or stem in out:
                continue
            text = _head(p)
            meta = front(text)[0] if ext == ".md" else _toml_meta(text)
            if not (isinstance(meta.get("name"), str) and meta["name"].strip()):
                continue
            desc = meta.get("description")
            a = {"name": stem[:64], "desc": sentence(desc if isinstance(desc, str) else "", 80)}
            title = next((meta[k] for k in STAFF_TITLE if isinstance(meta.get(k), str) and meta[k].strip()), "")
            if title and title.strip() != stem:
                a["title"] = sentence(title, 32)
                if a["desc"].startswith(a["title"]):          # 「前端工程师，负责…」 → 「负责…」 under the title
                    a["desc"] = a["desc"][len(a["title"]):].lstrip("，,：:;；·-— ") or a["desc"]
            out[stem] = a
    return list(out.values()), hidden


def staff_count(d: str) -> int:
    return len(_agents(d)[0])


def _docs(d: str) -> tuple[list, dict, int]:
    """([{name, title}], {name: real path}, hidden) of d/documentation/*.md."""
    out, paths, hidden = [], {}, 0
    try:
        names = sorted(n for n in os.listdir(os.path.join(d, "documentation")) if n.endswith(".md") and not n.startswith("."))
    except OSError:
        return out, paths, 0
    for n in names:
        if CRED.search(n):
            hidden += 1
            continue
        p = _safe_file(d, os.path.join("documentation", n))
        if p:
            out.append({"name": n[:120], "title": _title(_head(p))})
            paths[n] = p
    return out, paths, hidden


def skill_dir(base: str, scope: str, folder: str | None, root_of: str | None, skip=()) -> tuple[list, int]:
    """[S + private "_path"] for base/*/SKILL.md; root_of = the folder a local skill must stay inside."""
    out, hidden = [], 0
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out, 0
    for n in names:
        if n.startswith((".", "_")):
            continue
        if CRED.search(n):
            hidden += 1
            continue
        if n in skip:
            continue
        sd = os.path.join(base, n)
        if scope == "global" and os.path.realpath(sd).startswith(str(SKILLS.resolve()) + os.sep):
            continue                     # a bundled skill linked into the harness folder: listed once, as bundled
        p = _safe_file(root_of, os.path.relpath(os.path.join(sd, "SKILL.md"), root_of)) if root_of else \
            (os.path.realpath(os.path.join(sd, "SKILL.md")) if os.path.isfile(os.path.join(sd, "SKILL.md")) else None)
        if not p or any(CRED.search(part) for part in Path(p).parts):
            hidden += 1
            continue
        meta = _meta(p)
        name = meta.get("name") if isinstance(meta.get("name"), str) and meta.get("name") else n
        if CRED.search(name):
            hidden += 1
            continue
        al = meta.get("aliases") if isinstance(meta.get("aliases"), list) else []
        s = {"id": f"{scope}:{folder or ''}:{name}"[:160], "name": name[:64],
             "desc": sentence(meta.get("description") if isinstance(meta.get("description"), str) else "", 160),
             "scope": scope, "_al": [str(a)[:40] for a in al if str(a).strip()][:8], "_path": p}
        s.update(zh_lines(meta, s["desc"]))
        if folder:
            s["folder"] = folder
        out.append(s)
    return out, hidden


def zh_lines(meta: dict, desc: str) -> dict:
    """P299: the owner-language line (desc_zh) and usage (use_zh) for the 技能 page — front matter description_zh /
    usage_zh, else the first mostly-Chinese sentence of an English description. Bundled skills' lines live in the
    web dictionary (p299.sk.* / p299.su.*), so an older host shows them too."""
    d, u, out = meta.get("description_zh"), meta.get("usage_zh"), {}
    if not (isinstance(d, str) and d) and isinstance(meta.get("description"), str) and len(_CJK.findall(desc)) < 6:
        d = next((x for x in re.split(r"(?<=[。！？])|(?<=[.!?])\s+", meta["description"])
                  if len(_CJK.findall(x)) >= 6 and len(_CJK.findall(x)) * 2 >= len(re.sub(r"\s", "", x))), "")
    if isinstance(d, str) and d and sentence(d, 160) != desc:
        out["desc_zh"] = sentence(d, 160)
    if isinstance(u, str) and u:
        out["use_zh"] = re.sub(r"\s+", " ", u).strip()[:200]
    return out


def settle_aliases(skills: list, table: dict) -> list[dict]:
    """Front matter first, then the bundled table. An alias equal to another skill's name or an alias already taken is
    dropped (the later one) and reported. Sets S.aliases; returns [{alias, skill, with}] conflicts."""
    names = {s["name"] for s in skills}
    taken: dict[str, str] = {}
    conflicts = []
    for s in skills:
        s.setdefault("_al", [])
    for s in skills:
        for a in list(s["_al"]) + [x for x in (table.get(s["name"]) or []) if isinstance(x, str) and x not in s["_al"]]:
            if a == s["name"]:
                continue
            other = taken.get(a) or (a if a in names else None)
            if other and other != s["id"]:
                conflicts.append({"alias": a, "skill": s["id"], "with": other})
                continue
            taken[a] = s["id"]
            s.setdefault("aliases", []).append(a)
    for s in skills:
        s.pop("_al", None)
    return conflicts


def alias_table() -> dict:
    try:
        t = json.loads((SKILLS / "aliases.json").read_text(encoding="utf-8"))
        return {k: v for k, v in t.items() if isinstance(v, list) and not k.startswith("$")}
    except (OSError, ValueError, AttributeError):
        return {}


def global_dirs(kind: str | None) -> list[str]:
    h = os.path.expanduser("~")
    claude = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(h, ".claude")
    codex = os.environ.get("CODEX_HOME") or os.path.join(h, ".codex")
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(h, ".config")
    by = {"claude": [os.path.join(claude, "skills")], "codex": [os.path.join(h, ".agents", "skills"), os.path.join(codex, "skills")],
          "opencode": [os.path.join(xdg, "opencode", "skills")],
          "kimi": [os.path.join(h, ".kimi-code", "skills"), os.path.join(h, ".agents", "skills")],   # P305
          "qwen": [os.path.join(h, ".qwen", "skills"), os.path.join(h, ".agents", "skills")],   # P310
          "dsh": [os.path.join(os.environ.get("DSH_HOME") or os.path.join(h, ".dsh"), "skills"),
                  os.path.join(h, ".agents", "skills")]}   # P329
    return by.get(kind) or [d for v in by.values() for d in v]


def common_skills(kind: str | None) -> tuple[list, int, list]:
    """(bundled + global S, hidden, alias conflicts among them) — what doctor checks too."""
    bundled, h1 = skill_dir(str(SKILLS), "bundled", None, None)
    seen = {s["name"] for s in bundled}
    glob, hidden = [], h1
    for d in global_dirs(kind):
        got, h = skill_dir(d, "global", None, None, skip=seen)
        hidden += h
        for s in got:
            if s["name"] not in seen:
                seen.add(s["name"])
                glob.append(s)
    allsk = bundled + glob
    return allsk, hidden, settle_aliases([dict(s) for s in allsk], alias_table())


def _folder_entries(root: str, protected: list[str]) -> list[str]:
    out = []
    try:
        it = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return out
    for e in it:
        n = e.name
        if n.startswith(".") or n in SKIP or CRED.search(n):
            continue
        try:
            if e.is_symlink():
                real = os.path.realpath(e.path)
                if not _inside(real, root) or real == root or not os.path.isdir(real):
                    continue
            elif not e.is_dir():
                continue
            real = os.path.realpath(e.path)
            if any(_inside(real, p) or _inside(p, real) for p in protected):
                continue
            with os.scandir(e.path) as it2:      # P276: peek, never list a huge folder whole
                kids = [k.name for _, k in zip(range(PEEK + 1), it2)]
        except OSError:
            continue
        if kids and len(kids) <= PEEK and all(k.startswith(".env") for k in kids):
            continue
        out.append(n)
        if len(out) >= MAX_FOLDERS:
            break
    return out


def _sig(d: str) -> tuple:
    entries = []
    for sub in ("documentation", ".claude/agents", ".codex/agents", ".claude/skills", ".agents/skills"):
        base = Path(d) / sub
        try:
            for p in sorted(base.iterdir()):
                if not p.name.startswith(".") and not CRED.search(p.name):
                    entries.append((str(p), _mt(p), _mt(p / "SKILL.md") if p.is_dir() else 0))
        except OSError:
            pass
    doc = os.path.join(d, "documentation")
    return tuple(entries) + tuple(_mt(os.path.join(d, x)) for x in ("", "documentation", ".claude/agents", ".codex/agents", ".claude/skills",
                                                    ".agents/skills")) + \
        tuple(_mt(os.path.join(doc, x)) for x in ("IDENTITY.md", "STRUCTURE.json", "ROLES.md", "CONSTITUTION.md", "MEMORY.md",
                                                  "SOUL.md", "NEXT_SESSION.md"))


def scan_folder(root: str, name: str, rows) -> dict:
    """W (without colour / busy) + private keys _paths (doc allow-list), _skills (S list), _mem (memory files), _first."""
    d = os.path.join(root, name)
    wdb = is_wdb(d)
    w: dict = {"folder": name, "wdb": wdb}
    if wdb:
        w.update(_from_roster(rows, name))
        w.update(identity(d))
    agents, h1 = _agents(d)
    w["staff"] = len(agents)                 # P308: before fit() cuts the list
    docs, paths, h2 = _docs(d)
    skills, h3 = [], 0
    for sub in (".claude/skills", ".agents/skills"):
        got, h = skill_dir(os.path.join(d, sub), "local", name, d, skip={s["name"] for s in skills})
        skills += got
        h3 += h
    w.update({"agents": agents, "docs": docs, "skills": [s["id"] for s in skills]})
    py = pinyin_index(w.get("ceo"), w.get("company"))
    if py:
        w["py"] = py
    if h1 + h2 + h3:
        w["hidden"] = h1 + h2 + h3
    sig = _sig(d)
    w["mtime"] = max((x if isinstance(x, int) else max(x[1:])) for x in sig) // 1_000_000_000
    mem = []
    if wdb:
        for n, kind in MEM_KINDS.items():
            if n in paths:
                mem.append({"name": n, "kind": kind, "title": next(x["title"] for x in docs if x["name"] == n),
                            "mtime": _mt(paths[n]) // 1_000_000_000})
    first = _mt(os.path.join(d, "documentation", "CONSTITUTION.md")) or _mt(os.path.join(d, "documentation", "STRUCTURE.json"))
    w.update({"_paths": paths, "_skills": skills, "_mem": mem, "_first": first})
    return fit(w)


def fit(w: dict) -> dict:
    """Bound one W: ≤ 60 agents / 60 docs / 80 skill ids, and ≤ W_MAX JSON bytes (lists halved, `cut: true`)."""
    for k, n in (("agents", MAX_AGENTS), ("docs", MAX_DOCS), ("skills", MAX_SKILL_IDS)):
        if len(w[k]) > n:
            w[k], w["cut"] = w[k][:n], True

    def size():
        return len(json.dumps({k: v for k, v in w.items() if not k.startswith("_")}, ensure_ascii=False).encode())
    while size() > W_MAX - 300 and any(w[k] for k in ("agents", "docs", "skills")):   # room for ci / lap / robot / busy
        k = max(("agents", "docs", "skills"), key=lambda x: len(json.dumps(w[x], ensure_ascii=False)))
        w[k], w["cut"] = w[k][:len(w[k]) // 2], True
    return w


# ------------------------------------------------------------------ colours (P233 §2)
def colours(st, wdb: list[dict], now: float) -> dict:
    """{folder: {ci, lap, robot, assigned_at}} for the WDB folders in this scan, persisted in <state>/workflows.json."""
    path = Path(st.root) / "workflows.json" if st is not None else None
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        data = {}
    tab = data.get("folders") if isinstance(data, dict) and isinstance(data.get("folders"), dict) else {}
    tab = {k: v for k, v in tab.items() if isinstance(v, dict) and isinstance(v.get("ci"), int) and isinstance(v.get("lap"), int)}
    before = json.dumps(tab, sort_keys=True)
    here = {w["folder"]: w for w in wdb}
    for f, v in list(tab.items()):
        if f in here:
            v.pop("gone_at", None)
        else:
            v.setdefault("gone_at", int(now))
            if now - v["gone_at"] > RECLAIM:
                del tab[f]
    for w in sorted((w for w in wdb if w["folder"] not in tab), key=lambda w: (w["_first"], w["folder"])):
        ceo = w.get("ceo")
        moved = next((f for f, v in tab.items() if "gone_at" in v and ceo and v.get("ceo") == ceo), None)
        if moved:                                    # renamed folder, same CEO: keeps its colour
            v = tab.pop(moved)
            v.pop("gone_at", None)
            tab[w["folder"]] = v
            continue
        held = {(v["lap"], v["ci"]) for v in tab.values()}
        slot = next(s for s in ((lap, ci) for lap in range(1, 1000) for ci in range(COLORS)) if s not in held)
        tab[w["folder"]] = {"ci": slot[1], "lap": slot[0], "robot": slot[1], "assigned_at": int(now)}
    for w in wdb:
        if w.get("ceo"):
            tab[w["folder"]]["ceo"] = w["ceo"]
    if path and json.dumps(tab, sort_keys=True) != before:
        tmp = path.with_suffix(".json.tmp")
        keep = {k: v for k, v in data.items() if k not in ("v", "folders")} if isinstance(data, dict) else {}
        tmp.write_text(json.dumps({**keep, "v": 1, "folders": tab}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    return {f: tab[f] for f in here}


# ------------------------------------------------------------------ P276: the CEO registry (root documentation/ROLES.md)
ROSTER_REL = "documentation/ROLES.md"
ROSTER_MAX = 256 * 1024
_ROW_WORDS = {"zh": ("手动", "定时", "扫描补登", "## 扫描补登的工作流",
                     "Agent J 扫描工作根目录时补登的工作流；只追加，不改上面的内容。删掉某一行，扫描不会再加回来。",
                     "| 工作流 | CEO 入口 | 执行方式 | 报告位置 | 状态 |"),
              "en": ("manual", "scheduled", "registered by scan", "## Workflows registered by scan",
                     "Added when Agent J scanned the working root; append-only, nothing above is changed. Delete a row and the "
                     "scan will not add it back.",
                     "| Workflow | CEO entry | Execution | Report location | Status |")}


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def registry_of(root: str) -> dict:
    """{exists: bool, names: set} — the folders the CEO registry already names: any roster-table cell naming the folder (a
    path such as `x/CLAUDE.md` counts), or a STRUCTURE.json workflows entry (id / dir)."""
    p = _safe_file(root, ROSTER_REL)
    out = {"exists": bool(p) and not os.path.islink(os.path.join(root, ROSTER_REL)), "rows": roster(root) if p else [], "names": set()}
    try:
        sp = _safe_file(root, "documentation/STRUCTURE.json")
        sj = json.loads((_head(sp, 65536) if sp else "") or "{}")
    except (ValueError, TypeError):
        sj = {}
    for w in (sj.get("workflows") if isinstance(sj, dict) and isinstance(sj.get("workflows"), list) else []):
        if isinstance(w, dict):
            for k in ("id", "dir"):
                if isinstance(w.get(k), str):
                    out["names"].add(w[k].strip("/").split("/")[-1])
    return out


def is_registered(reg: dict, folder: str) -> bool:
    if folder in reg["names"]:
        return True
    pat = re.compile(r"(^|[/\s`])" + re.escape(folder) + r"($|[/\s`])")
    return any(pat.search(c) for r in reg["rows"] for c in r)


def _row(root: str, folder: str, n: int, words) -> str:
    entry = next((f"{folder}/{e}" for e in ENTRY_FILES if _safe_file(os.path.join(root, folder), e)), "—")
    run = words[1] if _safe_file(os.path.join(root, folder), "task.json") else words[0]
    std = [folder, entry, run, f"{folder}/reports/", words[2]]
    cells = std if n == 5 else ([folder, entry] + ["—"] * max(0, n - 3) + [words[2]] if n >= 3 else [folder, entry][:max(n, 1)])
    return "| " + " | ".join(cells) + " |"


def register(root, folders, lang: str | None = None) -> dict:
    """Append one roster row per folder not yet in the CEO registry. Never creates ROLES.md, never edits a byte already in it
    (append-only, checked), refuses a symlink, a file over 256 KiB, a cell-breaking folder name or anything that looks like a
    key; writes through the wizard's lock and atomic write. {registry: bool, added: [...], refused: [...]}"""
    from . import wizard
    root = os.path.realpath(str(root))
    rel_ok = [f for f in dict.fromkeys(folders) if isinstance(f, str) and _CELL.fullmatch(f) and "/" not in f and not CRED.search(f)]
    refused = [f for f in folders if f not in rel_ok]
    reg = registry_of(root)
    if not reg["exists"]:
        return {"registry": False, "added": [], "refused": refused}
    need = [f for f in rel_ok if not is_registered(reg, f)]
    if not need:
        return {"registry": True, "added": [], "refused": refused}
    rp = Path(root) / ROSTER_REL
    try:
        with wizard._locked(Path(root)):
            cur = wizard._read_regular(rp)
            if cur is None or len(cur) > ROSTER_MAX:
                return {"registry": cur is not None, "added": [], "refused": refused + need}
            reg = registry_of(root)
            need = [f for f in need if not is_registered(reg, f)]
            if not need:
                return {"registry": True, "added": [], "refused": refused}
            text = cur.decode("utf-8", "replace")
            # the document's own language (a roster written in English stays English); an unreadable guess → the phone's
            words = _ROW_WORDS["zh" if _CJK.search(text) else "en" if re.search(r"[A-Za-z]{3}", text) or lang != "zh" else "zh"]
            lines = text.rstrip("\n").split("\n")
            if lines and lines[-1].strip().startswith("|") and lines[-1].strip().endswith("|"):
                j = len(lines) - 1
                while j > 0 and lines[j - 1].strip().startswith("|"):
                    j -= 1
                n = len(_cells(lines[j]))
                add = "\n".join(_row(root, f, n, words) for f in need)
                new = text.rstrip("\n") + "\n" + add + "\n"
            else:
                add = "\n".join(_row(root, f, 5, words) for f in need)
                new = (text.rstrip("\n") + "\n\n" + words[3] + "\n\n" + words[4] + "\n\n" + words[5] + "\n"
                       + "|" + "---|" * 5 + "\n" + add + "\n")
            if not new.startswith(text.rstrip("\n")) or wizard.secret_hits({ROSTER_REL: add.encode()}):
                return {"registry": True, "added": [], "refused": refused + need}
            wizard._write_file(rp, new.encode())
    except (OSError, wizard.WizardError):
        return {"registry": True, "added": [], "refused": refused + need}
    return {"registry": True, "added": need, "refused": refused}


def _seen_reg(st, key: str = "reg") -> dict:
    try:
        d = json.loads((Path(st.root) / "workflows.json").read_text(encoding="utf-8"))
        r = d.get(key) if isinstance(d, dict) else None
        return {k: v for k, v in r.items() if isinstance(v, int)} if isinstance(r, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _remember_reg(st, root: str, folders, now: float, key: str = "reg") -> None:
    path = Path(st.root) / "workflows.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    r = data.get(key) if isinstance(data.get(key), dict) else {}
    for f in folders:
        r.setdefault(f"{root}\0{f}", int(now))
    data.update({"v": 1, key: dict(list(r.items())[-2000:])})
    data.setdefault("folders", {})
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------------ busy (§22 items → folders)
def busy_folders(items, root: str, ceos: dict) -> set:
    """Folders with a 「在办事项」 entry inside them: the entry's cwd (its pid's working directory), report path or a path in
    its command; or its `who` is that WDB folder's CEO name."""
    out = set()
    by_ceo = {v.lower(): f for f, v in ceos.items() if v}
    for it in items or ():
        if not isinstance(it, dict):
            continue
        paths = [it.get("cwd"), it.get("report")] + [t for t in str(it.get("cmd") or "").split() if t[:1] in "/~"]
        if isinstance(it.get("pid"), int):
            try:
                paths.append(os.readlink(f"/proc/{it['pid']}/cwd"))
            except OSError:
                pass
        for p in paths:
            if isinstance(p, str) and p:
                rp = os.path.realpath(os.path.expanduser(p.strip("'\"")))
                if _inside(rp, root) and rp != root:
                    out.add(os.path.relpath(rp, root).split(os.sep)[0])
        who = it.get("who")
        if isinstance(who, str) and who.lower() in by_ceo:
            out.add(by_ceo[who.lower()])
    return out


# ------------------------------------------------------------------ P308: a CEO without staff, mentioned once per company
STAFF_HINT = ("「{f}」只有 CEO，还没有员工。可以在手机「工作流」页点「复查组织结构」，让我按它的报告和 RUN.md 提 3–7 个员工方案，"
              "你同意后再写进去。 / {f} has only a CEO and no staff yet: tap Review organization on the phone's Workflows page "
              "and I will propose 3–7 staff roles from its reports and RUN.md, written only after you agree.")


def staff_hint(st, root, folder: str, now: float | None = None) -> dict:
    """{folder, staff[, staff_hint]}: the hint line only when the folder's CEO has no staff and this company was not
    mentioned before (remembered in <state>/workflows.json `staffhint`, root + folder); never twice for one company."""
    root = os.path.realpath(str(root))
    if not isinstance(folder, str) or not _CELL.fullmatch(folder) or "/" in folder or CRED.search(folder):
        return {"folder": str(folder)[:64], "staff": None}
    d = os.path.join(root, folder)
    if not os.path.isdir(d) or not _inside(os.path.realpath(d), root):
        return {"folder": folder, "staff": None}
    n = staff_count(d)
    out = {"folder": folder, "staff": n}
    if n or st is None:
        return out
    key = f"{root}\0{folder}"
    if key in _seen_reg(st, "staffhint"):
        return out
    _remember_reg(st, root, [folder], time.time() if now is None else now, "staffhint")
    out["staff_hint"] = STAFF_HINT.format(f=folder)
    return out


# ------------------------------------------------------------------ the scanner
class Scanner:
    def __init__(self, st=None):
        self.st = st
        self.lock = threading.Lock()
        self.folders: dict[str, tuple] = {}     # name → (sig, W)
        self.allow: dict[tuple, str] = {}       # (folder, name) → real path, from the last scan
        self.skill_paths: dict[str, str] = {}
        self.conflicts: list = []
        self.last: dict | None = None
        self.mem_cache: tuple | None = None
        self.added: list = []

    def scan(self, root, kind: str | None = None, lang: str = "zh", busy_items=(), now: float | None = None) -> dict:
        with self.lock:
            return self._scan(os.path.realpath(str(root)), kind, lang, busy_items, time.time() if now is None else now)

    def _scan(self, root, kind, lang, busy_items, now):
        if self.last and self.last["root"] != root:
            self.folders = {}
            self.mem_cache = None
        protected = []
        if self.st is not None:
            from .fence import protected_paths
            protected = [os.path.realpath(p) for p in protected_paths(self.st)]
        rows = roster(root)
        names = _folder_entries(root, protected)
        cache = {}
        deadline, partial = time.monotonic() + SCAN_BUDGET, False
        for n in names:
            old = self.folders.get(n)
            if time.monotonic() > deadline:          # P276: over budget → last card (if any) now, the rest next scan
                partial = True
                if old:
                    cache[n] = old
                continue
            d = os.path.join(root, n)
            sig = _sig(d) + (_mt(os.path.join(root, "documentation", "ROLES.md")),)
            cache[n] = old if old and old[0] == sig else (sig, scan_folder(root, n, rows))
        self.folders = cache
        ws = [dict(v[1]) for v in cache.values()]
        wdb = [w for w in ws if w["wdb"]]
        reg = registry_of(root)
        for w in wdb:
            w["reg"] = is_registered(reg, w["folder"])
        col = colours(self.st, wdb, now)
        busy = busy_folders(busy_items, root, {w["folder"]: w.get("ceo") for w in wdb})
        allow, sk_paths = {}, {}
        common, hidden_sk, _ = common_skills(kind)
        skills = [dict(s) for s in common]
        mem = [self._agent_memory(root, kind)]
        for w in ws:
            if w["folder"] in col:
                c = col[w["folder"]]
                w.update({"ci": c["ci"], "lap": c["lap"], "robot": c["robot"]})
            w["busy"] = w["folder"] in busy
            for n, p in w["_paths"].items():
                allow[(w["folder"], n)] = p
            skills += [dict(s) for s in w["_skills"]]
        ws.sort(key=lambda w: (not w["busy"], not w["wdb"], (w.get("lap", 0), w.get("ci", 0)) if w["wdb"] else (0, 0), w["folder"]))
        for w in ws:
            if w["wdb"]:
                files = w["_mem"]
                mem.append({"owner": w["folder"], "files": files, "latest": max([f["mtime"] for f in files], default=0)})
        self.conflicts = settle_aliases(skills, alias_table())
        for s in skills:
            sk_paths[s["id"]] = s.pop("_path")
        rdocs, rpaths, rhidden = _docs(root)
        rdocs = rdocs[:MAX_DOCS]
        for n, p in rpaths.items():
            allow[("", n)] = p
        core_p = CORE / f"core.{'en' if lang == 'en' else 'zh'}.md"
        allow[("@core", "core")] = str(core_p)
        try:
            version = json.loads((CORE / "manifest.json").read_text())["version"]
        except (OSError, ValueError, KeyError):
            version = None
        agentj = {"core": {"name": "core", "title": _title(_head(core_p)), "lang": "en" if lang == "en" else "zh",
                           "version": version}, "docs": rdocs, "hidden": rhidden}
        items = [{k: v for k, v in w.items() if not k.startswith("_")} for w in ws]
        tail = {"root_name": os.path.basename(root) or root, "counts": {"all": len(items), "wdb": len(wdb),
                "busy": sum(1 for w in items if w["busy"])}, "skills": skills, "agentj": agentj, "memory": mem,
                "org": {"registry": reg["exists"], "ready": reg["exists"] and any(w["reg"] for w in wdb),
                        "unreg": sum(1 for w in wdb if not w["reg"]),
                        "nostaff": sum(1 for w in wdb if not w.get("staff"))}}
        if partial:
            tail["partial"] = True
        if hidden_sk:
            tail["skills_hidden"] = hidden_sk
        tail["etag"] = hashlib.sha256(json.dumps([items, tail], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        self.allow, self.skill_paths = allow, sk_paths
        self.last = {"root": root, "items": items, "tail": tail}
        return self.last

    def sweep(self, root, kind: str | None = None, lang: str = "zh", busy_items=(), now: float | None = None) -> dict:
        """P276: scan, then add every WDB folder the CEO registry does not name yet — once per folder (remembered in
        <state>/workflows.json, so a row the owner deleted stays deleted) — and rescan when a row was added."""
        now = time.time() if now is None else now
        pic = self.scan(root, kind, lang, busy_items, now)
        if not pic["tail"]["org"]["registry"]:
            return pic
        seen = _seen_reg(self.st) if self.st is not None else {}
        todo = [w["folder"] for w in pic["items"] if w["wdb"] and not w.get("reg") and f"{pic['root']}\0{w['folder']}" not in seen]
        if not todo:
            return pic
        res = register(pic["root"], todo, lang)
        if self.st is not None and res["added"]:
            _remember_reg(self.st, pic["root"], res["added"], now)
            with contextlib.suppress(Exception):
                self.st.log("wf_registered", count=len(res["added"]))
        self.added = res["added"]
        return self.scan(root, kind, lang, busy_items, now) if res["added"] else pic

    def _agent_memory(self, root, kind) -> dict:
        """M for the main Agent: the working root's documentation/MEMORY.md + the harness memory memory.scan reads
        (counts and file names only)."""
        from . import memory
        sig = [kind, _mt(os.path.join(root, "documentation", "MEMORY.md"))]
        for src in (memory.source_list(kind, root) if kind in memory.HARNESSES else []):
            q = os.path.join(src["base"], src["rel"])
            sig.append(_mt(q))
            if src["kind"] == "dir":
                try:
                    sig += sorted((n, _mt(os.path.join(q, n))) for n in os.listdir(q))
                except OSError:
                    pass
        if self.mem_cache and self.mem_cache[0] == sig:
            return self.mem_cache[1]
        files, items, latest = [], 0, 0
        p = _safe_file(root, "documentation/MEMORY.md")
        if p:
            n = len(memory.split_items(_head(p, memory.MAX_TOTAL)))
            mt = _mt(p) // 1_000_000_000
            files.append({"name": "MEMORY.md", "kind": "memory", "src": "root", "items": n, "mtime": mt})
            items, latest = items + n, max(latest, mt)
        if kind:
            try:
                res = memory.scan(kind, root)
            except (OSError, ValueError):
                res = {"sources": []}
            for src in res["sources"]:
                for f in src["files"]:
                    if f.get("problem") or CRED.search(f["file"] or ""):
                        continue
                    fp = os.path.join(os.path.expanduser(src["path"]), f["file"]) if f["file"] else os.path.expanduser(src["path"])
                    mt = _mt(fp) // 1_000_000_000
                    files.append({"name": (f["file"] or os.path.basename(src["path"]))[:120], "kind": "harness",
                                  "src": src["id"], "items": len(f["items"]), "mtime": mt})
                    items, latest = items + len(f["items"]), max(latest, mt)
        self.mem_cache = (sig, {"owner": "agent", "items": items, "files": files[:40], "latest": latest})
        return self.mem_cache[1]

    # -------------------------------------------------------------- reads (allow-list of the last scan only)
    def _read(self, p: str, folder_real: str | None) -> tuple[str, int | None] | None:
        from . import privacy
        real = os.path.realpath(p)
        if folder_real and not _inside(real, folder_real):
            return None
        if any(CRED.search(part) for part in Path(real).parts):
            return None
        try:
            if not stat.S_ISREG(os.stat(real).st_mode):
                return None
            with open(real, "rb") as f:
                raw = f.read(4 * DOC_MAX + 1)
        except OSError:
            return None
        text = privacy.redact(raw.decode("utf-8", "replace"))
        b = text.encode()
        if len(b) <= DOC_MAX:
            return text, None
        return b[:DOC_MAX].decode("utf-8", "ignore"), len(b)

    def doc(self, folder, name) -> dict:
        bad = {"ok": False, "why": "not_found"}
        if not isinstance(folder, str) or not isinstance(name, str) or self.last is None:
            return bad
        p = self.allow.get((folder, name))
        if not p:
            return bad
        root = self.last["root"]
        base = str(CORE.resolve()) if folder == "@core" else os.path.realpath(os.path.join(root, folder))
        got = self._read(p, base)
        if got is None:
            return bad
        text, cut = got
        if folder == "@core":
            title = self.last["tail"]["agentj"]["core"]["title"]
        else:
            title = _title(text)
        return {"ok": True, "folder": folder, "name": name, "title": title, "text": text, **({"cut": cut} if cut else {})}

    def skill(self, sid) -> dict:
        p = self.skill_paths.get(sid) if isinstance(sid, str) else None
        if not p or self.last is None:
            return {"ok": False, "id": sid if isinstance(sid, str) else "", "why": "not_found"}
        scope, folder, name = (sid.split(":", 2) + ["", ""])[:3]
        base = os.path.realpath(os.path.join(self.last["root"], folder)) if scope == "local" else None
        got = self._read(p, base)
        if got is None:
            return {"ok": False, "id": sid, "why": "not_found"}
        _, body = front(got[0])
        return {"ok": True, "id": sid, "name": name, "text": body, **({"cut": got[1]} if got[1] else {})}


# ------------------------------------------------------------------ §30.2 message plan
def _n(o) -> int:
    return len(json.dumps(o, ensure_ascii=False).encode())


def _chunks(head: dict, key: str, items: list, budget: int = CHUNK) -> list[list]:
    out, batch, size = [], [], _n(head) + 40
    for it in items:
        n = _n(it) + 1
        if batch and size + n > budget:
            out.append(batch)
            batch, size = [], _n(head) + 40
        batch.append(it)
        size += n
    out.append(batch)
    return out


def messages(pic: dict, r: str) -> list[dict]:
    """The wf_list answer, in order: [wf_skills …] [wf_mem …] wf_items … — the last wf_items has more:false and the tail.
    skills / memory ride in the tail when the tail fits CHUNK; otherwise each that does not fit is sent first as its own
    chunked messages ({"t":"wf_skills"|"wf_mem","r","skills"|"memory":[…],"more"}) and is absent from the tail."""
    tail = dict(pic["tail"])
    pre = []
    for key, t in (("skills", "wf_skills"), ("memory", "wf_mem")):
        if _n(tail) > CHUNK - 512:
            parts = _chunks({"t": t, "r": r}, key, tail.pop(key))
            pre += [{"t": t, "r": r, key: b, "more": i < len(parts) - 1} for i, b in enumerate(parts)]
    head = {"t": "wf_items", "r": r}
    parts = _chunks(head, "items", pic["items"])
    if _n({**head, "items": parts[-1], **tail}) > CHUNK and parts[-1]:
        parts.append([])
    out = [{**head, "items": b, "more": i < len(parts) - 1} for i, b in enumerate(parts)]
    out[-1].update(tail)
    return pre + out


# ------------------------------------------------------------------ CLI: agentj workflows [--json]
def cmd_workflows(a) -> int:
    from .state import State
    from . import main_identity
    st = State()
    cfg = st.agent_config() or {}
    root = main_identity.working_root(cfg)
    if getattr(a, "staff_hint", None):           # P308: before dispatching to a CEO by another route than capability dispatch
        print(json.dumps(staff_hint(st, root, a.staff_hint), ensure_ascii=False))
        return 0
    pic = Scanner(st).scan(root, cfg.get("kind"), main_identity.language_of(cfg))
    t = pic["tail"]
    if a.json:
        print(json.dumps({"root": str(root), "counts": t["counts"], "skills": len(t["skills"]),
                          "workflows": [{k: w.get(k) for k in ("folder", "wdb", "ceo", "company", "ci", "lap", "busy", "staff")}
                                        for w in pic["items"]]}, ensure_ascii=False))
        return 0
    c = t["counts"]
    print(f"工作根目录 / working root: {root}")
    print(f"文件夹 / folders: {c['all']} · 认证工作流 / WDB: {c['wdb']} · 在办 / busy: {c['busy']} · 技能 / skills: {len(t['skills'])}")
    for w in pic["items"]:
        if w["wdb"]:
            print(f"  {w['folder']}" + (f" · CEO {w['ceo']}" if w.get("ceo") else "") + (" · 在办 busy" if w["busy"] else "")
                  + (f" · 员工 staff {w['staff']}" if w.get("staff") else " · 只有 CEO / CEO only"))
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("workflows", help="工作根目录里的工作流、技能和记忆文件（只读，手机「工作流」页看到的）/ the workflows, "
                                         "skills and memory files the phone's pages show (read-only)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--staff-hint", metavar="FOLDER", help="派活给这个工作流的 CEO 前：员工数，没有员工时（每家只一次）附一句提示 / "
                   "before dispatching to this workflow's CEO: its staff count, plus a one-time hint when it has none")
    p.set_defaults(fn=cmd_workflows)


__all__ = ["Scanner", "messages", "busy_folders", "colours", "common_skills", "add_parser"]


def task_assignment(task, pic):
    """P244: bounded organisational metadata, only when an actual folder / unique named role matches."""
    ws = [w for w in pic["items"] if w["wdb"]]
    folder, cwd, who = task.get("workflow"), task.get("cwd"), task.get("who")
    matches = [w for w in ws if folder == w["folder"]] if folder else []
    if not matches and isinstance(cwd, str):
        real = os.path.realpath(os.path.expanduser(cwd))   # P302: task_meta keeps cwd redacted (「~/…」)
        matches = [w for w in ws if _inside(real, os.path.realpath(os.path.join(pic["root"], w["folder"])))]
    if not matches and who:
        matches = [w for w in ws if who == w.get("ceo") or any(who == a["name"] for a in w["agents"])]
    if len(matches) != 1:
        return {"workflow": "", "role": "", "parent": ""}
    w = matches[0]
    ceo = task.get("role") == "CEO" or who == w.get("ceo")
    role = "CEO" if ceo else task.get("role") or who or "helper"
    h = 2166136261
    raw = f"{w['folder']}/{role}".encode("utf-16-le", "surrogatepass")
    for i in range(0, len(raw), 2):
        h = ((h ^ int.from_bytes(raw[i:i+2], "little")) * 16777619) & 0xffffffff
    pool = [i for i in range(16) if i not in (w.get("robot", 0), 14)]
    robot = w.get("robot", 0) if ceo else pool[h % len(pool)]
    return {"robot": robot, "workflow": w["folder"], "company": w.get("company") or w["folder"],
            "role": "CEO" if ceo else task.get("role") or who or "helper",
            "parent": "Agent J" if ceo else task.get("parent") or w.get("ceo") or "CEO"}
