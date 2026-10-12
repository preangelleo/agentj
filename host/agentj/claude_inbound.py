"""Owner-controlled Claude peer ingress. Never print settings or peer credentials."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
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


# Managed policy files (highest precedence; Agent J only reads them). Kept in step with agent._MANAGED.
MANAGED = ("/etc/claude-code/managed-settings.json", "/Library/Application Support/ClaudeCode/managed-settings.json")
VALUES = ("accept", "hold", "refuse")


def meta_path() -> Path:
    """Agent J's own private record (owner `off`, last change time, sessions started before it); never settings content."""
    return path().parent / "agentj-inbound.json"


def _meta() -> dict:
    p = meta_path()
    try:
        if p.is_symlink():
            return {}
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_meta(**changes) -> None:
    doc = {**_meta(), **changes}
    p = meta_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=".agentj-inbound-meta-", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def layers(directory=None) -> list[tuple[str, Path]]:
    from .claude_statusline import layers as native_layers
    return [("managed", p) for p in managed_paths()] + native_layers(directory)


def _legacy_off() -> bool:
    """A pre-P127 `claude-inbound off`: no Agent J record, user layer hold, and one of our own backups shows accept."""
    if "owner_off" in _meta():
        return False
    try:
        if read().get("crossSessionInbound") != "hold":
            return False
    except (OSError, SettingsError):
        return False
    for b in path().parent.glob(path().name + ".agentj-backup-*"):
        try:
            if json.loads(b.read_bytes()).get("crossSessionInbound") == "accept":
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def owner_off() -> bool:
    return _meta().get("owner_off") is True or _legacy_off()


def _apply(directory, want: str) -> bool:
    """Make `want` the effective value without touching managed policy or a shared project file: user/unset → user layer,
    local → in place, project → a private local-layer override."""
    layer, _, current = effective(directory)
    if layer == "managed":
        return False
    changed = False
    if want == "hold":
        changed = update(lambda doc: doc.update(crossSessionInbound="hold"))
        if not directory:
            return changed
        layer, _, current = effective(directory)
    if current == want or layer == "managed":
        return changed
    target = path() if layer == "user" else dict(layers(directory))["local"]
    changed = update(lambda doc: doc.update(crossSessionInbound=want), target) or changed
    return changed


def _record_change(directory) -> None:
    """Live sessions in the folder started before this change keep the old value until /clear or a new session."""
    stale = []
    if directory:
        try:
            from .shared import claude_sessions
            stale = [d["sessionId"] for d in claude_sessions(directory) if isinstance(d.get("sessionId"), str)]
        except (ImportError, OSError, KeyError, TypeError):
            stale = []
    _write_meta(changed_at=int(time.time() * 1000), stale_sessions=stale)


def set_enabled(enabled: bool, directory=None) -> bool:
    """Owner command. `off` is recorded as the owner's choice (never auto-reverted); `on` clears it.
    With `directory` the effective layer for that folder is changed (never managed, never a shared project file)."""
    changed = _apply(directory, "accept" if enabled else "hold")
    _write_meta(owner_off=not enabled)
    if changed:
        _record_change(directory)
    return changed


def _shared_dir(st, config=None):
    cfg = config if config is not None else st.agent_config()
    if not cfg or cfg.get("kind") != "claude" or cfg.get("session_mode", "shared") != "shared":
        return None
    return cfg.get("dir") or None


def managed_paths() -> list[Path]:
    """Native managed files are read-only; never create or repair them."""
    import sys
    base = Path("/Library/Application Support/ClaudeCode" if sys.platform == "darwin" else "/etc/claude-code")
    return [*[Path(p) for p in MANAGED if Path(p).parent == base or Path(p).parent not in (Path("/etc/claude-code"), Path("/Library/Application Support/ClaudeCode"))], *sorted((base / "managed-settings.d").glob("*.json"))]


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
    """Shared Claude: make the EFFECTIVE value for the session folder accept (P127). Repairs a default/user hold in place,
    a local hold in place and a project hold from the private local layer. Managed policy, refuse/unknown values and the
    owner's explicit `claude-inbound off` are only reported (hold_reason / doctor / phone)."""
    cfg = config if config is not None else st.agent_config()
    if not cfg or cfg.get("kind") != "claude" or cfg.get("session_mode", "shared") != "shared":
        return False
    directory = cfg.get("dir") or None
    layer, _, current = effective(directory)
    if layer == "managed" or current not in ("hold", "unset"):
        return False
    if _meta().get("owner_off") is True:
        return False
    if layer == "user" and current == "hold" and _legacy_off():
        _write_meta(owner_off=True)         # make an older version's `off` explicit
        return False
    changed = _apply(directory, "accept")
    if changed:
        _record_change(directory)
        st.write_private(st.root / "claude-inbound-notice.json", b'{"pending":true}\n')
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


def hold_reason(directory=None) -> str | None:
    """Why Claude Code may not take a phone message for `directory`, or None (accept is in effect: it is busy).
    managed · owner_off · project · local · user · default · stale · unreadable"""
    try:
        layer, _, current = effective(directory)
    except (OSError, SettingsError):
        return "unreadable"
    if current != "accept":
        if layer == "managed":
            return "managed"
        if _meta().get("owner_off") is True or (layer == "user" and current == "hold" and _legacy_off()):
            return "owner_off"
        if layer in ("project", "local", "local-legacy"):
            return "local" if layer == "local-legacy" else layer
        return "default" if current == "unset" else "user"
    meta = _meta()
    stale = meta.get("stale_sessions")
    if directory and isinstance(stale, list) and stale:
        try:
            from .shared import claude_sessions
            if any(d.get("sessionId") in stale for d in claude_sessions(directory)):
                return "stale"
        except (ImportError, OSError):
            pass
    return None


_REASONS = {
    "managed": ("组织策略（managed settings）要求跨会话消息等电脑批准，Agent J 不会改它。请在电脑上批准，或请管理员调整。",
                "your organization's managed policy holds cross-session messages for desktop approval; Agent J never "
                "changes it. Approve it on the computer or ask your administrator."),
    "owner_off": ("你之前运行过 agentj config claude-inbound off，所以手机消息要在电脑上逐条批准。想恢复运行 "
                  "agentj config claude-inbound on。",
                  "you ran agentj config claude-inbound off earlier, so phone messages wait for approval on the computer. "
                  "Run agentj config claude-inbound on to turn it back on."),
    "project": ("这个文件夹的项目设置（.claude/settings.json）拦下了跨会话消息。运行 agentj config claude-inbound on "
                "会在你本机的私有层（settings.local.json）覆盖，不改团队共享文件。",
                "this folder's project settings (.claude/settings.json) hold cross-session messages. Run agentj config "
                "claude-inbound on to override them in your private local layer (settings.local.json); the shared team "
                "file is not changed."),
    "local": ("这个文件夹的本机私有设置（.claude/settings.local.json）拦下了跨会话消息。运行 agentj config claude-inbound on。",
              "this folder's private local settings (.claude/settings.local.json) hold cross-session messages. "
              "Run agentj config claude-inbound on."),
    "user": ("你的 Claude Code 用户设置（~/.claude/settings.json）拦下了跨会话消息。运行 agentj config claude-inbound on。",
             "your Claude Code user settings (~/.claude/settings.json) hold cross-session messages. "
             "Run agentj config claude-inbound on."),
    "default": ("Claude Code 默认把跨会话消息拦下等你在电脑上批准。运行 agentj config claude-inbound on 后不再逐条确认。",
                "Claude Code holds cross-session messages for desktop approval by default. Run agentj config "
                "claude-inbound on to stop confirming each one."),
    "stale": ("这个 Claude Code 会话是在开启手机消息之前启动的，还在用旧设置。在电脑上对它执行 /clear 或开一个新会话后生效。",
              "this Claude Code session started before phone messages were enabled and still uses the old setting. "
              "Run /clear in it on the computer, or start a new session."),
    "unreadable": ("Claude 设置无法读取（JSON 损坏、软链或权限），Agent J 没有改它。运行 agentj doctor 查看。",
                   "Claude settings could not be read (broken JSON, a symlink or permissions); Agent J did not change "
                   "them. Run agentj doctor."),
    "busy": ("设置已允许手机消息，它多半正忙（在跑工具或等你处理电脑上的对话框）。",
             "phone messages are allowed, so it is most likely busy (running a tool or waiting on a desktop dialog)."),
}


def reason_text(reason: str | None, lang: str = "zh") -> str:
    zh, en = _REASONS.get(reason or "busy", _REASONS["busy"])
    return en if lang == "en" else zh


def turn_notice(directory, lang: str = "zh") -> str:
    """The phone's specific explanation when Claude Code did not acknowledge a message in time."""
    reason = hold_reason(directory)
    if lang == "en":
        return ("Claude Code on the computer has not taken this message: " + reason_text(reason, "en") +
                " Check the desktop before resending: the original may still arrive.")
    return "电脑上的 Claude Code 尚未接收这条消息：" + reason_text(reason, "zh") + "请先在电脑核实，别重复发送，原消息仍可能送达。"


def default_notice(st, lang="zh") -> str | None:
    """One-time phone notice for what the shared-Claude defaults just switched on (inbound, status line)."""
    cfg = st.agent_config() or {}
    if cfg.get("kind") != "claude" or cfg.get("session_mode", "shared") != "shared": return None
    texts = []
    p = st.root / "claude-inbound-notice.json"
    try:
        pending = json.loads(p.read_text()).get("pending") is True
    except (OSError, ValueError, AttributeError):
        pending = False
    if pending:
        directory = _shared_dir(st)
        try:
            ok = effective(directory)[2] == "accept"
            stale = ok and hold_reason(directory) == "stale"
        except (OSError, SettingsError):
            ok = stale = False
        if not ok:
            texts.append(diagnostic(st, lang))
            st.write_private(p, b'{"pending":false}\n')
        else:
            texts.append(("Enabled: phone messages no longer need individual confirmation on the computer; "
                          "to disable, run agentj config claude-inbound off. This only affects Claude Code."
                          if lang == "en" else
                          "已开启：手机消息不用在电脑上逐条确认；想关运行 agentj config claude-inbound off。仅影响 Claude Code。")
                         + " " + diagnostic(st, lang))
    try:
        from . import claude_statusline
        line = claude_statusline.default_notice(st, lang)
    except (OSError, ValueError, ImportError):
        line = None
    if line:
        texts.append(line)
    return "\n".join(texts) or None


def notice_delivered(st) -> None:
    st.write_private(st.root / "claude-inbound-notice.json", b'{"pending":false}\n')
    try:
        from . import claude_statusline
        claude_statusline.notice_delivered(st)
    except (OSError, ImportError):
        pass

def command(args) -> int:
    if len(args) != 1 or args[0] not in ("on", "off", "status"):
        print("Usage: agentj config claude-inbound on|off|status")
        return 2
    try:
        directory = None
        try:
            from .state import State
            st = State()
            directory = _shared_dir(st) if st.exists() else None
        except (OSError, ValueError):
            directory = None
        if args[0] != "status":
            set_enabled(args[0] == "on", directory)
        layer, _, current = effective(directory)
        print(f"Claude crossSessionInbound: {current} ({layer} layer)")
        reason = hold_reason(directory)
        if reason:
            print(reason_text(reason, "zh") + " / " + reason_text(reason, "en"))
        print("on 接受持本机 peer token 的进程消息；off 等你批准。组织策略（managed）只报告不改；项目共享文件不改，在本机私有层覆盖。 / "
              "on accepts messages authenticated with a local peer token; off holds them for approval. "
              "Managed policy is reported, never changed; a shared project file is overridden from the private local layer.")
        return 0
    except (OSError, SettingsError):
        print("Claude settings 未修改或无法读取；检查 JSON、文件权限及并发编辑。 / "
              "Could not safely update/read Claude settings; check JSON, permissions and concurrent edits.")
        return 1
