"""Current-profile Relay dispatch through the existing authenticated Host adapter.

No raw native exec, official configuration edits, permission bypass or
automatic replay is implemented here.
"""
from __future__ import annotations

import argparse
import copy
import asyncio
import json
import os
import re
from pathlib import Path
import uuid
import sys
import time
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class _RelayProfile:
    id: str
    base_url: str
    model: str


BASES = frozenset(('https://agentsrelay.net/v1', 'https://api.agentsrelay.net/v1'))


def _route(host, req=None, cfg=None):
    if req is not None and 'base_url' in req:
        # Explicit per-invocation profile: never edits or consults the owner's
        # official provider table. Native caller/ceiling admission precedes this.
        base = req['base_url']
        from . import busy_native
        model = busy_native.parse(req.get('argv', []))[1].get('model')
        if (not isinstance(base, str) or base not in BASES or not isinstance(model, str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,99}', model)):
            raise ValueError('relay_profile_required')
        return {'source': 'independent', 'base_url': base}

    from . import peer_session
    agent = getattr(host, 'agent', None)
    if (getattr(agent, 'kind', None) != 'codex' or not hasattr(agent, 'config_stale')
            or agent.config_stale()):
        raise ValueError('relay_profile_required')
    selected = peer_session.codex_user_model(os.environ)
    table = selected.get('provider_table') or {}
    active = (getattr(agent, 'thread', {}) or {}).get('modelProvider') or (getattr(agent, 'human', {}) or {}).get('model_provider')
    if (not active or active != selected.get('model_provider') or table.get('base_url') not in BASES
            or table.get('wire_api') != 'responses'
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', active)
            or table.get('env_key') != 'AGENTSRELAY_OPENAI_KEY'
            or table.get('requires_openai_auth') not in (None, False)):
        raise ValueError('relay_profile_required')
    return {'source': active, 'base_url': table['base_url']}


def pinned_profile(value):
    if (type(value) is not _RelayProfile
            or not isinstance(value.id, str) or not re.fullmatch(r'agentj_relay_[a-f0-9]{32}', value.id)
            or value.base_url not in BASES
            or not isinstance(value.model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,99}', value.model)):
        raise ValueError('relay_profile_required')
    return value


def native_model_key(cfg):
    profile = cfg.get('_agentsrelay_provider')
    if profile is None:
        return None
    pinned_profile(profile)  # A request/config JSON dictionary cannot forge this Host-only value.
    return 'AGENTSRELAY_OPENAI_KEY'


def provider_table(profile):
    p = pinned_profile(profile)
    return {'name': 'Agents Relay', 'base_url': p.base_url, 'wire_api': 'responses',
            'env_key': 'AGENTSRELAY_OPENAI_KEY', 'requires_openai_auth': False}


def provider_options(profile):
    p = pinned_profile(profile)
    out = ['-c', 'model_provider=' + json.dumps(p.id), '-c', 'model=' + json.dumps(p.model)]
    for key, value in provider_table(p).items():
        out += ['-c', f"model_providers.{p.id}.{key}=" + json.dumps(value)]
    return out


def rpc_options(profile, params):
    p = pinned_profile(profile)
    # Replace route overrides, preserving unrelated native thread settings.
    config = {**{k:v for k,v in (params.get('config') or {}).items()
                 if k not in ('model_provider', 'model_providers', 'model') and not k.startswith('model_providers.')}, 'model_provider': p.id,
              'model_providers': {p.id: provider_table(p)}}
    clean = {k:v for k,v in params.items() if k not in ('configOverrides', 'config_overrides', 'model_provider', 'model_providers')}
    return {**clean, 'modelProvider': p.id, 'model': p.model, 'config': config}


def _text(host, *texts, source_cfg=None):
    from types import SimpleNamespace
    from . import display_privacy, peer_guard
    try:
        # Each scan owns a fresh ephemeral index. Reusing the display cache only
        # revalidates files, not newly added/rotated process environment values.
        source = source_cfg if source_cfg is not None else (getattr(host, 'agent_cfg', None) or getattr(getattr(host, 'agent', None), 'cfg', None))
        current = SimpleNamespace(agent_cfg=source if isinstance(source, dict) else {'dir': os.getcwd()})
        index = display_privacy.index(current).require_complete()
        # Check exact individual provider strings. JSON escaping changes Unicode,
        # quotes and backslashes and is not the content the native adapter sends.
        for text in texts:
            if not isinstance(text, str) or not peer_guard.outbound(text, index).ok:
                raise ValueError('relay_text_refused')
    except Exception:
        raise ValueError('relay_text_refused') from None


async def start(host, req, pid, uid):
    from . import busy_native
    req = copy.deepcopy(req)
    # Validate the same live kernel/session authority and native ceiling as busy.
    # Labels, JSON owner flags and an unfenced local process are not authority.
    try:
        if host.stopped():
            raise ValueError
        text, cfg, *_ = busy_native.policy(host, req, pid, uid)
    except (ValueError, AttributeError, KeyError):
        raise ValueError('relay_dispatch_refused') from None
    from . import agentsrelay_health
    captured = agentsrelay_health.snapshot(host, req, pid, uid, policy=(text, cfg, *_)) if req.get('operation') == 'check' else None
    route = _route(host, req, cfg)
    from .busy_probe import identifier
    model = cfg.get('model') or (getattr(host.agent, 'thread', {}) or {}).get('model') or (getattr(host.agent, 'human', {}) or {}).get('model')
    if identifier(model) is None:
        raise ValueError('relay_profile_required')
    await asyncio.to_thread(_text, host, text, model)
    # Source and route may have changed while the secret scan was off-loop.
    if _route(host, req, cfg) != route:
        raise ValueError('relay_profile_required')
    profile = _RelayProfile('agentj_relay_' + uuid.uuid4().hex, route['base_url'], model)
    if req.get('operation') == 'check':
        from . import agentsrelay_health
        async with runtime(host)['gate']:
            # Waiting is not disclosure authority; source and raw model are fresh.
            await asyncio.to_thread(_text, host, model, source_cfg=cfg)
            if not agentsrelay_health.valid(host, req, pid, uid, captured):
                raise ValueError('relay_dispatch_refused')
            prepared = await asyncio.to_thread(agentsrelay_health.prepare, profile)
            def final_disclosure():
                try:
                    _text(host, model, source_cfg=cfg)
                    return (agentsrelay_health.valid(host, req, pid, uid, captured)
                            and _route(host, req, cfg) == route)
                except (ValueError, AttributeError, KeyError, OSError):
                    return False
            return await asyncio.to_thread(agentsrelay_health.check, prepared, final_disclosure)
    try:
        receipt = await busy_native.start(host, req, pid, uid, relay_profile=profile)
    except (ValueError, AttributeError, KeyError):
        raise ValueError('relay_dispatch_refused') from None
    # Never print raw child output, arbitrary model/provider metadata or errors.
    return {k: receipt[k] for k in ('ok', 'key', 'manager', 'sandbox', 'fence')} | {'route': 'agentsrelay-independent-profile' if route['source'] == 'independent' else 'agentsrelay-current-profile'}


def runtime(host):
    return host.__dict__.setdefault('_relay_runtime', {'gate': asyncio.Semaphore(4), 'rows': {}})


async def execution_allowed(host, cfg, text, it):
    # The admitted native job owns its captured root and opaque model snapshot;
    # switching the visible Host session cannot redirect the secret inventory.
    try:
        profile = pinned_profile(cfg.get('_agentsrelay_provider'))
        await asyncio.to_thread(_text, host, text, profile.model, source_cfg=cfg)
        return True
    except ValueError:
        it['_relay_refused'] = True
        return False


async def run_queued(host, cfg, text, mode, it):
    from . import busy_native, busy
    row = runtime(host)['rows'][it['key']]
    ran = False
    code = None
    try:
        async with runtime(host)['gate']:
            if host.stopped():
                code = 130
            else:
                if not await execution_allowed(host, cfg, text, it):
                    code = 126
                    return
                if host.stopped():
                    code = 130
                    return
                ran = True
                row['status'] = 'running'
                code = await busy_native.run(host, cfg, text, mode, it)
    except asyncio.CancelledError:
        code = 130
        raise
    except (OSError, RuntimeError, ValueError):
        pass  # May have produced effects; never retry from an exception.
    finally:
        row.update(status='refused' if it.get('_relay_refused') is True else 'exit_zero' if code == 0 else 'cancelled' if code == 130 else 'failed' if type(code) is int else 'unknown', exit_code=code)
        host.st.log('relay_terminal', key=it['key'], status=row['status'], exit_code=code)
        if not ran:
            busy.write_finished(host.st.root, it, code)
            busy.remove_item(host.st.root, it['key'])


def query(host, req, pid, uid):
    from . import busy_native
    # Reuse the live kernel/session gate with a Host-constructed read-only plan.
    try:
        busy_native.policy(host, {'argv': ['codex', 'exec', 'inspect relay metadata'],
                                  'cwd': getattr(host.agent, 'cfg', {}).get('dir')}, pid, uid)
    except (ValueError, AttributeError, KeyError):
        raise ValueError('relay_dispatch_refused') from None
    rows = runtime(host)['rows']
    if req.get('operation') == 'stats':
        counts = {status: sum(r['status'] == status for r in rows.values())
                  for status in ('queued', 'running', 'exit_zero', 'failed', 'unknown', 'cancelled', 'refused')}
        return {'ok': True, 'scope': 'current_host_process', 'counts': counts,
                'cost_usd': None, 'fallback_rate': None, 'quota_reset_at': None}
    key = req.get('key')
    row = rows.get(key) if isinstance(key, str) and re.fullmatch(r'run:[a-f0-9]{12}', key) else None
    return {'ok': row is not None, 'status': row['status'] if row else 'unknown',
            'replay_allowed': False, 'scope': 'current_host_process'}


def _call(st, req):
    from . import names
    for attempt in range(4):
        try:
            answer = names.ctl_call(st, req, timeout=10)
        except names.ServeBusy:
            return {'ok': False, 'key': req.get('key'), 'error': 'outcome_unknown'}
        if answer is not None:
            return answer
        # ctl_call returns None only for ENOENT/ECONNREFUSED before send.
        if attempt < 3:
            print('Host not connected; retrying before send', file=sys.stderr, flush=True)
            time.sleep((.1, .2, .4)[attempt])
    return {'ok': False, 'key': req.get('key'), 'error': 'host_unreachable', 'connect_attempts': 4}


def command(args):
    from .state import State
    if args.channel == 'status' and not str(getattr(args, 'key', None) or '').startswith('run:'):
        from . import relay_full   # 0.30.29: key names (+ --check) or a `relay:` job receipt; `run:` keys stay below
        raise SystemExit(relay_full.status_command(args))
    if args.channel in ('claude', 'codex') and getattr(args, 'full', False):
        from . import relay_full   # 0.30.29: one owner Face ID card, then serve runs the full-permission child
        raise SystemExit(relay_full.command(args))
    if args.channel in ('status', 'stats'):
        answer = _call(State(), {'cmd': 'agentsrelay_query', 'operation': args.channel,
                                 'key': getattr(args, 'key', None)})
        safe = {k: answer[k] for k in ('ok', 'status', 'scope', 'replay_allowed', 'counts',
                'cost_usd', 'fallback_rate', 'quota_reset_at', 'error', 'connect_attempts') if k in answer}
        print(json.dumps(safe))
        raise SystemExit(0 if safe.get('ok') else 1)
    if args.channel not in ('codex', 'check'):
        print(json.dumps({'ok': False, 'error': 'claude_host_adapter_unavailable',
                          'hint': 'agentj relay claude --full "<task>"'}))
        raise SystemExit(2)
    argv = ['codex', 'exec']
    if args.sandbox:
        argv += ['--sandbox', args.sandbox]
    if args.model:
        argv += ['--model', args.model]
    argv.append('Agent J connectivity check' if args.channel == 'check' else args.prompt)
    key = 'run:' + uuid.uuid4().hex[:12]
    req = {'cmd': 'agentsrelay_native', 'key': key, 'argv': argv, 'cwd': str(Path.cwd()),
           'who': 'Agents Relay', 'label': 'Relay native dispatch', 'report': args.report}
    if args.channel == 'check':
        req['operation'] = 'check'
    if getattr(args, 'base_url', None) is not None:
        req['base_url'] = args.base_url
    answer = _call(State(), req)
    safe = {k: answer[k] for k in ('ok', 'key', 'manager', 'sandbox', 'fence', 'route', 'connect_attempts', 'health') if k in answer}
    if not safe.get('ok'):
        reasons = {'relay_profile_required', 'relay_text_refused', 'relay_dispatch_refused', 'outcome_unknown', 'host_unreachable'}
        safe['error'] = answer.get('error') if answer.get('error') in reasons else 'relay_dispatch_refused'
    print(json.dumps(safe))
    raise SystemExit(0 if safe.get('ok') else 1)


def add_parser(sub):
    p = sub.add_parser('relay', help='用当前已配置的 Agents Relay 派单 / dispatch through the current Agents Relay profile')
    modes = p.add_subparsers(dest='channel', required=True)
    q = modes.add_parser('status', help='没有参数：两把 Agents Relay 密钥是否已设置（只给名字）；relay:/run: 编号：查看结果，不执行也不续跑 / '
                         'no argument: whether the two Agents Relay keys are set (names only); a relay:/run: id: inspect it without executing or resuming')
    q.add_argument('key', nargs='?')
    q.add_argument('--check', action='store_true', help='用已存的密钥向 api.agentsrelay.net 发一次只读检查（不跟随跳转）/ '
                   'one read-only request to api.agentsrelay.net with each stored key (redirects refused)')
    q.set_defaults(fn=command)
    modes.add_parser('stats', help='current Host process metadata; unavailable provider telemetry stays unknown').set_defaults(fn=command)
    for channel, text in (("codex", "Host-managed current Codex profile; with --full: one owner Face ID card, then the Agent J host runs Codex headless"),
                          ("claude", "only with --full: one owner Face ID card, then the Agent J host runs Claude Code headless through Agents Relay"),
                          ("check", "one minimal fixed-text Codex Relay request; may use provider quota")):
        q = modes.add_parser(channel, help=text)
        q.add_argument('--sandbox', choices=('read-only', 'workspace-write', 'danger-full-access'))
        q.add_argument('--model')
        q.add_argument('--base-url', help='background-only exact Agents Relay /v1 HTTPS URL; requires --model; preserves official settings')
        q.add_argument('--report')
        if channel != 'check':
            q.add_argument('--full', action='store_true', help='主人在手机上 Face ID 同意后，由 Agent J 主机以完全权限在后台跑一次（经主人的 Agents Relay 密钥）/ '
                           "after the owner's Face ID card, the Agent J host runs it once headless with full permissions through the owner's Agents Relay key")
            q.add_argument('--cwd', help='--full：工作目录（默认当前目录）/ --full: working directory (default: here)')
            q.add_argument('--minutes', type=int, help='--full：时限分钟，1–240，默认 60 / --full: time limit in minutes, 1-240, default 60')
            q.add_argument('prompt', help='任务；- 表示从标准输入读 / the task; - reads it from stdin')
        q.set_defaults(fn=command)
