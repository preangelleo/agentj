"""Checked model limits, P67b. Native/config metadata always takes precedence.

The 922,000 input budget reserves the published 128,000 output tokens from the
1,050,000 window. The 272,000 Codex-pool budget is owner policy, not an API
model capacity claim or an inference from the long-context pricing threshold.
"""
from urllib.parse import urlsplit

CHECKED_ON = "2026-10-06"
MODEL_LIMITS = {
    "gpt-6": {"source_url": "https://developers.openai.com/api/docs/models/gpt-6.1-sol", "checked_on": CHECKED_ON, "verification": "owner-authorized alias; gpt-6 page unavailable", "openai_input": 922000, "agentsrelay_input": 272000},
    "gpt-6.1-sol": {"source_url": "https://developers.openai.com/api/docs/models/gpt-6.1-sol", "checked_on": CHECKED_ON, "verification": "official context 1050000, output 128000; input = difference", "openai_input": 922000, "agentsrelay_input": 272000},
    "gpt-6-astra": {"source_url": "https://developers.openai.com/api/docs/models/gpt-6-astra", "checked_on": CHECKED_ON, "verification": "official context 1050000, output 128000; input = difference", "openai_input": 922000, "agentsrelay_input": 272000},
    "gpt-6-luna": {"source_url": "https://developers.openai.com/api/docs/models/gpt-6-luna", "checked_on": CHECKED_ON, "verification": "official context 1050000, output 128000; input = difference", "openai_input": 922000, "agentsrelay_input": 272000},
}
# Pool caps and the explicit gpt-6 alias are owner-approved product policy.


def positive(value):
    return value if type(value) is int and value > 0 else None


def provider_kind(provider_id, provider):
    """Classify endpoint identity, never the display name or key's value."""
    opts = provider.get("options") if isinstance(provider, dict) else None
    base = opts.get("baseURL") if isinstance(opts, dict) else None
    if base is not None:
        if not isinstance(base, str):
            return None
        try:
            url = urlsplit(base)
            host = url.hostname
            if url.scheme != "https" or url.username or url.password or url.query or url.fragment:
                return None
        except ValueError:
            return None
        if host in ("agentsrelay.net", "www.agentsrelay.net"):
            return "agentsrelay"
        if host == "api.openai.com":
            return "openai"
        return None
    # Native OpenCode OpenAI uses its official endpoint without a configured URL.
    return "openai" if provider_id == "openai" else None


def context_limit(provider_id, model_id, configured=None, native=None):
    """Explicit config > native model metadata > checked provider/model table."""
    configured = configured if isinstance(configured, dict) else {}
    native = native if isinstance(native, dict) else {}
    for provider in (configured, native):
        models = provider.get("models")
        model = models.get(model_id) if isinstance(models, dict) else None
        limits = model.get("limit") if isinstance(model, dict) else None
        limit = positive(limits.get("context")) if isinstance(limits, dict) else None
        if limit is not None:
            return limit
    row = MODEL_LIMITS.get(model_id)
    effective = {**native, **configured}
    native_opts = native.get('options') if isinstance(native.get('options'), dict) else {}
    config_opts = configured.get('options') if isinstance(configured.get('options'), dict) else {}
    effective['options'] = {**native_opts, **config_opts}
    kind = provider_kind(provider_id, effective)
    return row.get(kind + "_input") if row and kind else None


def codex_context_metadata(config):
    """Whitelist config/read capacity/endpoint identity; never retain auth data.

    Fields: https://developers.openai.com/codex/config-reference/ (2026-10-06).
    Built-in openai alone is ambiguous between account auth and direct API.
    """
    config = config if isinstance(config, dict) else {}
    pid = config.get('model_provider') or 'openai'
    providers = config.get('model_providers')
    p = providers.get(pid) if isinstance(providers, dict) and isinstance(pid, str) else None
    base = p.get('base_url') if isinstance(p, dict) else None
    if base is None and pid == 'openai':
        base = config.get('openai_base_url')
    return {'limit': positive(config.get('model_context_window')),
            'provider_kind': provider_kind('', {'options': {'baseURL': base}}) if base is not None else None}


def codex_context_limit(model, native_limit, metadata):
    metadata = metadata if isinstance(metadata, dict) else {}
    limit = positive(native_limit) or positive(metadata.get('limit'))
    if limit is not None:
        return limit
    row = MODEL_LIMITS.get(model)
    kind = metadata.get('provider_kind')
    return row.get(kind + '_input') if row and kind in ('agentsrelay', 'openai') else None
