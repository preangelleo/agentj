"""Host-owned review adapter for independently sealed approval controllers (#541).

This never grants an operation. The original controller retains password, UV,
execution, delivery and timeout ownership; only safe display context is shared.
"""
from __future__ import annotations

import asyncio
import copy
import secrets
import time

from . import approval_review as review, wire


class AuthorityChanged(RuntimeError):
    pass


def _current_context(host):
    # Compare Host-held live origin, not the request worker's frozen contextvars.
    from . import provenance
    tokens=[(review.ORIGIN,review.ORIGIN.set(None)),
            (provenance._REQUEST,provenance._REQUEST.set(provenance._UNSET)),
            (provenance._TASK,provenance._TASK.set(None))]
    try:return review.snapshot(host),host._task_id()
    finally:
        for variable,token in reversed(tokens):variable.reset(token)


def _current_source(host):
    return _current_context(host)[0]


def _current_task(host):
    return _current_context(host)[1]


def _context_allows(adapter,host):
    if (host.stopped() or host.agent is not adapter.review_agent
            or str(host.hist.epoch)!=adapter.envelope['session']):return False
    current=review.digest(_current_source(host))
    if _current_task(host)==adapter.live_task_id and current==adapter.live_source_digest:return True
    # Only the exact Host outbox object can bridge a question continuation.
    send=getattr(host.agent,'cur_send',None)
    meta=getattr(send,'approval_review',None)
    eid=getattr(adapter,'review_host_event',None)
    item=host.__dict__.get('_approval_event_outbox',{}).get(eid)
    return bool(isinstance(meta,dict) and isinstance(item,dict)
                and meta.get('event_id')==eid and meta.get('id')==adapter.rid
                and meta.get('op')=='question' and item.get('op')=='question'
                and item.get('request_id')==adapter.rid and item.get('turn')==getattr(send,'turn',None)
                and item.get('target')==adapter.review_thread==review.thread_ref(host.agent)
                and item.get('status') in ('queued','acknowledged')
                and current==review.digest(item.get('origin'))==review.digest(adapter.review_origin))


class Adapter:
    def __init__(self, host, card, *, tool, action, fields, render, rotate, finish, refresh, visible=None):
        self.host, self.card = host, card
        self.rid, self.deadline = card['id'], card['deadline']
        self.tool, self.tool_input = tool, {'display_fields': list(fields)}
        self.input_sha = review.digest(self.tool_input)
        self.task = self.task_scope = None
        self.summary = '\n'.join(fields)
        self.reason, self.goal_what = action, ''
        self.cats = ['credentials']
        self.only = card.get('only')
        self.fut = card.get('fut') or asyncio.get_running_loop().create_future()
        self.render, self.rotate, self.finish = render, rotate, finish
        self.refresh = refresh
        self.visible = visible or (lambda session: True)
        self.events = {}
        self.terminal = False
        self.rebind('pending')
        host.__dict__.setdefault('_approval_special', {})[self.rid] = self

    def rebind(self, state):
        origin = copy.deepcopy(getattr(self, 'review_origin', None))
        review.bind(self.host, self, origin)
        if not hasattr(self,'live_source_digest'):
            self.live_source_digest=review.digest(_current_source(self.host))
            self.live_task_id=_current_task(self.host)
        self.envelope['special'] = {'tool': self.tool, 'fields_sha256': self.input_sha}
        self.digest = review.digest(self.envelope)
        self.review_state = self.card['review_state'] = state
        self.card['envelope_sha256'] = self.digest
        self.card['revision'] = self.review_revision
        self.refresh()

    def decorate(self, message):
        return {**message, 'approval_v': 2, 'envelope': copy.deepcopy(self.envelope),
                'envelope_sha256': self.digest, 'revision': self.review_revision,
                'review_state': self.review_state}

    def ended(self, result):
        self.terminal = True
        self.review_state = ('expired' if result in ('expired', 'timeout') else
                             'completed' if result in ('picked', 'saved', 'done') else
                             'unknown' if result == 'unknown' else
                             'denied' if result in ('declined', 'denied') else 'revoked')
        self.card['review_state'] = self.review_state
        if self.card.get('fut') is not self.fut and not self.fut.done():
            self.fut.set_result({'result': result})
        registry = self.host.__dict__.setdefault('_approval_special', {})
        ended = [rid for rid, adapter in registry.items() if adapter.terminal]
        for rid in ended[:-64]:
            registry.pop(rid, None)

    def denied(self):
        if getattr(self, 'deny_event', None):
            return
        self.deny_event = secrets.token_hex(16)
        review.submit_event(self.host, self, 'deny', self.deny_event)

    async def broadcast(self):
        await self.host._send_ready(lambda session: self.render(session) if self.visible(session) else None)

    async def on_phone(self, session, obj):
        op, eid = obj.get('op'), obj.get('event_id')
        result = {'t': 'approval_review_result', 'id': self.rid, 'op': op, 'ok': False}
        async def refuse(reason):
            await self.host.send_app(session, {**result, 'why': reason})
        if not self.visible(session):
            return await refuse('wrong_remote')
        key = self.host.st.sign_key(session.device)
        try:
            message = review.review_message(self.host.channel, session.device, self.rid, op,
                                            obj.get('revision'), eid, obj.get('envelope_sha256'))
            signature = wire.unb64u(obj.get('sig', ''))
        except (ValueError, TypeError):
            return await refuse('shape')
        if not key or not review.verify(key, signature, message):
            return await refuse('bad_signature')
        event_key = (session.device, eid)
        if event_key in self.events:
            return await self.host.send_app(session, self.events[event_key])
        if obj.get('revision') != self.review_revision or obj.get('envelope_sha256') != self.digest:
            return await refuse('stale_revision')
        if not _context_allows(self,self.host):
            return await refuse('authority_changed')
        if op == 'renew':
            if self.review_state != 'expired' or self.card.get('busy') or not self.terminal:
                return await refuse('not_safely_expired')
            if not getattr(self, 'renew_event', None):
                self.renew_event = eid
                review.submit_event(self.host, self, 'renew', eid)
            result.update(ok=True, state='restart_requested', event_id=self.renew_event,
                          why='new_request_required')
        elif self.terminal or self.card.get('busy') or time.monotonic() >= self.deadline:
            return await refuse('expired' if time.monotonic() >= self.deadline else 'gone')
        elif op == 'deny':
            self.review_device = session.device
            self.denied()
            self.finish('declined' if self.tool == 'SecretPickup' else 'denied')
            result.update(ok=True, event_id=eid, state='denied')
        elif op == 'question':
            if self.review_state != 'questioned':
                self.rotate()
                self.rebind('questioned')
                self.question_event = eid
                self.review_device = session.device
                owner_text = review.submit_event(self.host, self, 'question', eid, obj.get('biometric'), session.device)
                self.card['review_detached'] = True
                pause = self.card.get('review_pause')
                if pause is not None and not pause.done():
                    pause.set_result(self.rid)
                await self.host._send_ready(lambda target: {'t': 'approval_questioned', 'id': self.rid,
                    'event_id': eid, 'owner_text': owner_text, 'revision': self.review_revision,
                    'ask': self.render(target)} if self.visible(target) else None)
            result.update(ok=True, event_id=self.question_event, ask=self.render(session))
        elif op == 'reopen':
            self.rotate()
            self.rebind('pending')
            await self.broadcast()
            result.update(ok=True, event_id=eid, ask=self.render(session))
        else:
            return await refuse('shape')
        self.events[event_key] = result
        while len(self.events) > 128:
            self.events.pop(next(iter(self.events)))
        self.host.st.log('approval_review', id=self.rid, op=op, device=session.device,
                         revision=self.review_revision)
        await self.host.send_app(session, result)


def attach(host, card, **kwargs):
    adapter = Adapter(host, card, **kwargs)
    card['review_adapter'] = adapter
    return adapter


def decorate(card, message):
    adapter = card.get('review_adapter')
    return adapter.decorate(message) if adapter is not None else message


def pending(card):
    return card.get('review_state', 'pending') == 'pending'


def phone_origin(host, session, action, mode=None):
    """Called only after the native signed phone control/UV check; never from an Agent-supplied description."""
    from . import provenance
    live = getattr(host, '_paired_live', None)
    labels = {
        'terminal_login': ('解锁这台遥控器的网页终端，需要确认 Agent J 主机登录密码。',
                           'Unlock the web terminal on this remote using the Agent J host login password.'),
        'terminal_enable': ('启用这台遥控器的网页终端，需要确认 Agent J 主机登录密码。',
                            'Enable the web terminal on this remote using the Agent J host login password.'),
        'vault_share': ('让这台遥控器使用已保存的 Agent J 主机登录密码。',
                        'Let this remote use the saved Agent J host login password.'),
    }
    if action not in labels or not callable(live) or not live(session) or \
            mode is not None and mode not in ('line', 'pty', 'enable'):
        return {}
    text = labels[action][1 if getattr(host, 'lang', 'zh') == 'en' else 0]
    turn = host.hist_add({'k': 'phone', 'dev': session.device, 'name': '你', 'text': text}, '', 'done')
    origin = review.remember(host, turn, provenance.phone())
    origin['owner_action'] = {'action': action, 'device': session.device,
                              **({'mode': mode} if mode is not None else {})}
    return origin


def source_allows(card,host):
    adapter=card.get('review_adapter')
    if adapter is None:
        return True  # non-review legacy controller retains its own independent authority checks
    source=adapter.envelope['source']
    return (adapter.card is card and adapter.host is host and not adapter.terminal
            and host.__dict__.get('_approval_special',{}).get(adapter.rid) is adapter
            and source.get('verified') is True and source.get('suspicious') is not True
            and _context_allows(adapter,host))
