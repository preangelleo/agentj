"""Bounded retries from native failure metadata, never model-authored instructions."""
from . import decisions


def result(kind, code, history, *, ended, known):
    kind = decisions.failure_kind(kind, exit_code=code)
    row = {'failure_kind':kind, 'root_cause_hash':decisions.root_cause_hash('task', kind, []),
           'verdict':'failed', 'process_ended':bool(ended), 'result_known':bool(known)}
    row['retry_advice'] = decisions.retry_advice([*history, row])
    if not ended or not known:
        row['retry_advice'] = 'inspect_uncertain'
    return row


def allowed(row, attempts, limit):
    return (type(limit) is int and 0 <= attempts < min(5, limit) and row['retry_advice'] == 'retry_same'
            and row['failure_kind'] in ('network', 'rate_limit', 'server')
            and row['process_ended'] and row['result_known'])
