"""P175: can this computer reach OpenAI (what Codex needs), asked before the owner picks Codex — not after the first turn
fails. One unauthenticated GET to api.openai.com (no key is sent, nothing is read but the status and the error code):
  401 → "ok" (reachable, and OpenAI serves this region) · 403 + unsupported_country… → "region" ·
  no answer (DNS / connect / TLS / timeout) → "unreachable" · anything else → "unknown".
Advisory only: it never changes the harness decision (install.md Step 3: mainland China → OpenCode + a model there)."""
from __future__ import annotations

import os
import urllib.error
import urllib.request

URL = "https://api.openai.com/v1/models"
TIMEOUT = 5.0
REGION_WORDS = ("unsupported_country", "country, region, or territory", "not available in your")

ADVICE = {
    "region": "OpenAI 不服务这台电脑所在的地区：选 Codex 的话第一条消息就会失败。建议改用 Claude Code，或 OpenCode + 国内模型"
              "（install.md 第 3a 步）。 / OpenAI does not serve this computer's region: with Codex the first message would "
              "fail. Prefer Claude Code, or OpenCode with a model available here (install.md Step 3a).",
    "unreachable": "这台电脑连不上 OpenAI（api.openai.com）：选 Codex 的话第一条消息就会失败。在中国大陆请用 OpenCode + 国内模型"
                   "（install.md 第 3a 步）；其他地区先查网络。 / This computer cannot reach OpenAI: with Codex the first "
                   "message would fail. In mainland China use OpenCode with a local model (install.md Step 3a); elsewhere "
                   "check the network first.",
}


def _opener(environ: dict | None):
    """The proxy Codex itself would use: HTTPS_PROXY / https_proxy of `environ` (proxy.environment for the service)."""
    if environ is None:
        return urllib.request.build_opener()
    proxies = {}
    for scheme in ("https", "http"):
        v = environ.get(scheme.upper() + "_PROXY") or environ.get(scheme + "_proxy")
        if v:
            proxies[scheme] = v
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))


def probe(environ: dict | None = None, timeout: float = TIMEOUT) -> str:
    if os.environ.get("AGENTJ_REACH_PROBE") == "off":     # tests (tests/_hermetic.py): never touch the network
        return "skipped"
    req = urllib.request.Request(URL, method="GET", headers={"User-Agent": "agentj-reach-probe"})
    try:
        with _opener(environ).open(req, timeout=timeout) as r:
            return "ok" if r.status < 500 else "unknown"
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4000).decode("utf-8", "replace").lower()
        except Exception:  # noqa: BLE001
            body = ""
        finally:
            e.close()
        if e.code == 451 or (e.code == 403 and any(w in body for w in REGION_WORDS)):
            return "region"
        if e.code in (401, 403, 404, 429):
            return "ok"
        return "unknown"
    except (urllib.error.URLError, OSError, ValueError):
        return "unreachable"


def doctor_row(environ: dict | None = None) -> tuple[str, str, str] | None:
    """(status, summary, hint) for `agentj doctor` — warn at most: it is advice. None = not probed (tests)."""
    r = probe(environ)
    if r == "ok":
        return "ok", "OpenAI 可达 / reachable (Codex can connect)", ""
    if r == "skipped":
        return None
    if r == "unknown":
        return "warn", "OpenAI 回应异常，未能确认 / unexpected answer, not confirmed", ""
    return "warn", ("OpenAI 不服务此地区 / region not served" if r == "region" else
                    "连不上 OpenAI / unreachable") + " — Codex 会失败 / Codex will fail", ADVICE[r]

