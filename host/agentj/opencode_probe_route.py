"""Project settings may disqualify a login probe, never choose its credential or destination."""
from itertools import chain
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from . import harness, opencode_free
from .opencode_provider import NPM


MAX_CONFIG = 1024 * 1024
BUILTIN_KEYS = {
    'openai':'OPENAI_API_KEY', 'anthropic':'ANTHROPIC_API_KEY', 'deepseek':'DEEPSEEK_API_KEY',
    'google':'GEMINI_API_KEY', 'openrouter':'OPENROUTER_API_KEY', 'opencode':'OPENCODE_API_KEY',
    'zhipuai':'ZHIPU_API_KEY', 'moonshotai':'MOONSHOT_API_KEY', 'alibaba':'DASHSCOPE_API_KEY',
}
BASE_ENV = {'PATH','LANG','LANGUAGE','TZ','SSL_CERT_FILE','SSL_CERT_DIR','NODE_EXTRA_CA_CERTS','MISE_DATA_DIR',
            'OPENCODE_SERVER_PASSWORD','OPENCODE_SERVER_USERNAME','OPENCODE_DISABLE_AUTOUPDATE',
            'OPENCODE_DISABLE_SHARE','OPENCODE_DISABLE_LSP_DOWNLOAD','OPENCODE_DISABLE_MODELS_FETCH',
            'OPENCODE_DISABLE_EMBEDDED_WEB_UI'}
KEY = re.compile(r'\{env:([A-Z][A-Z0-9_]{0,127})\}')
MODEL_FIELDS = {'name','id','attachment','reasoning','temperature','tool_call','release_date','limit','modalities','cost'}


def _read(path):
    """Walk with dirfds and NOFOLLOW. Never block on, or read, a special file."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    directory = os.open(path.anchor, flags | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, flags, dir_fd=directory)
        with os.fdopen(fd, 'rb') as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG:
                raise ValueError
            raw = f.read(MAX_CONFIG + 1)
            if len(raw) > MAX_CONFIG:
                raise ValueError
            return harness._jsonc(raw.decode('utf-8'))
    except FileNotFoundError:
        return {}
    finally:
        os.close(directory)


def _selection(doc, provider):
    if provider in doc.get('disabled_providers', []) or ('enabled_providers' in doc and provider not in doc['enabled_providers']):
        raise opencode_free.ProbeError('provider_unavailable')
    return doc.get('provider', {}).get(provider)


def _block(selected, mid):
    if not isinstance(selected, dict) or set(selected) - {'name','npm','options','models','whitelist','blacklist'}:
        raise ValueError
    if 'name' in selected and not isinstance(selected['name'],str):
        raise ValueError
    if selected.get('npm') and selected['npm'] not in NPM.values():
        raise ValueError
    if mid in selected.get('blacklist', []) or ('whitelist' in selected and mid not in selected['whitelist']):
        raise opencode_free.ProbeError('model_missing')
    options = selected.get('options', {})
    if not isinstance(options, dict) or set(options) - {'baseURL','apiKey'}:
        raise ValueError
    key = options.get('apiKey')
    if key is not None and not (isinstance(key, str) and KEY.fullmatch(key)):
        raise ValueError
    base = options.get('baseURL')
    if base is not None:
        u = urlsplit(base)
        if ('{' in base or '}' in base or u.username or u.password or u.query or u.fragment or not u.hostname or
                not (u.scheme == 'https' or (u.scheme == 'http' and u.hostname in ('127.0.0.1','::1','localhost')))):
            raise ValueError
    result = {k:v for k,v in selected.items() if k in ('name','npm')}
    definition = selected.get('models', {}).get(mid)
    if definition is not None:
        if not isinstance(definition, dict) or set(definition) - MODEL_FIELDS:
            raise ValueError
        for k, v in definition.items():
            if k == 'modalities':
                if not isinstance(v, dict) or set(v) - {'input','output'} or not all(
                        isinstance(a,list) and set(a) <= {'text','audio','image','video','pdf'} for a in v.values()):
                    raise ValueError
            elif k in ('limit','cost'):
                allowed = {'context','output','input'} if k == 'limit' else {'input','output','cache_read','cache_write'}
                if not isinstance(v, dict) or set(v) - allowed or not all(type(n) in (int,float) for n in v.values()):
                    raise ValueError
            elif not isinstance(v, (str,bool)):
                raise ValueError
        result['models'] = {mid:definition}
    if re.search(r'\{(?:file|env):', json.dumps(result)):
        raise ValueError
    return {**result,'options':options}


def selected_route(env, project, model):
    """Only an owner's global provider block supplies connection fields. Conflicting local layers fail closed."""
    provider, _, mid = model.partition('/')
    home = Path(env.get('HOME') or Path.home())
    global_dir = Path(env.get('XDG_CONFIG_HOME') or home / '.config') / 'opencode'
    folder = Path(project).resolve()
    local = [p / n for p in (*reversed(folder.parents),folder)
             for n in ('opencode.json','opencode.jsonc','.opencode/opencode.json','.opencode/opencode.jsonc')]
    if env.get('OPENCODE_CONFIG'):
        local.append(Path(env['OPENCODE_CONFIG']))
    if env.get('OPENCODE_CONFIG_DIR'):
        local += [Path(env['OPENCODE_CONFIG_DIR'])/n for n in ('opencode.json','opencode.jsonc')]
    try:
        trusted = None
        for name in ('config.json','opencode.json','opencode.jsonc'):
            selected = _selection(_read(global_dir / name), provider)
            if selected is not None:
                if trusted is not None and trusted != selected:
                    raise ValueError
                trusted = selected
        inline = env.get('OPENCODE_CONFIG_CONTENT') or '{}'
        if len(inline) > MAX_CONFIG:
            raise ValueError
        for doc in chain((harness._jsonc(inline),), (_read(p) for p in dict.fromkeys(local))):
            declared = _selection(doc, provider)
            if declared is not None and declared != trusted:
                raise ValueError
        route = {'model': model, 'instructions': [], 'plugin': [], 'mcp': {}, 'permission': {'*':'deny'}}
        if trusted is not None:
            route['provider'] = {provider:_block(trusted, mid)}
        return route
    except (ValueError, AttributeError, OSError, TypeError):
        raise opencode_free.ProbeError('probe_model_unconfirmed') from None


def environment(env, project, model, root, *, agent_plan=False):
    home = Path(env.get('HOME') or Path.home())
    route = selected_route(env, project, model)
    provider = model.partition('/')[0]
    if agent_plan:
        from . import ark_connect
        block, preset = ark_connect.official(env,project,model)
        route['provider'] = {provider:block or _block(preset, model.split('/',1)[1])}
    auth = route.get('provider', {}).get(provider, {}).get('options', {}).get('apiKey')
    key = KEY.fullmatch(auth)[1] if auth else BUILTIN_KEYS.get(provider)
    config = Path(root) / 'config' / 'opencode' / 'opencode.json'
    config.parent.mkdir(parents=True, mode=0o700)
    with config.open('x', encoding='utf-8') as f:
        f.write(json.dumps(route))
    config.chmod(0o600)
    keep = BASE_ENV | {key}
    child = {k:v for k,v in env.items() if k in keep or k.lower() in ('https_proxy','http_proxy','all_proxy','no_proxy')}
    # Retain only the native credential store location. No owner instructions, plugins, history or other inherited keys.
    return {**child, 'HOME':root, 'XDG_CONFIG_HOME':str(config.parent.parent),
            'XDG_DATA_HOME':str(env.get('XDG_DATA_HOME') or home/'.local/share'),
            'OPENCODE_DISABLE_PROJECT_CONFIG':'1', 'OPENCODE_DISABLE_DEFAULT_PLUGINS':'1',
            'OPENCODE_DISABLE_EXTERNAL_SKILLS':'1','OPENCODE_DISABLE_CLAUDE_CODE':'1'}
