"""#118: forgot the remote pairing password, keep every remote — a paired remote's signed approval lets the owner set a new one.

Flow (`agentj passphrase reset --phone`, ADR-P393-passphrase):
1. The owner runs it in a terminal on this Agent J host. The CLI refuses without a terminal or inside an Agent harness; serve
   refuses a claim to an Agent process (ctl_auth) and wants a terminal on the caller's stdin (`passphrase_consent`).
2. serve puts ONE card (tool `passphrase_reset`) on every ready paired remote. It shows a 6-digit match code the terminal
   printed. The first signed answer decides (+ Face ID when that remote has a passkey, like the pair card); nobody in
   CARD_TTL seconds = nothing changes. approvals.log keeps the decision, the device and its signature.
3. On an allow, serve hands the CLI process (same peer pid) a one-use ticket valid TICKET_TTL seconds. The owner types the
   new password at the terminal (never on the phone, never through the channel); `passphrase_replace` with the ticket makes
   serve store only the new scrypt hash (gate.replace). Every remote stays paired; every phone gets a notice.

Without any paired remote, `agentj passphrase reset` is unchanged (deletes the password, unpairs everything).
"""
from __future__ import annotations

import re

TOOL = "passphrase_reset"
PK_KIND = "passphrase"   # passkey.elevate_challenge kind for this card
CARD_TTL = 300           # s: the card waits five minutes
TICKET_TTL = 300         # s: after the allow, time to type the new password twice
CODE = re.compile(r"[0-9]{6}")

TEXT = {
    "zh": {"head": "重设这台主机的遥控器配对密码",
           "body": "有人在这台Agent J 主机的终端运行了 agentj passphrase reset --phone。确认后，新的遥控器配对密码要在那台主机的"
                   "终端里输入；所有遥控器保持配对。",
           "code": "对照码：{code}（终端上应显示同一串数字；不一致或不是你本人操作就点拒绝）",
           "notice": "这台主机的遥控器配对密码刚刚重设了（由「{name}」确认）。所有遥控器都保持配对。不是你本人操作的话，"
                     "请马上在主机的终端再重设一次。"},
    "en": {"head": "Reset this host's remote pairing password",
           "body": "Someone ran agentj passphrase reset --phone in a terminal on this Agent J host. After you confirm, the new "
                   "remote pairing password is typed in that terminal; every remote stays paired.",
           "code": "Match code: {code} (the terminal shows the same digits; if not, or it isn't you, tap Deny)",
           "notice": "This host's remote pairing password was just reset (confirmed on \"{name}\"). Every remote stays paired. "
                     "If this wasn't you, reset it again in a terminal on the host now."},
}


def lang_of(lang: str) -> str:
    return "en" if str(lang or "").lower().startswith("en") else "zh"


def summary(lang: str, code: str) -> str:
    """The card text, in the host's language: exactly what the approving remote signs (approvals.shown_digest)."""
    t = TEXT[lang_of(lang)]
    return "\n".join((t["head"], t["body"], t["code"].format(code=f"{code[:3]} {code[3:]}")))


def notice(lang: str, name: str) -> str:
    return TEXT[lang_of(lang)]["notice"].format(name=name or "?")
