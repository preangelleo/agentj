"""Fixed startup diagnostics: no provider exception, environment value or guessed cause."""
CODES = {"launch", "listen", "prepare", "events", "session", "start_failed", "start_timeout", "no_reply", "timeout", "call_failed", "native_failed"}


def guidance(kind: str, phase: str, lang: str = "zh") -> str:
    phase = phase if phase in CODES else "start_failed"
    code = "AJ_START_" + kind.upper() + "_" + phase.upper()
    if lang.startswith("en"):
        return (f"Diagnostic code: {code}. Run agentj doctor --offline to check the background service's PATH, HOME and proxy settings; "
                "a working terminal command can have a different environment. In mainland China, consider Volcano Ark "
                "(agentj provider ark), Alibaba Bailian (agentj provider bailian), or Kimi (agentj agent switch kimi). "
                "Fill keys only on the phone key card, then retry the switch.")
    return (f"原因码：{code}。运行 agentj doctor --offline 检查后台服务的 PATH、HOME 和代理；终端能运行时，后台服务的环境也可能不同。"
            "如果你在中国大陆，建议切换国内模型：火山方舟（agentj provider ark）、阿里百炼（agentj provider bailian）或 Kimi（agentj agent switch kimi）。"
            "密钥只填手机密钥卡，完成后再试切换。")
