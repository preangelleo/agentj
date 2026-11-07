"""Read-only, bounded order projection for the existing paired busy detail route."""
from __future__ import annotations
import os
from . import askbridge, enabled, orders, order_gates, order_host


def project(host, wid, detail):
    if not enabled() or not isinstance(wid, str) or not wid.startswith('order:'):
        return detail
    try:
        o = orders.get(wid.removeprefix('order:'), host.st.root)
        ag = host.agent
        meta = o.meta
        parent = {'kind': 'owner_turn', 'session': host.st.agent_session(ag.kind), 'harness': ag.kind}
        frozen = meta.get('parent') or {}
        if frozen.get('kind') == 'task' and frozen.get('source_class') in ('system', 'same_host'):
            parent.update(kind='task', source_class=frozen['source_class'])
        if not parent['session'] or meta.get('parent') != parent or not order_host.order_available(o, os.path.realpath(ag.cfg['dir'])):
            return detail
        head = orders.parse_head(o.report())
        clean = lambda value, limit=300: str(value or '')[:limit]
        gates = []
        for gate in order_gates.pending(o)[-10:]:
            options = gate.get('options')
            if gate.get('answer') is not None or not isinstance(options, list) or gate.get('default') not in options:
                continue
            gates.append({'id': gate['id'], 'question': clean(gate.get('question')), 'default': clean(gate['default']),
                          'options': [clean(x, 120) for x in options[:6]], 'deadline': gate.get('deadline')})
        out = dict(detail or {})
        out['order'] = {'id': o.id, 'title': clean(meta['title'], 80), 'state': o.state(),
                        'origin': [askbridge.origin(meta, None)], 'gates': gates,
                        'head': {'done': f"{head.get('done', 0)}/{head.get('total', 0)}",
                                 'ready': clean(head.get('ready')), 'needs': clean(head.get('needs')),
                                 'verdict': clean(head.get('verdict'))}}
        return out
    except (orders.OrderError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return detail
