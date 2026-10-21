"""P127: the one pairing pre-check — which approval route works on this host *before* any QR is shown.

Used by `agentj pair` (and `agentj pair --check [--json]`), the admin page (`/api/state` → `pair_check`, and /api/pair/start
refuses `set_passphrase` before a QR exists), `agentj onboarding` and the agentj-pair skill / install.md (they run the
`--check`). Nobody gets to the last step and only then learns a passphrase is missing.

Routes:
- ``account``: the host is account-bound, account-page pairing is on (`agentj remote-pair`) and the relay is up. The owner
  adds a remote from the account page's seat card with a passkey, or scans a local QR and approves the waiting phone there.
  The remote pairing password is optional there (P221: the account page also accepts it); pairing at the terminal needs it
  (`agentj pair` asks the owner to set it first).
- ``passphrase``: local approval with the 6-digit code + the remote pairing password (already set).
- ``set_passphrase``: neither works yet — the owner sets the remote pairing password themselves first (`agentj pair` asks for it,
  at a terminal, never through an agent).
- ``seat`` (P185): this host has no usable Agent J seat (unbound, suspended, or unconfirmed for > 72 h — `seatcert`). No route
  pairs a remote until it is bound / renewed; the message says where. Checked first.
Read-only; never shows a link, QR, code or passphrase.
"""
from __future__ import annotations

from . import cloud, gate, remote_pair, seatcert

ROUTES = ("seat", "account", "passphrase", "set_passphrase")

TEXT = {
    "zh": {
        "account": "这台Agent J 主机已绑定账号：在账户页 {url} 这台Agent J 主机的席位卡点「添加遥控器」，用通行密钥（或这台Agent J 主机的遥控器配对密码）确认后扫码，核对账号页自动显示的六位码并批准。"
                   "在这台Agent J 主机的终端配对（`agentj pair`）要有遥控器配对密码：还没设，它会先请你设一个（至少 8 位）。",
        "account_with_pass": "这台Agent J 主机已绑定账号：可以在账户页 {url} 这台Agent J 主机的席位卡点「添加遥控器」，用通行密钥或遥控器配对密码确认，"
                             "也可以在自己的终端运行 `agentj pair`，输入手机上的 6 位码和遥控器配对密码。",
        "passphrase": "遥控器配对密码已设置：扫码后输入手机上的 6 位码和遥控器配对密码即可。",
        "set_passphrase": "在终端配对要先设遥控器配对密码（只存在这台Agent J 主机上）：请你自己在这台Agent J 主机的终端运行 `agentj pair`，"
                          "它会先请你设密码（至少 8 位），别让 Agent 代劳。",
        "set_passphrase_bound": "这台Agent J 主机已绑定账号，但账户页现在批准不了（{why}）。请你自己在终端运行 `agentj pair`（会先请你设遥控器配对密码），"
                                "或者等{fix}后到账户页 {url} 添加遥控器。",
        "why_offline": "这台Agent J 主机还没连上中继", "fix_offline": "连上",
        "why_off": "账户页添加遥控器已关闭", "fix_off": "运行 `agentj remote-pair on`",
    },
    "en": {
        "account": "the Agent J host is bound to your account: on the account page {url}, choose Add a remote on the Agent J host's seat "
                   "card, confirm with your passkey (or the Agent J host's remote pairing password) and scan; compare the code shown automatically on the account page, then approve. Pairing at this "
                   "computer's terminal (`agentj pair`) needs a remote pairing password; if none is set, it asks you to set one "
                   "first (at least 8 characters).",
        "account_with_pass": "the Agent J host is bound to your account: choose Add a remote on the account page {url} and confirm with "
                             "your passkey or the remote pairing password, compare the displayed code and approve; or run `agentj pair` in your terminal and type the phone's 6-digit code and the remote pairing "
                             "password here.",
        "passphrase": "Remote pairing password is set: after the scan, type the phone's 6-digit code and the remote pairing password.",
        "set_passphrase": "Pairing at the terminal needs a remote pairing password first (kept on the Agent J host only): run "
                          "`agentj pair` in a terminal on the Agent J host yourself; it asks you to set one (at least 8 characters). "
                          "Never through an Agent.",
        "set_passphrase_bound": "the Agent J host is bound, but the account page cannot approve right now ({why}). Run `agentj pair` "
                                "yourself in a terminal (it asks you to set a remote pairing password first), or {fix} and add a "
                                "remote on the account page {url}.",
        "why_offline": "the Agent J host is not connected to the relay yet", "fix_offline": "wait until it connects",
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
    try:
        relay = st.config().get("relay") or ""
    except (OSError, ValueError):
        relay = ""
    seat = seatcert.SeatCert(st, relay).verdict()
    if seat not in seatcert.ALLOWED:
        route, account = "seat", False
    return {"route": route, "passphrase_set": pp, "bound": bound, "account": account,
            "account_url": cloud.app_url(st) if bound else None, "why": why, "seat": seat}


def message(chk: dict, lang: str = "zh") -> str:
    """The one sentence every surface shows for this pre-check (terminal, skill via --check, onboarding)."""
    t = TEXT["en" if lang == "en" else "zh"]
    url = chk.get("account_url") or cloud.DEFAULT_APP
    if chk["route"] == "seat":
        return seatcert.message("pair", lang, url) + seatcert.message(chk.get("seat") or "unbound", lang, url)
    if chk["route"] == "account":
        return t["account_with_pass" if chk["passphrase_set"] else "account"].format(url=url)
    if chk["route"] == "passphrase":
        return t["passphrase"]
    if chk["bound"] and chk.get("why"):
        return t["set_passphrase_bound"].format(url=url, why=t["why_" + chk["why"]], fix=t["fix_" + chk["why"]])
    return t["set_passphrase"]


def both(chk: dict) -> str:
    return message(chk, "zh") + "\n" + message(chk, "en")
