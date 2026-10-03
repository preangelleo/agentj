"""Plaza package bundles `.ajpkg` (`agentj.bundle/v1`, protocol/PLAZA_PACKAGES.md §1). Stdlib only: the admin tool
(`tools/plaza`) imports this file by path.

    bundle = gzip(canonical_json({"schema", "manifest", "files": [{"path", "b64"}, …]}))   level 9, mtime 0, no file name

`build(dir) -> (bytes, manifest)` packs a package folder (manifest.json at its root; `manifest.files` is regenerated from the
folder) and refuses anything the contract refuses: symlinks, devices, forbidden paths (`.git`, `.ssh`, `.env*` other than
`.env.example`, keys, `__pycache__`, `.DS_Store`, and agent / IDE configuration that runs commands by itself: `.mcp.json`,
`.claude/settings*.json`, `.vscode/`, `.codex/`, … — protocol/vectors/bundle-rules.json), hidden characters (bidi controls,
zero-width, tag characters, a BOM after the start) in any UTF-8 file or manifest string, manifest JSON nested deeper than 32,
bad names, limits, a manifest that fails §1. Same folder → same bytes.
`parse(bytes) -> (manifest, {path: bytes})` validates everything again (it is what the installer and the server trust): size
caps before and while decompressing, canonical JSON only (one byte string per package), exact file set, SHA-256 + length of
every file. `sha256(bytes)` = the package id. Nothing here touches the network or runs anything.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import json
import os
import re
import stat
import unicodedata
import zlib

SCHEMA = "agentj.bundle/v1"
CATALOG_SCHEMA = "agentj.catalog/v1"
# packages built before the rename (0.9: R2 bundles, the official catalog) carry the old names: read them, write the new ones
SCHEMAS = (SCHEMA, "agentjarvis.bundle/v1")
CATALOG_SCHEMAS = (CATALOG_SCHEMA, "agentjarvis.catalog/v1")
MAX_BUNDLE = 2 * 1024 * 1024          # compressed
MAX_JSON = 12 * 1024 * 1024           # decompressed
MAX_FILES = 500
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_MANIFEST = 64 * 1024              # the server stores the manifest JSON in one column (≤ 64 KiB)
PATH_MAX, SEGMENT_MAX = 200, 80
CATEGORIES = ("agent-ops", "security", "browser", "communication", "content", "media", "seo", "storage", "data", "workflow")
TYPES = ("skill", "workflow")
HARNESSES = ("claude_code", "codex", "opencode")

NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")
SEMVER = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})(-[0-9A-Za-z.-]{1,40})?")
HEX64 = re.compile(r"[0-9a-f]{64}")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_SEG_PUNCT = frozenset("._-+@")


class BundleError(ValueError):
    """One line: why the folder / bundle is refused. `reason` = a fixed code (the server's §9 shape: `path_forbidden`,
    `hidden_characters`, `too_deep`, …; `bad_bundle` when no finer code applies)."""

    def __init__(self, msg: str, reason: str = "bad_bundle"):
        super().__init__(msg)
        self.reason = reason


# bundle-rules.json `forbidden_paths_rule`: agent / IDE configuration that runs commands by itself (hooks, MCP servers,
# tasks, plugins). Case-insensitive, at any depth.
_AUTORUN_FILES = frozenset((".mcp.json", ".envrc", "opencode.json", "opencode.jsonc"))
_AUTORUN_DIRS = frozenset((".vscode", ".idea", ".devcontainer", ".husky", ".cursor", ".gemini", ".opencode", ".codex"))
_CLAUDE_SETTINGS = frozenset(("settings.json", "settings.local.json"))
# bundle-rules.json `hidden_characters_rule` (U+200C / U+200D stay allowed: scripts, emoji; U+FEFF only as a file's first
# character)
_HIDDEN = frozenset([0x061C, 0x180E, 0x200B, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2060, 0x2065),
                     *range(0x2066, 0x206A), *range(0xFFF9, 0xFFFC), *range(0xE0000, 0xE0080), 0xFEFF])
_HIDDEN_RX = re.compile("[" + "".join(re.escape(chr(c)) for c in sorted(_HIDDEN)) + "]")
MAX_DEPTH = 32                        # bundle-rules.json `depth_rule`


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _path_key(p: str) -> bytes:
    return p.encode("utf-8")


# ------------------------------------------------------------------ paths
def _seg_ok(seg: str) -> bool:
    if not 1 <= len(seg) <= SEGMENT_MAX or seg in (".", ".."):
        return False
    for ch in seg:
        if ch in _SEG_PUNCT:
            continue
        cat = unicodedata.category(ch)
        if not (cat.startswith("L") or cat.startswith("M") or cat == "Nd"):
            return False
    return True


def forbidden(path: str) -> str | None:
    """Why this (well-formed) path may never be in a package, or None."""
    segs = path.split("/")
    lows = [s.lower() for s in segs]
    if lows[-1] in _AUTORUN_FILES:
        return f"{segs[-1]} (agent / IDE configuration that runs commands by itself)"
    if any(d in _AUTORUN_DIRS for d in lows[:-1]):
        return ("a file under an agent / IDE configuration folder (.vscode, .idea, .devcontainer, .husky, .cursor, .gemini, "
                ".opencode, .codex) that runs commands by itself")
    if len(lows) >= 2 and lows[-2] == ".claude" and lows[-1] in _CLAUDE_SETTINGS:
        return ".claude/settings*.json (its hooks run commands by themselves)"
    for seg in segs:
        low = seg.lower()
        if low in (".git", ".ssh"):
            return f"a {seg} folder"
        if low == ".env" or (low.startswith(".env.") and low != ".env.example"):
            return "an .env file (only .env.example, with names and no values)"
        if low.endswith(".pem") or low.endswith(".key"):
            return "a key file (*.pem / *.key)"
        if low.startswith("id_rsa") or low.startswith("id_ed25519"):
            return "an SSH key file"
        if low == "__pycache__":
            return "__pycache__"
        if low == ".ds_store":
            return ".DS_Store"
    return None


def check_path(path) -> str:
    """NFC, `/`-separated, relative, 1–200 code points; segments 1–80 of letters / digits / marks / `._-+@`; no `.` / `..`;
    not forbidden. Returns the path; raises BundleError."""
    if not isinstance(path, str) or not 1 <= len(path) <= PATH_MAX:
        raise BundleError(f"bad path {path!r:.80}: 1–{PATH_MAX} characters")
    if unicodedata.normalize("NFC", path) != path:
        raise BundleError(f"bad path {path!r:.80}: not NFC")
    segs = path.split("/")
    if not all(_seg_ok(s) for s in segs):
        raise BundleError(f"bad path {path!r:.80}: relative, '/'-separated, segments of letters, digits, marks and ._-+@ only")
    why = forbidden(path)
    if why:
        raise BundleError(f"refused path {path!r:.80}: {why}", "path_forbidden")
    return path


# ------------------------------------------------------------------ hidden characters, depth
def hidden_character(text: str, file: bool = True) -> int | None:
    """Index of the first character the Owner and a reviewer cannot see (bundle-rules.json), or None. `file`: a leading
    U+FEFF (byte-order mark) is allowed; in a manifest string it is not."""
    start = 1 if file and text.startswith("﻿") else 0
    m = _HIDDEN_RX.search(text, start)
    return m.start() if m else None


def _hidden_error(where: str, text: str, i: int) -> BundleError:
    line = text.count("\n", 0, i) + 1
    return BundleError(f"{where[:120]}: hidden character U+{ord(text[i]):04X} (line {line}), invisible to the Owner and the "
                       "reviewer: refused", "hidden_characters")


def check_text_file(path: str, data: bytes) -> None:
    """Every file that decodes as UTF-8: no hidden character (a leading BOM is fine)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return
    i = hidden_character(text, file=True)
    if i is not None:
        raise _hidden_error(path, text, i)


def depth(obj) -> int:
    """Nesting depth of JSON data (a scalar 0, `{}` / `[]` 1), iteratively (never a RecursionError); stops counting just
    past MAX_DEPTH."""
    deepest, stack = 0, [(obj, 1)]
    while stack:
        o, d = stack.pop()
        if not isinstance(o, (dict, list)):
            continue
        deepest = max(deepest, d)
        if d > MAX_DEPTH:
            return d
        stack.extend((v, d + 1) for v in (o.values() if isinstance(o, dict) else o) if isinstance(v, (dict, list)))
    return deepest


def check_depth(obj, what: str = "manifest") -> None:
    """depth_rule: an object or array inside 32 others → refused."""
    if depth(obj) > MAX_DEPTH:
        raise BundleError(f"{what}: JSON nested deeper than {MAX_DEPTH}: refused", "too_deep")


def _strings(o, where="manifest"):
    """Every string of a (depth-checked) JSON value, keys included, with where it is."""
    stack = [(o, where)]
    while stack:
        x, w = stack.pop()
        if isinstance(x, str):
            yield w, x
        elif isinstance(x, list):
            stack.extend((v, f"{w}[{i}]") for i, v in enumerate(x))
        elif isinstance(x, dict):
            for k, v in x.items():
                if isinstance(k, str):
                    yield f"{w} (key)", k
                stack.append((v, f"{w}.{k}"))


def check_manifest_strings(m) -> None:
    """hidden_characters_rule for every manifest string (a BOM is refused here too)."""
    for where, text in _strings(m):
        i = hidden_character(text, file=False)
        if i is not None:
            raise _hidden_error(where, text, i)


def check_path_set(paths) -> None:
    """No case-insensitive duplicates; no path that is also a folder of another path; ≤ 500."""
    paths = list(paths)
    if len(paths) > MAX_FILES:
        raise BundleError(f"too many files ({len(paths)} > {MAX_FILES})")
    files, dirs = {}, {}
    for p in paths:
        k = p.casefold()
        if k in files:
            raise BundleError(f"duplicate path (ignoring case): {p!r:.80}")
        files[k] = p
        segs = p.split("/")
        for i in range(1, len(segs)):
            d = "/".join(segs[:i])
            dk = d.casefold()
            if dirs.setdefault(dk, d) != d:
                raise BundleError(f"folder names differ only in case: {d!r:.80}")
    for k, p in files.items():
        if k in dirs:
            raise BundleError(f"{p!r:.80} is both a file and a folder")


# ------------------------------------------------------------------ manifest
def _str(v, lo: int, hi: int) -> bool:
    return isinstance(v, str) and lo <= len(v) <= hi


def _list_of_dicts(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, dict) for x in v)


def check_requires(r) -> None:
    """FORMAT §2 shapes (unknown keys ignored)."""
    if r is None:
        return
    if not isinstance(r, dict):
        raise BundleError("requires: an object")
    if "harness" in r and not (isinstance(r["harness"], list) and all(h in HARNESSES for h in r["harness"])):
        raise BundleError("requires.harness: a list of claude_code / codex / opencode")
    if "os" in r and not (isinstance(r["os"], list) and all(_str(x, 1, 40) for x in r["os"])):
        raise BundleError("requires.os: a list of strings")
    if "cli" in r:
        if not _list_of_dicts(r["cli"]) or len(r["cli"]) > 40:
            raise BundleError("requires.cli: a list of {name, version?, optional?}")
        for c in r["cli"]:
            if not _str(c.get("name"), 1, 60) or ("version" in c and not _str(c["version"], 0, 60)) or \
                    ("optional" in c and not isinstance(c["optional"], bool)):
                raise BundleError("requires.cli: each {name, version?, optional?}")
    if "python_requirements" in r and not (r["python_requirements"] is None or _str(r["python_requirements"], 1, 200)):
        raise BundleError("requires.python_requirements: a file name or null")
    if "env" in r:
        if not _list_of_dicts(r["env"]) or len(r["env"]) > 60:
            raise BundleError("requires.env: a list of {name, required?, secret?, description_zh?, how_to_get?}")
        for e in r["env"]:
            if not (isinstance(e.get("name"), str) and ENV_NAME.fullmatch(e["name"])):
                raise BundleError("requires.env: each name an environment variable name")
            for k in ("required", "secret"):
                if k in e and not isinstance(e[k], bool):
                    raise BundleError(f"requires.env.{k}: a boolean")
            for k in ("description_zh", "description_en", "how_to_get"):
                if k in e and not _str(e[k], 0, 1000):
                    raise BundleError(f"requires.env.{k}: a string")
    if "accounts" in r:
        if not _list_of_dicts(r["accounts"]) or len(r["accounts"]) > 30:
            raise BundleError("requires.accounts: a list of {name, url}")
        for a in r["accounts"]:
            if not _str(a.get("name"), 1, 200) or ("url" in a and not (a["url"] is None or _str(a["url"], 0, 500))):
                raise BundleError("requires.accounts: each {name, url}")
    if "skills" in r and not (isinstance(r["skills"], list) and len(r["skills"]) <= 40
                              and all(isinstance(s, str) and NAME.fullmatch(s) for s in r["skills"])):
        raise BundleError("requires.skills: a list of package names")


def check_manifest(m, paths=None) -> dict:
    """§1 manifest checks. `paths` = the set of file paths of the package (manifest.json excluded) when known."""
    if not isinstance(m, dict):
        raise BundleError("manifest: an object")
    check_depth(m)
    check_manifest_strings(m)
    if m.get("schema_version") not in CATALOG_SCHEMAS:
        raise BundleError(f"manifest.schema_version must be {CATALOG_SCHEMA}")
    if not (isinstance(m.get("name"), str) and NAME.fullmatch(m["name"])):
        raise BundleError("manifest.name: ^[a-z0-9][a-z0-9-]{1,39}$")
    if m.get("type") not in TYPES:
        raise BundleError("manifest.type: skill or workflow")
    if not (isinstance(m.get("version"), str) and SEMVER.fullmatch(m["version"])):
        raise BundleError("manifest.version: MAJOR.MINOR.PATCH[-pre]")
    for k in ("title_zh", "title_en"):
        if not _str(m.get(k), 1, 80):
            raise BundleError(f"manifest.{k}: 1–80 characters")
    for k in ("summary_zh", "summary_en"):
        if not _str(m.get(k), 1, 500):
            raise BundleError(f"manifest.{k}: 1–500 characters")
    tags = m.get("tags", [])
    if not (isinstance(tags, list) and len(tags) <= 12 and all(_str(t, 1, 24) for t in tags)):
        raise BundleError("manifest.tags: ≤ 12 strings of 1–24 characters")
    if m.get("category") not in CATEGORIES:
        raise BundleError(f"manifest.category: one of {', '.join(CATEGORIES)}")
    files = m.get("files")
    if not _list_of_dicts(files):
        raise BundleError("manifest.files: a list of {path, sha256, bytes}")
    listed = []
    for f in files:
        check_path(f.get("path"))
        if not (isinstance(f.get("sha256"), str) and HEX64.fullmatch(f["sha256"])) or \
                not (isinstance(f.get("bytes"), int) and not isinstance(f["bytes"], bool) and 0 <= f["bytes"] <= MAX_FILE):
            raise BundleError(f"manifest.files: {f.get('path')!r:.80} needs sha256 (64 hex) and bytes")
        listed.append(f["path"])
    check_path_set(listed)
    if listed != sorted(listed, key=_path_key):
        raise BundleError("manifest.files: not sorted by path")
    if paths is not None and set(paths) != set(listed):
        extra, missing = sorted(set(paths) - set(listed))[:3], sorted(set(listed) - set(paths))[:3]
        raise BundleError(f"manifest.files does not list exactly the package's files (extra {extra}, missing {missing})")
    have = set(listed)
    if m.get("entry") not in have:
        raise BundleError("manifest.entry: must be a listed file")
    install = m.get("install", {})
    if not isinstance(install, dict):
        raise BundleError("manifest.install: an object")
    if "post_install" in install and not (isinstance(install["post_install"], list) and len(install["post_install"]) <= 20
                                          and all(_str(x, 0, 1000) for x in install["post_install"])):
        raise BundleError("manifest.install.post_install: a list of strings")
    if "verify" in install and not (install["verify"] is None or _str(install["verify"], 0, 1000)):
        raise BundleError("manifest.install.verify: a string or null")
    if m["type"] == "skill":
        for need in ("SKILL.md", "README.md"):
            if need not in have:
                raise BundleError(f"a skill needs {need}")
        if not (isinstance(install.get("skill_dir_name"), str) and NAME.fullmatch(install["skill_dir_name"])):
            raise BundleError("manifest.install.skill_dir_name: ^[a-z0-9][a-z0-9-]{1,39}$")
    else:
        for need in ("README.md", "params.schema.json"):
            if need not in have:
                raise BundleError(f"a workflow needs {need}")
        if not any(p.startswith("scaffold/") for p in have):
            raise BundleError("a workflow needs at least one file under scaffold/")
        for k in ("roles", "automations"):
            if k in m and not (_list_of_dicts(m[k]) and len(m[k]) <= 60):
                raise BundleError(f"manifest.{k}: a list of objects")
    check_requires(m.get("requires"))
    if "certified" in m and not isinstance(m["certified"], bool):
        raise BundleError("manifest.certified: a boolean")
    if len(canonical_json(m)) > MAX_MANIFEST:
        raise BundleError("manifest too large (> 64 KiB)")
    return m


def _check_params_schema(raw: bytes) -> None:
    try:
        s = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise BundleError("params.schema.json does not parse") from None
    check_depth(s, "params.schema.json")
    if not isinstance(s, dict) or not isinstance(s.get("properties", {}), dict):
        raise BundleError("params.schema.json: an object schema with properties")


# ------------------------------------------------------------------ build
def _walk(root: str, rel: str, out: dict) -> None:
    with os.scandir(os.path.join(root, rel) if rel else root) as it:
        entries = sorted(it, key=lambda e: e.name)
    for e in entries:
        name = e.name
        p = f"{rel}/{name}" if rel else name
        nfc = unicodedata.normalize("NFC", p)
        st = os.lstat(e.path)
        if stat.S_ISLNK(st.st_mode):
            raise BundleError(f"refused: {p!r:.80} is a symlink")
        if stat.S_ISDIR(st.st_mode):
            check_path(nfc)          # a forbidden folder (.git, __pycache__, …) is refused even when empty
            check_path(nfc + "/x")   # … and so is an auto-running configuration folder (.vscode/, .codex/, …)
            _walk(root, p, out)
            continue
        if not stat.S_ISREG(st.st_mode):
            raise BundleError(f"refused: {p!r:.80} is not a regular file")
        if p == "manifest.json":
            continue
        check_path(nfc)
        if nfc != p:
            raise BundleError(f"refused: {p!r:.80} is not NFC (rename it)")
        if st.st_size > MAX_FILE:
            raise BundleError(f"{p!r:.80} is larger than 2 MiB")
        fd = os.open(e.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise BundleError(f"refused: {p!r:.80} is not a regular file")
            chunks, n = [], 0
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    break
                n += len(b)
                if n > MAX_FILE:
                    raise BundleError(f"{p!r:.80} is larger than 2 MiB")
                chunks.append(b)
        finally:
            os.close(fd)
        out[p] = b"".join(chunks)
        check_text_file(p, out[p])
        if len(out) > MAX_FILES:
            raise BundleError(f"too many files (> {MAX_FILES})")


def read_manifest_file(directory: str) -> dict:
    mp = os.path.join(directory, "manifest.json")
    try:
        st = os.lstat(mp)
    except OSError:
        raise BundleError("no manifest.json in the folder") from None
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_MANIFEST * 4:
        raise BundleError("manifest.json must be a regular file ≤ 256 KiB")
    try:
        with open(mp, "rb") as f:
            m = json.loads(f.read().decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise BundleError("manifest.json does not parse") from None
    if not isinstance(m, dict):
        raise BundleError("manifest.json: an object")
    return m


def pack(manifest: dict, files: dict) -> bytes:
    """(manifest with files listed, {path: bytes}) → bundle bytes (no checks; build / tests use it)."""
    paths = sorted(files, key=_path_key)
    b = {"schema": SCHEMA, "manifest": manifest,
         "files": [{"path": p, "b64": base64.b64encode(files[p]).decode("ascii")} for p in paths]}
    return gzip.compress(canonical_json(b), compresslevel=9, mtime=0)


def files_list(files: dict) -> list:
    return [{"path": p, "sha256": sha256(files[p]), "bytes": len(files[p])} for p in sorted(files, key=_path_key)]


def build(directory, manifest_override: dict | None = None) -> tuple[bytes, dict]:
    """Package folder → (bundle bytes, manifest). `manifest.files` is regenerated; `manifest_override` (publish) is merged
    over manifest.json before the checks."""
    directory = os.fspath(directory)
    try:
        st = os.lstat(directory)
    except OSError:
        raise BundleError("no such folder") from None
    if not stat.S_ISDIR(st.st_mode):
        raise BundleError("not a folder (symlinks are refused)")
    m = read_manifest_file(directory)
    files: dict = {}
    _walk(directory, "", files)
    check_path_set(files)
    total = sum(len(v) for v in files.values())
    if total > MAX_TOTAL:
        raise BundleError(f"files total {total} bytes > 8 MiB")
    m = {**m, **(manifest_override or {}), "files": files_list(files)}
    check_manifest(m, set(files))
    if m["type"] == "workflow":
        _check_params_schema(files["params.schema.json"])
    data = pack(m, files)
    if len(data) > MAX_BUNDLE:
        raise BundleError(f"bundle {len(data)} bytes > 2 MiB")
    return data, m


# ------------------------------------------------------------------ parse
def _no_dupes(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise BundleError("duplicate JSON key")
        d[k] = v
    return d


def _gunzip(data: bytes) -> bytes:
    if len(data) > MAX_BUNDLE:
        raise BundleError("bundle larger than 2 MiB")
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        raw = d.decompress(data, MAX_JSON + 1)
    except zlib.error:
        raise BundleError("not a gzip bundle") from None
    if len(raw) > MAX_JSON or d.unconsumed_tail:
        raise BundleError("bundle expands beyond 12 MiB")
    if not d.eof or d.unused_data:
        raise BundleError("truncated bundle or trailing data")
    return raw


def parse(data: bytes) -> tuple[dict, dict]:
    """Bundle bytes → (manifest, {path: bytes}); raises BundleError on anything §1 refuses."""
    if not isinstance(data, (bytes, bytearray)):
        raise BundleError("bundle: bytes")
    raw = _gunzip(bytes(data))
    try:
        b = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_dupes)
    except BundleError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise BundleError("bundle JSON does not parse") from None
    if not isinstance(b, dict) or set(b) != {"schema", "manifest", "files"} or b["schema"] not in SCHEMAS:
        raise BundleError(f"not an {SCHEMA} bundle")
    check_depth(b["manifest"])
    if depth(b["files"]) > 2:
        raise BundleError("bundle.files: a list of ≤ 500 {path, b64}")
    if canonical_json(b) != raw:
        raise BundleError("bundle JSON is not canonical")
    fl = b["files"]
    if not _list_of_dicts(fl) or len(fl) > MAX_FILES:
        raise BundleError("bundle.files: a list of ≤ 500 {path, b64}")
    files: dict = {}
    total = 0
    order = []
    for f in fl:
        if set(f) != {"path", "b64"} or not isinstance(f["b64"], str):
            raise BundleError("bundle.files: each exactly {path, b64}")
        p = check_path(f["path"])
        if p in files:
            raise BundleError(f"duplicate file {p!r:.80}")
        if len(f["b64"]) > (MAX_FILE * 4) // 3 + 8:
            raise BundleError(f"{p!r:.80} is larger than 2 MiB")
        try:
            content = base64.b64decode(f["b64"], validate=True)
        except (binascii.Error, ValueError):
            raise BundleError(f"{p!r:.80}: bad base64") from None
        if base64.b64encode(content).decode("ascii") != f["b64"]:
            raise BundleError(f"{p!r:.80}: non-canonical base64")
        if len(content) > MAX_FILE:
            raise BundleError(f"{p!r:.80} is larger than 2 MiB")
        total += len(content)
        if total > MAX_TOTAL:
            raise BundleError("files total > 8 MiB")
        check_text_file(p, content)
        files[p] = content
        order.append(p)
    if order != sorted(order, key=_path_key):
        raise BundleError("bundle.files: not sorted by path")
    check_path_set(order)
    m = check_manifest(b["manifest"], set(files))
    for f in m["files"]:
        c = files[f["path"]]
        if len(c) != f["bytes"] or sha256(c) != f["sha256"]:
            raise BundleError(f"{f['path']!r:.80}: SHA-256 / length differ from the manifest")
    if m["type"] == "workflow":
        _check_params_schema(files["params.schema.json"])
    return m, files
