"""Local heartbeat signals and measured usage; never execute owner note contents."""
from __future__ import annotations
import contextlib
import datetime as dt
import json
import time
from . import preferences


def quiet(p, now):
    if preferences.get(p, 'heartbeat.quiet_enabled', True) is False:
        return False
    start = preferences.get(p, 'heartbeat.quiet_start', '23:00')
    end = preferences.get(p, 'heartbeat.quiet_end', '07:00')
    at = time.strftime('%H:%M', time.localtime(now))
    return start <= at < end if start < end else at >= start or at < end if start != end else False


def collect(host, now):
    from . import jcards, jcards_todo, jcards_cal, avatar_log
    root = host.st.root
    clock = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    reasons, unknown = [], []
    for cid, mod, field in [('todo', jcards_todo, 'items'), ('calendar', jcards_cal, 'events')]:
        try:
            if jcards.find(root, cid) is None:
                continue  # an uninstalled card is not a failed read
            r = mod.listing(root, 'due', **({'now': clock, 'who': None} if cid == 'todo' else {'clock': clock}))
            if not r.get('ok') or r.get('problem'):
                unknown.append(cid)
            elif r.get(field):
                reasons.append(cid + '_due')
        except Exception:
            unknown.append(cid)
    pending = getattr(getattr(host, 'background_done', None), 'pending', {})
    finished = list(getattr(getattr(host, 'busy', None), 'finished', []))
    if pending or finished:
        reasons.append('background_reports')
        if any(v.get('exit_code') not in (None, 0) for v in [*pending.values(), *finished] if isinstance(v, dict)):
            reasons.append('failed_work')
    stores = [getattr(getattr(host, name, None), 'store', None) for name in ('wechat', 'external_channels')]
    try:
        if any(r['needs_review'] for r in avatar_log.rows(stores, 0)):
            reasons.append('avatar_review')
    except Exception:
        unknown.append('avatar')
    try:
        import subprocess
        import sys
        if sys.platform != 'linux':
            unknown.append('failed_units')
        else:
            r = subprocess.run(['systemctl', '--user', '--failed', '--no-legend', '--plain', '--no-pager'],
                               capture_output=True, timeout=2, text=True)
            if r.returncode:
                unknown.append('failed_units')
            elif r.stdout.strip():
                reasons.append('failed_units')
    except (OSError, subprocess.TimeoutExpired):
        unknown.append('failed_units')
    try:
        peer = getattr(host, 'peers', None)
        if peer and peer.store.on:
            from .relay_guards import requests
            if peer.store.pending_in(now):
                reasons.append('friends_pending')
            rows = requests(peer.store)['requests']
            if any(r['state'] == 'pending' or
                   (r['state'] in ('blocked', 'released', 'expired') and now - (r.get('updated') or r['created']) < 3600)
                   for r in rows if r['direction'] == 'in'):
                reasons.append('friend_requests')
    except Exception:
        unknown.append('friends')
    from . import quota, auth_rescue
    try:
        if quota.reminders_on(getattr(host, 'preferences', {})):
            rep = quota.report(root, now, meter_state=getattr(host, 'meter_state', None))
            kind = getattr(getattr(host, 'agent', None), 'kind', None)
            rep = {**rep, 'harnesses': [a for a in rep['harnesses'] if a['harness'] == kind]}
            if quota.due(rep, root, mark=False):
                reasons.append('quota_reminder')
    except Exception:
        unknown.append('quota')
    try:
        if auth_rescue.heartbeat_line(host.st, 'en'):
            reasons.append('login_expiry')
    except Exception:
        unknown.append('login')
    from . import goal
    try:
        if goal.heartbeat_line(host.st, 'en'):
            reasons.append('active_goal')
    except Exception:
        unknown.append('goal')
    return reasons, unknown


class Usage:
    def __init__(self, host):
        self.host = host
        self.path = host.st.root / 'heartbeat-usage.json'
        self.data = dict(input_tokens=0, output_tokens=0, total_tokens=0, turns=0, measured_turns=0)
        with contextlib.suppress(OSError, ValueError, TypeError):
            from .heartbeat import _read_regular
            d = json.loads(_read_regular(self.path, 16384) or '{}')
            if not isinstance(d, dict):
                raise ValueError('usage')
            self.data.update({k:v for k,v in d.items() if k in self.data and type(v) is int and v >= 0})
        self.active, self.samples, self.task, self.dirty = False, {}, None, False
        self.observed = {}

    def view(self):
        known = self.data['measured_turns'] > 0
        return {**{k:self.data[k] if known else None for k in ('input_tokens','output_tokens','total_tokens')},
                'turns':self.data['turns'], 'measured_turns':self.data['measured_turns'],
                'available':known, 'complete':self.data['turns'] == self.data['measured_turns']}

    def start(self, send):
        self.active = bool(getattr(send, 'heartbeat_key', None))
        self.samples = {}
        self.codex_base = None

    def observe(self, key):
        self.observed[str(key)] = True
        if len(self.observed) > 2048:
            self.observed.pop(next(iter(self.observed)))

    def sample(self, key, inp, out):
        key = str(key)
        if self.active and not all(type(v) is int and v >= 0 for v in (inp, out)):
            return
        if key.startswith('msg:'):
            if key in self.observed and key not in self.samples:
                return
            self.observe(key)
        if self.active and all(type(v) is int and v >= 0 for v in (inp, out)):
            self.samples[str(key)] = (inp, out)

    def end(self):
        if not self.active:
            return
        self.active = False
        self.data['turns'] += 1
        if self.samples:
            self.data['measured_turns'] += 1
            a = sum(v[0] for v in self.samples.values())
            b = sum(v[1] for v in self.samples.values())
            self.data['input_tokens'] += a
            self.data['output_tokens'] += b
            self.data['total_tokens'] += a+b
        self.dirty = True
        import asyncio
        with contextlib.suppress(RuntimeError):
            if self.task is None or self.task.done():
                self.task = asyncio.get_running_loop().create_task(self.save())
        self.host._post(self.host._send_p33, lambda s: self.host._meter_msg())

    async def save(self):
        import asyncio
        from .state import _write_private
        while self.dirty:
            self.dirty = False
            raw = json.dumps(self.data).encode()
            try:
                await asyncio.to_thread(_write_private, self.path, raw)
            except OSError:
                self.host.st.log('heartbeat_usage_save_failed')


def sample(agent, key, inp, out):
    h = getattr(agent, 'host', None)
    if h is not None and getattr(h, 'agent', None) is agent:
        hb = getattr(h, 'heartbeat', None)
        if hb is not None:
            hb.usage.sample(key, inp, out)
