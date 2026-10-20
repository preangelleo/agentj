"""P410 local request lifecycle. Metadata only; no retries or automatic execution.
Envelope is authenticated by PeerNet, not a claim to owner authority. Legacy messages
start a chain at the immediate sender. Replies keep the envelope but are never tasks.
"""
from __future__ import annotations

import re
import secrets

HEX = re.compile(r'[0-9a-f]{32}')
FINAL = {'done', 'released', 'expired', 'cancelled', 'loop', 'hops', 'rate', 'shape', 'blocked'}
REASONS = {
    'loop': ('这条转交绕回了原来的电脑，已停止。', 'This request came back to its starting computer and was stopped.'),
    'hops': ('这条请求已转交超过 4 次，已停止。', 'This request was handed on more than 4 times and was stopped.'),
    'rate': ('这位好友发得太快，请等一分钟再试。', 'This friend is sending too quickly. Try again in a minute.'),
    'shape': ('转交信息不完整，已停止，请重新发起。', 'The handover details are incomplete. Start a new request.'),
    'released': ('对方认领后一直没回复，已释放请求；可以重新派或取消。已开始的工作可能仍在运行，请先核实，避免重复。', 'No reply after the request was claimed. It is released; you can reassign or cancel. Work may still be running: check first to avoid duplicates.'),
    'expired': ('请求一直没人认领，已过期；可以重新派或取消。', 'Nobody claimed this request before it expired. You can reassign or cancel.'),
    'cancelled': ('请求已取消，没有自动重派。', 'The request was cancelled. It was not automatically reassigned.'),
}


def message(reason, lang):
    return REASONS[reason][lang != 'zh']


class RelayGuards:
    def __init__(self, svc):
        self.svc, self.store = svc, svc.store

    def envelope(self, value):
        from .peer import parse_id
        if not isinstance(value, dict) or set(value) != {'chain', 'origin', 'hops'}:
            return None
        chain, origin, hops = value.get('chain'), value.get('origin'), value.get('hops')
        if (not isinstance(chain, str) or not HEX.fullmatch(chain) or not isinstance(origin, str)
                or parse_id(origin) != origin or type(hops) is not int or not 1 <= hops <= 255):
            return None
        return dict(value)

    def outgoing(self, incoming=None):
        src = self.envelope(incoming)
        return {**src, 'hops': src['hops'] + 1} if src else {
            'chain': secrets.token_hex(16), 'origin': self.svc.keys.id, 'hops': 1}

    def rows(self):
        return self.store._read('relay-requests.json', {})

    @staticmethod
    def key(fid, mid, direction='in'):
        return f'{direction}:{fid}:{mid}'

    def row(self, fid, mid, direction='in'):
        return self.rows().get(self.key(fid, mid, direction))

    def remember(self, fid, m, direction='in'):
        key = self.key(fid, m['mid'], direction)
        with self.store._edit('relay-requests.json', {}) as d:
            if key not in d:
                # Keep tombstones for transport dedup. Bounded to the newest 8192 requests.
                while len(d) >= 8192:
                    terminal = next((k for k, r in d.items() if r['state'] in FINAL), None)
                    if terminal is None:
                        return False
                    del d[terminal]
                d[key] = {'friend': fid, 'mid': m['mid'], 'thread': m['thread'], 'relay': m['relay'],
                          'created': self.svc.clock(), 'state': 'pending', 'direction': direction}
        return True

    def receipt(self, fid, mid, state):
        self.svc._send(fid, {'t': 'relay_state', 'mid': mid, 'state': state})
        # Stop the transport retry too. The richer state is an app receipt.
        self.svc._send(fid, {'t': 'pack', 'mid': mid, 's': 'got'})

    def state(self, fid, mid, state, direction='in'):
        changed = False
        with self.store._edit('relay-requests.json', {}) as d:
            r = d.get(self.key(fid, mid, direction))
            if r and r['state'] not in FINAL and r['state'] != state and not (r['state'] == 'claimed' and state == 'pending'):
                r['state'] = state
                if state == 'claimed':
                    r['claimed'] = self.svc.clock()
                changed = True
        if changed:
            if direction == 'out' and state in FINAL:
                cancel = getattr(self.svc.net, 'cancel_message', None)
                if callable(cancel):
                    cancel(fid, mid)
            if direction == 'in' and state in ('released', 'expired', 'cancelled'):
                for ask in list(self.svc.asks.values()):
                    if ask.data.get('friend') == fid and ask.data.get('mid') == mid:
                        self.svc._decided(ask, 'deny', state)
            self.store.hist_set_status(fid, mid, state)
            if state in REASONS:
                self.svc.notice(message(state, self.svc.lang))
            self.svc.changed(fid)
        return changed

    def accept(self, fid, m):
        old = self.row(fid, m['mid'])
        if old:
            self.receipt(fid, m['mid'], old['state'])
            return False
        env = self.envelope(m.get('relay')) if 'relay' in m else self.outgoing()
        if 'relay' not in m:  # old sender: use its pinned ID, not our own
            env['origin'] = fid
        m['relay'] = env
        reason = 'shape' if env is None else 'hops' if env['hops'] > 4 else None
        if env and not (m.get('irt') or m.get('end')):
            # An ID generated here must never return as a new task, even if origin is changed.
            known = any(r['relay'] and r['relay']['chain'] == env['chain'] and
                        r['direction'] == 'out' and r['relay']['origin'] == self.svc.keys.id for r in self.rows().values())
            if env['origin'] == self.svc.keys.id or known:
                reason = 'loop'
        with self.store._edit('relay-rate.json', {}) as d:
            times = [t for t in d.get(fid, []) if t > self.svc.clock() - 60]
            if len(times) >= self.store.settings()['relay_per_minute']:
                reason = reason or 'rate'
            else:
                times.append(self.svc.clock())
            d[fid] = times
        if reason:
            # Invalid metadata isn't persisted; a fixed reason is safe for both owners.
            self.svc.notice(message(reason, self.svc.lang))
            self.receipt(fid, m['mid'], reason)
            self.svc.log('peer_relay', result=reason)
            return False
        if m.get("irt"):
            original = self.row(fid, m["irt"], "out")
            if original and original["relay"] == env:
                self.state(fid, m["irt"], "done", "out")
        if not (m.get('irt') or m.get('end')):
            if not self.remember(fid, m):
                self.receipt(fid, m['mid'], 'rate')
                return False
        return True

    def claim(self, fid, m):
        self.sweep()
        if m.get('irt') or m.get('end'):
            return True
        if not self.row(fid, m['mid']):
            # Direct local calls (including old question cards) use the same checks.
            if not self.accept(fid, m):
                return False
        r = self.row(fid, m['mid'])
        if not r or r['state'] != 'pending':
            return False
        self.state(fid, m['mid'], 'claimed')
        self.receipt(fid, m['mid'], 'claimed')
        return True

    def complete(self, fid, mid):
        if self.state(fid, mid, 'done'):
            self.receipt(fid, mid, 'done')

    def receive(self, fid, obj):
        mid, state = obj.get('mid'), obj.get('state')
        if not isinstance(mid, str) or not isinstance(state, str) or state not in FINAL | {'claimed', 'pending'}:
            return
        if not self.row(fid, mid, 'out'):
            return
        self.state(fid, mid, state, 'out')

    def sweep(self):
        cfg, now = self.store.settings(), self.svc.clock()
        for r in self.rows().values():
            state = r['state']
            if state not in ('pending', 'claimed'):
                continue
            duration = cfg['relay_claim_seconds'] if state == 'claimed' else cfg['relay_pending_seconds']
            if now < r.get('claimed', r['created']) + duration:
                continue
            end = 'released' if state == 'claimed' else 'expired'
            if self.state(r['friend'], r['mid'], end, r['direction']) and r['direction'] == 'in':
                self.receipt(r['friend'], r['mid'], end)
            elif r['direction'] == 'out':
                self.svc._send(r['friend'], {'t': 'relay_cancel', 'mid': r['mid']})
        # Never submit, replay, or kill a running task here.
