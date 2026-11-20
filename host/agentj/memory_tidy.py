"""Conservative, local memory maintenance. Apply only evidenced edits; keep the exact original before writing."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
import re

from . import memory
from .privacy import owner_text

PROJECT = "agentj.project_memory"
MEMORY_SECTION = re.compile(r"\bmemor(?:y|ies)\b|记忆|記憶", re.I)
DATED = re.compile(r"^\s*[-*+]\s+\[?(\d{4}-\d\d-\d\d)\]?\s+(.+)$")
TOPIC = re.compile(r"^([^:：|]{1,64})\s*[:：|]\s*(.+)$")
OWNER = re.compile(r"(?:主人(?:原话|说)|owner(?: said| quote)|董事长(?:原话|说))\s*[:：]?\s*", re.I)
COVERED = re.compile(r"^\s*[-*+]\s+(?:已纳入|已归入|covered by|documented in)\s*[:：]?\s*\[[^\]]+\]\(([^)]+)\)\s*[-—:：]\s*(.+)$", re.I)
LINK = re.compile(r"^\s*[-*+]\s+\[([^\]]+)\]\((?:\./)?([^()/#]+\.md)\)(.*)$")
SAFE_FILE = re.compile(r"(?:^|[._-])(?:auth|credential[s]?|secret[s]?|password[s]?|token[s]?|private[-_]key)(?:[._-]|$)", re.I)
LIMIT_INDEX_LINE = 240
LIMIT_INDEX_ROWS = 200


def project_source(workdir):
    return {"id": PROJECT, "kind": "file", "base": os.path.realpath(workdir),
            "rel": "documentation/MEMORY.md", "label": "项目记忆 documentation/MEMORY.md"}


def resolve(kind, workdir, source_id, filename):
    if source_id == PROJECT and not filename:
        s = project_source(workdir)
        return memory._no_link(s["base"], s["rel"])
    return memory._resolve(kind, workdir, source_id, filename)[1]


def _files(kind, workdir):
    used = 0
    seen = set()
    for s in [project_source(workdir), *memory.source_list(kind, workdir)]:
        try:
            path = Path(memory._no_link(s["base"], s["rel"]))
            names = [""]
            if s["kind"] == "dir":
                with os.scandir(path) as entries:
                    names = sorted(e.name for e in entries if not e.name.startswith(".") and
                                   e.name.endswith(".md") and not SAFE_FILE.search(e.name))[:memory.MAX_DIR_FILES]
            for name in names:
                file = str(path / name if name else path)
                if file in seen or os.path.islink(file):
                    continue
                seen.add(file)
                try:
                    memory.check_data_path(file)
                    data = memory.read_file(file)
                    used += len(data)
                    if used > memory.MAX_TOTAL:
                        return
                    text = data.decode("utf-8")
                except (memory.MemoryError_, UnicodeError):
                    continue
                # Existing credential masking also covers access codes. Never archive or rewrite a sensitive file.
                if owner_text(text, mask_access_codes=True) != text or "\x00" in text:
                    continue
                yield s, name, file, data
        except (memory.MemoryError_, OSError):
            continue


def _covered(workdir, match):
    rel, phrase = match.groups()
    if (not rel.startswith(("documentation/", ".agents/skills/", ".claude/skills/")) or
            ".." in Path(rel).parts or not rel.endswith(".md") or len(phrase) < 6):
        return False
    try:
        target = memory._no_link(os.path.realpath(workdir), rel)
        memory.check_data_path(target)
        return phrase in memory.read_file(target).decode("utf-8")
    except (memory.MemoryError_, UnicodeError):
        return False


def _plan(text, source, filename, path, workdir):
    # Match split_items' LF line coordinates exactly, even if an owner quote contains Unicode line separators.
    chunks = text.split("\n")
    lines = [chunk + "\n" for chunk in chunks[:-1]] + [chunks[-1]]
    sections = []
    keep = list(lines)
    groups = {}
    counts = {"merged": 0, "archived": 0, "conflicts": 0, "index_fixed": 0}
    in_memory = source["id"] == PROJECT or source["kind"] == "dir"
    reference = {}
    index_entries = 0
    for item in memory.split_items(text):
        i, j = item["start"], item["end"]
        if item["kind"] == "heading":
            title = item["text"].lstrip("#").strip()
            level = len(item["text"].lstrip()) - len(item["text"].lstrip().lstrip("#"))
            sections = [x for x in sections if x[0] < level] + [(level, title)]
        if not in_memory and not any(MEMORY_SECTION.search(t) for _, t in sections):
            continue
        if item["kind"] != "list" or j != i + 1:
            continue
        ln = lines[i].rstrip("\r\n")
        eol = lines[i][len(ln):]
        # A checked task and a verified copy with an explicit source are conclusive.
        covered = COVERED.fullmatch(ln)
        if re.match(r"^\s*[-*+]\s+\[[xX]\]\s+\S", ln) or (covered and _covered(workdir, covered)):
            keep[i] = ""
            counts["archived"] += 1
            continue
        link = LINK.fullmatch(ln) if filename == "MEMORY.md" else None
        if link:
            label, dest, extra = link.groups()
            folder = Path(path).parent
            target = folder / dest
            try:
                memory.check_data_path(target)
            except memory.MemoryError_:
                continue
            if not target.exists():
                if not reference:
                    for p in folder.iterdir():
                        if not p.is_symlink() and p.is_file() and p.suffix == ".md":
                            reference.setdefault(p.name.casefold(), []).append(p.name)
                choices = reference.get(dest.casefold(), [])
                if len(choices) > 1:
                    continue
                fixed = choices[0] if choices else None
                if not fixed:
                    # No unique known destination: archive the dead entry, leaving its original available.
                    keep[i] = ""
                    counts["index_fixed"] += 1
                    continue
                try:
                    memory.check_data_path(folder / fixed)
                except memory.MemoryError_:
                    continue
                dest = fixed
            index_entries += 1
            if index_entries > LIMIT_INDEX_ROWS:
                keep[i] = ""
            elif dest != link[2] or len(ln) > LIMIT_INDEX_LINE:
                normalized = f"- [{label}]({dest})"
                if len(normalized) > LIMIT_INDEX_LINE:
                    normalized = f"- [{label[:max(1, LIMIT_INDEX_LINE-len(dest)-7)]}]({dest})"
                keep[i] = normalized + eol
            counts["index_fixed"] += int(keep[i] != lines[i])
            continue
        dated = DATED.fullmatch(ln)
        if not dated:
            continue
        try:
            date = dt.date.fromisoformat(dated[1])
        except ValueError:
            continue
        body = dated[2]
        parsed = TOPIC.fullmatch(body)
        if not parsed:
            continue
        topic, value = parsed.groups()
        # A section is a scope. Never collapse separate projects or settings sharing a short name.
        key = (tuple(sections), topic.casefold().strip())
        groups.setdefault(key, []).append((date, i, value))

    for entries in groups.values():
        if len(entries) < 2:
            continue
        conflict = len({e[2] for e in entries}) > 1
        eligible = [e for e in entries if OWNER.search(e[2])] if conflict else entries
        if not eligible:
            continue
        latest = max(e[0] for e in eligible)
        newest = [e for e in eligible if e[0] == latest]
        if len({e[2] for e in newest}) > 1:
            continue
        _, winner, _ = newest[-1]
        earliest = min(e[0] for e in entries)
        for _, idx, _ in entries:
            if idx != winner:
                keep[idx] = ""
            # Extend the existing history on a later update instead of accumulating stale timeline comments.
            if idx + 1 < len(lines):
                prior = re.fullmatch(r"<!-- memory timeline (\d{4}-\d\d-\d\d) → \d{4}-\d\d-\d\d -->",
                                     lines[idx + 1].rstrip("\r\n"))
                if prior:
                    try:
                        earliest = min(earliest, dt.date.fromisoformat(prior[1]))
                    except ValueError:
                        continue
                    keep[idx + 1] = ""
        counts["merged"] += len(entries)-1
        counts["conflicts"] += int(conflict)
        # Separate metadata keeps the current owner's statement byte-for-byte, including its punctuation.
        line = keep[winner]
        eol = "\r\n" if line.endswith("\r\n") else "\n"
        keep[winner] = line.rstrip("\r\n") + eol + f"<!-- memory timeline {earliest} → {latest} -->" + (eol if line.endswith("\n") else "")
    return "".join(keep), counts


def _note_groups(files):
    """Native single-note folders often keep each revision in a separate file with the same front-matter name."""
    groups = {}
    for s, filename, path, raw in files:
        if s["kind"] != "dir" or filename == "MEMORY.md":
            continue
        text = raw.decode()
        meta, body = memory._front(text)
        name = meta.get("name", "").strip()
        if not name:
            continue
        items = memory.split_items(body)
        content = [i for i in items if i["kind"] != "heading" and
                   not i["text"].startswith("<!-- memory timeline ")]
        if len(content) != 1 or content[0]["kind"] != "list":
            continue
        record = DATED.fullmatch(content[0]["text"])
        if not record:
            continue
        try:
            day = dt.date.fromisoformat(record[1])
        except ValueError:
            continue
        topic = TOPIC.fullmatch(record[2])
        if not topic:
            continue
        headings = tuple(item["text"] for item in items if item["kind"] == "heading")
        # Front matter can contain additional qualifiers that the display parser omits.
        header = text.split("\n", 1)[1].splitlines()
        end = next(i for i, line in enumerate(header) if line.strip() in ("---", "..."))
        scope = (name, tuple(header[:end]), headings, topic[1].strip())
        groups.setdefault((s["id"], scope), []).append((day, record[2], filename, path, raw))
    return groups


def _replace(st, kind, workdir, source, filename, raw, after):
    path = resolve(kind, workdir, source["id"], filename)
    if memory.sha(memory.read_file(path)) != memory.sha(raw):
        raise memory.MemoryError_("changed")
    rec = {"harness": kind, "workdir": os.path.realpath(workdir), "src": source["id"], "file": filename,
           "label": source["label"], "path": path, "kind": "tidy", "text": raw.decode(),
           "before": memory.sha(raw), "after": memory.sha(after)}
    archive = memory._put_trash(st, rec)
    memory._write_atomic(resolve(kind, workdir, source["id"], filename), after, rec["before"])
    return archive


def _merge_notes(st, kind, workdir, files, result, dry_run, still_idle):
    sources = {s["id"]: s for s, *_ in files}
    for (sid, _), notes in _note_groups(files).items():
        if len(notes) < 2:
            continue
        conflict = len({n[1] for n in notes}) > 1
        eligible = [n for n in notes if OWNER.search(n[1])] if conflict else notes
        if not eligible:
            continue
        latest = max(n[0] for n in eligible)
        options = [n for n in eligible if n[0] == latest]
        if len({n[1] for n in options}) != 1:
            continue
        current = options[-1]
        if still_idle and not still_idle():
            result["deferred"] = True
            return
        date, _, filename, path, original = current
        try:
            memory.check_data_path(Path(path).parent / "MEMORY.md")
        except memory.MemoryError_:
            result["guarded"] += 1
            continue
        timeline = f"<!-- memory timeline {min(n[0] for n in notes)} → {date} -->".encode()
        after = original
        try:
            if timeline not in original.splitlines():
                eol = b"\r\n" if b"\r\n" in original else b"\n"
                after = original + (b"" if original.endswith(b"\n") else eol) + timeline + eol
                if not dry_run:
                    result["archive_ids"].append(_replace(st, kind, workdir, sources[sid], filename, original, after))
                result["changed_files"] += 1
            for old in notes:
                if old == current:
                    continue
                if still_idle and not still_idle():
                    result["deferred"] = True
                    return
                if not dry_run:
                    if memory.read_file(path) != after:
                        raise memory.MemoryError_("changed")
                    removed = memory.remove(st, kind, workdir, sid, old[2], memory.sha(old[4]), "file", retain=True)
                    result["archive_ids"].append(removed["id"])
                result["merged"] += 1
                result["changed_files"] += 1
            result["conflicts"] += int(conflict)
        except (OSError, memory.MemoryError_):
            result["guarded"] += 1


def run(st, kind, workdir, *, dry_run=False, still_idle=None):
    if kind not in memory.HARNESSES or not workdir:
        raise memory.MemoryError_("unknown_source")
    result = {"harness": kind, "dry_run": dry_run, "merged": 0, "archived": 0, "conflicts": 0,
              "index_fixed": 0, "changed_files": 0, "guarded": 0, "archive_ids": []}
    with st.file_lock("memory-tidy"):
        files = list(_files(kind, workdir))
        _merge_notes(st, kind, workdir, files, result, dry_run, still_idle)
        # remove() also updates a native index. Re-read it before calculating independent index repairs.
        if not dry_run and result["changed_files"]:
            files = list(_files(kind, workdir))
        for s, filename, path, raw in files:
            if still_idle is not None and not still_idle():
                result["deferred"] = True
                break
            text = raw.decode("utf-8")
            new, counts = _plan(text, s, filename, path, workdir)
            if new == text:
                continue
            after = new.encode()
            if not dry_run:
                try:
                    tid = _replace(st, kind, workdir, s, filename, raw, after)
                except (memory.MemoryError_, OSError):
                    result["guarded"] += 1
                    continue
                result["archive_ids"].append(tid)
            for key, val in counts.items():
                result[key] += val
            result["changed_files"] += 1
    return result


def restore(st, record):
    with st.file_lock("memory-tidy"):
        path = resolve(record["harness"], record["workdir"], record["src"], record["file"])
        if os.path.realpath(path) != record["path"]:
            raise memory.MemoryError_("unknown_source")
        memory.check_data_path(path)
        data = record["text"].encode()
        current = memory.sha(memory.read_file(path))
        if current != record["before"]:
            memory._write_atomic(path, data, record["after"])
    return record
