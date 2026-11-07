"""Safe-point handoff records for the caller's own live owner session; never process control."""
from __future__ import annotations
import json
import os
import time
from . import inbox, provenance, shared_hook
from .heartbeat import session_key


def reminder(host):
    from .heartbeat import _read_regular
    raw = _read_regular(host.st.root / 'capability-pause.json', 65536)
    if not raw:
        return ''
    try:
        r = json.loads(raw)
        directory = (getattr(host.agent, 'cfg', None) or {}).get('dir')
        if r.get('paused') and r.get('directory') == directory:
            return '有暂停交接，请先读 .agentj/handover/pause.md，再用 agentj capability resume 接续。'
    except (TypeError, ValueError, AttributeError):
        pass
    return ''


def paused(host):
    """Only this original conversation's automatic wakes are held; other sessions keep running."""
    from .heartbeat import _read_regular
    raw = _read_regular(host.st.root / 'capability-pause.json', 65536)
    try:
        row = json.loads(raw) if raw else {}
        return bool(row.get('paused') and row.get('owner') == session_key(host.agent)
                    and row.get('directory') == (getattr(host.agent, 'cfg', None) or {}).get('dir'))
    except (ValueError, TypeError, AttributeError):
        return False


def handle(host, req, pid):
    from . import self_compact
    from .state import _write_private
    agent = getattr(host, 'agent', None)
    if set(req) - {'t', 'reason'} or req.get('t') not in ('capability_pause', 'capability_resume'):
        return {'result':'refused', 'why':'shape'}
    if agent is None or type(pid) is not int or not (self_compact.roots(agent) & set(shared_hook.ancestors(pid))):
        return {'result':'refused', 'why':'caller'}
    if (host.stopped() or provenance.untrusted_turn(agent) or provenance.delegated_turn(agent)
            or provenance.owner_grant_required(agent) or getattr(host, 'task_label', None)):
        return {'result':'refused', 'why':'source'}
    source = self_compact.turn_source(agent)
    if source is self_compact._UNKNOWN or provenance.normalize(source).get('kind') not in ('phone', 'terminal'):
        return {'result':'refused', 'why':'source'}
    directory = (getattr(agent, 'cfg', None) or {}).get('dir')
    if not directory:
        return {'result':'refused', 'why':'workdir'}
    from . import privacy
    reason = req.get('reason') or 'Paused at a safe point; read the decision stream before resuming.'
    if not isinstance(reason, str) or len(reason) > 500:
        return {'result':'refused', 'why':'shape'}
    reason = privacy.owner_text(reason)
    path = host.st.root / 'capability-pause.json'
    try:
        with inbox.dot_dir(directory) as base:
            fd = inbox._open_dir('handover', base, True)
            try:
                if req['t'] == 'capability_pause':
                    # Exclusive handoff: never replace another unconsumed checkpoint.
                    out = os.open('pause.md', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                    with os.fdopen(out, 'w') as f:
                        f.write('# Paused\n\n' + reason + '\n\nDecision stream: reports/decisions.jsonl\n'
                                'Resume only after checking current outputs and any uncertain external effects.\n')
                        f.flush()
                        os.fsync(f.fileno())
                    row = dict(paused=True, owner=session_key(agent), directory=directory, at=time.time())
                else:
                    from .heartbeat import _read_regular
                    raw = _read_regular(path, 65536)
                    row = json.loads(raw) if raw else {}
                    if not row.get('paused') or row.get('directory') != directory:
                        return {'result':'refused', 'why':'no_pause'}
                    # The existing no-follow regular-file probe rejects an injected checkpoint.
                    from .acceptance import regular
                    regular(directory, '.agentj/handover/pause.md')
                    os.rename('pause.md', 'pause-used.md', src_dir_fd=fd, dst_dir_fd=fd)
                    row.update(paused=False, resumed_by=session_key(agent), resumed_at=time.time())
                _write_private(path, json.dumps(row).encode())
            finally:
                os.close(fd)
    except (OSError, ValueError, inbox.Unsafe):
        return {'result':'refused', 'why':'handoff_unavailable'}
    host.st.log(req['t'], owner=session_key(agent))
    return {'result':'paused' if row['paused'] else 'resumed', 'path':'.agentj/handover/' + ('pause.md' if row['paused'] else 'pause-used.md')}
