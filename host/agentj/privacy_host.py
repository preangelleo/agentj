"""Fixed Host semantic proxy for the existing Jev outbound privacy check."""
from __future__ import annotations
import asyncio
import contextvars
import contextlib
from dataclasses import asdict
import json

MAX_DRAFT = 256 * 1024
# Only the authenticated Host broker installs this in its operation context.
# asyncio.to_thread copies it; no input field or socket request can select it.
_BROKER_REVIEW = contextvars.ContextVar('host_outbound_privacy_review', default=None)


@contextlib.contextmanager
def broker_scope(host, peer_pid):
    """Preserve the kernel peer across one Host-owned diagnostics execution.

    This is transport adaptation only: handle still proves the original peer
    against the live owner task before and after the fixed Jev check.
    """
    from . import approval_material
    loop=asyncio.get_running_loop()
    state,agent=host.st,getattr(host,'agent',None)
    origin=approval_material.verified_caller_origin(host,peer_pid) if peer_pid is not None else None
    active=True
    def live():
        return (active and host.st is state and getattr(host,'agent',None) is agent
                and approval_material.verified_caller_origin(host,peer_pid) == origin)
    async def review(draft,gate):
        if gate != 'feedback':return failure('bad_request')
        if origin is None:return failure('peer_unverified')
        if not live():
            return failure('session_changed')
        result=await handle(host,{'t':'privacy_check','gate':gate,'draft':draft},peer_pid=peer_pid)
        return result if live() else failure('session_changed')
    def request_bound(draft,gate):
        # The synchronous support operation runs in to_thread. Never block the
        # owning event loop on itself or make a second socket with Host's PID.
        try:
            if asyncio.get_running_loop() is loop:return failure('broker_thread_required')
        except RuntimeError:
            pass
        job=asyncio.run_coroutine_threadsafe(review(draft,gate),loop)
        try:return job.result(timeout=30)
        except Exception:
            job.cancel()
            return failure('host_unavailable')
    token=_BROKER_REVIEW.set(request_bound)
    try:yield
    finally:
        active=False
        _BROKER_REVIEW.reset(token)



def failure(why):
    return {'status': 'unavailable', 'reason': 'privacy-check/' + why,
            'probability': None, 'kind': None}


async def handle(host, req, *, peer_pid=None):
    from . import privacy, approval_material
    from .heartbeat import session_key
    why = None
    if (not isinstance(req, dict) or set(req) != {'t', 'gate', 'draft'} or req['t'] != 'privacy_check'
            or req['gate'] not in ('feedback', 'plaza')):
        why = 'bad_request'
    else:
        try:
            if len(json.dumps(req['draft'], ensure_ascii=False).encode()) > MAX_DRAFT:
                why = 'draft_limit'
        except (TypeError, ValueError):
            why = 'bad_request'
    agent = getattr(host, 'agent', None)
    owner = session_key(agent)
    origin = approval_material.verified_caller_origin(host, peer_pid) if peer_pid is not None else None
    if why is None:
        if origin is None:
            why = 'peer_unverified'
        elif host.stopped():
            why = 'stopped'
        elif not origin.get('verified') or origin.get('suspicious') or origin.get('material_unknown'):
            why = 'source_denied'
    result = None
    if why is None:
        try:
            # Model failures are optional; source/session proofs never are.
            v = await asyncio.to_thread(privacy.layer2, req['draft'], gate=req['gate'], _host=True)
        except Exception:
            v = privacy.Verdict('unavailable', reason='privacy-check/backend_unavailable')
        try:
            same_authority = (not host.stopped() and host.agent is agent and session_key(agent) == owner
                              and approval_material.verified_caller_origin(host, peer_pid) == origin)
        except Exception:
            same_authority = False
        if not same_authority:
            why = 'session_changed'
        elif v.status not in ('ok', 'blocked', 'unavailable') or v.status == 'unavailable':
            why = 'backend_unavailable'
        else:
            result = asdict(v)
    if why:
        result = failure(why)
    if result['status'] != 'ok':
        code = why or 'blocked'
        seen = host.__dict__.setdefault('_privacy_receipts', set())
        gate = req.get('gate') if isinstance(req, dict) else None
        key = (owner, gate if gate in ('feedback', 'plaza') else 'invalid', code)
        if key not in seen:
            if len(seen) >= 128:
                seen.clear()
            seen.add(key)
            host.st.log('privacy_check_optional' if privacy.optional_review(privacy.Verdict(**result))
                        else 'privacy_check_failed', reason=code, gate=key[1])
            if not privacy.optional_review(privacy.Verdict(**result)):
                host.agent_notice('隐私外发检查未通过（' + code + '），请保留草稿，恢复后再试。')
    return result


def request(draft, gate):
    from . import elevate, privacy
    from .state import State
    try:
        bound=_BROKER_REVIEW.get()
        obj = (bound(draft,gate) if bound is not None else
               elevate.client_request(State(), {'t': 'privacy_check', 'draft': draft, 'gate': gate}, timeout=30))
        if (not isinstance(obj, dict) or obj.get('status') not in ('ok', 'blocked', 'unavailable')
                or set(obj) - {'status', 'reason', 'probability', 'kind', 'model', 'criteria_version'}):
            obj = failure('host_unavailable')
        return privacy.Verdict(**obj)
    except (OSError, TypeError, ValueError):
        return privacy.Verdict(**failure('host_unavailable'))
