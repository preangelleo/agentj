"""Shared desktop auth is independent from service auth; never copy shell secrets."""
import json
import re

from . import harness, service


def available(*, running=False, svc=None):
    import os
    env = dict(os.environ)
    if not running:
        for key in service.CLAUDE_AUTH_ENV:
            env.pop(key, None)  # invoking terminal is not the background service
        svc = service.status() if svc is None else svc
        if svc.get("installed"):
            env.update(service.claude_env(service.env_file(svc.get("name") or service.name())))
    return harness.claude_login(env)[0] in ("ok", "env")


def require_transition(st, old, candidate, *, running=False):
    from . import preferences
    cfg = st.config().get("agent") or {} if st.exists() else {}
    if cfg.get("kind") != "claude":
        return
    if preferences.get(candidate, "agent.session_mode") != "independent":
        return
    if preferences.get(old, "agent.session_mode") == "independent":
        return
    try:
        ready = available(running=running)
    except (OSError, service.ServiceError):
        ready = False
    if not ready:
        raise preferences.ConfigError("agent.session_mode", service.token_hint(service.name()))


def login_failure(text):
    return isinstance(text, str) and bool(re.search(r"not logged in|please run /login|authentication required", text, re.I))


def failure_notice(lang="zh"):
    if lang == "en":
        return ("Claude is not logged in for the Agent J host's service. On your paired phone: Settings → Emergency repair → AI login rescue "
                "signs it in again. For command-line troubleshooting, open Web Terminal on your phone and run `claude` and /login "
                "(Keychain on macOS), or `claude setup-token` and write the token to the private service env file "
                "(chmod 600), then `agentj service restart`. Never paste the token into chat.")
    return ("Agent J 主机上的服务没有 Claude 登录。先在手机 设置 → 紧急修复 → AI 登录救援 可以直接重新登录。需要命令排障时，用手机的网页终端："
            "在终端运行 `claude` 并 /login 一次（macOS 存进钥匙串），"
            "或 `claude setup-token` 后自己写入服务专用 env 文件（chmod 600），再运行 `agentj service restart`。"
            "不要把 token 发到聊天里。")


_SWITCH_PATTERNS = (
    ("native_cli_unsupported", r"(?:unknown|unrecognized) (?:option|argument|flag)[^\n]{0,80}--"),
    ("quota_exhausted", r"weekly limit|5-hour limit|insufficient[ _-]quota|quota (?:exceeded|exhausted)|"
                        r"credit balance is too low|out of credits|hit your limit|limit will reset|payment required|"
                        r"(?:http|api error:)\s*402\b"),
    ("rate_limited", r"rate[ _-]limit|too many requests|(?:http|api error:)\s*429\b"),
    ("region", r"country, region, or territory not supported|unsupported (?:country|region)|region (?:is )?not supported"),
    ("network", r"unable to connect|connection (?:error|refused|reset|timed out)|econnreset|econnrefused|eai_again|"
                r"enotfound|etimedout|certificate (?:error|verify|has expired)|self.signed certificate|"
                r"proxy authentication required|http\s*407\b"),
    ("plan", r"subscription (?:does not|doesn't|is required)|requires (?:an? )?(?:active )?subscription|"
             r"only available with [^\n]{0,100}subscription|disabled claude subscription access"),
    ("model_missing", r"model (?:not found|does not exist|is not available|is not supported)|invalid model|"
                      r"unknown model|unsupported model|error with the selected model|not_found_error[^\n]*\bmodel\b"),
    ("not_logged_in", r"not logged in|please run /login|authentication required|"
                      r"(?:oauth|refresh) token.{0,20}(?:expired|revoked|invalid)|"
                      r"login (?:has )?expired|(?:sign in|log in) again"),
    ("auth", r"authentication_error|invalid (?:x-)?api[ -]key|invalid bearer token"),
    ("access", r"permission_error|access denied|403\s+forbidden"),
)
SWITCH_FAILURES = frozenset(code for code, _ in _SWITCH_PATTERNS)


def switch_failure(text):
    from . import auth_rescue

    text = auth_rescue.plain(text).lower()
    if auth_rescue.USAGE_LIMIT_RE.search(text):
        return "quota_exhausted"
    return next((code for code, pattern in _SWITCH_PATTERNS if re.search(pattern, text)), None)


def result_failure(event):
    """Select only known error fields from a failed Claude print/stream envelope."""
    if not isinstance(event, dict) or not (event.get("is_error") or event.get("error")
                                           or event.get("subtype") not in ("success", None)):
        return ""
    values = [event.get("result")]
    errors = event.get("errors")
    if isinstance(errors, list):
        values.extend(errors[:16])
    error = event.get("error")
    values.append(error)
    if isinstance(error, dict):
        values.extend(error.get(k) for k in ("type", "message", "code"))
    return "\n".join(v[:8192] for v in values if isinstance(v, str))


def probe_failure(stdout, stderr):
    try:
        event = json.loads(stdout)
    except ValueError:
        return switch_failure(stdout) or switch_failure(stderr)
    return switch_failure(result_failure(event)) or switch_failure(stderr)
