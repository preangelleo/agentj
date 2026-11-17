"""R-0055 (chairman 11-17): the owner lost the only remote with Face ID. Words only — who decides is unchanged.

`agentj revoke` says up front which remotes can confirm, what to tap and for how long, counts down while serve waits, and
on a refusal or timeout names the way out. `agentj faceid approve` refused with approver_exists names those remotes and
the same way out. The way out is the existing account-dashboard unbind (serve.remote_unbind, no Face ID: the Dashboard
login is the proof) or, with Agent J stopped, the `agentj admin` page (admin.revoke edits the allowlist directly). Both
remove the device record, its passkey with it, so afterwards it no longer blocks `agentj faceid approve`."""
from __future__ import annotations

import threading
import time

from .text import pick

CARD_MAX = 120      # serve.ASK_TTL: a revoke card never lives longer (approval.timeout may only shorten it)
TICK = 30           # seconds between "still waiting" lines (plain lines: safe in a pipe / a service log)


def card_secs(st) -> int:
    try:
        from . import preferences
        v = int(preferences.get(preferences.effective(st), "approval.timeout", CARD_MAX))
        return max(1, min(CARD_MAX, v))
    except Exception:  # noqa: BLE001 — a display figure, never a failure
        return CARD_MAX


def faceid_remotes(st, online: set) -> list[tuple[str, str, bool]]:
    """(device id, name, online) of every listed remote with a bound passkey — the ones that can give Face ID / UV."""
    out = []
    for did, v in st.devices().items():
        if st.passkey_of_device(did):
            out.append((did, str((v or {}).get("name") or ""), did in online))
    return out


def _rows(lang, rows, target=None) -> list[str]:
    return [pick(lang, f"  - 「{n}」 {d}  ", f"  - \"{n}\" {d}  ") + pick(lang, "在线" if on else "离线", "online" if on else "offline")
            + (pick(lang, "（就是要解绑的这台）", " (the one being unbound)") if d == target else "") for d, n, on in rows]


def recovery(st, lang) -> list[str]:
    """The way out when no Face ID remote is at hand (lost, broken, reset)."""
    from . import cloud
    out = [pick(lang, "手边没有设了 Face ID 的遥控器（比如丢的正是它）：", "No remote with Face ID at hand (for example, the lost one was it):")]
    try:
        linked, url = cloud.read_cloud(st) is not None, cloud.app_url(st)
    except Exception:  # noqa: BLE001
        linked, url = False, cloud.DEFAULT_APP
    if linked and st.remote_unbind():
        out.append(pick(lang, f"  1. 在账号后台 {url} 登录，在这个 Agent 的卡片上找到那台遥控器，点「解绑」。Agent J 在线时大约 20 秒内执行，不用 Face ID。",
                        f"  1. Sign in at {url}, find that remote on this Agent's card and tap \"Unlink\". Agent J carries it out within about 20 seconds while online; no Face ID needed."))
    elif linked:
        out.append(pick(lang, "  1. 账号后台解绑被关了：先运行 `agentj remote-unbind on`，再到账号后台 " + url + " 的 Agent 卡上点「解绑」。",
                        "  1. Unlinking from the account dashboard is off: run `agentj remote-unbind on`, then tap \"Unlink\" on the Agent card at " + url + "."))
    else:
        out.append(pick(lang, "  1. 这台 Agent J 主机没绑账号，账号后台解绑用不了，看第 2 条。",
                        "  1. This Agent J host is not linked to an account, so the account dashboard cannot unlink; see 2."))
    out.append(pick(lang, "  2. 在这台 Agent J 主机上：`agentj service stop`，再运行 `agentj admin`，在管理页里点那台遥控器的「解绑」，最后 `agentj service start`。"
                          "（Agent J 运行时管理页解绑也要 Face ID。）",
                    "  2. On this Agent J host: `agentj service stop`, run `agentj admin` and tap \"Unlink\" beside that remote, then `agentj service start`. "
                    "(While Agent J runs, the admin page's unlink also needs Face ID.)"))
    out.append(pick(lang, "  解绑后它的 Face ID 也一起删了。没有别的 Face ID 遥控器时，在留下的遥控器上点「在本机设置」：只有它在线就直接设好，否则用 `agentj faceid approve <对照码>` 在这里批准。",
                    "  Unlinking also removes its Face ID. With no other Face ID remote left, tap \"Set up here\" on a remaining remote: if it is the only one online it is set up at once, otherwise approve it here with `agentj faceid approve <code>`."))
    return out


def revoke_intro(st, lang, did: str, online: set) -> list[str]:
    """Printed before `agentj revoke` waits: what is unbound, who can confirm, what to tap, how long."""
    name = str((st.devices().get(did) or {}).get("name") or "")
    rows = faceid_remotes(st, online)
    secs = card_secs(st)
    out = [pick(lang, f"要解绑「{name}」（{did}）：需要在一台设了 Face ID 的遥控器上确认。",
                f"Unbinding \"{name}\" ({did}) needs a confirmation on a remote with Face ID.")]
    if rows:
        out.append(pick(lang, "能确认的遥控器：", "Remotes that can confirm:"))
        out += _rows(lang, rows, did)
        out.append(pick(lang, f"在其中一台上打开 Agent J，点解绑卡片上的批准并用 Face ID / 指纹验证。卡片 {secs} 秒内有效。没设 Face ID 的遥控器点了也不算。",
                        f"Open Agent J on one of them, tap approve on the unbind card and confirm with Face ID / fingerprint. The card lasts {secs} s. A tap on a remote without Face ID does not count."))
    else:
        out.append(pick(lang, "现在没有一台遥控器设了 Face ID，这张卡没人能批准。先在一台遥控器上设 Face ID（没有别的 Face ID 遥控器时用 `agentj faceid approve <对照码>` 在这里批准），再运行这条命令。",
                        "No remote has Face ID now, so nobody can approve this card. Set up Face ID on a remote first (with no other Face ID remote, approve it here with `agentj faceid approve <code>`), then run this again."))
    if not any(on and d != did for d, _, on in rows):
        out.append(pick(lang, "注意：现在没有别的设了 Face ID 的遥控器在线。", "Note: no other remote with Face ID is online right now."))
        out += recovery(st, lang)
    return out


def revoke_failed(st, lang, why: str) -> str:
    head = {"uv_required": pick(lang, "✗ 没有解绑：卡片过期、被拒绝，或不是用 Face ID 批准的。",
                                "✗ Not unbound: the card expired, was declined, or was not approved with Face ID."),
            "busy": pick(lang, "✗ 确认未完成，没有解绑。", "✗ Confirmation did not finish; the remote was not unbound.")}[why]
    return "\n".join([head, *recovery(st, lang)])


def approver_exists(st, lang, online: set) -> str:
    rows = faceid_remotes(st, online)
    out = [pick(lang, "✗ 还有设了 Face ID 的遥控器能批准，所以这里不能批准：", "✗ A remote with Face ID can still approve this, so this terminal may not:")]
    out += _rows(lang, rows)
    out.append(pick(lang, "在其中一台（不是正在设置的这台）上打开 Agent J，确认卡上的对照码一致后点「批准（Face ID）」。",
                    "Open Agent J on one of them (not the one being set up) and tap \"Approve (Face ID)\" when the codes match."))
    out += recovery(st, lang)
    out.append(pick(lang, "解绑之后再运行一次 `agentj faceid approve <对照码>`。对照码 5 分钟有效，过期了就在遥控器上再点一次「在本机设置」。",
                    "After unlinking, run `agentj faceid approve <code>` again. The code lasts 5 minutes; if it expired, tap \"Set up here\" again on the remote."))
    return "\n".join(out)


def wait(call, secs: int, lang, say, tick: float | None = None):
    """Run `call()` (a blocking ctl call) in a thread; every `tick` s say how long the card still has. Re-raises its error."""
    tick = TICK if tick is None else tick
    box: dict = {}

    def run():
        try:
            box["res"] = call()
        except BaseException as e:  # noqa: BLE001 — handed back to the caller's thread
            box["err"] = e
    t = threading.Thread(target=run, daemon=True)
    t0 = time.monotonic()
    t.start()
    while t.is_alive():
        t.join(tick)
        if t.is_alive():
            left = int(secs - (time.monotonic() - t0))
            say(pick(lang, f"… 还在等 Face ID 确认，卡片还剩约 {left} 秒" if left > 0 else "… 卡片已到期，等 Agent J 回话",
                     f"… still waiting for Face ID, about {left} s left on the card" if left > 0 else "… card expired, waiting for Agent J's answer"))
    if "err" in box:
        raise box["err"]
    return box.get("res")
