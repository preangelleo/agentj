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
PACKAGE = "@opencode/cli"
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


def offer(st) -> dict:
    """Open the existing owner checklist; no install/role/owner permission is created."""
    from . import elevate
    action = {'item':'harness', 'op':'ai.install', 'params':{'cli':'opencode','source':'official'}}
    try:
        elevate.client_request(st, {'t':'setup','op':'resume'}, timeout=15)
    except (OSError, ValueError):
        pass  # the fixed local command remains usable without a running Host
    return action


def install_text(lang: str = "zh", *, native_only: bool = False) -> str:
    if lang == "en":
        text = ("open `agentj setup checklist --resume`; tap Install OpenCode (official native binary, no Node.js required); "
                "progress and Cancel stay on the card")
        return text if native_only else text + ". Only if Node.js is already configured, terminal fallback: `" + NPM + "`"
    text = "打开 `agentj setup checklist --resume`，点「安装 OpenCode」（官方独立二进制，无需 Node.js；卡上可看进度或取消）"
    return text if native_only else text + "。仅在已配置 Node.js 时，也可在终端运行 `" + NPM + "`"


def upgrade_text(version, lang: str = "zh") -> str:
    """The 1.x hint: what is installed and the one command that moves it to the newest 2.x."""
    if lang == "en":
        return (f"OpenCode {version} is 1.x; Agent J uses the newest 2.x: the Agent runs `{NPM}`, then "
                "`agentj agent detect --json` (must show 2.x as the one Agent J uses; 1.x never upgrades itself) and "
                "`agentj agent restart`")
    return (f"OpenCode {version} 是 1.x；Agent J 用最新的 2.x：由 Agent 运行 `{NPM}`，再 `agentj agent detect --json`"
            "（Agent J 用的那个要显示 2.x；1.x 不会自己升）和 `agentj agent restart`")
