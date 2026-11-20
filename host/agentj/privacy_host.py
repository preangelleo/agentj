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
_BROKER_SEND = contextvars.ContextVar('host_outbound_send_source', default=None)


@contextlib.contextmanager
def broker_scope(host, peer_pid):
    """Preserve the kernel peer across one Host-owned diagnostics execution.

    This is transport adaptation only: handle still proves the original peer
    against the live owner task before and after the fixed Jev check.
    """
    loop=asyncio.get_running_loop()
    state,agent=host.st,getattr(host,'agent',None)
    origin=_origin(host,peer_pid,'feedback')
    active=True
    def live():
        return (active and host.st is state and getattr(host,'agent',None) is agent
                and _origin(host,peer_pid,'feedback') == origin)
    def refusal():
        return _refusal(host, origin) or (None if live() else 'session_changed')
    async def review(draft,gate):
        if gate != 'feedback':return failure('bad_request')
        if why := refusal():return failure(why)
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
    send_token=_BROKER_SEND.set(refusal)
    try:yield refusal
    finally:
        active=False
        _BROKER_SEND.reset(send_token)
        _BROKER_REVIEW.reset(token)



def _origin(host, peer_pid, gate=None):
    """Return only the live, request-bound caller proof for outbound feedback.

    Do not infer owner identity from process ancestry: background/system turns share
    the host process tree but are not an interactive owner request.
    """
    from . import approval_material
    if peer_pid is None:
        return None
    origin = approval_material.verified_caller_origin(host, peer_pid)
    if gate != 'feedback':
        return origin
    if origin is None:
        return approval_material.verified_phone_feedback(host, peer_pid)
    send = getattr(getattr(host, 'agent', None), 'cur_send', None)
    if (origin.get('channel') == 'phone' and origin.get('message_id') == getattr(send, 'turn', None)
            and not approval_material.feedback_pairing_live(host, send)):
        return None
    return origin


def _refusal(host, origin):
    if origin is None:
        return 'peer_unverified'
    if host.stopped():
        return 'stopped'
    if not origin.get('verified') or origin.get('suspicious') or origin.get('material_unknown'):
        return 'source_denied'
    return None


def send_refusal():
    check = _BROKER_SEND.get()
    return check() if check is not None else None


def review_draft(draft, gate):
    from . import privacy, report_privacy
    if gate == 'feedback':
        draft, _, residual = report_privacy.prepare(draft)
        if residual:
            return privacy.Verdict('blocked', kind='credential', reason='privacy-check/residual_credential')
    return privacy.layer2(draft, gate=gate, _host=True)


SUPPORT_FEEDBACK_RETRY_KIND = 'support_feedback_retry'


def support_feedback_card(gate, why):
    """Return a payload-free formal card for a Host-owned feedback failure."""
    return {'kind': SUPPORT_FEEDBACK_RETRY_KIND} if gate == 'feedback' and isinstance(why, str) else None


def notify_failure(host, gate, why):
    """Project native feedback failures as one safe system card.

    This projection deliberately contains neither the draft nor a command, and
    it only tells the phone that the operation failed; it never retries it.
    """
    if not isinstance(why, str):
        why = 'host_unavailable'
    seen = host.__dict__.setdefault('_privacy_receipts', set())
    key = (None, gate if gate in ('feedback', 'plaza') else 'invalid', why)
    if key in seen:
        return
    if len(seen) >= 128:
        seen.clear()
    seen.add(key)
    card = support_feedback_card(gate, why)
    host.agent_notice('隐私外发检查未通过（' + why + '），请保留草稿，恢复后再试。',
                      **({'card': card} if card else {}))


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
    gate = req.get('gate') if isinstance(req, dict) else None
    if why is None:
        if gate == 'feedback':
            await approval_material.observe_phone_delivery(host, peer_pid)
        origin = _origin(host, peer_pid, gate)
        why = _refusal(host, origin)
    result = None
    if why is None:
        try:
            # Model failures are optional; source/session proofs never are.
            v = await asyncio.to_thread(review_draft, req['draft'], req['gate'])
        except Exception:
            v = privacy.Verdict('unavailable', reason='privacy-check/backend_unavailable')
        try:
            same_authority = (not host.stopped() and host.agent is agent and session_key(agent) == owner
                              and _origin(host, peer_pid, gate) == origin)
        except Exception:
            same_authority = False
        if not same_authority:
            why = 'session_changed'
        elif v.status not in ('ok', 'blocked', 'unavailable') or v.status == 'unavailable':
            why = 'backend_unavailable'
        else:
            # A content verdict is already a complete policy result. Preserve
            # its kind/reason so the existing blocked/owner-confirmation
            # contract remains in force; only infrastructure failures retry.
            result = asdict(v)
    if why:
        result = failure(why)
    # Content blocks are not host outages. Do not emit retry-card or temporary
    # failure semantics for a deliberate privacy decision.
    if result['status'] == 'blocked':
        return result
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
                notify_failure(host, gate, code)
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
