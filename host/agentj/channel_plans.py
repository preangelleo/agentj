"""Host-only plan inventory; public rows contain identifiers, never credentials.

Saved provider profiles are selected without changing the main native config.
Unavailable/deleted profile material fails closed at execution time.
"""
from __future__ import annotations
import json
from . import provider_profiles as profiles
from .channel_avatar import KINDS as EXECUTORS


def profile_available(row):
    try:
        raw = profiles.secure_read(profiles.profile(row['id']) / 'key', 16384)
        return bool(raw.strip()) and not any(chr(c).isspace() for c in raw.strip())
    except (OSError, ValueError, KeyError):
        return False


def discover(detected, saved):
    installed = {r['name'] for r in detected['harnesses'] if r.get('installed') and r.get('supported')}
    out = [{'id':r['name'], 'kind':r['name'], 'config':{'kind':r['name']}}
           for r in detected['harnesses'] if r['name'] in installed and r['name'] in EXECUTORS and r.get('logged_in') is True]
    for name, label in (('kimi','Kimi Code'), ('qwen','Qwen Code'), ('dsh','DeepSeek Harness')):
        if name in installed:
            out.append({'id':name, 'kind':name, 'selectable':False, 'why':'harness_unavailable',
                        'label':{'zh':label + '：暂不支持，请使用已有服务商套餐',
                                 'en':label + ': not supported yet; use a saved provider profile'}})
    for row in saved:
        if row.get('harness') in EXECUTORS and row.get('harness') in installed and profile_available(row):
            kind = row['harness']
            model = row['id'] + '/' + row['model'] if kind == 'opencode' else row['model']
            out.append({'id':'profile:' + row['id'], 'kind':kind,
                        'config':{'kind':kind, 'model':model, 'channel_profile':row['id']}})
    return out


def inventory():
    from . import harness
    try:
        saved = profiles.listing()
    except (OSError, ValueError):
        saved = []
    detected = harness.detect()
    rows = discover(detected, saved)
    if any(r['name'] == 'opencode' and r.get('installed') and r.get('supported') for r in detected['harnesses']):
        from .binaries import resolve
        from .channel_free import option
        rows.append(option(resolve('opencode')['path']))
    return rows


def load(cfg):
    pid = cfg.get('channel_profile')
    if not pid:
        return None
    row = json.loads(profiles.secure_read(profiles.profile(pid) / 'profile.json'))
    if row.get('id') != pid or row.get('harness') != cfg.get('kind') or not profile_available(row):
        raise ValueError('channel_profile_unavailable')
    expected = pid + '/' + row['model'] if row['harness'] == 'opencode' else row['model']
    if expected != cfg.get('model'):
        raise ValueError('channel_profile_changed')
    return row


def environment(cfg, env):
    row = load(cfg)
    if not row:
        return env
    key = profiles.secure_read(profiles.profile(row['id']) / 'key', 16384).decode().strip()
    env = dict(env)
    if row['harness'] == 'claude':
        for name in ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN',*profiles.TIERS.values(),profiles.MAX_OUTPUT_ENV):
            env.pop(name, None)
        env.update(ANTHROPIC_BASE_URL=row['base_url'].removesuffix('/v1'), ANTHROPIC_AUTH_TOKEN=key)
        env.update(profiles.claude_env(row))
    elif row['harness'] == 'codex':
        env['AGENTJ_CHANNEL_PROFILE_KEY'] = key
    # OpenCode uses a host-built private in-memory provider configuration below.
    return env


def codex_settings(cfg):
    row = load(cfg)
    return {'model':row['model'], 'model_provider':row['id'], 'provider_table':{
        'name':row['id'], 'base_url':row['base_url'], 'wire_api':'responses',
        'env_key':'AGENTJ_CHANNEL_PROFILE_KEY', 'requires_openai_auth':False}}


def opencode_settings(cfg):
    row = load(cfg)
    key = profiles.secure_read(profiles.profile(row['id']) / 'key', 16384).decode().strip()
    return json.loads(profiles.merge(row, key, b'{}'))
