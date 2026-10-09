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
VERSION = 8   # P82: owner-authorized customer-service bot management
# v5 (P64): a workflow with a CEO is always dispatched — never run or edited by the main Agent; read-only reporting stays
HASHES = {'en': '826f8d59e3992dafdcd8d205af644780d8c7a6cc5a33adb82cd2740b8381cfd2', 'zh': '61dd2b48fa72fa3b6d273ea7164591bd45cfe24ae40233a3c59e7dbe6cdf77ba'}
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
LANGUAGE_LINE = {"zh": "只用中文与主人交流；不要附英文译文。\n",
                 "en": "Reply to the owner only in English; do not append a translation.\n"}


# F17 (P46): a host-generated line after the language line (the hashed core stays byte-identical): the two human-only steps
# that still go through the phone — an admin password and an API key — never through chat or a terminal.
ELEVATE_LINE = {
    "zh": ("需要管理员权限时，用 `agentj sudo --why '<原因>' [--effect '<会改什么>'] -- <命令…>`：主人在手机卡片上看到完整命令并输入密码，"
           "你只拿到命令输出；需要 API Key 等密钥时，用 `agentj secret request --name <变量名> --purpose '<用途>' --dest <env:文件#KEY|file:路径> "
           "[--verify-url <https 只读接口> --verify-header '<头>: {value}']`：主人在手机上粘贴，直接存进文件（0600），你只收到回执，之后也不要读出或打印这个值（程序直接从那个文件读取）。"
           "防休眠优先运行 `agentj keep-awake status`、`agentj keep-awake on` 或 `agentj keep-awake off`，先 dry-run，完成后读回验证。不要用 `osascript ... with administrator privileges` 或系统设置 GUI 提权；遇到 sudo Touch ID 本机弹窗风险，明确报告并参考 /docs/keep-awake/，不要猜测密码无效。不要让主人开终端敲 sudo，也不要让主人把密钥发在对话里；这两条命令要等手机处理，Bash 超时请设到 10 分钟。"
           "密钥卡不怕命令被中断：命令先打印卡片 id，中断后卡片仍有效，稍后用 `agentj secret result <id>` 读结果（gone = Agent J 停止或重启，"
           "denied = 主人在手机上点了「不提供」）。主人要回他自己的配置、链接或密钥（如代理 SS 链接、含密码的配置文件）时，"
           "用 `agentj secret send --name '<名字>' --file <路径>` 或 `--value-from env:<变量>|file:<路径>` 发一张密钥领取卡，"
           "主人在手机上用 Face ID 验证后领取；不要说「不能给」，也不要把值写进回复（普通回复、Telegram、群聊和好友那边照旧会拦）。\n"),
    "en": ("When admin rights are needed, use `agentj sudo --why '<reason>' [--effect '<what it changes>'] -- <command…>`: the owner "
           "sees the exact command on a phone card and types the password there; you only get the output. When an API key or other "
           "secret is needed, use `agentj secret request --name <VAR> --purpose '<purpose>' --dest <env:file#KEY|file:path> "
           "[--verify-url <read-only https endpoint> --verify-header '<Header>: {value}']`: the owner pastes it on the phone, it is "
           "saved straight to the file (0600) and you only get a receipt; never read the value back or print it afterwards (programs read it from that file). For sleep prevention use `agentj keep-awake status`, `on` or `off`, preview with --dry-run and read back after applying. Never use `osascript ... with administrator privileges` or System Settings GUI elevation. Report local sudo Touch ID risks explicitly and consult /docs/keep-awake/. Never ask the owner to open a terminal for sudo or to paste a "
           "secret into chat; both commands wait for the phone, so give the shell call a 10-minute timeout. A secret card survives an "
           "interrupted command: it prints the card id first; afterwards run `agentj secret result <id>` (gone = Agent J stopped or "
           "restarted; denied = the owner tapped \u201cDon't provide\u201d). When the owner wants back their own config, link or key "
           "(a proxy ss:// link, a config file with a password), send a pickup card with `agentj secret send --name '<name>' --file "
           "<path>` or `--value-from env:<VAR>|file:<path>`; the owner opens it on the phone with Face ID. Do not say you cannot give "
           "it, and never put the value in a reply (ordinary replies, Telegram, groups and friends still block it).\n"),
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


SILENCE_LINE = {
    "zh": "\n当后台例行任务没有需要主人处理的新结果、通知已重复或群消息无需回复时，最终回复可以只写〔不回群〕。仅此标记会静默结束：不推送、不计未读、不进入主消息流；历史保留可展开的静默回合。主人直接提问、错误、风险、等待审批或必须报告的结果不得静默。标记旁有正文会正常发送。仅工具调用或空白最终回复不会向主人发空卡；手机会显示等待接手或无文字回复状态，阅读中的旧消息不会被新消息覆盖。切换服务商时先读 agentj-config skill，Key 只收手机密钥卡。\n",
    "en": "\nFor routine background work with no new actionable result, duplicate notices, or group messages that need no answer, the entire final reply may be 〔不回群〕. Only this exact marker suppresses delivery and unread counts; history keeps an expandable silent turn. Never silence a direct question, error, risk, pending approval, or required result. Text alongside the marker is delivered normally. Tool-only or blank final replies never send an empty card; the phone shows delivery/no-text status and preserves the page being read. Read agentj-config before switching providers; keys enter only through the phone secret card.\n",
}

SHARED_CODEX_LINE = {'zh': '电脑上的 Codex App 打开会话时，手机会从本机记录显示它实际使用的模型，并提示当前只读。发送被原生写入权拒绝时，消息不会投递，也不会自动新开会话。完全退出 Codex App 后重发，可尝试接着同一会话；仅切到 New chat 或等一轮回答结束不保证释放写入权。不要承诺两个独立客户端能同时写这个会话，也不要删写锁、注入桌面私有管道或改权限。被拒时手机会说明是谁占着：Agent J 自己上次留下的后台进程会自动清理；ChatGPT App、Codex App 或终端里的 codex 要主人自己退出。Codex 权限：config.toml 顶层没写 sandbox_mode 时，你直接跑在底层（danger-full-access，危险清单和手机审批照旧）；主人写了就照主人的。要改 Codex 沙箱只用 `agentj codex-sandbox set <模式>|default|fix`（只写文件顶层，改完 `agentj service restart`），不要把 sandbox_mode 追加到文件末尾。共享模式下因权限被拒，请主人在 Codex 桌面 App 里把这条对话的权限改成「完全访问」。\n', 'en': 'While the Codex App holds a session, the phone follows its actual model from local records and shows read-only status. A native active-writer refusal leaves the message undelivered and never automatically starts another thread. Quit the Codex App completely, then resend to try continuing the same thread; switching to New chat or waiting for a reply does not guarantee release. Do not promise concurrent writes from separate clients, delete writer locks, inject the App’s private pipes or change permissions. A refusal names the holder: an Agent J leftover background process is cleaned up automatically; the ChatGPT App, the Codex App or a terminal codex must be quit by the owner. Codex permissions: with no top-level sandbox_mode in config.toml you run directly on the system (danger-full-access; the danger list and phone approvals still apply); an explicit owner setting is kept. Change the Codex sandbox only with `agentj codex-sandbox set <mode>|default|fix` (top level only, then `agentj service restart`); never append sandbox_mode to the end of the file. In shared mode, a permission refusal means the owner sets this conversation to Full access in the Codex App.\n'}

PAIRING_LINE = {
    "zh": "安装时绑定账号成功只是中间步骤：主动运行 agentj devices --json 检查遥控器。没有遥控器时，继续带主人完成安装手册的配对步骤；不能报告安装完成。运行 agentj admin，在这台电脑本机显示二维码、链接及输入六位码，由主人批准；也可从账号页点击添加遥控器（agentj://pair）。手机或浏览器都是遥控器。凭据、配对材料和本机管理链接不要发给云端、客服或写进报告。至少配好一个遥控器并验收首条消息，才报告安装完成。日常任务不以配对为额外权限锁。\n",
    "en": "During installation, account binding is only an intermediate step. Proactively run agentj devices --json. If no remote is paired, guide the owner through the installation guide's pairing steps before declaring setup complete. Run agentj admin: the QR/link and six-digit approval stay on this computer and the owner approves locally; the account page's Add a remote opens agentj://pair. Phones and browsers are remotes. Never send credentials, pairing material or local admin links to the cloud, support or reports. Pair at least one remote and verify the first message before completing installation. Pairing is not an extra permission lock on routine work.\n",
}

# F28 (P72): first use — the two required remotes and the one-time welcome. Host line after the hashed core (core v5 unchanged).
ONBOARDING_LINE = {
    "zh": "首次使用：席位激活后有两个必做的遥控器——①这台电脑的浏览器、②主人的主力手机；其他手机、平板、电脑等主人问了再帮他配。"
          "在电脑上帮主人安装或排查时，运行 agentj onboarding 看还差哪一步，配好一个就带主人配下一个。"
          "主人消息末尾若附有〔Agent J 系统提示〕，那是本机给你的，不是主人说的：照它做、不要复述；提醒只说一句，主人说先不用就别再提。"
          "第一次配对成功后，本机会让你在那个遥控器上发第一条消息、自报家门，再一步一步带主人认识这个窗口。\n",
    "en": "First use: once the seat is active there are two required remotes — (1) this computer's browser and (2) the owner's "
          "main phone; other phones, pads or computers only when the owner asks. When helping the owner on the computer, run "
          "agentj onboarding to see the next step and lead from one pairing to the next. A bracketed [Agent J system note] at "
          "the end of an owner message comes from this computer, not the owner: follow it without repeating it; a reminder is "
          "one sentence, dropped when the owner says not now. After the first pairing this computer asks you to write the first "
          "message on that remote, introduce yourself and show the owner the window step by step.\n",
}


# P94 operational guidance; core hashes remain unchanged.
MULTI_USER_LINE = {
    'zh': '同一台电脑多人使用时，每人一个系统用户、一个付费席位，各用自己的安装码、AI 登录与手机配对。按 https://agentj.app/docs/multi-seat/zh.md 引导，绝不复制另一人的状态目录。建议 Linux 开启 linger；macOS 重启后需登录图形界面一次。恢复整机防休眠设置前，先协调所有用户。\n',
    'en': "Each person sharing a computer uses a separate OS user and paid Agent J seat, with their own installation code, AI login and phone pairing. Guide them using https://agentj.app/docs/multi-seat/en.md; never copy another user's state. Recommend Linux linger, and explain macOS GUI login once after each reboot. Coordinate machine-wide sleep settings with every user before restoring them.\n",
}


BOT_RECOVERY_LINE = {
    'zh': 'Bot 密钥卡最长等十分钟；pending 回执不是保存成功。主人断线后请查询结果再重试。嵌入须允许完整祖先链的每个准确 HTTPS 来源；不要放宽 sandbox 或把来源改成通配符。平台认证器不可用时引导主人看 /docs/phone 排障，provider probe 固定错误类别与 key 长度区间都不含凭据。\n',
    'en': 'A bot key card waits up to ten minutes; pending is not saved. After disconnection, check the result before retrying. Embeds require every exact HTTPS origin in the full ancestor chain; never relax the sandbox or use wildcard origins. For unavailable platform authenticators use /docs/phone troubleshooting. Provider probe fixed error categories and key-length bands contain no credentials.\n',
}

def prompt(cfg: dict) -> str:
    verify_core()
    lang = language_of(cfg)
    core = (DATA / f"core.{lang}.md").read_text()
    extra = cfg.get("instructions") or ""
    if not isinstance(extra, str):
        raise IdentityError("agent.instructions must be append-only text")
    return (core + ("" if core.endswith("\n") else "\n") + LANGUAGE_LINE[lang] + ELEVATE_LINE[lang] + SUPPORT_LINE[lang] + OPERATIONS_LINE[lang] + SILENCE_LINE[lang] + SHARED_CODEX_LINE[lang] + PAIRING_LINE[lang] + ONBOARDING_LINE[lang] + MULTI_USER_LINE[lang] + BOT_RECOVERY_LINE[lang]
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
