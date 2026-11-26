"""Report-only rescue for authorized fixed diagnostics on the installed Host."""
from __future__ import annotations

import re

from .rescue_kit import MARKER, os_kind


def report_fixed_fault(host, *, recipient, nonce, native_failure, lang=None):
    """Send the journaled Host-observed config fault after a definite native pre-send failure.

    The caller owns the incident journal and reconciles ambiguous transport outcomes by nonce.
    A successful return is the official message receipt. No advice or repair commands are read.
    """
    from . import __version__, cloud, privacy, privacy_host, seatcert, support, support_config_fault, support_incident

    if (native_failure not in ('native_adapter_unavailable', 'native_service_unavailable') or not isinstance(recipient, str) or not recipient
            or not isinstance(nonce, str) or not support._FAULT_RECEIPT.fullmatch(nonce)):
        return 0, {'error': 'rescue_not_authorized'}

    st = host.st

    def allowed():
        if (support_incident.account(st) != recipient or not support_incident.enabled(st, 'config')
                or support_config_fault.observed(host) != 'fault' or privacy_host.send_refusal()):
            return False
        incident = support_incident._observed_issue(st, support_config_fault.SETTING)
        return (incident.get('pending') is True and incident.get('nonce') == nonce
                and incident.get('thread') is None and incident.get('pending_thread') is None)

    try:
        if not allowed():
            return 0, {'error': 'rescue_not_authorized'}
        channel = support.channel_for(st)
        if not channel.signed:
            return 0, {'error': 'rescue_not_authorized'}
        platform_kind = os_kind()
        if platform_kind not in ('linux', 'macos', 'wsl', 'windows'):
            platform_kind = 'other'
        if not re.fullmatch(r'[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:(?:a|b|rc)[0-9]{1,5})?(?:\.dev[0-9]{1,6})?', __version__):
            return 0, {'error': 'rescue_version_unrecognized'}
        body = '\n'.join((MARKER + ' [report-only]', 'component: config', 'stage: validate',
                          'reason: invalid-heartbeat-interval', 'platform: ' + platform_kind,
                          'version: ' + __version__))
        draft, hits = support.prepare(body, None)
        if hits or draft != {'body': body}:
            return 0, {'error': 'rescue_redaction_failed'}
        verdict = privacy.layer2(draft, gate='feedback', _host=True)
        if verdict.status != 'ok' and not privacy.optional_review(verdict):
            return 0, {'error': 'rescue_privacy_refused'}
        if not allowed():
            return 0, {'error': 'rescue_not_authorized'}
        if seatcert.refusal(st, 'support'):
            return 0, {'error': 'rescue_seat_required'}
        fields = {'kind': 'bug', 'body': body, 'nonce': nonce}
        if lang in ('zh', 'en'):
            fields['lang'] = lang
    except support.SupportSeatError:
        return 0, {'error': 'rescue_seat_required'}
    except (support.SupportError, cloud.CloudError, OSError, ValueError):
        return 0, {'error': 'rescue_preflight_failed'}

    try:
        status, reply = channel.send(fields)
    except (support.SupportError, cloud.CloudError, OSError, ValueError):
        return 0, {'error': 'rescue_delivery_unconfirmed'}
    if status == 201:
        source = reply if isinstance(reply, dict) else {}
        ticket, message, seq = source.get('thread'), source.get('message'), source.get('seq')
        if (isinstance(ticket, str) and support._THREAD.fullmatch(ticket) and isinstance(message, str)
                and support._MESSAGE.fullmatch(message) and type(seq) is int and seq > 0):
            return status, {'thread': ticket, 'message': message, 'seq': seq}
        return 0, {'error': 'rescue_delivery_unconfirmed'}
    if status == 429:
        return status, {'error': 'rescue_rate_limited'}
    if type(status) is int and 400 <= status < 500 and status not in (408, 409):
        return status, {'error': 'rescue_refused'}
    return 0, {'error': 'rescue_delivery_unconfirmed'}
