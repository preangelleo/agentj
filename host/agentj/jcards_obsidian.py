"""P432 (#66 Cards phase 2, spec AGENTJ-CARDS-SPEC §8 Obsidian): the official Obsidian card — the notes the owner picked
from an Obsidian vault on the Agent J host, shown on the phone; 「看，改交给 Agent」: every note has an @ that drafts
`/agentj-obsidian @<note>` for the Agent, which edits the vault here (its ordinary approvals apply).

The selection is the card's data: `<state>/agentperm/cards-data/obsidian/selection.json` {"vault": <folder>, "notes":
[<path in the vault>.md, …]} — written only by `agentj card obsidian vault|pick|unpick` (atomic, the no-follow card storage),
so the Agent can set it up inside the fence. What it may name is narrow: a vault is a real folder with an `.obsidian/`
folder (Obsidian's own marker), never `/`, the state dir or a hidden / credential folder; a note is a `.md` file inside it,
≤ NOTES_MAX of them, read one directory at a time with O_NOFOLLOW (a link anywhere → refused), nothing hidden on the way
(so never `.obsidian/` itself), ≤ NOTE_MAX bytes (cut at a word), privacy.redact(owner=True) before it leaves the host.
`agentj card obsidian find` lists the vaults Obsidian itself knows (its obsidian.json, read-only). Nothing else is read or run.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import stat

from . import jcards, jcards_files, privacy

CID = "obsidian"
CONFIG = "selection.json"
NOTES_MAX, NOTE_MAX = 20, 32 * 1024
_NB = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)


def config(root) -> dict:
    try:
        with jcards._dirfd(root, ("cards-data", CID)) as d:
            raw, cut = jcards._read_fd(os.open(CONFIG, _NB, dir_fd=d), 64 * 1024)
        obj = json.loads(raw or b"{}") if not cut else {}
    except (OSError, ValueError):
        obj = {}
    vault = obj.get("vault") if isinstance(obj, dict) and isinstance(obj.get("vault"), str) else None
    notes = [n for n in obj.get("notes", []) if isinstance(n, str)][:NOTES_MAX] if isinstance(obj, dict) and isinstance(obj.get("notes"), list) else []
    return {"vault": vault, "notes": notes}


def vault_problem(root, path) -> str | None:
    if not isinstance(path, str) or not path:
        return "not_set"
    why = jcards_files._root_problem(root, os.path.expanduser(path))
    if why:
        return why
    try:
        if not stat.S_ISDIR(os.lstat(os.path.join(os.path.realpath(os.path.expanduser(path)), ".obsidian")).st_mode):
            return "no_vault"
    except OSError:
        return "no_vault"
    return None


def note_parts(rel) -> list | None:
    parts = jcards_files._parts(rel)
    if not parts or not parts[-1].lower().endswith(".md") or any(jcards_files._hidden(s, i < len(parts) - 1) for i, s in enumerate(parts)):
        return None
    return parts


def read_note(vault: str, rel: str, state_root=None) -> tuple[str | None, str | None, bool]:
    """→ (text, problem, cut). `vault` = the real path that was checked: a folder swapped in since is refused."""
    parts = note_parts(rel)
    if parts is None:
        return None, "refused", False
    try:
        d = jcards_files._open_dir(vault, parts[:-1], state_root)
    except LookupError:
        return None, "refused", False
    except OSError as e:                      # a link on the way (ELOOP / ENOTDIR) is refused, a missing folder is gone
        return None, ("gone" if isinstance(e, FileNotFoundError) else "refused"), False
    try:
        raw, cut = jcards._read_fd(os.open(parts[-1], _NB, dir_fd=d), NOTE_MAX)
    except OSError as e:
        return None, ("gone" if isinstance(e, FileNotFoundError) else "refused"), False
    finally:
        os.close(d)
    if raw is None:
        return None, "refused", False
    text = raw.decode("utf-8", "replace")
    if cut:                                   # a cut key may lose its shape: drop the trailing half word first
        ws = list(re.finditer(r"\s", text))
        text = text[:ws[-1].start()] if ws else ""
    return privacy.redact(text, owner=True), None, cut


def phone(root, card: dict, lang: str) -> dict:
    """§34 jcard_get for the official Obsidian card."""
    d, c = card["doc"], config(root)
    res = {"ok": True, "id": CID, "template": "obsidian", "plus": d.get("plus") or jcards.PLUS_DEFAULT, "links": [], "anchors": [],
           "actions": [jcards._act(card, a, lang, root) for a in d.get("actions", [])], "entries": []}
    vault = os.path.realpath(os.path.expanduser(c["vault"])) if c["vault"] else None   # looked up once; reads must open this
    why = vault_problem(root, vault)
    if why:
        res["problem"] = why
        return res
    res["vault"] = jcards_files._short(vault)
    for i, rel in enumerate(c["notes"]):
        text, problem, cut = read_note(vault, rel, root)
        title = os.path.splitext(rel.rsplit("/", 1)[-1])[0]
        res["entries"].append({"id": f"o{i + 1}", "path": privacy.redact(rel, owner=True), "title": privacy.redact(title, owner=True),
                               "text": text or "", **({"problem": problem} if problem else {}), **({"cut": True} if cut else {})})
    if not c["notes"]:
        res["problem"] = "no_notes"
    return res


def known_vaults() -> list:
    """The vaults Obsidian itself lists in its obsidian.json (Linux / macOS / Windows locations, flatpak / snap too)."""
    home = pathlib.Path.home()
    cands = [home / ".config/obsidian/obsidian.json", home / "Library/Application Support/obsidian/obsidian.json",
             home / ".var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json", home / "snap/obsidian/current/.config/obsidian/obsidian.json"]
    if os.environ.get("APPDATA"):
        cands.append(pathlib.Path(os.environ["APPDATA"]) / "obsidian" / "obsidian.json")
    out = []
    for f in cands:
        try:
            obj = json.loads(f.read_text(encoding="utf-8")[:1_000_000])
        except (OSError, ValueError):
            continue
        for v in (obj.get("vaults") or {}).values() if isinstance(obj, dict) and isinstance(obj.get("vaults"), dict) else []:
            p = v.get("path") if isinstance(v, dict) else None
            if isinstance(p, str) and p not in out and os.path.isdir(p):
                out.append(p)
    return out


def _save(root, c: dict) -> None:
    jcards._write_json(root, ("cards-data", CID), CONFIG, {"vault": c["vault"], "notes": c["notes"]})


def change(root, op: str, args: list) -> dict:
    """agentj card obsidian find | list | vault <folder> | pick <note.md>… | unpick <note.md>…"""
    c = config(root)
    if op == "find":
        return {"ok": True, "vaults": known_vaults()}
    if op == "list":
        why = vault_problem(root, c["vault"])
        return {"ok": why is None, **({"why": why} if why else {}), **c}
    if op == "vault":
        if len(args) != 1:
            return {"ok": False, "why": "one_folder"}
        why = vault_problem(root, args[0])
        if why:
            return {"ok": False, "why": why}
        c = {"vault": os.path.realpath(os.path.expanduser(args[0])), "notes": c["notes"] if c["vault"] == os.path.realpath(os.path.expanduser(args[0])) else []}
    else:
        why = vault_problem(root, c["vault"])
        if why:
            return {"ok": False, "why": why}
        vault = os.path.realpath(c["vault"])
        for rel in args:
            rel = rel[len(vault) + 1:] if rel.startswith(vault + os.sep) else rel
            if op == "pick":
                text, problem, _cut = read_note(vault, rel, root)
                if problem:
                    return {"ok": False, "why": problem, "note": rel}
                if rel not in c["notes"]:
                    if len(c["notes"]) >= NOTES_MAX:
                        return {"ok": False, "why": "too_many"}
                    c["notes"].append(rel)
            elif rel in c["notes"]:
                c["notes"].remove(rel)
    try:
        _save(root, c)
    except OSError:
        return {"ok": False, "why": "refused"}
    return {"ok": True, **c}


def command(a, root) -> int:
    r = change(root, a.op, a.args or [])
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1

