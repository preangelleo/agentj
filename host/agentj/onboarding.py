"""F28 (P72): first-install onboarding — seat → the two required remotes → one welcome, then a short guided tour.

Owner request 2026-10-07: after a friend's agent installs Agent J, the main Agent itself carries the owner forward: activate the seat,
pair ① this computer's browser and ② the main phone (both required; other phones / pads / computers only when the owner
asks), and on the FIRST pairing say who it is on that new remote and walk the owner through the screen one small step at a
time. No new buttons or UI (PRODUCT_SPEC §0.1): everything is chat text from the one main Agent.

State: `<state>/onboarding.json` (0600), persistent, so a restart / upgrade / reinstall over the same state never welcomes
twice. A host that already had remotes before this version is seeded as welcomed (no welcome on upgrade).
  {"v":1, "welcomed": {"at","device","kind"} | null, "computer": {"at","device"} | null, "phone": {"at","device"} | null,
   "announced": [device ids that already got their one line], "tour_left": n, "tour_until": ts, "remind_at": ts}
Seat = this computer is linked to an Agent J account (`cloud.json`, setup code or 8-character code).

Delivery (serve.py): the welcome is a host-originated turn for the main Agent (`WelcomeSend`, like official notices), so
Claude Code / Codex / OpenCode all write it in their own voice into the chat; with no Agent running the host posts a fixed
fallback text. Later remotes get one host line. While the tour runs, and then at most every REMIND_SECS while a required
remote is missing, the owner's next message carries a short bracketed note for the Agent (never shown on the phone).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import time

from . import cloud
from .compose import Send

FILE = "onboarding.json"
TOUR_TURNS = 6                  # 5 steps + one slack turn; the Agent stops earlier when the owner says skip
TOUR_SECS = 2 * 86400           # a tour nobody continued is not resumed days later
REMIND_SECS = 6 * 3600          # a missing required remote is mentioned at most this often (and never after both are paired)
MANUAL = {"zh": "https://agentj.app/docs/manual/", "en": "https://agentj.app/docs/manual/"}

_PHONE = re.compile(r"\b(ios|ipados|android|iphone|ipad)\b", re.I)
_COMPUTER = re.compile(r"\b(macos|mac|windows|linux|chromeos|cros)\b", re.I)


def kind_of(label: str) -> str:
    """The device label the web client sends (`网页 · iOS Safari`, `网页 · Linux Chrome`, `网页 · Android 主屏幕`) → phone |
    computer | other. Phones and tablets count as the phone; a desktop OS browser counts as this computer's browser (the
    host cannot prove which computer a browser runs on; a desktop browser is what install.md pairs first)."""
    s = str(label or "")
    if _PHONE.search(s):
        return "phone"
    if _COMPUTER.search(s):
        return "computer"
    return "other"


def _path(st):
    return st.root / FILE


@contextlib.contextmanager
def _lock(st):
    fd = os.open(st.root / "onboarding.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _blank() -> dict:
    return {"v": 1, "welcomed": None, "computer": None, "phone": None, "announced": [], "tour_left": 0, "tour_until": 0,
            "remind_at": 0, "firstrun_offered": 0}


def _seed(st) -> dict:
    """First read on this host. Remotes paired before F28 existed = an installed, already-used host: welcomed, no tour."""
    rec = _blank()
    try:
        devs = st.devices()
    except (OSError, ValueError):
        devs = {}
    now = int(time.time())
    for did, d in devs.items() if isinstance(devs, dict) else ():
        k = kind_of(d.get("name", "") if isinstance(d, dict) else "")
        if k in ("computer", "phone") and rec[k] is None:
            rec[k] = {"at": now, "device": did}
        rec["announced"].append(did)
    if devs:
        rec["welcomed"] = {"at": now, "device": None, "kind": "legacy"}
    return rec


def _read(st) -> dict:
    try:
        rec = json.loads(_path(st).read_text())
    except FileNotFoundError:
        rec = _seed(st)
        _write(st, rec)
        return rec
    except (OSError, ValueError, UnicodeDecodeError):
        rec = None
    if not isinstance(rec, dict) or rec.get("v") != 1:
        rec = _seed(st)                 # unreadable: re-derive from the device list, never welcome an in-use host again
        _write(st, rec)
    base = _blank()
    base.update({k: rec[k] for k in base if k in rec})
    if not isinstance(base["announced"], list):
        base["announced"] = []
    return base


def _write(st, rec: dict) -> None:
    st.write_private(_path(st), json.dumps(rec, ensure_ascii=False).encode())


def read(st) -> dict:
    with _lock(st):
        return _read(st)


def status(st) -> dict:
    """What `agentj onboarding`, doctor and the reminder use. A required remote counts as done once it was ever paired
    (the milestone stays; replacing a lost phone is ordinary pairing, not onboarding)."""
    rec = read(st)
    seat = cloud.read_cloud(st) is not None
    done = {k: rec[k] is not None for k in ("computer", "phone")}
    nxt = "seat" if not seat else "computer" if not done["computer"] else "phone" if not done["phone"] else None
    return {"seat": seat, "computer": done["computer"], "phone": done["phone"], "welcomed": rec["welcomed"] is not None,
            "next": nxt, "complete": nxt is None}


def paired(st, device: str, label: str, now: float | None = None) -> str | None:
    """A remote was just approved. → "welcome" (the first remote ever: the main Agent introduces itself here), "also" (any
    later new remote: one line), or None (this device already got its line)."""
    now = int(now if now is not None else time.time())
    k = kind_of(label)
    with _lock(st):
        rec = _read(st)
        if k in ("computer", "phone") and rec[k] is None:
            rec[k] = {"at": now, "device": device}
        if rec["welcomed"] is None:
            rec["welcomed"] = {"at": now, "device": device, "kind": k}
            rec["announced"] = [device]
            rec["tour_left"], rec["tour_until"] = TOUR_TURNS, now + TOUR_SECS
            out = "welcome"
        elif device in rec["announced"]:
            out = None
        else:
            rec["announced"] = (rec["announced"] + [device])[-32:]
            out = "also"
        _write(st, rec)
    st.log("onboarding", kind=k, device=device, status=out or "known")
    return out


# ------------------------------------------------------------------ texts
KIND = {"zh": {"computer": "这台电脑的浏览器", "phone": "主力手机", "other": "遥控器"},
        "en": {"computer": "this computer's browser", "phone": "the main phone", "other": "remote"}}

HOW = {
    "zh": {"computer": "已绑定账号时，在账户页这台电脑的席位卡点「添加遥控器」，用通行密钥确认一次，无需设置批准口令（未绑定时运行 agentj admin），用这台电脑的浏览器打开 https://m.agentj.app 扫码或粘贴配对链接",
           "phone": "手机打开 https://m.agentj.app，先添加到主屏幕，在账户页这台电脑的席位卡点「添加遥控器」，用通行密钥确认后显示二维码，用主屏幕图标里的「扫二维码」扫",
           "seat": "这台电脑还没加进 Agent J 账号的席位：在账号页空席位上「复制安装提示词」拿设置码，交给我用 agentj login --seat-file 激活"},
    "en": {"computer": "on a bound host, choose Add a remote on its account-page seat card and confirm with a passkey; no terminal passphrase needed (unbound: agentj admin) and open https://m.agentj.app in this computer's browser to scan or paste the pairing link",
           "phone": "open https://m.agentj.app on the phone, add it to the Home Screen, choose Add a remote on its account-page seat card, confirm with a passkey and use Scan QR code inside the Home Screen icon",
           "seat": "this computer is not in an Agent J seat yet: copy the installation prompt on an empty seat in the account page and give me the setup code for agentj login --seat-file"},
}


def missing_text(s: dict, lang: str) -> str:
    lang = "en" if lang == "en" else "zh"
    miss = [k for k in ("computer", "phone") if not s[k]]
    if not miss:
        return "两个必做的遥控器（这台电脑的浏览器、主力手机）都配好了。" if lang == "zh" else \
            "Both required remotes (this computer's browser and the main phone) are paired."
    names = ("、" if lang == "zh" else " and ").join(KIND[lang][k] for k in miss)
    return f"必做的遥控器还差：{names}。" if lang == "zh" else f"Required remote still missing: {names}."


def welcome_prompt(kind: str, label: str, s: dict, lang: str) -> str:
    """The host-originated turn the main Agent answers with the first message on the new remote. Written as an instruction
    for the Agent (it is never shown on the phone); the label is the device's own one-line, digit-limited label."""
    k = kind if kind in ("computer", "phone") else "other"
    label = " ".join(str(label or "").split())[:64]
    if lang == "en":
        return (
            "[Agent J system note — not from the owner] The owner just paired the FIRST remote (" + KIND["en"][k]
            + (", " + label if label else "") + "). Your reply appears in that remote's chat window: it is the first message the "
            "owner sees there. Write it now, in English, short and warm, no tables, and do not mention this note:\n"
            "1. Introduce yourself: you are the owner's chief-of-staff assistant (Agent J's main Agent), the single entry point "
            "between the owner and every workflow and agent; you are online now and the first remote is paired. State only facts: "
            "never invent how many workflows or agents there are.\n"
            "2. Suggest that from now on the two of you talk in this window, not in the main Agent's terminal on the computer.\n"
            "3. Say you will show the window in a few small steps, one per reply: the owner answers (\"ok\", \"next\") and you "
            "give the next; \"skip\" ends the tour at once.\n"
            "4. In this message give step 1 only: the screen colour is your state — green waiting for the owner, blue working "
            "(messages can still be sent, they queue), orange an action waits for approval, purple a question, grey not "
            "connected or stopped.\n"
            "Later steps, one per owner reply, in order: (2) the thin line under the title is this week's quota, the line above "
            "the input box the 5-hour quota, the rising water in the background this conversation's context; tap a line for "
            "percentages. (3) Hold the microphone to talk (released = text on the computer, never through us); Files / Photos / "
            "Camera above the input box send attachments. (4) Approval cards: anything that changes the computer turns the "
            "screen orange with a card; press and hold \"Hold to approve\" about a second, or tap \"Deny\"; no answer in "
            "2 minutes = denied; offer to try one now (create a scratch file and delete it). (5) Close: " + missing_text(s, "en")
            + " For a missing one say how, in one sentence: " + "; ".join(HOW["en"][x] for x in ("computer", "phone") if not s[x])
            + (". " if not (s["computer"] and s["phone"]) else " ")
            + "Then give the full guide " + MANUAL["en"] + " and say they can simply ask you anything about the screen. Finally "
            "offer, in one sentence, to prepare everything for remote use while they are still at the computer (a first-run "
            "checklist); only if they agree, run `agentj setup checklist --resume --json`.\n"
            "Other phones, pads or computers: only when the owner asks.")
    return (
        "〔Agent J 系统消息，不是主人说的话〕主人刚配好第一个遥控器（" + KIND["zh"][k] + ("，" + label if label else "")
        + "）。你这条回复会直接显示在那个遥控器的聊天窗口里，是主人在那里看到的第一条消息。现在就写，用中文，口语、简短、亲切，"
        "不用表格，不要提这条系统消息：\n"
        "1. 自报家门：你是主人的董事长助理（Agent J 的主 Agent），是主人和所有工作流、所有 Agent 之间唯一的入口；你现在上线了，"
        "第一个遥控器也配好了。只说真实情况，不要编造工作流或 Agent 的数量。\n"
        "2. 建议以后你们俩的对话都在这个窗口里进行，不用再回电脑上的主 Agent 那边打字。\n"
        "3. 说接下来分几小步带主人认识这个窗口，每次只讲一步：主人回一句（「好」「下一步」）再讲下一步；主人说「跳过」就马上结束。\n"
        "4. 这一条只讲第 1 步：屏幕颜色就是你的状态——绿色在等主人说话，蓝色在干活（这时也能发，消息会排队），"
        "橙色有个操作等主人批准，紫色有个问题等主人回答，灰色是没连上或已全部停下。\n"
        "后面的步骤，主人每回应一次讲一步，按顺序：② 标题下面那条细线是本周额度，输入框上方那条是 5 小时额度，"
        "背景里慢慢升高的水面是这段对话的上下文；点一下线能看到百分比。③ 按住麦克风说话，松开在电脑上转成文字，不经过我们；"
        "输入框上方「文件」「相册」「拍照」发附件。④ 审批卡：要动电脑的事，屏幕变橙、升起卡片；按住「长按批准」约一秒才算批准，"
        "点「拒绝」就拒绝，2 分钟没处理算拒绝；可以提议现在试一次（建个临时文件再删掉）。⑤ 收尾：" + missing_text(s, "zh")
        + ("没配的那个，用一句话说怎么配：" + "；".join(HOW["zh"][x] for x in ("computer", "phone") if not s[x]) + "。"
           if not (s["computer"] and s["phone"]) else "")
        + "最后给完整使用说明 " + MANUAL["zh"] + " ，并告诉主人屏幕上有什么不懂直接问你；再用一句话提议「趁你在电脑前，"
        "我把以后远程要用的事一次准备好」，主人同意才运行 agentj setup checklist --resume --json。\n"
        "其他手机、平板、电脑：主人问的时候再帮他配。")


def fallback_welcome(kind: str, s: dict, lang: str) -> str:
    """No Agent running (installation paused with the Agent off, or 「全部停下」): the host says the first lines itself."""
    k = kind if kind in ("computer", "phone") else "other"
    if lang == "en":
        return ("I'm your chief-of-staff assistant, the main Agent of Agent J — the one entry point between you and every "
                "workflow and agent. The first remote (" + KIND["en"][k] + ") is paired. From now on we can talk right here "
                "instead of the terminal. " + missing_text(s, "en") + " Once the Agent is running, say \"hi\" and I'll show "
                "you this window step by step. Guide: " + MANUAL["en"])
    return ("我是你的董事长助理，Agent J 的主 Agent——你和所有工作流、所有 Agent 之间唯一的入口。第一个遥控器（" + KIND["zh"][k]
            + "）已经配好了，以后咱们就在这个窗口里说话，不用回终端。" + missing_text(s, "zh")
            + "Agent 启动后跟我说一声「你好」，我一步一步带你认识这个窗口。使用说明：" + MANUAL["zh"])


def also_text(kind: str, label: str, s: dict, lang: str) -> str:
    k = kind if kind in ("computer", "phone") else "other"
    label = " ".join(str(label or "").split())[:64]
    if lang == "en":
        return "This " + KIND["en"][k] + (" (" + label + ")" if label else "") + " is connected too. " + missing_text(s, "en")
    return "这台" + ("" if k == "other" else "（" + KIND["zh"][k] + "）") + ("「" + label + "」" if label else "") \
        + "也连上了。" + missing_text(s, "zh")


def turn_note(st, lang: str, now: float | None = None) -> str:
    """Appended to the owner's next message for the Agent (phone / browser / Telegram), never shown on a remote:
    - right after the welcome: «the tour is running» for up to TOUR_TURNS messages (so every harness keeps the one-step pace
      even after a restart), with the live required-remote status;
    - afterwards, while a required remote (or the seat) is missing: one gentle reminder at most every REMIND_SECS (only on
      an install onboarded here: not on a host upgraded with remotes already paired, not before the first remote);
    - nothing once both required remotes are paired and the tour is over."""
    now = int(now if now is not None else time.time())
    lang = "en" if lang == "en" else "zh"
    with _lock(st):
        rec = _read(st)
        tour = rec["welcomed"] is not None and rec["tour_left"] > 0 and now < rec["tour_until"]
        seat = cloud.read_cloud(st) is not None
        s = {"computer": rec["computer"] is not None, "phone": rec["phone"] is not None, "seat": seat}
        # only an install whose first remote was welcomed here is still onboarding; a host that already had remotes when
        # F28 arrived (legacy), or one not paired at all yet (the installing Agent leads that, install.md Step 11), is not
        fresh = isinstance(rec["welcomed"], dict) and rec["welcomed"].get("kind") != "legacy"
        pending = fresh and (not (s["computer"] and s["phone"]) or not seat)
        offer = False
        if tour:
            rec["tour_left"] -= 1
        elif pending and now - int(rec.get("remind_at") or 0) >= REMIND_SECS:
            rec["remind_at"] = now
        elif fresh and not pending and not rec.get("firstrun_offered") and _firstrun_unstarted(st):
            rec["firstrun_offered"] = now      # P115 (P92): offered once after a fresh install, also when the tour was skipped
            offer = True
        else:
            return ""
        _write(st, rec)
    if offer:
        if lang == "en":
            return ("\n\n[Agent J system note, not from the owner; do not repeat it] Setup is complete. After answering the owner, "
                    "unless you already offered it in this conversation, offer once, in one sentence, to prepare everything for remote use while they are at the computer; only if "
                    "they agree, run `agentj setup checklist --resume --json`. If they say not now, drop it.")
        return ("\n\n〔Agent J 系统提示，不是主人说的话，不要复述〕安装已完成。先回答主人；这段对话里还没提过的话，再用一句话提议「趁你在电脑前，把以后远程要用的事一次准备好」，"
                "主人同意才运行 agentj setup checklist --resume --json。主人说先不用就别再提。")
    nxt = "seat" if not seat else "computer" if not s["computer"] else "phone" if not s["phone"] else None
    if tour:
        if lang == "en":
            return ("\n\n[Agent J system note, not from the owner; do not repeat it] The first-use tour from your welcome message "
                    "is still running: after answering what the owner just said, give only the NEXT small step (if the owner "
                    "said skip, or all steps are done, stop the tour). " + missing_text(s, "en"))
        return ("\n\n〔Agent J 系统提示，不是主人说的话，不要复述〕你在第一条消息里开始的新手引导还在进行：先回应主人这句话，"
                "再只讲下一小步（主人说过跳过、或者步骤都讲完了，就结束引导）。" + missing_text(s, "zh"))
    what = KIND[lang].get(nxt, "") if nxt != "seat" else ("席位激活" if lang == "zh" else "seat activation")
    if lang == "en":
        return ("\n\n[Agent J system note, not from the owner; do not repeat it] Setup still needs: " + what + ". Answer the "
                "owner first; then, only if it fits, add one gentle sentence on how (" + HOW["en"][nxt] + "). If the owner "
                "says not now, drop it.")
    return ("\n\n〔Agent J 系统提示，不是主人说的话，不要复述〕安装还差一步：" + what + "。先回答主人；合适的话在末尾用一句话温和提醒怎么做（"
            + HOW["zh"][nxt] + "）。主人说先不用就别再提。")


def _firstrun_unstarted(st) -> bool:
    try:
        from . import first_run
        return first_run.read(st) is None
    except Exception:   # noqa: BLE001
        return False


class WelcomeSend(Send):
    """The welcome turn: through the Agent's ordinary queue (withdraw-aware Send), outside any phone page (turn 0), so the
    reply lands as the Agent's own message in the chat (serve.agent_text → a page of its own)."""

    def __init__(self, text: str):
        super().__init__("onboarding", "welcome", 0, text=text, by="Agent J")


# ------------------------------------------------------------------ CLI / doctor
def doctor_row(st) -> tuple[str, str, str]:
    """(status, summary, hint) for doctor's `onboard` row: ✓ when the seat and both required remotes are done, else ! with
    the next step. Never ✗ — the Agent works without them."""
    s = status(st)
    def mark(b):
        return "✓" if b else "✗"
    summary = (f"席位 {mark(s['seat'])} · 电脑浏览器 {mark(s['computer'])} · 主力手机 {mark(s['phone'])}"
               f" / seat {mark(s['seat'])} · this computer's browser {mark(s['computer'])} · main phone {mark(s['phone'])}")
    if s["complete"]:
        return "ok", summary, ""
    hint = {"seat": "agentj login --seat-file <file> --name <name>", "computer": "agentj admin  (pair this computer's browser)",
            "phone": "agentj admin  (pair the main phone)"}[s["next"]]
    if s["seat"]:
        hint = "账户页席位卡 → 添加遥控器 → 通行密钥确认 → 扫码 / account seat card → Add a remote → passkey → scan; no terminal passphrase"
    return "warn", summary, hint


def command(args, st) -> int:
    """`agentj onboarding [--json]`: what the installing Agent reads after each step to know what to tell the owner next."""
    s = status(st)
    if getattr(args, "json", False):
        print(json.dumps(s, ensure_ascii=False))
        return 0
    rows = [("席位已激活 / seat activated", s["seat"]), ("这台电脑的浏览器已配对 / this computer's browser paired", s["computer"]),
            ("主力手机已配对 / main phone paired", s["phone"]), ("第一条欢迎已发 / welcome sent", s["welcomed"])]
    for name, ok in rows:
        print(("✓ " if ok else "· ") + name)
    for d in st.devices().values():
        if d.get("source") == "account":
            print("✓ 经账户页添加 / Added from account page: " + d.get("name", ""))
    if s["next"]:
        print("下一步 / next: " + HOW["zh"][s["next"]] + "\n            " + HOW["en"][s["next"]])
    else:
        print("✓ 必做的都完成了；其他设备主人问的时候再配 / required steps done; other devices only when the owner asks")
    return 0
