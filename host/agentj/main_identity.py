"""Packaged main-Agent constitution, native injection and metadata-only evidence.

Codex 0.159.2 generated ThreadStart/ResumeParams: developerInstructions string.
OpenCode official server API: prompt_async accepts system string on every prompt.
https://dev.opencode.ai/docs/server/
The pinned hashes detect accidental/package asset changes, not an OS owner replacing code.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

DATA = Path(__file__).with_name("identity")
VERSION = 2
HASHES = {'en': 'bd4152c41c6746e427f832f7f16eade4e9e0bca29245a9f7d530af4cf42ba229', 'zh': '277995885b8bfb911b426a09df329407db1f4f6c11e3ea91b4185c23aa76baef'}
MECHANISMS = {"claude": "append-system-prompt", "codex": "developerInstructions", "opencode": "prompt_async.system"}

class IdentityError(ValueError):
    pass

def verify_core() -> dict:
    try:
        manifest = json.loads((DATA / "manifest.json").read_text())
        if manifest != {"version": VERSION, "hashes": HASHES}:
            raise IdentityError("main Agent core manifest changed")
        for lang, digest in HASHES.items():
            if hashlib.sha256((DATA / f"core.{lang}.md").read_bytes()).hexdigest() != digest:
                raise IdentityError("main Agent core changed: " + lang)
    except (OSError, ValueError) as e:
        raise IdentityError("main Agent core integrity failed") from e
    return {"version": VERSION, "hashes": dict(HASHES)}

def working_root(cfg: dict) -> Path:
    return Path(cfg.get("working_root") or cfg.get("dir") or "~/coding").expanduser().resolve()

def validate_working_root(cfg: dict, st) -> Path:
    """Reject unsafe/missing main cwd even when the human disabled the fence."""
    raw = cfg.get("working_root") or cfg.get("dir") or "~/coding"
    if not isinstance(raw, str) or not (Path(raw).is_absolute() or raw == "~" or raw.startswith("~/")):
        raise IdentityError("working root must be an absolute path or ~/path")
    root = working_root(cfg)
    if not root.is_dir():
        raise IdentityError("working root must be an existing directory")
    from .fence import protected_paths
    for value in protected_paths(st):
        protected = Path(value).resolve()
        if root == protected or protected in root.parents:
            raise IdentityError("working root is inside a protected Agent J path")
    return root

def prompt(cfg: dict) -> str:
    verify_core()
    lang = "zh" if str(cfg.get("language", "en")).lower().startswith("zh") else "en"
    core = (DATA / f"core.{lang}.md").read_text()
    extra = cfg.get("instructions") or ""
    if not isinstance(extra, str):
        raise IdentityError("agent.instructions must be append-only text")
    return core + ("\n<User preferences — append only; core takes precedence>\n" + extra + "\n</User preferences>\n" if extra else "")

def expected(cfg: dict, harness: str) -> dict:
    text = prompt(cfg)
    lang = "zh" if str(cfg.get("language", "en")).lower().startswith("zh") else "en"
    return {"version": VERSION, "language": lang, "core_sha256": HASHES[lang],
            "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(), "mechanism": MECHANISMS[harness],
            "working_root_sha256": hashlib.sha256(str(working_root(cfg)).encode()).hexdigest()}

def audit(cfg: dict, harness: str, st, session_id=None) -> None:
    st.log("agent_identity", agent=harness, identity_session=session_id, **expected(cfg, harness))

def latest_audit(st, cfg: dict) -> tuple[bool, str]:
    """Check the latest main session evidence for this harness, never log instruction text.

    This proves the host submitted native fields, not what a remote model understood.
    """
    want = expected(cfg, cfg["kind"])
    try:
        with st.log_path.open("rb") as f:
            f.seek(max(0, f.seek(0, 2) - 1024 * 1024))
            lines = f.read().splitlines()
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get("ev") == "agent_identity" and row.get("agent") == cfg["kind"]:
                ok = all(row.get(k) == v for k, v in want.items())
                return ok, "native identity injection metadata matches" if ok else "restart main Agent: identity metadata differs"
    except OSError:
        pass
    return False, "no verified main Agent launch yet; start a conversation"
