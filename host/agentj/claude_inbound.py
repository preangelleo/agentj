"""Owner-controlled Claude peer ingress. Never print settings or peer credentials."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile
import uuid


class SettingsError(ValueError):
    pass


def path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"


def _load(p: Path | None = None) -> tuple[dict, bytes | None]:
    p = p or path()
    if p.is_symlink():
        raise SettingsError("Claude settings is a symlink; edit its target yourself")
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return {}, None
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise SettingsError("Claude settings is not valid JSON; it was not changed") from None
    if not isinstance(doc, dict):
        raise SettingsError("Claude settings must be a JSON object; it was not changed")
    return doc, raw


def read() -> dict:
    return _load()[0]


def value() -> str:
    v = read().get("crossSessionInbound")
    return v if v in ("accept", "hold", "refuse") else "unset" if v is None else "invalid"


def update(change, p: Path | None = None) -> bool:
    """Serialize our writers, back up original bytes 0600, fsync + atomic replace.

    The callback mutates only its owned keys. A no-op creates no backup.
    Original settings bytes remain local; contents are never printed.
    """
    p = p or path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = os.open(p.parent / ".agentj-inbound.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc, original = _load(p)
        before = json.dumps(doc, sort_keys=True)
        change(doc)
        if json.dumps(doc, sort_keys=True) == before:
            return False
        if original is not None:
            backup = p.with_name(p.name + ".agentj-backup-" + uuid.uuid4().hex)
            with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
                f.write(original)
                f.flush()
                os.fsync(f.fileno())
        fd, tmp = tempfile.mkstemp(prefix=".agentj-inbound-", dir=p.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            # Detect an outside writer before replacing; leave its edits intact.
            current = p.read_bytes() if p.exists() else None
            if p.is_symlink() or current != original:
                raise SettingsError("Claude settings changed concurrently; retry after checking it")
            os.replace(tmp, p)
            dir_fd = os.open(p.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return True


def set_enabled(enabled: bool) -> bool:
    return update(lambda doc: doc.update(crossSessionInbound="accept" if enabled else "hold"))


def managed_paths() -> list[Path]:
    """Native managed files are read-only; never create or repair them."""
    import sys
    base = Path("/Library/Application Support/ClaudeCode" if sys.platform == "darwin" else "/etc/claude-code")
    return [base / "managed-settings.json", *sorted((base / "managed-settings.d").glob("*.json"))]


def effective(directory=None) -> tuple[str, Path, str]:
    from .claude_statusline import layers
    managed = None
    for p in managed_paths():
        doc, _ = _load(p)
        if "crossSessionInbound" in doc:
            managed = ("managed", p, _value(doc["crossSessionInbound"]))
    if managed is not None:
        return managed
    for layer, p in layers(directory):
        doc, _ = _load(p)
        if "crossSessionInbound" in doc:
            return layer, p, _value(doc["crossSessionInbound"])
    return "user", path(), "unset"


def _value(v) -> str:
    return v if v in ("accept", "hold", "refuse") else "unset" if v is None else "invalid"


def ensure_shared_default(st, config=None) -> bool:
    """Shared mode needs accept in the effective writable layer, with a local backup.

    A project policy is overridden in private local settings; shared repository and
    managed files are never modified. Existing native sessions may cache the old value.
    """
    cfg = config if config is not None else st.agent_config()
    if not cfg or cfg.get("kind") != "claude" or cfg.get("session_mode", "shared") != "shared":
        return False
    directory = cfg.get("dir")
    layer, _, current = effective(directory)
    if layer == "managed" or current == "accept":
        return False
    from .claude_statusline import layers
    target = dict(layers(directory))["local" if layer in ("local", "local-legacy", "project") else "user"]
    changed = update(lambda doc: doc.update(crossSessionInbound="accept"), target)
    if changed:
        import time
        st.write_private(st.root / "claude-inbound-notice.json", json.dumps({
            "pending": True, "layer": layer, "previous": current, "changed_at": time.time(),
            "restart_required": True}).encode())
    return changed


def diagnostic(st, lang="zh", config=None) -> str:
    cfg = config if config is not None else st.agent_config() or {}
    layer, _, current = effective(cfg.get("dir"))
    if current != "accept":
        return (f"Claude phone messages are blocked by {layer} crossSessionInbound={current}. "
                "Managed policy is read-only; ask its administrator to enable accept."
                if lang == "en" and layer == "managed" else
                f"Claude phone messages are blocked by {layer} crossSessionInbound={current}; "
                "run agentj doctor to repair writable settings."
                if lang == "en" else
                f"手机消息被 {layer} 层 crossSessionInbound={current} 拦截。" +
                ("组织 managed 策略只读，需管理员改为 accept。" if layer == "managed" else
                 "运行 agentj doctor 修正可写设置。"))
    return (f"Claude settings: {layer} crossSessionInbound=accept. Sessions started before the change "
            "may still hold messages; start a new session or run /clear on the computer for it to take effect. "
            "Session/CLI and remote managed overrides cannot be verified from these files."
            if lang == "en" else
            f"Claude 实际文件设置：{layer} 层 crossSessionInbound=accept。设置前启动的会话仍可能扣住消息，"
            "需新会话或在电脑运行 /clear 生效；会话/CLI 与远程 managed 覆盖无法从文件核实。")


def default_notice(st, lang="zh") -> str | None:
    cfg = st.agent_config() or {}
    if cfg.get("kind") != "claude" or cfg.get("session_mode", "shared") != "shared":
        return None
    p = st.root / "claude-inbound-notice.json"
    try:
        pending = json.loads(p.read_text()).get("pending") is True
    except (OSError, ValueError, AttributeError):
        pending = False
    layer, _, current = effective(cfg.get("dir"))
    if current != "accept":
        # Even without a migration, tell the paired phone the concrete blocking layer.
        return diagnostic(st, lang)
    if not pending:
        return None
    return (("Enabled: " if lang == "en" else "已开启：") + diagnostic(st, lang) +
            (" To disable, run agentj config claude-inbound off (shared startup re-enables it)."
             if lang == "en" else "可运行 agentj config claude-inbound off 临时关闭；共享模式重新启动会恢复开启。"))


def notice_delivered(st) -> None:
    st.write_private(st.root / "claude-inbound-notice.json", b'{"pending":false}\n')

def command(args) -> int:
    if len(args) != 1 or args[0] not in ("on", "off", "status"):
        print("Usage: agentj config claude-inbound on|off|status")
        return 2
    try:
        if args[0] != "status":
            set_enabled(args[0] == "on")
        print("Claude crossSessionInbound: " + value())
        print("on 接受持本机 peer token 的进程消息；off 等你批准。组织/项目策略仍优先。 / "
              "on accepts messages authenticated with a local peer token; off holds them for approval. "
              "Managed/repository policies still apply.")
        return 0
    except (OSError, SettingsError):
        print("Claude settings 未修改或无法读取；检查 JSON、文件权限及并发编辑。 / "
              "Could not safely update/read Claude settings; check JSON, permissions and concurrent edits.")
        return 1
