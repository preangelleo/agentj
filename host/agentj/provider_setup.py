"""Provider setup: public routing metadata and confirmed, local key-row correction.

No credential shape guesses. Only same-vendor fixed read-only endpoints receive the exact selected key.
Offers are private, bounded, one-use and expire; the key manager's existing UV challenge authorizes each write.
"""
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path
from . import ark, bailian, elevate, keys, opencode_provider as op

GROUPS = (
    ('ARK_AGENT_PLAN_API_KEY', 'ARK_CODING_PLAN_API_KEY', 'ARK_API_KEY'),
    ('BAILIAN_TOKEN_PLAN_API_KEY', 'DASHSCOPE_API_KEY'),
)
LEGACY = 'BAILIAN_CODING_PLAN_API_KEY'
NAMES = frozenset(n for g in GROUPS for n in g) | {LEGACY}
TTL = 600


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _owned(host):
    a = getattr(host, 'agent', None)
    return bool(a and a.kind == 'opencode' and a.owns_harness() and not a.cfg.get('_workflow_ceo'))


def _idle(host):
    a = getattr(host, 'agent', None)
    turn = getattr(a, 'turn_done', None)
    return not turn or turn.is_set()


def remember(agent, cfg):
    if not isinstance(cfg, dict):
        return
    safe = {}
    if isinstance(cfg.get('small_model'), str):
        safe['small_model'] = cfg['small_model']
    if isinstance(cfg.get('agent'), dict):
        safe['agent'] = {name: {'model': v['model']} for name, v in cfg['agent'].items()
                         if isinstance(name, str) and isinstance(v, dict) and isinstance(v.get('model'), str)}
    agent.auxiliary_config = safe


def scope_stamp(host):
    """Local metadata only. A later owner edit ends the approved override, including project/config-content choices."""
    import os
    base = Path(op.config_file())
    paths = {base, base.with_suffix('.jsonc')}
    directory = Path(host.agent.cfg.get('dir') or '.').resolve()
    for parent in (directory, *directory.parents):
        paths.update(parent / name for name in ('opencode.json', 'opencode.jsonc', '.opencode/opencode.json', '.opencode/opencode.jsonc'))
    if os.environ.get('OPENCODE_CONFIG'):
        paths.add(Path(os.environ['OPENCODE_CONFIG']))
    info = []
    for path in sorted(paths):
        try:
            st = path.stat()
            info.append((str(path), st.st_ino, st.st_mtime_ns, st.st_size))
        except OSError:
            info.append((str(path), None))
    return _hash(json.dumps([info, os.environ.get('OPENCODE_CONFIG_CONTENT') or '']))


def overlay(host, content):
    """An owner-approved model-only override, applied after permission injection. Never changes permissions."""
    try:
        saved = json.loads((host.st.root / 'provider-routing.json').read_text())
        data = json.loads(content)
        main = host.agent.cfg.get('model')
        if (saved.get('unified') is not True or saved.get('scope_stamp') != scope_stamp(host)
                or not isinstance(data, dict) or not isinstance(main, str)):
            return content
        data['small_model'] = main
        agents = data.setdefault('agent', {})
        for name in saved.get('agents', []):
            if isinstance(name, str) and isinstance(agents.get(name, {}), dict):
                agents.setdefault(name, {})['model'] = main
        return json.dumps(data)
    except (OSError, ValueError, TypeError, AttributeError):
        return content


def routing(host):
    a = getattr(host, 'agent', None)
    if not a or a.kind != 'opencode':
        return None
    main = a.cfg.get('model')
    if not isinstance(main, str) or '/' not in main or len(main) > 160:
        return None
    try:
        cfg = op._load(op.config_file())
    except (OSError, op.ProviderError):
        return {'main': main, 'rows': [], 'writable': False}
    effective = getattr(a, 'auxiliary_config', None)
    routes = effective if isinstance(effective, dict) else cfg
    rows = []
    small = routes.get('small_model')
    if isinstance(small, str):
        rows.append({'role': 'small_model', 'model': small[:160], 'provider': small.split('/', 1)[0][:40]})
    else:
        rows.append({'role': 'small_model', 'model': '', 'provider': ''})
    for name, data in (routes.get('agent') or {}).items() if isinstance(routes.get('agent'), dict) else ():
        model = data.get('model') if isinstance(data, dict) else None
        if isinstance(model, str):
            rows.append({'role': 'agent:' + name[:60], 'model': model[:160], 'provider': model.split('/', 1)[0][:40]})
    info = Path(op.config_file()).stat() if Path(op.config_file()).exists() else None
    meta = (info.st_ino, info.st_mtime_ns, info.st_size) if info else None
    stamp = _hash(json.dumps([main, meta, rows, scope_stamp(host)], sort_keys=True))[:56]
    different = any(not r['model'] or r['provider'] != main.split('/', 1)[0] for r in rows)
    return {'main': main, 'rows': rows[:80], 'different': different, 'stamp': stamp,
            'writable': _owned(host) and len(rows) <= 80, 'effective': isinstance(effective, dict)}


def unify(host, stamp):
    if not _owned(host):
        return {'ok': False, 'why': 'unavailable'}
    if callable(getattr(host, 'stopped', None)) and host.stopped():
        return {'ok': False, 'why': 'unavailable'}
    if not _idle(host):
        return {'ok': False, 'why': 'busy'}
    with host.st.config_lock():
        current = routing(host)
        if not current or not current.get('writable') or not hmac.compare_digest(current.get('stamp', ''), stamp):
            return {'ok': False, 'why': 'changed'}
        path = op.config_file()
        if Path(path).is_symlink():
            return {'ok': False, 'why': 'symlink'}
        cfg = op._load(path)
        cfg['small_model'] = current['main']
        for data in (cfg.get('agent') or {}).values() if isinstance(cfg.get('agent'), dict) else ():
            if isinstance(data, dict) and isinstance(data.get('model'), str):
                data['model'] = current['main']
        agents = [r['role'][6:] for r in current['rows'] if r['role'].startswith('agent:')]
        # Owned child reads this last, so project and inherited env model choices are corrected too.
        op._write(path, cfg)
        host.st.write_private(host.st.root / 'provider-routing.json', json.dumps({'unified': True, 'agents': agents, 'scope_stamp': scope_stamp(host)}).encode())
        host.agent.auxiliary_config = {'small_model': current['main'], 'agent': {n: {'model': current['main']} for n in agents}}
    # The existing credentials/config fingerprint restarts an owned child before its next turn; no prompt is replayed.
    return {'ok': True}


def _verify(name, value):
    from .ark_plan_fix import _models
    if name.startswith('ARK_'):
        plan = {'ARK_AGENT_PLAN_API_KEY': 'agent', 'ARK_CODING_PLAN_API_KEY': 'coding', 'ARK_API_KEY': 'payg'}.get(name)
        if not plan:
            return False
        url = ark.plan_data(plan)['block']['options']['baseURL'].rstrip('/') + '/models'
    elif name == 'BAILIAN_TOKEN_PLAN_API_KEY':
        url = bailian.plan('token-plan')['verify_url']
    elif name == 'DASHSCOPE_API_KEY':
        url = 'https://dashscope.aliyuncs.com/compatible-mode/v1/models'
    else:
        return False
    # Ark plan servers sometimes lack a model-list route. No native call / paid fallback for a key-row check.
    return _models(url, value) == 200


def check_key(host, source, *, verify=None):
    if not isinstance(source, str) or source not in NAMES:
        return {'ok': False, 'why': 'shape'}
    from .ark_plan_fix import read_key
    value = read_key(source)
    if not value:
        return {'ok': False, 'why': 'missing'}
    test = verify or _verify
    if source != LEGACY and test(source, value):
        return {'ok': True, 'valid': True}
    group = GROUPS[1] if source == LEGACY else next(g for g in GROUPS if source in g)
    targets = [n for n in group if n != source and test(n, value)]
    if len(targets) != 1:
        return {'ok': True, 'valid': False}  # network / no route / ambiguous is never a plan diagnosis
    offers = getattr(host, '_provider_row_offers', {})
    offers = {k: v for k, v in offers.items() if 0 <= time.time() - v['at'] < TTL}
    if len(offers) >= 16:
        offers.pop(next(iter(offers)))
    offer = 'FIX_' + secrets.token_hex(12)
    offers[offer] = {'source': source, 'target': targets[0], 'hash': _hash(value), 'at': time.time()}
    host._provider_row_offers = offers
    return {'ok': True, 'valid': False, 'offer': offer, 'source': source, 'target': targets[0]}


def move_key(host, offer, *, verify=None):
    rec = getattr(host, '_provider_row_offers', {}).pop(offer, None)
    if not rec or not 0 <= time.time() - rec['at'] < TTL:
        return {'ok': False, 'why': 'gone'}
    from .ark_plan_fix import read_key
    with host.st.config_lock():
        value = read_key(rec['source'])
        if not value or not hmac.compare_digest(_hash(value), rec['hash']):
            return {'ok': False, 'why': 'changed'}
        previous = read_key(rec['target'])
        if previous and not hmac.compare_digest(previous, value):
            return {'ok': False, 'why': 'exists'}
        if not (verify or _verify)(rec['target'], value):
            return {'ok': False, 'why': 'verify'}
        # Network verification can take seconds; a newer save on another remote must win.
        current = read_key(rec['source'])
        target = read_key(rec['target'])
        if not current or not hmac.compare_digest(_hash(current), rec['hash']):
            return {'ok': False, 'why': 'changed'}
        if target and not hmac.compare_digest(target, value):
            return {'ok': False, 'why': 'exists'}
        buf = bytearray(value.encode())
        try:
            spec = elevate.parse_dest('env:' + keys.env_path() + '#' + rec['target'], rec['target'], None, host.st.root)
            elevate.write_secret(spec, buf)
            # Copy first; only a successful write can remove the mistaken row. A failure leaves a recoverable copy.
            current = read_key(rec['source'])
            if current and hmac.compare_digest(_hash(current), rec['hash']):
                keys.delete_name(rec['source'])
        except (OSError, elevate.Refused):
            return {'ok': False, 'why': 'write'}
        finally:
            elevate.wipe(buf)
    return {'ok': True, 'target': rec['target']}


def error_model(data):
    """Only explicit structured model attribution; a session error without this must stay unknown."""
    if not isinstance(data, dict):
        return None
    p, m = data.get('providerID'), data.get('modelID')
    if isinstance(p, str) and isinstance(m, str) and op.ID_RE.fullmatch(p) and op.MODEL_RE.fullmatch(m):
        return p + '/' + m
    return None


def limit_note(model, main, data, lang):
    zh = lang.startswith('zh')
    text = str(data.get('message') or '')[:4096].lower() if isinstance(data, dict) else ''
    window = next((label[0 if zh else 1] for terms, label in (
        (('weekly', 'week', '本周', '每周'), ('本周', 'weekly')),
        (('monthly', 'month', '本月', '每月'), ('本月', 'monthly')),
        (('5 hour', '5-hour', '5h', '5小时', '5 小时'), ('5 小时', '5-hour')),
        (('daily', 'day', '每日', '当天'), ('每日', 'daily')),
    ) if any(t in text for t in terms)), '周期未说明' if zh else 'window unspecified')
    from .privacy import redact
    model = redact(model) if isinstance(model, str) else None
    main = redact(main)
    source = model or ('具体来源未确认（当前主模型：' + main + '）' if zh else 'exact source unconfirmed (current main model: ' + main + ')')
    reset = data.get('resetAt') or data.get('reset_at') if isinstance(data, dict) else None
    # Only a timestamp, never an arbitrary provider string or raw response/key.
    reset = reset if isinstance(reset, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?(?:Z|[+-]\d\d:\d\d)', reset) else None
    tail = ('恢复时间：' if zh else 'Reset: ') + reset if reset else ('服务商未提供恢复时间。' if zh else 'The provider did not supply a reset time.')
    return (f'{source} · {window}额度/限流。{tail} 套餐请求限额与账户余额分别计算。' if zh else
            f'{source} · {window} quota/rate limit. {tail} Plan request limits are separate from account balance.')
