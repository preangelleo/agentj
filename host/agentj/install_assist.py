"""Host installation namespace, separate from official TASKS/PTy assistance.

No Cloud qualification provider exists in this release. No caller-supplied role,
uid, device name or JSON assertion can activate an installer. Lease is only a
lifecycle primitive, never an authorization token. Ordinary paired owners retain
existing controls; this module grants no general tool or device-management access.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import time

from . import controls, setup_ops, wire

ASSIST_TTL = 3 * 3600
CLOSE_DELAY = 60
# Narrower than the historical P223 "everything except four floors" policy.
INSTALLER_OPS = frozenset(('sys.check', 'ai.plan', 'ai.install', 'ai.probe', 'browser.setup', 'sys.repair_hint'))
ITEM_OPS = {
    'ai': frozenset(('sys.check',)) | frozenset(k for k in setup_ops.OPS if k.startswith('ai.')),
    'harness': frozenset(('sys.check',)) | frozenset(k for k in setup_ops.OPS if k.startswith('ai.')),
    'browser': frozenset(('browser.setup',)),
    'desktop': frozenset(('sys.check', 'sys.repair_hint')),
}
TIMEOUT = {'ai.install': 600, 'ai.login': 120, 'ai.key': 600, 'ai.probe': 60,
           'ai.connect': 60, 'browser.setup': 120, 'sys.repair_hint': 30,
           'sys.check': 10, 'ai.plan': 10}


class Unavailable(Exception):
    pass


@dataclass
class Lease:
    """Volatile lifecycle record. Never inferred from devices.json or persisted."""
    device: str
    owner: str
    started: float
    reason: str | None = None
    close_at: float | None = None

    def live(self, now: float) -> bool:
        return (self.reason is None and self.started <= now < self.started + ASSIST_TTL
                and (self.close_at is None or now < self.close_at))

    def end(self, reason: str) -> None:
        self.reason = reason if reason in ('owner_kick', 'cancel', 'disconnected', 'stopped', 'expired', 'handover') else 'cancel'

    def handover(self, now: float) -> None:
        self.close_at = min(self.started + ASSIST_TTL, now + CLOSE_DELAY)


class AssistantAuthority:
    """Fail closed until an independently verified Cloud binding contract exists.

    Deliberately no enable flag, environment override or injectable allow callback.
    A future implementation must verify Cloud eligibility AND a paired owner's
    signature binding helper key, host/seat, capability list, TTL and nonce.
    """
    def status(self) -> dict:
        return {'available': False, 'why': 'cloud_qualification_unavailable',
                'next': 'Use the existing local owner checklist; temporary helper pairing is unavailable.'}

    def activate(self, evidence) -> None:
        raise Unavailable('cloud_qualification_unavailable')


class Runner:
    """Signed fixed-schema owner dispatch. No shell/URL/path/secret parameters.

    Native operations reuse fixed Host installer/card/login/browser paths.
    Cloud helper authority remains unavailable. Model probes are not initiated.
    Tasks are cancellable, bounded and continuously recheck pairing.
    """
    def __init__(self, host):
        self.host = host
        self.assist = AssistantAuthority()
        self.events = []
        self.active = None
        from .setup_runtime import Runtime
        self.runtime = Runtime(host=host)

    def live(self, s) -> bool:
        return (s.state == 'ready' and self.host.sessions.get(s.cid) is s
                and self.host.st.is_allowed(s.pub) and bool(self.host.st.sign_key(s.device)))

    def _audit(self, s, op, result):
        # No params, model identifiers, exception messages, device names or keys.
        allowed_results = set(setup_ops.ERRORS) | {'started', 'done', 'system_check', 'download',
                          'verified_download', 'package_install', 'owner_secret_card', 'owner_login',
                          'ai_probe', 'ai_connect', 'service_apply', 'browser_setup'}
        result = result if isinstance(result, str) and result in allowed_results else 'error'
        rec = {'event': 'setup_run', 'op': op if isinstance(op, str) and op in setup_ops.OPS else '?',
               'result': result, 'device_hash': hashlib.sha256(s.device.encode()).hexdigest()[:16],
               'ts': int(time.time())}
        self.events.append(rec)
        self.events[:] = self.events[-200:]
        # Use existing private append facility, and fail closed on log failures.
        if not hasattr(self.host.st, 'root'):
            raise OSError('audit_unavailable')
        self.host.st.append_private(self.host.st.root / 'install_assist.log', setup_ops.canonical(rec))

    def _current_revision(self):
        from . import first_run
        journal = first_run.read(self.host.st)
        if not journal or journal.get('recovered'):
            return None
        return journal.get('revision')

    async def _operation(self, s, op, params):
        if op == 'ai.plan':
            # No network or account probes: unknown evidence is not a recommendation
            # to buy a plan. The owner can run the existing checklist locally.
            return {'route': 'owner', 'observed': False,
                    'next': 'agentj setup checklist --resume', 'steps': []}
        if op == 'sys.repair_hint':
            return {'issue': params['issue'], 'next': 'agentj doctor --read-only'}
        from .setup_runtime import RuntimeErrorCode
        def guard():
            if self.host.stopped():
                raise RuntimeErrorCode('stopped')
            if not self.live(s):
                raise RuntimeErrorCode('no_device')
            if self._current_revision() != self.expected_revision:
                raise RuntimeErrorCode('changed')
        try:
            from . import provenance
            # Only the verified paired phone request supplies owner provenance.
            # The callback is rechecked by elevate when the card is requested.
            with provenance.paired_request(lambda: self.live(s) and bool(
                    getattr(self.host, '_paired_live', lambda _: False)(s))):
                return await self.runtime.execute(op, params, s.device, guard,
                                                  lambda stage: self._audit(s, op, stage))
        except RuntimeErrorCode as e:
            raise setup_ops.OpError(e.code) from None

    async def run(self, s, obj):
        op = obj.get('op') if isinstance(obj, dict) else None
        def refuse(why):
            why = why if isinstance(why, str) and why in setup_ops.ERRORS else 'error'
            try:
                self._audit(s, op, why)
            except OSError:
                why = 'io'
            from .startup_help import category, domestic_note
            failure = category(why)
            return {'ok': False, 'why': why, 'explain': setup_ops.explain(why),
                    'failure_category': failure,
                    **({'help': domestic_note(getattr(self.host, 'lang', 'zh'))}
                       if failure in ('network', 'region') else {})}
        if not isinstance(obj, dict):
            return refuse('shape')
        if not self.live(s):
            return refuse('no_device')
        if self.host.stopped():
            return refuse('stopped')
        if set(obj) - {'t','r','item','op','params','rev','n','ts','sig'}:
            return refuse('unknown_field')
        try:
            params = setup_ops.validate(op, obj.get('params'))
        except setup_ops.OpError as e:
            return refuse(e.code)
        item, rev = obj.get('item'), obj.get('rev')
        if not isinstance(item, str) or op not in ITEM_OPS.get(item, ()):
            return refuse('not_for_item')
        if type(rev) is not int or rev < 0:
            return refuse('shape')
        target = {'item': item, 'op': op, 'params': params, 'rev': rev}
        why = controls.check(self.host.st, self.host.nonces, self.host.channel, s.device, obj, 'setup_run', target)
        if why:
            return refuse(why)
        # Host journal revision is authoritative, never the request's own claim.
        try:
            current = self._current_revision()
        except (OSError, ValueError):
            return refuse('io')
        if rev != current:
            return refuse('changed')
        if self.active is not None:
            return refuse('busy')
        try:
            self._audit(s, op, 'started')
        except OSError:
            return {'ok': False, 'why': 'io', 'explain': setup_ops.explain('io')}
        self.expected_revision = rev
        task = asyncio.create_task(self._operation(s, op, params))
        self.active = (s.device, task)
        deadline = time.monotonic() + TIMEOUT[op]
        result = None
        try:
            # Reserve the active slot before yielding to transport; a second
            # signed request during the progress ACK must still see busy.
            if op == 'ai.install' and hasattr(self.host, 'send_app'):
                await self.host.send_app(s, {'t':'ctl_pending', 'r':obj.get('r'),
                                             'action':'setup_run', 'timeout_ms':620000})
            while not task.done():
                await asyncio.wait({task}, timeout=min(.05, max(0, deadline - time.monotonic())))
                if self.host.stopped():
                    return refuse('stopped')
                if not self.live(s):
                    return refuse('no_device')
                if self._current_revision() != rev:
                    return refuse('changed')
                if time.monotonic() >= deadline:
                    return refuse('timeout')
            result = task.result()
            if self.host.stopped() or not self.live(s):
                return refuse('stopped' if self.host.stopped() else 'no_device')
            self._audit(s, op, 'done')
            return {'ok': True, 'result': result}
        except setup_ops.OpError as e:
            return refuse(e.code)
        except Unavailable:
            return refuse('unavailable')
        except asyncio.CancelledError:
            return refuse('denied')
        except Exception:
            return refuse('error')
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.active = None

    async def on_phone(self, s, obj):
        if not isinstance(obj, dict) or not wire.is_rid(obj.get('r')):
            return
        if obj.get('t') == 'setup_cancel':
            why = None if self.live(s) else 'no_device'
            if why is None:
                why = controls.check(self.host.st, self.host.nonces, self.host.channel, s.device,
                                     obj, 'setup_cancel', {})
            if why is None:
                try:
                    self._audit(s, 'cancel', 'denied')
                except OSError:
                    why = 'io'
            if why is None and self.active is not None:
                self.active[1].cancel()
            return await self.host._ctl_res(s, obj.get('r'), 'setup_cancel', why is None,
                                            **({'why': why} if why else {}))
        result = await self.run(s, obj)
        await self.host._ctl_res(s, obj.get('r'), 'setup_run', result.pop('ok'), **result)
