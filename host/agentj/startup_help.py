"""Fixed startup diagnostics: no provider exception, environment value or guessed cause."""
CODES = {"launch", "listen", "prepare", "events", "session", "start_failed", "start_timeout", "no_reply", "timeout", "call_failed", "native_failed"}


def category(reason=None):
    """Closed, structured reasons only. Startup timeouts do not prove a network fault."""
    return {'network': 'network', 'provider_probe_network': 'network', 'provider_probe_tls': 'network',
            'provider_probe_timeout': 'network', 'region': 'region', 'unsupported_country': 'region',
            'auth': 'auth', 'login': 'auth', 'no_key': 'auth', 'provider_probe_auth': 'auth',
            'access': 'access', 'model': 'model', 'model_missing': 'model',
            'provider_probe_model_or_endpoint': 'model', 'provider_probe_model': 'model',
            'rate_limit': 'quota', 'quota_window': 'quota', 'balance': 'quota',
            'provider_probe_rate_limit': 'quota', 'server': 'server', 'provider_probe_server': 'server',
            'missing': 'installation'}.get(reason, 'unknown')


def domestic_note(lang='zh'):
    # Existing registered setup paths; reply links ask the current main Agent.
    # They do not execute a CLI command, buy a plan or enable a paid model.
    from .ai_route import domestic_routes
    routes = domestic_routes()
    facts = '; '.join(f"{r['name'] if lang.startswith('en') else r['name_zh']} ({'/'.join(r['harnesses'])}; {r['setup']})" for r in routes)
    if lang.startswith('en'):
        return ('If mainland availability is the issue, choose [Volcano Ark](reply:Help me select Volcano Ark), '
                '[Alibaba Bailian](reply:Help me select Alibaba Bailian), or [Kimi](reply:Help me switch to Kimi). '
                f'Existing provider setup/switch paths: {facts}. '
                'Confirm plan/API compatibility first; no purchase or paid model is enabled automatically. Keys stay on the phone key card.')
    return ('若是中国大陆可用性问题，可选择[火山方舟](reply:帮我选择火山方舟)、[阿里百炼](reply:帮我选择阿里百炼)或'
            f'[Kimi](reply:帮我切换Kimi)，沿现有服务商配置/切换路径办理：{facts}。'
            '先确认套餐与 API 兼容性，不自动购买或启用付费模型；密钥只填手机密钥卡。')


def guidance(kind: str, phase: str, lang: str = "zh", *, reason=None) -> str:
    phase = phase if phase in CODES else "start_failed"
    code = "AJ_START_" + kind.upper() + "_" + phase.upper()
    if lang.startswith("en"):
        text = (f"Diagnostic code: {code}. Cause: {category(reason)}. Run agentj doctor --offline to check the background service's PATH, HOME and proxy settings; "
                "a working terminal command can have a different environment. An unknown cause does not establish a key or network failure.")
    else:
        text = (f"原因码：{code}。原因分类：{category(reason)}。运行 agentj doctor --offline 检查后台服务的 PATH、HOME 和代理；"
                "终端能运行时，后台服务的环境也可能不同。未知原因不能据此判定 Key 或网络失效。")
    return text + ('\n' + domestic_note(lang) if category(reason) in ('network', 'region') else '')
