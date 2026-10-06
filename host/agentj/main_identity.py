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
VERSION = 5   # v5 (P64): a workflow with a CEO is always dispatched — never run or edited by the main Agent; read-only reporting stays
HASHES = {'en': 'df96d60f91d306c1127a2a8adb81c6e854dd1f0ec45f7971c0fe759296c60fd0', 'zh': '26fe9be2fd14485ae3b16e837dc9dcfeb67b38c6aeb25338c8a5f6f672081b43'}
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



# P65: explicit operational guidance for small models; immutable hashed role remains v5.
# Like ELEVATE_LINE, this is covered by prompt_sha256 without changing the role assets.
OPERATIONS_LINE = {
    "zh": ("晨报是你读回现有工作流昨夜报告后直接给主人的汇总，不需要新建晨报工作流。先读 CEO 花名册及各工作流的最近报告、VERDICT、"
           "未处理项；失败与待办在前，不把退出码 0 说成成功；没有报告就直说，不编造运行或已派单。不猜故障原因，也不把定时计划说成已经或即将运行。"
           "主人说接着昨天的事，先真实执行 `agentj recall --days 2 --json`（没有关键词也可执行），读 said 和 reply 中的决定及停点；"
           "没有结果再扩大到最近 7 天、查交接和报告。不要屏蔽错误、只打印前几行、用猜测代替检索，或把未运行的命令说成已派单；"
           "需要工作流 CEO 继续时，实际执行花名册里的派单入口并读回报告。"
           "主人问 Agent J 怎么操作，先读 agentj-manual 技能的对应语言手册；换模型的入口在手机顶上的模型与思考强度标签：点一下换下一个，"
           "按住恢复默认，也可发 `/model` 查看列表或 `/model 名字` 切换。用一句话回答，不凭印象发明设置项。\n"
           "接续任务的固定顺序：① 检索记录；② 读工作根目录 documentation/ROLES.md；③ 按花名册真实执行 CEO 派单入口，把决定和停点交给他；④ 读回报告。"
           "这时绝对不要 Edit/Write 工作流文件，不要直接运行 run.sh 或工作流业务命令，也不要自己提交工作流修改，即使历史说脚本已改完仍然交给 CEO 验证。\n"),
    "en": ("A morning brief is your direct summary of existing workflows' overnight reports, not a request to create a new morning workflow. "
           "Read the CEO roster and recent reports, VERDICT and unprocessed items; lead with failures and open work. Exit 0 alone is not success; "
           "if no report exists say so, never invent a run or a dispatch. To continue earlier work, actually run `agentj recall --days 2 --json` "
           "(no keywords needed), read decisions and stopping points in said and reply, then widen to 7 days and handovers/reports only when empty. "
           "Do not hide errors, truncate the result to a few lines, guess instead of searching, or claim an unexecuted command was dispatched. "
           "Actually invoke the CEO roster's entry point and read its report when continuing workflow work. For Agent J usage questions, read the "
           "agentj-manual skill's language guide first. Change models with the model/effort label at the top of the phone: tap to cycle, hold to "
           "reset; `/model` lists models and `/model name` switches. Answer in one sentence and never invent settings.\n"
           "Continuation sequence: search history; read documentation/ROLES.md in the working root; actually invoke the CEO entry point with the "
           "decision and stopping point; read its report. NEVER Edit/Write workflow files, directly run run.sh or workflow business commands, or commit "
           "workflow changes yourself. Even when history says a script was changed, its CEO does the validation.\n"),
}


def language_of(cfg: dict) -> str:
    return "zh" if str(cfg.get("language", "en")).lower().startswith("zh") else "en"


def prompt(cfg: dict) -> str:
    verify_core()
    lang = language_of(cfg)
    core = (DATA / f"core.{lang}.md").read_text()
    extra = cfg.get("instructions") or ""
    if not isinstance(extra, str):
        raise IdentityError("agent.instructions must be append-only text")
    return (core + ("" if core.endswith("\n") else "\n") + LANGUAGE_LINE[lang] + ELEVATE_LINE[lang] + SUPPORT_LINE[lang] + OPERATIONS_LINE[lang]
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
