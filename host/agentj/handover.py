"""`agentj handover [--lang zh|en]` — the note the installing AI relays to its human at the end of the install (install.md
Step 13), filled with THIS computer's facts so the AI only has to repeat it.

Read-only: in `cli.NO_MIGRATE` (moves no state directory), creates nothing, reads whichever state directory exists
(state.state_dir(): $AGENTJ_STATE_DIR, else the new path, else a 0.9 one that was not moved yet). Without any state it
still prints the generic note and says this computer is not set up yet. Never prints a key, a code or a path outside
the work folder the human chose.

Wording: plain words; no 公司 / 主机 / 员工 / 客户 in the zh text (电脑, 账号); the quoted button labels are the exact
strings of the phone page and the account dashboard (the site's UI-wording test reads them from here).
"""
from __future__ import annotations

import os

from . import cloud
from .state import DEFAULT_WEB, MAX_DEVICES, State, state_dir

LANGS = ("zh", "en")
DOCS_URL = "https://agentj.app/docs/"
MANUAL_URL = "https://agentj.app/docs/manual/"   # F23 (0.15.2): the illustrated user guide (= the Agent's agentj-manual skill)
REFUND_DAYS = 7
CONTACT = "founder@agentj.app"   # refunds and billing (site pricing / refund policy)
AGENT_LABEL = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}


def _tilde(p: str) -> str:
    h = os.path.expanduser("~").rstrip("/")
    return "~" + p[len(h):] if h and (p == h or p.startswith(h + "/")) else p


def _https(url, default: str) -> str:
    return url if isinstance(url, str) and url.startswith("https://") and url.isprintable() and " " not in url else default


def facts(root=None) -> dict:
    """What this computer knows about itself — read-only. Missing / unreadable pieces are simply absent."""
    st = State(root) if root is not None else State(state_dir())
    f = {"set_up": False, "name": None, "account": None, "phones": 0, "agent": None, "folder": None,
         "web": DEFAULT_WEB, "account_url": cloud.DEFAULT_APP, "service": None}
    try:
        if not st.exists():
            return f
        f["set_up"] = True
        cfg = st.config()
    except (OSError, ValueError):
        return f
    f["web"] = _https(cfg.get("web"), DEFAULT_WEB)
    try:
        f["account_url"] = cloud.app_url(st)
    except cloud.CloudError:
        pass
    f["name"] = st.agent_name()
    try:
        f["phones"] = len(st.devices())
    except (OSError, ValueError):
        pass
    link = cloud.read_cloud(st)
    if link:
        f["account"] = link["tenant"]["slug"]
    a = st.agent_config()
    if a:
        f["agent"], f["folder"] = AGENT_LABEL[a["kind"]], _tilde(a["dir"])
    try:
        from . import service
        f["service"] = bool(service.status().get("installed"))
    except Exception:  # noqa: BLE001 — a missing service manager only changes one line of the note
        f["service"] = None
    return f


def note(f: dict, lang: str = "en") -> str:
    if lang not in LANGS:
        raise ValueError(lang)
    return _zh(f) if lang == "zh" else _en(f)


def _zh(f: dict) -> str:
    web, acct_url = f["web"], f["account_url"]
    out = []
    if not f["set_up"]:
        out += ["这台电脑上还没有设置好 Agent J（还没运行过 `agentj init`）。下面是装好以后的用法，先留着备用。", ""]
    else:
        out += ["Agent J 装好了。下面这些留着备用。", ""]
        out.append("**这台电脑**")
        out.append(f"- Agent 名：「{f['name']}」" if f["name"] else "- Agent 名：还没起名（在电脑上运行 `agentj name <名字>`）")
        if f["agent"]:
            out.append(f"- 手机上跟你说话的是这台电脑上的 {f['agent']}，它在「{f['folder']}」这个文件夹里干活")
        else:
            out.append("- 还没接上 AI 工具（Claude Code、Codex 或 OpenCode），手机上的消息暂时没人回")
        out.append(f"- Agent J 账号 ID：{f['account']}" if f["account"] else "- 还没加到 Agent J 账号里")
        out.append(f"- 已配对的手机：{f['phones']} 台（最多 {MAX_DEVICES} 台）")
        out.append("")
    if f.get("account"):
        out += ['已绑定账号：接下来在账户页这台电脑的席位卡上点「添加遥控器」，用通行密钥确认一次，扫码即可；不需要在终端设置批准口令。十分钟内免重复确认，也可在账户页批准等待中的手机、恢复「全部停下」和启用具体定时任务。下面的终端配对与口令说明是本机备选。', ""]
    out += [
        f"**手机上打开哪里**：从主屏幕上的 Agent J 图标打开（网址是 {web} ）。手机这边不用注册，也不用登录。"
        "锁屏提醒只有从主屏幕图标打开才能用。如果图标打开后显示「还没配对」，就照下面再配一次。",
        "",
        "**加一台手机、换手机**：先确认 Agent J 在运行（`agentj service status`），再在这台电脑的终端里运行 `agentj pair`，"
        "安卓手机在页面里点「扫二维码」扫电脑屏幕上的码；iPhone 改用 `agentj pair --link`，把链接粘贴到主屏幕图标打开的页面里"
        "「或者粘贴配对链接」下面的框里，点「开始配对」，不要用 Safari 打开。手机上会显示 6 位码，在电脑上输入，再输批准口令。"
        "这个链接就是钥匙：5 分钟内有效，只能用一次，别发到微信、群里、邮件里，也别发给任何 AI。",
        "",
        "**平时怎么用**",
        "- 直接打字告诉它要做什么。",
        "- 需要你点头时，手机上会弹出来问你：按住「长按批准」才算批准（点一下不算），点「拒绝」就拒绝；2 分钟不按就当拒绝。"
        "花钱、删除、对外发送、改密码或密钥、改价格，这五类事每一次都要你单独「长按批准这一条」。",
        "- 红色的「全部停下」：马上让它停手，定时任务也停。要继续，在手机上点「恢复」，或者在电脑上运行 `agentj resume`"
        "（要输批准口令）。",
        "- 「记忆」看它记住了什么，「记录」看它最近做了什么，「定时任务」看它按时做的事（只有你能打开），"
        "输入框左边的 ≡「全部命令」里有压缩、清空、换模型这些。",
        "",
    ]
    if f["service"] is False:
        out.append("**电脑要开着**：这台电脑关机、睡眠或断网时，手机连不上它。Agent J 现在没装成后台服务，电脑重启后要在终端里"
                   "运行 `agentj service install`（或者再运行一次 `agentj serve`）。")
    else:
        out.append("**电脑要开着**：这台电脑关机、睡眠或断网时，手机连不上它；电脑恢复后会自己连回来。")
    out += [
        "",
        "**手机丢了**：在这台电脑上运行 `agentj devices` 找到那部手机的设备号，再运行 `agentj revoke <设备号>`，它马上就断开了。"
        + ("不在电脑旁边的话，在账号后台的 Agent 卡片上找到那部手机点「解绑」，电脑在线时大约 20 秒内生效。" if f["account"] else ""),
        "",
        "**忘了批准口令**：在这台电脑的终端里运行 `agentj passphrase reset`（输入 RESET 确认）。所有手机都会被解除配对，"
        "之后运行 `agentj pair` 重新配对，它会先请你设一个新的批准口令。",
        "",
        "**有新版本时**：手机上会收到一句提醒。它不会自己升级。想看看有没有新版本：`agentj update check`；"
        "要升级，你自己在这台电脑的终端里运行 `agentj update apply`，它先给你看要运行的命令，你输入 y 才升级。",
        "",
        f"**账号后台**（买席位、改席位数、看账单、取消订阅）：{acct_url} ，用邮箱验证码或通行密钥登录。账单和取消都在「管理账单」里。",
        "",
        f"**退款**：第一次付款后 {REFUND_DAYS} 天内，写信到 {CONTACT}（写上你的账号 ID），全额退回原卡。过了 {REFUND_DAYS} 天，"
        "没用完的天数不退钱。",
        "",
        "**不想用了**：先在账号后台的「管理账单」里取消订阅——卸载不会停止扣费。然后在电脑上运行 `agentj service uninstall` "
        "和 `uv tool uninstall agentj`。",
        "",
        f"**使用说明**（手机上每种颜色、每条线、每个按钮是什么意思，带截图）：{MANUAL_URL} 。也可以直接在手机上问 Agent J「这个颜色是什么意思」。",
        f"**说明文档**：{DOCS_URL} 。有问题就问这台电脑上帮你装 Agent J 的 AI，它会先去说明文档里查，查不到再帮你问 Agent J 的团队。",
    ]
    return "\n".join(out) + "\n"


def _en(f: dict) -> str:
    web, acct_url = f["web"], f["account_url"]
    out = []
    if not f["set_up"]:
        out += ["Agent J is not set up on this computer yet (`agentj init` has not run). Here is how it works once it is; "
                "keep it for later.", ""]
    else:
        out += ["Agent J is set up. Keep this for later.", ""]
        out.append("**This computer**")
        out.append(f"- Agent name: \"{f['name']}\"" if f["name"] else "- Agent name: not named yet (on the computer: "
                   "`agentj name <name>`)")
        if f["agent"]:
            out.append(f"- Your phone talks to {f['agent']} on this computer; it works in the folder \"{f['folder']}\"")
        else:
            out.append("- No AI tool connected yet (Claude Code, Codex or OpenCode): nobody answers messages from the phone yet")
        out.append(f"- Agent J account ID: {f['account']}" if f["account"] else "- Not in an Agent J account yet")
        out.append(f"- Phones paired: {f['phones']} (up to {MAX_DEVICES})")
        out.append("")
    if f.get("account"):
        out += ['Account bound: choose Add a remote on this computer’s account-page seat card, confirm once with your passkey and scan. No terminal approval passphrase is required. Confirmation is reused for ten minutes; the account page also approves waiting phones, resumes Stop everything and enables a reviewed scheduled task. The terminal pairing and passphrase instructions below are an optional local alternative.', ""]
    out += [
        f"**On your phone:** open Agent J from its Home Screen icon (the address is {web}). No account and no sign-in on "
        "the phone. Lock-screen alerts only work when you open it from that icon. If the icon shows \"Not paired yet\", "
        "pair again as below.",
        "",
        "**Adding or replacing a phone:** first check that Agent J is running (`agentj service status`), then run "
        "`agentj pair` in a terminal on this computer. Android: tap \"Scan QR code\" on the page and scan the code on the "
        "computer screen. iPhone: run `agentj pair --link` instead and paste the link into the page opened from the "
        "Home Screen icon, under \"Or paste the pairing link\", then tap \"Pair\"; don't open it in Safari. The phone "
        "shows a 6-digit code: type it on the computer, then your approval passphrase. The link is the key: it works once, "
        "for 5 minutes. Never send it through WeChat, a group chat, email or to any AI.",
        "",
        "**Every day**",
        "- Just type what you want done.",
        "- Whenever it needs your OK, your phone asks you: press and hold \"Hold to approve\" to approve (a tap is not "
        "enough), or tap \"Deny\". No answer within 2 minutes counts as deny. Spending money, deleting, sending anything "
        "out, changing passwords or keys, and changing prices always need your OK one at a time (\"Hold to approve this "
        "one\").",
        "- The red \"Stop everything\" button stops it at once, scheduled tasks too. To carry on, tap \"Resume\" on the "
        "phone, or run `agentj resume` on the computer (it asks your approval passphrase).",
        "- \"Memory\" shows what it remembers, \"Activity\" what it did, \"Schedules\" what it does on a schedule (only you "
        "can turn one on), and the ≡ \"All commands\" button left of the text box has compact, clear, switch model and the "
        "rest.",
        "",
    ]
    if f["service"] is False:
        out.append("**Keep the computer on:** while it is off, asleep or offline, the phone cannot reach it. Agent J is not "
                   "installed as a background service yet: after a restart, run `agentj service install` in a terminal "
                   "(or start `agentj serve` again).")
    else:
        out.append("**Keep the computer on:** while it is off, asleep or offline, the phone cannot reach it; it reconnects "
                   "by itself when the computer is back.")
    out += [
        "",
        "**Lost a phone:** on this computer run `agentj devices` to find its id, then `agentj revoke <id>`; it is cut off "
        "at once."
        + (" Away from the computer? In the account dashboard, find the phone on the Agent card and tap \"Unlink\"; it "
           "takes effect within about 20 seconds while the computer is online." if f["account"] else ""),
        "",
        "**Forgot the approval passphrase:** run `agentj passphrase reset` in a terminal on this computer (type RESET to "
        "confirm). Every phone is unpaired; then run `agentj pair` to pair again, and it asks you to choose a new "
        "approval passphrase first.",
        "",
        "**New versions:** the phone gets a one-line notice. Nothing upgrades by itself. To see whether there is one: "
        "`agentj update check`. To upgrade, you run `agentj update apply` yourself in a terminal on this computer; it "
        "shows the command first and upgrades only after you type y.",
        "",
        f"**Account dashboard** (buy or change seats, invoices, cancel): {acct_url}, sign in with an email code or your "
        "passkey. Billing and cancelling are under \"Manage billing\".",
        "",
        f"**Refunds:** within {REFUND_DAYS} days of your first payment, email {CONTACT} (with your account ID) for a full "
        f"refund to your card. After {REFUND_DAYS} days, unused days are not refunded.",
        "",
        "**Stopping for good:** first cancel the subscription under \"Manage billing\" in the account dashboard: "
        "uninstalling does not stop the billing. Then run `agentj service uninstall` and `uv tool uninstall agentj` on "
        "the computer.",
        "",
        f"**User guide** (what every colour, line and button on the phone means, with screenshots): {MANUAL_URL}. You can also "
        "just ask Agent J on the phone \"what does this colour mean?\".",
        f"**Help pages:** {DOCS_URL}. Questions? Ask the AI that set up Agent J on this computer: it looks things up in the "
        "help pages first and asks the Agent J team if they don't answer it.",
    ]
    return "\n".join(out) + "\n"


def cmd(a) -> None:
    print(note(facts(), a.lang), end="")


def add_parser(sub) -> None:
    h = sub.add_parser("handover", help="打印交接说明（按这台电脑的实际情况填好）/ print the handover note, filled with this "
                                        "computer's facts",
                       description="只读：不搬状态目录，不新建任何东西。安装的 AI 把它原样转给主人。/ Read-only: moves and creates "
                                   "nothing. The installing AI relays it to its human as is.")
    h.add_argument("--lang", choices=list(LANGS), default="en", help="主人的语言（默认 en）/ your human's language (default en)")
    h.set_defaults(fn=cmd)
