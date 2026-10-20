"""P409: one owner-controlled Jev mode and fixed upstream routes.

An off value in editable preferences is insufficient: a live paired remote's
approval_set signature must back it. No environment flag can disable review.
"""
from dataclasses import dataclass, field
import os

KEY = "jev.mode"
MODES = ("typesafe", "openrouter", "off")
ROUTES = {
    "typesafe": ("TYPESAFE_API_KEY", "https://api.typesafe.ai/v1/systemone", "jev-1.13.0"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/alpha/decisions", "~typesafe/jev-latest"),
}

@dataclass(frozen=True)
class Route:
    mode: str | None
    key: str = field(default="", repr=False)
    endpoint: str = ""
    model: str = ""
    confirmed_at: int | None = None

    def public(self):
        return {"mode": self.mode, "ready": bool(self.key) or self.mode == "off",
                "confirmed_at": self.confirmed_at}


def resolve(*, st=None, prefs=None, env=None):
    from . import preferences, approval_level
    from .state import State
    st = st if st is not None else State()
    if prefs is None:
        try: prefs = preferences.effective(st)
        except (ValueError, OSError): prefs = {}
    if env is None:
        from .provider_runtime import fresh_environment
        env = fresh_environment(os.environ, extra=[r[0] for r in ROUTES.values()])
    choice = preferences.get(prefs, KEY, "")
    if choice == "off":
        signed = approval_level._live(st, KEY)
        if signed and signed.get("value") == "off":
            return Route("off", confirmed_at=signed["at"])
        choice = ""  # forged / revoked choice never disables review
    if choice not in ROUTES:
        # Existing OpenRouter owners keep their current route; official TypeSafe
        # is the recommended choice for new owners without an OpenRouter key.
        choice = next((m for m in ("openrouter", "typesafe") if str(env.get(ROUTES[m][0]) or "").strip()), None)
    if choice is None: return Route(None)
    name, endpoint, model = ROUTES[choice]
    key = str(env.get(name) or "").strip()
    if any(c.isspace() for c in key): key = ""
    return Route(choice, key, endpoint, model)


def instructions(lang="zh"):
    if lang == "en":
        return ("Safety review (Jev): recommended TypeSafe — register at https://typesafe.ai, create a key and add a small "
                "balance. Enter TYPESAFE_API_KEY in Models & API keys using the secret card. Already use OpenRouter? Enter "
                "OPENROUTER_API_KEY instead (https://openrouter.ai). In Settings → Safety choose the route, or turn Jev off "
                "and explicitly confirm weaker safety. Only your signed phone confirmation can skip; the Agent cannot. "
                "Local credential filters and dangerous-action approvals stay on. Jev can misjudge; off avoids its delay "
                "and false positives but removes model review of bot/friend traffic.")
    return ("安全审核（Jev）：推荐在 https://typesafe.ai 注册、创建 key 并充值少量金额，在「模型和 API Key」用密钥卡填写 "
            "TYPESAFE_API_KEY。已有 OpenRouter 账号可改填 OPENROUTER_API_KEY（https://openrouter.ai）。在 设置 → 安全 "
            "选择来源，或关闭 Jev 并明确确认「我知道安全性会弱一点」。只有主人在手机上的签名确认才能跳过，Agent 不能代选。"
            "本地凭据过滤和危险操作审批照常；Jev 也会误判，关掉可减少延迟与误拦，但客服和好友消息不再经过模型审核。")
