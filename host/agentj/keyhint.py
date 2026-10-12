"""P163: "the key may be incomplete" — a hint for a failed provider check, from the key's LENGTH only.

A pasted key that is much shorter than what its provider issues was almost always cut off while copying (support
st_3ahq: an 18-character key). The provider is recognised from the key's name, the provider address or the key's own
public prefix (sk-ant-, sk-or-, …). Nothing here returns, logs or compares key content: the answer is one word.
"""
from __future__ import annotations

import re
import urllib.parse

# Shortest length each provider issues today, with margin (real keys: OpenAI sk-proj 164 / legacy 51, Anthropic 108,
# OpenRouter 73, AgentsRelay 67, DeepSeek 35, Google AIza 39, Groq 56). A shorter key is "short" — never "invalid":
# providers change formats, so this only adds a hint to a check that already failed.
MIN_LEN = {"openai": 40, "anthropic": 90, "openrouter": 60, "agentsrelay": 50, "deepseek": 30, "google": 35,
           "groq": 45, "xai": 50, "mistral": 28}
GENERIC_MIN = 24              # any provider: a key shorter than this is never complete
_HOSTS = {"api.openai.com": "openai", "api.anthropic.com": "anthropic", "openrouter.ai": "openrouter",
          "agentsrelay.net": "agentsrelay", "api.deepseek.com": "deepseek", "generativelanguage.googleapis.com": "google",
          "api.groq.com": "groq", "api.x.ai": "xai", "api.mistral.ai": "mistral"}
_PREFIXES = (("sk-ant-", "anthropic"), ("sk-or-", "openrouter"), ("sk-proj-", "openai"), ("gsk_", "groq"),
             ("xai-", "xai"), ("AIza", "google"))
_NAMES = (("AGENTSRELAY", "agentsrelay"), ("OPENROUTER", "openrouter"), ("ANTHROPIC", "anthropic"), ("DEEPSEEK", "deepseek"),
          ("GEMINI", "google"), ("GOOGLE", "google"), ("GROQ", "groq"), ("XAI", "xai"), ("MISTRAL", "mistral"),
          ("OPENAI", "openai"))     # most specific first: AGENTSRELAY_OPENAI_KEY is AgentsRelay


def provider_of(name: str | None = None, url: str | None = None, value: bytes | str | None = None) -> str | None:
    """The provider these hints know, or None."""
    host = (urllib.parse.urlsplit(url).hostname or "") if isinstance(url, str) and url else ""
    for h, p in _HOSTS.items():
        if host == h or host.endswith("." + h):
            return p
    if isinstance(value, (bytes, bytearray)):
        head = bytes(value[:8]).decode("ascii", "replace")
    else:
        head = (value or "")[:8]
    for prefix, p in _PREFIXES:
        if head.startswith(prefix):
            return p
    n = (name or "").upper()
    for word, p in _NAMES:
        if re.search(r"(^|_)" + word + r"(_|$)", n):
            return p
    return None


def looks_short(length: int, name: str | None = None, url: str | None = None, value: bytes | str | None = None) -> bool:
    """True when a key of this length is clearly shorter than its provider issues (or shorter than any provider's)."""
    if length < GENERIC_MIN:
        return True
    p = provider_of(name, url, value)
    return bool(p) and length < MIN_LEN[p]


HINT = {"zh": "这把 key 比该服务商常见的短很多，可能复制不完整：请回服务商后台重新完整复制一次再粘贴。",
        "en": "This key is much shorter than this provider's keys usually are — it may not have been copied completely. "
              "Copy it again in full from the provider's dashboard and paste it."}
