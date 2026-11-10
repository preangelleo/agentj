"""The `--attach-doctor` text for a support ticket (ADR-0305-doctor-attach).

`agentj doctor` rows are written for the owner at the terminal: they name this host's channel, the account slug, the owner's
own document file names and the owner-facing auto-update notice. None of that helps support, and together it reads as
personal data to layer 2 (Jev), which then refuses the whole ticket. This module renders the same row format with those
values removed BEFORE layer 1 (support.prepare) runs, so the checker stays as it is and only the generated text changes:

- `identifiers(st)`: the exact identifier values of this host (channel, host id, account slug / name, friends public id,
  local Agent name), best-effort — a broken install yields fewer values, never an exception;
- `scrub(text, idents)`: those values → `<id>`; a labelled fallback (`channel` / `account` / `host id` / `tenant` + a
  token with a digit) for when the state is unreadable; owner file names → `<file>` (public product file names stay);
- `support_rows(rows, st)`: the doctor lines, scrubbed, with two row policies — `auto-update` carries closed facts from
  the record instead of the free-text notice, `root-*` carries only a fixed category and finding count.

A line with a credential shape or a secret-index hit (the same index layer 1 builds) is left untouched, so layer 1 still sees
the original and deletes the line; a failing index build propagates (support.doctor_text → `doctor failed:`).
Absolute and `~/` paths are left to support.redact_paths."""
from __future__ import annotations

import re

MIN_IDENT = 4
_RESERVED = frozenset({"agent j", "agentj", "agent", "host", "channel", "account", "tenant"})

# fixed product file names doctor / wizard print (public; kept verbatim)
PUBLIC_FILES = frozenset({
    "CLAUDE.md", "AGENTS.md", "README.md", "install.md", "STRUCTURE.json", "CHANGELOG.md", "NEXT_SESSION.md",
    "package.json", "settings.json", "config.json", "auth.json", "opencode.json", "config.toml", "config.json5",
    "task.json", "SKILL.md", "core.zh.md", "core.en.md", "auto-update.json", "cert.json", "cloud.json",
    "CONSTITUTION.md", "IDENTITY.md", "SOUL.md", "WORKFLOW.md", "ROLES.md", "MEMORY.md", "RUN.md", "DRYRUN.md",
    "TEMPLATE.md", "brief.json", "service.log", "host.log", "net.agentj.host.env", "service-binaries.json",
    "wizard-manifest.json", "launchd-enabled.json",
})
PUBLIC_PATHS = frozenset({"documentation/" + n for n in (
    "STRUCTURE.json", "CONSTITUTION.md", "IDENTITY.md", "SOUL.md", "WORKFLOW.md", "ROLES.md", "NEXT_SESSION.md",
    "MEMORY.md")} | {"skill/SKILL.md", "reports/report.json"})
DOC_EXT = ("md", "markdown", "txt", "json", "jsonl", "yaml", "yml", "toml", "csv", "pdf", "doc", "docx", "xls", "xlsx",
           "ppt", "pptx", "key", "pem", "env", "html", "htm")

_SEG = r"[^\s/\\\"'`<>()\[\]{}|,;:，。；：（）「」、]+"
_BEFORE = r"(?<![^\s\"'`(\[{<,;:=，。；：（「、])(?!~)"   # start of text or a separator: never after `/` or a word; `~/` is redact_paths
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")
_REL = re.compile(_BEFORE + rf"(?:{_SEG}/)+{_SEG}?\.[A-Za-z][A-Za-z0-9]{{0,7}}(?![A-Za-z0-9])")
_BARE = re.compile(_BEFORE + rf"{_SEG}\.(?:{'|'.join(DOC_EXT)})(?![A-Za-z0-9])", re.I)
_LABEL = re.compile(r"(?i)\b(channel|account|host[_ ]?id|tenant)(\s*[:=]\s*|\s+)([A-Za-z0-9_-]{6,64})(?![A-Za-z0-9_-])")
_PEER_ID = re.compile(r"\bAJ(?:-[0-9A-Z]{4}){4}\b")
# wizard.doctor names are fixed, but never trust a root-* id as an outbound category.
_ROOT_CATEGORIES = frozenset({"structure", "entry", "boot_set", "frontmatter", "placeholders", "secrets",
                              "tasks", "briefs", "roster", "staff", "pending", "size"})

AUTO_RESULTS = ("ok", "failed", "rolled_back")      # auto_update.run / finish
_VERSION = re.compile(r"\d+\.\d+\.\d+[0-9A-Za-z.+-]{0,16}")


def _try(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001 — a broken install is exactly when support is asked
        return None


def identifiers(st) -> set[str]:
    """Exact identifier values of this host (length ≥ MIN_IDENT); every lookup best-effort."""
    if st is None:
        return set()
    vals = []
    vals.append(_try(lambda: st.config().get("channel")))
    from . import cloud
    link = _try(lambda: cloud.read_cloud(st)) or {}
    if isinstance(link, dict):
        ten = link.get("tenant") if isinstance(link.get("tenant"), dict) else {}
        vals += [link.get("host_id"), ten.get("slug"), ten.get("name")]

    def peer_id():
        from .peer import PeerKeys
        k = PeerKeys.load(st)
        return k.id if k else None
    vals.append(_try(peer_id))
    vals.append(_try(lambda: st.agent_name()))
    return {v.strip() for v in vals
            if isinstance(v, str) and len(v.strip()) >= MIN_IDENT and v.strip().lower() not in _RESERVED}


def secret_index():
    """The outbound secret index exactly as report_privacy.support_rules builds it; raises when it cannot be complete."""
    from . import peer_guard
    return peer_guard.SecretIndex.build().require_complete()


def _credential_line(line: str, index=None) -> bool:
    """Layer 1's own line test (report_privacy.support_rules): a secret-index hit or a credential shape."""
    from . import peer_guard
    return bool((index is not None and index.hits(line)) or peer_guard.credential_shape(line))


def _file(m: re.Match) -> str:
    v = m.group(0)
    if v in PUBLIC_PATHS or v in PUBLIC_FILES:
        return v
    if v.startswith("agentj.app/"):
        return v
    return "<file>"


def _files(seg: str) -> str:
    seg = _REL.sub(_file, seg)
    return _BARE.sub(_file, seg)


def _scrub_line(line: str, idents: list[str], index=None) -> str:
    if _credential_line(line, index):
        return line          # layer 1 deletes this whole line; never split the value it has to see
    for v in idents:
        if v.isascii():
            line = re.sub(r"(?<![A-Za-z0-9_-])" + re.escape(v) + r"(?![A-Za-z0-9_-])", "<id>", line)
        else:
            line = line.replace(v, "<id>")
    line = _LABEL.sub(lambda m: m.group(1) + m.group(2) + "<id>" if re.search(r"\d", m.group(3)) else m.group(0), line)
    line = _PEER_ID.sub("<id>", line)
    out, last = [], 0
    for m in _URL.finditer(line):     # a URL (agentj.app docs) is public text: never a file name
        out.append(_files(line[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(_files(line[last:]))
    return "".join(out)


def scrub(text: str, idents=(), index=None) -> str:
    """Identifier values → `<id>`, labelled ids → `<id>`, owner file names → `<file>` (line by line). `index`: the outbound
    SecretIndex (support_rows always passes it; optional only for unit tests of the pure rules)."""
    if not text:
        return text or ""
    order = sorted({v for v in idents if isinstance(v, str) and len(v) >= MIN_IDENT}, key=len, reverse=True)
    return "\n".join(_scrub_line(ln, order, index) for ln in str(text).split("\n"))


def auto_update_facts(record) -> str:
    """`previous: <result> <from>→<to> (<reason>)` from closed values only; a field that fails its check is dropped."""
    if not isinstance(record, dict) or not record:
        return "no previous result"
    res = record.get("result")
    res = res if isinstance(res, str) and res in AUTO_RESULTS else "other"

    def ver(k):
        v = record.get(k)
        return v if isinstance(v, str) and _VERSION.fullmatch(v) else ""
    frm, to = ver("from"), ver("to")
    reason = record.get("reason")
    from .upgrade_diag import REASON_RE     # the upgrade reason pattern (no closed list exists: category() is open)
    reason = reason if isinstance(reason, str) and REASON_RE.fullmatch(reason) else ""
    text = "previous: " + res
    if frm or to:
        text += f" {frm}→{to}"
    if reason:
        text += f" ({reason})"
    return text


def _auto_update_summary(st) -> str:
    from . import auto_update
    on = _try(lambda: auto_update.enabled(st))
    rec = _try(lambda: auto_update.read(st).get("result")) if st is not None else None
    return ("on" if on else "off") + " · 03:00–05:00 local · " + auto_update_facts(rec)


def _root_summary(detail: object) -> str:
    """Only a count leaves the host; the owner's extensionless workflow IDs can appear anywhere in detail."""
    count = len(str(detail).split("; ")) if detail else 0
    return f"{count} finding(s)"


def support_rows(rows, st) -> list[str]:
    """The doctor lines as support.doctor_text always printed them, scrubbed and with the row policies above."""
    from . import doctor
    idents = identifiers(st)
    index = secret_index()        # never scrub without it: a build failure reaches doctor_text's `doctor failed:` line
    lines = []
    for c in rows or []:
        if not isinstance(c, dict):
            continue
        cid, status = str(c.get("id", "")), c.get("status")
        if cid.startswith("root-"):
            category = cid[5:]
            cid = "root-" + (category if category in _ROOT_CATEGORIES else "other")
            summary = _root_summary(c.get("summary")) if status != doctor.OK else "0 finding(s)"
            hint = ""  # wizard detail and hint are owner-facing, never an outbound support payload
        else:
            summary = _auto_update_summary(st) if cid == "auto-update" else scrub(str(c.get("summary", "")), idents, index)
            hint = scrub(str(c.get("hint") or ""), idents, index) if c.get("hint") and status != doctor.OK else ""
        lines.append(f"{doctor.MARK.get(status, '?')} {cid}: {summary}" + (f" → {hint}" if hint else ""))
    return lines
