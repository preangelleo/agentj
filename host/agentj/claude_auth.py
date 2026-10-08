"""Shared desktop auth is independent from service auth; never copy shell secrets."""
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
    import re
    return isinstance(text, str) and bool(re.search(r"not logged in|please run /login|authentication required", text, re.I))


def failure_notice(lang="zh"):
    if lang == "en":
        return ("Claude is not logged in for the computer's service. In a computer terminal, run `claude` and /login "
                "(Keychain on macOS), or `claude setup-token` and write the token to the private service env file "
                "(chmod 600), then `agentj service restart`. Never paste the token into chat.")
    return ("电脑上的服务没有 Claude 登录：在电脑终端运行 `claude` 并 /login 一次（macOS 存进钥匙串），"
            "或 `claude setup-token` 后自己写入服务专用 env 文件（chmod 600），再运行 `agentj service restart`。"
            "不要把 token 发到聊天里。")
