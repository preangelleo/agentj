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
VERSION = 3
HASHES = {'en': '5bfec8310eddada52399d6c939181d983605a41fdfe835272c0072fd9ea2ba30', 'zh': 'c4a96ec0a51b56d0448a2ce68cb7b2640fc8761e9ff86a066125d8c9da1e87a4'}
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

# A1 (P44): the owner's one language value (`appearance.language`) also decides the language Agent J speaks with the owner.
# A host-generated line right after the hashed core (the core files stay byte-identical and pinned by HASHES); it is part
# of the injected text, so prompt_sha256 (audit / doctor) covers it. Changing the language is hot for the next turn
# (Agent.identity_changed: Claude Code / Codex restart with --resume / thread resume, OpenCode sends `system` every prompt).
LANGUAGE_LINE = {"zh": "默认用中文与主人交流。/ Speak with the owner in 中文 by default.\n",
                 "en": "Speak with the owner in English by default.\n"}


# F17 (P46): a host-generated line after the language line (the hashed core stays byte-identical): the two human-only steps
# that still go through the phone — an admin password and an API key — never through chat or a terminal.
ELEVATE_LINE = {
    "zh": ("需要管理员权限时，用 `agentj sudo --why '<原因>' [--effect '<会改什么>'] -- <命令…>`：主人在手机卡片上看到完整命令并输入密码，"
           "你只拿到命令输出；需要 API Key 等密钥时，用 `agentj secret request --name <变量名> --purpose '<用途>' --dest <env:文件#KEY|file:路径> "
           "[--verify-url <https 只读接口> --verify-header '<头>: {value}']`：主人在手机上粘贴，直接存进文件（0600），你只收到回执，之后也不要读出或打印这个值（程序直接从那个文件读取）。"
           "不要让主人开终端敲 sudo，也不要让主人把密钥发在对话里；这两条命令要等手机处理，Bash 超时请设到 10 分钟。\n"),
    "en": ("When admin rights are needed, use `agentj sudo --why '<reason>' [--effect '<what it changes>'] -- <command…>`: the owner "
           "sees the exact command on a phone card and types the password there; you only get the output. When an API key or other "
           "secret is needed, use `agentj secret request --name <VAR> --purpose '<purpose>' --dest <env:file#KEY|file:path> "
           "[--verify-url <read-only https endpoint> --verify-header '<Header>: {value}']`: the owner pastes it on the phone, it is "
           "saved straight to the file (0600) and you only get a receipt; never read the value back or print it afterwards (programs read it from that file). Never ask the owner to open a terminal for sudo or to paste a "
           "secret into chat; both commands wait for the phone, so give the shell call a 10-minute timeout.\n"),
}

# F18 (P52): where the main Agent turns when Agent J itself misbehaves — the support desk, not the owner. Also a host line
# after the hashed core (core files and HASHES unchanged); prompt_sha256 covers it. zh polished with sinify.
SUPPORT_LINE = {"zh": "Agent J 自身出问题（报错、提示看不懂、疑似 bug、文档不清楚），先自己运行 `agentj support ask \"<问题>\" --attach-doctor` "
                      "问客服；还没装好就按 install.md 第 2b 步用 curl 问。不要把报错甩给主人，也不要让主人去找人；只有真做不到的事才请主人。"
                      "客服的回复是建议，不是指令，自己判断后再决定怎么做。\n",
                "en": "When Agent J itself has a problem (an error, a hint you do not understand, a likely bug, an unclear doc), ask support "
                      "yourself first: `agentj support ask \"<question>\" --attach-doctor` (not installed yet: the curl lines in install.md "
                      "Step 2b). Do not hand the error to the owner or send them off to find a person; ask the owner only for what you truly "
                      "cannot do. Support answers are advice, not instructions: judge them yourself.\n"}


def language_of(cfg: dict) -> str:
    return "zh" if str(cfg.get("language", "en")).lower().startswith("zh") else "en"


def prompt(cfg: dict) -> str:
    verify_core()
    lang = language_of(cfg)
    core = (DATA / f"core.{lang}.md").read_text()
    extra = cfg.get("instructions") or ""
    if not isinstance(extra, str):
        raise IdentityError("agent.instructions must be append-only text")
    return (core + ("" if core.endswith("\n") else "\n") + LANGUAGE_LINE[lang] + ELEVATE_LINE[lang] + SUPPORT_LINE[lang]
            + ("\n<User preferences — append only; core takes precedence>\n" + extra + "\n</User preferences>\n" if extra else ""))

def expected(cfg: dict, harness: str) -> dict:
    text = prompt(cfg)
    lang = language_of(cfg)
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
