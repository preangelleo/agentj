"""Structured product fault reports over the existing signed support channel."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import platform
import time

from . import __version__, cloud, preferences
from .state import _write_private

PREFIX = "product-fault "
DAY = 86400
# Public event names only. Never include log fields, paths, exception messages or user content.
EVENTS = {
    'config': ('config_migration_failed', 'config_history_cleanup_failed', 'pref_set'),
    'service': ('service_hold', 'host_diagnostics_failed'),
    'relay': ('relay_down', 'relay_full', 'relay_up'),
    'update': ('update_available', 'update_check_switch', 'host_diagnostics_failed'),
    'agent': ('agent_mode_reset', 'host_diagnostics_failed'),
    'other': ('host_diagnostics_failed',),
}
SYMPTOMS = ('invalid-config', 'unavailable', 'timeout', 'stalled', 'unexpected-result', 'startup-failed')
REPAIRS = {
    'config-default': 'Invalid setting in config.json5: restored the packaged default and verified the affected operation.',
    'config-restore': 'Agent J configuration: restored the last working revision and verified it.',
    'service-restart': 'Agent J service: restarted the existing service and verified its response.',
    'update-install': 'Agent J distribution: installed the verified update and repeated the failed operation.',
    'source-patch': 'Agent J source: applied the owner-approved local change and verified the affected operation.',
    'reconnect': 'Agent J connection: reconnected and verified the affected operation.',
}


CAUSES = ('invalid-value', 'stale-state', 'missing-check', 'timeout', 'version-mismatch', 'unsupported-format')
CHANGES = ('restore-default', 'reset-state', 'validate-input', 'retry-transient-error', 'adjust-timeout', 'update-format')


def _events(st, component):
    try:
        with st.log_path.open('rb') as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - 32768))
            lines = f.read(32768).splitlines()
    except OSError:
        return []
    result = []
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            continue
        if isinstance(row, dict) and row.get('ev') in EVENTS[component]:
            result.append(row['ev'])
    return result[-5:]


def enabled(st, component=None):
    # Read the owner's current setting even while another component is broken.
    # An unreadable preference file cannot establish permission to send.
    try:
        try:
            with preferences.path().open('rb') as stream:
                raw = stream.read(preferences.MAX_BYTES + 1).decode()
        except FileNotFoundError:
            raw = '{}'
        doc = preferences.decode(raw)
        if component == 'update':
            from . import upgrade_diag
            if os.environ.get(upgrade_diag.REPORT_ENV, '').lower() == 'off' or preferences.get(doc, 'updates.report_failures', True) is not True:
                return False
        return preferences.get(doc, 'support.auto_report', True) is True and isinstance(doc, dict) and isinstance(doc.get('support', {}), dict)
    except (ValueError, OSError, TypeError):
        return False


def message(st, component, symptom, target, repair=None, cause=None, change=None):
    events = _events(st, component)
    system = platform.system()
    body = '\n'.join(['Agent J product fault' if not repair else 'Agent J repair follow-up',
                      'Component: ' + component, 'Symptom: ' + symptom, 'Agent J: ' + __version__,
                      'System: ' + (system if system in ('Linux','Darwin','Windows') else 'other'),
                      'Related host.log events: ' + (', '.join(events) or 'none recorded')])
    if target:
        body += '\nProduct setting or file: ' + target
    if repair:
        body += '\nVerified repair: ' + REPAIRS[repair]
        if cause:
            body += '\nCause: ' + cause + '\nChange: ' + change
    return body


class NotDispatched(Exception):
    """A local guard denied the request before any network operation."""


def account(st):
    linked = cloud.read_cloud(st)
    if not linked:
        return None
    identity = [linked['host_id'], linked['tenant']['slug'], linked['linked_at'],
                cloud.api_url(st, linked), st.signing_pub().hex()]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def open_issue(st, setting):
    scope = 'host-preferences/' + (account(st) or '') + '/'
    try:
        rows = json.loads((st.root / 'support-product-faults.json').read_text())
        row = rows[scope + 'config/invalid-config/' + setting]
        return (isinstance(row, dict) and isinstance(row.get('thread'), str)
                and row.get('at', 0) + DAY > time.time() and not row.get('repaired') and not row.get('pending'))
    except (ValueError, TypeError, OSError, KeyError):
        return False


def submit(text, channel, send, out, *, clock=time.time, observed=False):
    from .support import SupportError, _THREAD
    parts = text.removeprefix(PREFIX).split(' ')
    target = None
    if len(parts) >= 3 and parts[2].startswith('at:'):
        target = parts.pop(2)[3:]
        if target not in preferences.SCHEMA and target not in ('browser.py', 'serve.py', 'service.py', 'update.py', 'config.json5'):
            raise SupportError('Unknown public product component; do not send personal paths.')
    cause = change = None
    if len(parts) == 5 and parts[3].startswith('cause:') and parts[4].startswith('change:'):
        cause, change = parts[3][6:], parts[4][7:]
        if cause not in CAUSES or change not in CHANGES:
            raise SupportError('Unknown verified repair cause or change category.')
        parts = parts[:3]
    if parts[-1] == 'source-patch' and (target is None or cause is None):
        raise SupportError('For an approved source repair, identify the product file, cause and verified change.')
    if (len(parts) not in (2, 3) or parts[0] not in EVENTS or parts[1] not in SYMPTOMS
            or (len(parts) == 3 and parts[2] not in REPAIRS)):
        raise SupportError('Unknown product fault or repair. See feedback-to-agentj-father.')
    st = channel.st
    if not channel.signed or st is None:
        raise SupportError('Automatic product diagnostics require a bound Agent J host.')
    recipient = account(st)
    if not recipient:
        raise SupportError('Product diagnostics require a current bound account.')
    scope = ('host-preferences/' if observed else 'native/') + recipient + '/'
    if not enabled(st, parts[0]):
        print('自动上报已关闭或无法读取设置 / Automatic reporting is off or settings are unreadable.', file=out)
        return 1
    component, symptom = parts[:2]
    repair = parts[2] if len(parts) == 3 else None
    path = st.root / 'support-product-faults.json'
    lockpath = path.with_suffix('.lock')
    with contextlib.ExitStack() as stack:
        fd = os.open(lockpath, os.O_RDWR | os.O_CREAT, 0o600)
        stack.callback(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            rows = json.loads(path.read_text())
        except FileNotFoundError:
            rows = {}
        except (ValueError, OSError):
            raise SupportError('Automatic report history unavailable. Check existing support tickets.')
        if not isinstance(rows, dict):
            raise SupportError('Automatic report history invalid.')
        now = int(clock())
        key = scope + '/'.join(filter(None, (component, symptom, target)))
        rows = {k: r for k, r in rows.items() if isinstance(r, dict) and isinstance(r.get('at'), int) and r['at'] + DAY > now}
        entry = rows.get(key, {})
        thread = entry.get('thread')
        if not observed and (component, symptom, target) == ('config', 'invalid-config', 'heartbeat.interval_minutes'):
            # Only the installed host validator opens/follows this incident. A native CLI can read an issued receipt.
            observed_key = 'host-preferences/' + recipient + '/config/invalid-config/heartbeat.interval_minutes'
            owner_entry = rows.get(observed_key) or {}
            existing = owner_entry.get('thread')
            if (isinstance(existing, str) and _THREAD.fullmatch(existing) and not owner_entry.get('pending')
                    and (not repair or owner_entry.get('repaired')) and account(st) == recipient and enabled(st, component)):
                confirmed = getattr(channel, 'existing_incident', None)
                if confirmed:
                    confirmed(existing)
                print('主机已向客服提交 / Host-confirmed support ticket ' + existing, file=out)
                return 0
            print('等待主机核验 / Waiting for the host to verify this setting. Check the phone for a ticket.', file=out)
            return 1
        if thread and not _THREAD.fullmatch(thread):
            raise SupportError('Stored ticket identifier invalid.')
        def save():
            _write_private(path, json.dumps(rows, separators=(',', ':')).encode())
        if entry.get('pending'):
            print('发送结果待核实，请查客服工单 / Delivery unconfirmed; check support tickets.', file=out)
            return 1
        if thread and (not repair or entry.get('repaired')):
            if account(st) != recipient or not enabled(st, component):
                raise SupportError('The diagnostic recipient or reporting preference changed.')
            confirmed = getattr(channel, 'existing_incident', None)
            if confirmed:
                confirmed(thread)
            print('已提交 / Existing ticket ' + thread, file=out)
            return 0
        if repair and not thread:
            raise SupportError('先报故障再追加修复 / Submit the fault before reporting its repair.')
        body = message(st, component, symptom, target, repair, cause, change)
        class ReceiptChannel:
            signed = True
            def __init__(self):
                self.st = st

            def send(self, fields):
                if not enabled(st, component):
                    raise SupportError('Automatic reporting is off.')
                if account(st) != recipient:
                    raise SupportError('The bound support account changed.')
                entry.update(at=entry.get('at', now), pending=True)
                rows[key] = entry
                save()  # an ambiguous write or process crash cannot produce a second ticket
                try:
                    if account(st) != recipient or not enabled(st, component):
                        raise NotDispatched()
                    status, response = channel.send(fields)
                except NotDispatched:
                    entry['pending'] = False
                    save()
                    raise SupportError('Local diagnostic or reporting permission changed before dispatch.') from None
                tid = response.get('thread') if isinstance(response, dict) else None
                if 200 <= status < 300 and isinstance(tid, str) and _THREAD.fullmatch(tid) and (not repair or tid == thread):
                    entry.update(thread=tid, repaired=bool(repair), pending=False)
                    save()
                elif 400 <= status < 500 and status not in (408, 409):
                    entry['pending'] = False
                    save()  # explicit rejection; the next attempt is eligible
                return status, response
        return send(body, thread, ReceiptChannel())
