"""Private exact-source plan preparation, without authority or an execution entry point.

This module deliberately cannot elevate or execute a serialized plan. The caller
must retain the live native phone authority; a digest is only an integrity check.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

from . import native_scheduler_control as native


def prepare(controller, target, guard):
    """Read a precise file plan, retaining private bytes outside public descriptors.

    Writer permissions are checked separately at installation, never inferred
    from this successful read. No helper, password, manager or job is invoked.
    """
    target = dict(native.validate(target))
    if target['source'] not in ('cron-system', 'anacron'):
        raise native.Refused('read_only')
    backend = controller.backend
    guard()
    fd, path, info, data = backend.source_state(target, readonly=True)
    try:
        before = backend.source_metadata(target, info, data, path.name.startswith('.'))
        if target['snapshot'] != before['snapshot']:
            raise native.Refused('changed')
        journal = controller.journal()
        key = controller.key(target)
        record = journal.get(key)
        if target['op'] == 'disable':
            if record or not before['enabled']:
                raise native.Refused('changed' if record else 'marker_spoof')
            if any(isinstance(value, dict)
                   and value.get('original', {}).get('source') in ('cron-system', 'anacron')
                   and value['original']['id'].split(':')[0] == target['id'].split(':')[0]
                   for value in journal.values()):
                raise native.Refused('source_has_pending_control')
            original = data
            original_snapshot = before
        else:
            if not record:
                raise native.Refused('no_restore')
            if not backend.source_matches(record, before):
                raise native.Refused('changed')
            original = base64.b64decode(record['bytes'], validate=True)
            original_snapshot = record['original']
            if hashlib.sha256(original).hexdigest() != original_snapshot['content']:
                raise native.Refused('corrupt_original')
        script = ':' not in target['id']
        desired = data
        if not script:
            if target['op'] == 'disable':
                lines = data.splitlines(keepends=True)
                number = int(target['id'].rsplit(':', 1)[1])
                marker = b'# agentj-disabled:' + hashlib.sha256(data + target['id'].encode()).hexdigest().encode() + b' '
                lines[number - 1] = marker + lines[number - 1]
                desired = b''.join(lines)
            else:
                desired = original
        destination = path.name
        if script:
            destination = (path.name.removeprefix('.agentj-disabled-') if target['op'] == 'restore'
                           else '.agentj-disabled-' + path.name)
        plan = dict(version=1, target=target, path=str(path), destination=destination,
                    stat=list(backend.source_stat(info)), preimage=hashlib.sha256(data).hexdigest(),
                    before=before, original=original_snapshot, journal_key=key,
                    journal_digest=native.digest(journal),
                    preimage_bytes=base64.b64encode(data).decode('ascii'),
                    original_bytes=base64.b64encode(original).decode('ascii'),
                    desired_bytes=base64.b64encode(desired).decode('ascii'))
        guard()
        # Detect source/parent replacement while journal or guard work was running.
        check_fd, check_path, check_info, check_data = backend.source_state(target, readonly=True)
        try:
            if (os.fstat(fd) != os.fstat(check_fd) or check_path != path
                    or backend.source_stat(check_info) != backend.source_stat(info) or check_data != data):
                raise native.Refused('changed')
        finally:
            os.close(check_fd)
        guard()
        return plan
    finally:
        os.close(fd)


def canonical(plan):
    """Private transport representation; never place these bytes in argv or UI."""
    return json.dumps(plan, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
