"""Install-time guards of the plaza (P426 #181-2), kept out of market.py: the publisher's own signature (#47) checked over the
downloaded bytes, trust-on-first-use of each package name's publisher key, and the static audit (plaza_audit) rendered for the
owner. market.run_install / run_publish call these; nothing here talks to the network.

TOFU file: <state>/plaza/publishers.json (0600) {"items": {"<package name>": {"fp": "<64 hex>" | null, "first_seen": "<UTC>"}}}
— the first publisher this machine saw for a name; null = it was unsigned then (recorded too, security review N4). A later
install signed by another key, signed where it was unsigned, or no longer signed at all, is a publisher change: the preview says
so and the confirm needs the owner's signed allow on the phone (the plaza_perms card, listing the old and the new fingerprint);
no command-line flag accepts it. The allow records the new publisher.
"""
from __future__ import annotations

import datetime
import json

from . import plaza_audit, plaza_trust
from .plaza import META, FENCE_CLOSE, FENCE_OPEN, PlazaError
from .text import clean_line

AUDIT_SIGNED_NOTE = ("这个包带有效的 Agent J 官方签名，但命中拒绝规则的照样拦：确认安装时手机上会弹一张卡逐条列出，你签名允许才装，只放行这一次 / "
                     "a valid Agent J signature does not exempt a BLOCK finding: at confirm a card listing each one comes to your "
                     "phone, and it installs only after you allow it — for this one install")
AUDIT_CARD_MAX = 30


class PublisherMismatch(PlazaError):
    """The served publisher signature does not verify over these bytes (exit 5 in market)."""


def _path(st):
    return st.root / "plaza" / "publishers.json"


def read_publishers(st) -> dict:
    try:
        d = json.loads(_path(st).read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    items = d.get("items") if isinstance(d, dict) else None
    return {k: v for k, v in (items or {}).items() if isinstance(v, dict) and (v.get("fp") is None or isinstance(v.get("fp"), str))} \
        if isinstance(items, dict) else {}


def record_publisher(st, name: str, fp: str | None) -> None:
    """Record `fp` (None = unsigned) as this name's publisher: the first one seen, or the new one the owner allowed."""
    items = read_publishers(st)
    if name in items and items[name].get("fp") == fp:
        return
    items[name] = {"fp": fp, "first_seen": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (st.root / "plaza").mkdir(mode=0o700, exist_ok=True)
    st.write_private(_path(st), json.dumps({"items": items}, ensure_ascii=False, indent=1).encode())


def publisher(st, d: dict, sha: str, m: dict, official: bool) -> dict:
    """{fp | None, short, known, changed, prev} for the preview / the confirm (known: this name has a TOFU record; prev: its
    fingerprint, None = recorded unsigned). Raises PublisherMismatch when a publisher signature is
    served but does not verify over exactly these bytes (sha) and this manifest's name / type / version."""
    try:
        f = plaza_trust.fields(d)
    except plaza_trust.TrustError:
        raise PublisherMismatch("发布方签名格式不对，拒绝安装（包可能在服务器上被改过）/ malformed publisher signature: refused "
                                "(the package may have been changed on the server)") from None
    rec = read_publishers(st).get(m["name"])
    known, prev = rec is not None, (rec or {}).get("fp")
    if f is None:          # an official package dropping a host key stays vouched for by the Agent J signature
        return {"fp": None, "short": None, "known": known, "prev": prev, "changed": known and prev is not None and not official}
    pub, sig, at = f
    if not plaza_trust.verify(pub, sig, m["name"], m["type"], m["version"], sha, at):
        raise PublisherMismatch("发布方签名对不上这个包，拒绝安装（包可能在服务器上被改过）/ the publisher signature does not match "
                                "this package: refused (it may have been changed on the server)")
    fp = plaza_trust.fingerprint(pub)
    return {"fp": fp, "short": plaza_trust.short_fp(fp), "known": known, "prev": prev, "changed": known and prev != fp}


def publisher_lines(p: dict, official: bool) -> list[str]:
    if official and not p["fp"]:
        who = "Agent J 官方发布（官方签名在本机核验）/ published by Agent J (official signature checked here)"
    elif p["fp"]:
        who = f"{p['short']}（签名已在本机核验 / verified on this machine）"
    else:
        who = "未签名 unsigned：发布者没有用自己的主机签名（旧版本发的，或服务器不支持）/ the publisher's host did not sign it"
    out = [f"{META}发布方签名 publisher signature: {who}"]
    if p["changed"]:
        out.append(f"{META}⚠ 发布方换了 / THE PUBLISHER CHANGED：本机上次见到「这个包名」时是 "
                   + (plaza_trust.short_fp(p["prev"]) if p["prev"] else "未签名 unsigned") + "，这次是 "
                   + (p["short"] or "未签名 unsigned") + "。可能是作者换了电脑，也可能是别人冒名。确认安装时手机上会弹一张确认卡（列出新旧指纹），"
                   "你签名允许才装 / the author may have a new computer — or someone else is using the name; at confirm a card "
                   "with the old and the new fingerprint comes to your phone, and it installs only after you allow it")
    return out


def audit_lines(findings: list, signed: bool) -> list[str]:
    """Fenced block for the preview / refusal: one agentj line per finding + the author's excerpt as escaped text."""
    from .market import _t
    if not findings:
        return [f"{META}静态审计 static audit: 没发现问题 / nothing found"]
    out = [f"{META}静态审计 static audit（固定规则，不用模型 / fixed rules, no model）：{len(findings)} 处 finding(s)"]
    if signed and plaza_audit.blocked(findings):
        out.append(f"{META}{AUDIT_SIGNED_NOTE}")
    for f in findings[:30]:
        # Jarvis ruling 3: a signed package's block finding is refused too, unless the owner signs it off on the phone
        tag = ("BLOCK（官方包也拦，要你在手机上签名放行 / official: your signed yes needed）" if signed else "拒绝 BLOCK") \
            if f["level"] == "block" else "注意 WARN"
        out.append(f"{META}[{tag}] {clean_line(f['path'], 160)} 第 {f['line']} 行 line {f['line']}：{f['zh']} / {f['en']}")
        out += _t(f["excerpt"]) if f["excerpt"] else []
    if len(findings) > 30:
        out.append(f"{META}……还有 {len(findings) - 30} 处 / {len(findings) - 30} more")
    return out


def audit_items(findings: list) -> list[dict]:
    """The block findings as card items {rule, path, line} — serve rebuilds the text from the rule table, never from these."""
    return [{"rule": f["rule"], "path": clean_line(f["path"], 160), "line": int(f["line"])}
            for f in plaza_audit.blocked(findings)][:AUDIT_CARD_MAX]


def refusal(findings: list, what: str) -> str:
    """The fenced text printed when block findings stop an install / a publish."""
    head = (f"静态审计拦下了这个包，{what} / the static audit blocks this package — "
            + ("not installed" if what == "没有安装" else "nothing sent") + ":")
    return "\n".join([head, FENCE_OPEN, *audit_lines(findings, False), FENCE_CLOSE,
                      "这些规则拦的是偷钥匙、下载后直接运行、藏起来的命令和想操纵 AI 的话。觉得是误判，可以在广场发反馈 / these rules "
                      "stop key theft, run-from-the-internet, hidden commands and words that steer the AI; a false alarm? send feedback"])
