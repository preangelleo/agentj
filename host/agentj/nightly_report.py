"""One nightly metadata snapshot on the existing signed host report; opt-out never disables checking."""
import json
import re
import secrets
import time
from . import auto_update, update

VERSION = re.compile(r'\d{1,4}\.\d{1,4}(?:\.\d{1,4})?(?:(?:a|b|rc)\d{1,4})?(?:\.dev\d{1,6})?')
FILE = 'nightly-update-report.json'


def check(st, *, now=None, rand=secrets.randbelow, check_fn=update.check):
    now = time.time() if now is None else now
    local = time.localtime(now)
    day = time.strftime('%Y-%m-%d', local)
    second = local.tm_hour*3600 + local.tm_min*60 + local.tm_sec
    with st.config_lock():
        rec = auto_update.read(st)
        if rec.get('day') != day:
            rec.update(day=day, second=3*3600 + rand(2*3600), attempted=False)
            rec.setdefault('phone_at', now)
            auto_update.write(st, rec)
        if rec.get('reported_day') == day or second < rec['second']:
            return None
        if second >= 5*3600:
            rec['reported_day'] = day
            auto_update.write(st, rec)
            return None
    # Network outside the config lock and event loop; even disabled/busy/stopped hosts check and report.
    try:
        latest = check_fn()
    except Exception:
        latest = {'latest': None, 'why': 'unavailable'}
    rec = update.read_rec(st)
    version = latest.get('latest')
    rec.update(checked=int(now), latest=version, why=latest.get('why', 'unknown'))
    note = None
    if isinstance(version, str) and update.compare(version, update.__version__) == 1 and rec.get('notified') != version:
        rec['notified'] = version
        note = update.notice_text(version)
    st.write_private(update._rec_path(st), json.dumps(rec).encode())
    au = auto_update.read(st)
    result = au.get('result') or {}
    last = None
    if (result.get('result') in ('ok', 'failed', 'rolled_back') and isinstance(result.get('to'), str)
            and VERSION.fullmatch(result['to']) and (result.get('from') is None or
            isinstance(result.get('from'), str) and VERSION.fullmatch(result['from']))
            and isinstance(result.get('reason'), str) and re.fullmatch(r'[a-z][a-z0-9_]{0,39}', result['reason'])):
        last = {k: result.get(k) for k in ('from','to','result','reason')}
        last['at'] = int(au.get('result_at') or 0)
    snapshot = {'state': 'on' if auto_update.enabled(st) else 'off', 'at': int(now), 'last': last}
    with st.config_lock():
        auto_update.write(st, snapshot, FILE)
        rec = auto_update.read(st)
        rec['reported_day'] = day
        auto_update.write(st, rec)
    return {'notice': note}


def field(st):
    rec = auto_update.read(st, FILE)
    if rec.get('state') not in ('on', 'off') or type(rec.get('at')) is not int or not 0 <= rec['at'] <= 4102444800:
        return {}
    last = rec.get('last')
    if last is not None:
        if (not isinstance(last, dict) or set(last) != {'from','to','result','reason','at'}
                or last['result'] not in ('ok','failed','rolled_back') or type(last['at']) is not int
                or not 0 <= last['at'] <= 4102444800
                or not isinstance(last['to'], str) or not VERSION.fullmatch(last['to'])
                or last['from'] is not None and (not isinstance(last['from'], str) or not VERSION.fullmatch(last['from']))
                or not isinstance(last['reason'], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', last['reason'])):
            return {}
    return {'auto_update': {'state': rec['state'], 'at': rec['at'], 'last': last}}


def acknowledge(st, sent):
    if not isinstance(sent, dict):
        return
    with st.config_lock():
        if auto_update.read(st, FILE) == sent:
            (st.root / FILE).unlink(missing_ok=True)
