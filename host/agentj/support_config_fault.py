"""Host observations of the installed heartbeat setting. No event payload comes from the Agent."""
import io
import os
import stat

from . import preferences, privacy, support, support_incident

SETTING = 'heartbeat.interval_minutes'
ISSUE = 'product-fault config invalid-config at:' + SETTING


def _config_text():
    """Read only the instance's regular private preferences file. Keep all values local."""
    path = preferences.path()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        return None
    try:
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
            return None
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o022
                    or not os.path.samestat(info, path.stat()) or info.st_size > preferences.MAX_BYTES):
                return None
            raw = stream.read(preferences.MAX_BYTES + 1).decode()
        return raw
    except (OSError, ValueError, RecursionError):
        return None


def file_state():
    raw = _config_text()
    if raw is None:
        return None
    try:
        doc = preferences.decode(raw)
        if (not isinstance(doc, dict) or not isinstance(doc.get('support', {}), dict)
                or preferences.get(doc, 'support.auto_report', True) is not True):
            return None
        try:
            effective = preferences.validate(doc, raw)
        except preferences.ConfigError as exc:
            return 'fault' if exc.key == SETTING else None
        return 'default' if preferences.get(effective, SETTING) == preferences.SCHEMA[SETTING]['default'] else None
    except (OSError, ValueError, RecursionError):
        return None


def observed(host):
    if (host.stopping.is_set() or host.stopped() or
            preferences.get(host.preferences, 'support.auto_report', True) is not True):
        return None
    state = file_state()
    problem = host.config_problem or {}
    if state == 'fault' and problem.get('key') == SETTING:
        return 'fault'
    if state == 'default' and not problem and preferences.get(host.preferences, SETTING) == preferences.SCHEMA[SETTING]['default']:
        return 'default'
    return None


def verified_draft(st, phase, body):
    if phase not in ('fault', 'default'):
        raise support.SupportError('Unknown host diagnostic.')
    expected = support_incident.message(st, 'config', 'invalid-config', SETTING,
                                        'config-default' if phase == 'default' else None)
    if body != expected:
        raise support.SupportError('Host diagnostic body differs from current observation.')
    return support.prepare(expected, None)


def deliver(host, phase):
    st = host.st
    recipient = support_incident.account(st)
    if not recipient or phase not in ('fault', 'default') or observed(host) != phase:
        return None
    # A healthy installation has no incident to follow up. Unrelated native reports cannot supply this predecessor.
    if phase == 'default' and not support_incident.open_issue(st, SETTING, allow_pending=True):
        return None
    channel = support.channel_for(st)
    if not channel.signed:
        return None
    from . import support_fault_mail
    fallback = support_fault_mail.configured(st) if phase == 'fault' else None

    class ObserverChannel:
        signed = True

        def __init__(self):
            self.st = st
            self.ticket = None

        def fault_receipt(self, nonce):
            if support_incident.account(st) != recipient or observed(host) != phase:
                return None
            receipt = fallback.cached_ticket(host, nonce) if fallback else None
            if not receipt:
                receipt = channel.fault_receipt(nonce)
            if receipt and support_incident.account(st) == recipient and observed(host) == phase:
                return receipt
            return None

        def reconciled_fault(self, thread):
            self.ticket = thread

        def send(self, fields):
            if support_incident.account(st) != recipient or observed(host) != phase:
                raise support_incident.NotDispatched()
            native_send = getattr(channel, 'send', None)
            reason = None
            if callable(native_send):
                status, response = native_send(fields)
                if fallback and status in (502, 503, 504):
                    reason = 'native_service_unavailable'
            elif phase == 'fault':
                reason = 'native_adapter_unavailable'
            else:
                raise support_incident.NotDispatched()
            if reason:
                from . import rescue_report
                nonce = fields.get('nonce')
                if reason == 'native_service_unavailable' and (receipt := self.fault_receipt(nonce)):
                    status, response = 201, receipt
                else:
                    status, response = rescue_report.report_fixed_fault(
                        host, recipient=recipient, nonce=nonce, native_failure=reason)
                if status == 201:
                    support.remember(st, response['thread'], 'bug', response['seq'])
                elif fallback and status == 0 and response.get('error') == 'rescue_delivery_unconfirmed':
                    outcome = fallback.send_fault(host, nonce)
                    response = {'error': 'fault_mail_' + outcome}
            tid = response.get('thread') if isinstance(response, dict) else None
            if (200 <= status < 300 and isinstance(tid, str) and support._THREAD.fullmatch(tid)
                    and fields.get('thread') in (None, tid)):
                self.ticket = tid
            return status, response

    guarded = ObserverChannel()
    def send(body, thread, receipt_channel):
        # This private watcher constructs its own draft; no CLI text, path, peer or override is accepted.
        if support_incident.account(st) != recipient or observed(host) != phase:
            return None
        draft, hits = verified_draft(st, phase, body)
        verdict = privacy.layer2(draft, gate='feedback', _host=True)
        if verdict.status not in ('ok', 'unavailable', 'blocked'):
            raise support.SupportError('Unrecognized privacy decision.')
        return support._send_reviewed('bug', draft, hits, verdict, receipt_channel, io.StringIO(), thread=thread)

    command = ISSUE + (' config-default' if phase == 'default' else '')
    support_incident.submit(command, guarded, send, io.StringIO(), observed=True)
    return guarded.ticket


def reconcile_pending(host):
    phase = observed(host)
    if phase and support_incident.pending_receipt(host.st, SETTING, phase):
        return deliver(host, phase)
    return None


def repair_default(host, key, value):
    """The existing paired preference write may repair only this rejected field of the live configuration."""
    if key != SETTING or type(value) is not int or value != preferences.SCHEMA[SETTING]['default']:
        return None
    raw = _config_text()
    if raw is None:
        return None
    try:
        preferences.parse(raw)
    except preferences.ConfigError as exc:
        if exc.key != key:
            return None
    else:
        return None
    fixed = preferences.edit(raw, key, value)
    effective = preferences.validate(preferences.parse(fixed))
    # Applying the repaired file must never activate any other outstanding edit.
    old = dict(preferences.flatten(host.preferences))
    new = dict(preferences.flatten(effective))
    old.pop(key, None)
    new.pop(key, None)
    return fixed if new == old else None
