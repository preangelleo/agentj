"""#459: the one way Agent J tells anyone to install or upgrade OpenCode — the newest 2.x.

Chairman 2026-11-04: Agent J must always help users install the latest OpenCode 2.x. Every install / switch / rescue /
doctor hint reads these constants; nothing else in the package spells an OpenCode install command. Do not point at
GitHub `releases/latest`, mise/asdf `latest` or the `opencode-ai` npm package: those follow the maintained 1.x line.
The one-line installer (setup-agents.sh) pins a SHA256-verified 2.x archive of its own; its major must equal MIN_MAJOR.
Security A-N3: what the Agent is told to run is the major-pinned npm package (NPM); the official `curl | bash` script
(SCRIPT) is for the owner's own terminal in install.md only, never in host hints.
"""
from __future__ import annotations

import re

MIN_MAJOR = 2
SCRIPT = "curl -fsSL https://opencode.ai/v2/install | bash"          # official v2 installer, newest 2.x, no Node needed
NPM = "npm i -g @opencode/cli@2"                                     # newest 2.x from npm (needs Node.js)
NPM_MIRROR = NPM + " --registry=https://registry.npmmirror.com"


def major(version) -> int | None:
    m = re.search(r"(\d+)\.\d+", str(version or ""))
    return int(m.group(1)) if m else None


def current(version) -> bool:
    """True when the installed OpenCode is 2.x or newer."""
    n = major(version)
    return n is not None and n >= MIN_MAJOR


def install_text(lang: str = "zh") -> str:
    if lang == "en":
        return (f"install the newest OpenCode 2.x: `{NPM}` (needs Node.js; without it your human follows install.md "
                "step 3a in their own terminal)")
    return f"装最新的 OpenCode 2.x：`{NPM}`（需要 Node.js；没有的话由主人按 install.md 第 3a 步在自己的终端装）"


def upgrade_text(version, lang: str = "zh") -> str:
    """The 1.x hint: what is installed and the one command that moves it to the newest 2.x."""
    if lang == "en":
        return (f"OpenCode {version} is 1.x; Agent J uses the newest 2.x: the Agent runs `{NPM}`, then "
                "`agentj agent detect --json` (must show 2.x as the one Agent J uses; 1.x never upgrades itself) and "
                "`agentj agent restart`")
    return (f"OpenCode {version} 是 1.x；Agent J 用最新的 2.x：由 Agent 运行 `{NPM}`，再 `agentj agent detect --json`"
            "（Agent J 用的那个要显示 2.x；1.x 不会自己升）和 `agentj agent restart`")
