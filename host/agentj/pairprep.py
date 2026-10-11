"""P127: the one pairing pre-check — which approval route works on this host *before* any QR is shown.

Used by `agentj pair` (and `agentj pair --check [--json]`), the admin page (`/api/state` → `pair_check`, and /api/pair/start
refuses `set_passphrase` before a QR exists), `agentj onboarding` and the agentj-pair skill / install.md (they run the
`--check`). Nobody gets to the last step and only then learns a passphrase is missing.

Routes:
- ``account``: the host is account-bound, account-page pairing is on (`agentj remote-pair`) and the relay is up. The owner
  adds a remote from the account page's seat card with a passkey, or scans a local QR and approves the waiting phone there.
  The local approval passphrase is optional.
- ``passphrase``: local approval with the 6-digit code + the approval passphrase (already set).
- ``set_passphrase``: neither works yet — the owner sets the approval passphrase themselves first (`agentj passphrase set`,
  at a terminal, never through an agent). Unbound hosts always need it: nothing here relaxes that.
Read-only; never shows a link, QR, code or passphrase.
"""
from __future__ import annotations

from . import cloud, gate, remote_pair

ROUTES = ("account", "passphrase", "set_passphrase")

TEXT = {
    "zh": {
        "account": "这台电脑已绑定账号：在账户页 {url} 这台电脑的席位卡点「添加遥控器」，用通行密钥确认一次再扫码——不需要批准口令。"
                   "在这里扫码也行：手机连上后到同一个席位卡上批准等待中的手机。批准口令可选（离线时本机批准用：`agentj passphrase set`）。",
        "account_with_pass": "这台电脑已绑定账号：可以在账户页 {url} 这台电脑的席位卡点「添加遥控器」用通行密钥批准，"
                             "也可以在这里输手机上的 6 位码和批准口令。",
        "passphrase": "批准口令已设置：扫码后输入手机上的 6 位码和批准口令即可。",
        "set_passphrase": "配对前要先设置批准口令（只在这台电脑上，每次批准新遥控器都要输）：请你自己在这台电脑的终端运行 "
                          "`agentj passphrase set`，别让 Agent 代劳。",
        "set_passphrase_bound": "这台电脑已绑定账号，但账户页现在批准不了（{why}）。先自己在终端运行 `agentj passphrase set` 设批准口令，"
                                "或者等{fix}后到账户页 {url} 添加遥控器。",
        "why_offline": "这台电脑还没连上中继", "fix_offline": "连上",
        "why_off": "账户页添加遥控器已关闭", "fix_off": "运行 `agentj remote-pair on`",
    },
    "en": {
        "account": "This computer is bound to your account: on the account page {url}, choose Add a remote on this computer's seat "
                   "card, confirm once with your passkey and scan — no approval passphrase needed. Scanning here also works: "
                   "approve the waiting phone on the same seat card. The approval passphrase is optional (for local approval "
                   "while offline: `agentj passphrase set`).",
        "account_with_pass": "This computer is bound to your account: approve with your passkey from Add a remote on the account page "
                             "{url}, or type the phone's 6-digit code and your approval passphrase here.",
        "passphrase": "Approval passphrase is set: after the scan, type the phone's 6-digit code and your approval passphrase.",
        "set_passphrase": "Set the approval passphrase before pairing (on this computer only; asked for every new remote): run "
                          "`agentj passphrase set` in a terminal on this computer yourself — never through an agent.",
        "set_passphrase_bound": "This computer is bound, but the account page cannot approve right now ({why}). Set an approval "
                                "passphrase yourself with `agentj passphrase set`, or {fix} and add a remote on the account page {url}.",
        "why_offline": "this computer is not connected to the relay yet", "fix_offline": "wait until it connects",
        "why_off": "account-page pairing is off", "fix_off": "run `agentj remote-pair on`",
    },
}


def precheck(st, relay_up: bool | None = None) -> dict:
    """{route, passphrase_set, bound, account, account_url, why}. `relay_up` from serve's status; None = not known (the
    serve is not asked here) — then the account route is offered when bound and enabled, as the account page itself will say
    "offline" if it is."""
    pp = gate.is_set(st)
    link = cloud.read_cloud(st)
    bound = link is not None
    on = bound and remote_pair.enabled(st)
    account = bool(on and relay_up is not False)
    route = "account" if account else ("passphrase" if pp else "set_passphrase")
    why = None if account or not bound else ("off" if not on else "offline")
    return {"route": route, "passphrase_set": pp, "bound": bound, "account": account,
            "account_url": cloud.app_url(st) if bound else None, "why": why}


def message(chk: dict, lang: str = "zh") -> str:
    """The one sentence every surface shows for this pre-check (terminal, skill via --check, onboarding)."""
    t = TEXT["en" if lang == "en" else "zh"]
    url = chk.get("account_url") or cloud.DEFAULT_APP
    if chk["route"] == "account":
        return t["account_with_pass" if chk["passphrase_set"] else "account"].format(url=url)
    if chk["route"] == "passphrase":
        return t["passphrase"]
    if chk["bound"] and chk.get("why"):
        return t["set_passphrase_bound"].format(url=url, why=t["why_" + chk["why"]], fix=t["fix_" + chk["why"]])
    return t["set_passphrase"]


def both(chk: dict) -> str:
    return message(chk, "zh") + "\n" + message(chk, "en")
