"""Owner-scoped official GitHub reads; never a webhook or a generic HTTP proxy."""
from __future__ import annotations

import datetime
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

REPO = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}')
MAX_PAGES = 5
PAGE_SIZE = 100
MAX_BYTES = 2 * 1024 * 1024


class Unavailable(ValueError):
    pass


def validate(s):
    """The entire repository, numeric thread, kind and lower time bound are signed."""
    if not REPO.fullmatch(s.get('repository', '')) or '..' in s['repository']:
        raise ValueError('repository')
    if not re.fullmatch(r'[1-9][0-9]{0,14}', s['thread']):
        raise ValueError('thread')
    if type(s.get('since')) is not int or not 0 <= s['since'] < s['deadline']:
        raise ValueError('since')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Unavailable('redirect_denied')


def credential():
    """Existing host gh login only. The token stays in memory, never in argv/log/state."""
    try:
        p = subprocess.run(['gh', 'auth', 'token', '--hostname', 'github.com'],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        token = p.stdout.decode('ascii').strip() if p.returncode == 0 else ''
        if not token or len(token) > 4096 or any(c.isspace() for c in token):
            raise Unavailable('needs_setup')
        return token
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise Unavailable('needs_setup') from None


def read(path, token):
    # Only this module constructs paths. No URL, proxy, link header or redirect is followed.
    if not path.startswith('/repos/') or '://' in path or '\\' in path:
        raise Unavailable('endpoint_denied')
    req = urllib.request.Request('https://api.github.com' + path, headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'AgentJ-readonly-subscriptions'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=15) as r:
            if r.status != 200 or r.geturl() != req.full_url:
                raise Unavailable('endpoint_denied')
            raw = r.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise Unavailable('response_limit')
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raise Unavailable('needs_setup' if e.code in (401, 403) else 'github_unavailable') from None
    except (OSError, ValueError):
        raise Unavailable('github_unavailable') from None


def page(s, number, token, *, get=read):
    validate(s)
    if type(number) is not int or not 1 <= number <= MAX_PAGES:
        raise ValueError('page')
    if s['kind'] == 'github.pr_comment':
        stamp = datetime.datetime.fromtimestamp(s['since'], datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        path = f"/repos/{s['repository']}/issues/{s['thread']}/comments?" + urllib.parse.urlencode(
            {'since': stamp, 'per_page': PAGE_SIZE, 'page': number})
        rows = get(path, token)
    elif s['kind'] == 'github.ci_failure':
        # The signed thread is a workflow-run ID, not a caller-provided path.
        path = f"/repos/{s['repository']}/actions/runs/{s['thread']}/jobs?" + urllib.parse.urlencode(
            {'filter': 'latest', 'per_page': PAGE_SIZE, 'page': number})
        obj = get(path, token)
        rows = obj.get('jobs') if isinstance(obj, dict) else None
    else:
        raise ValueError('kind')
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise Unavailable('response_shape')
    events = []
    for r in rows:
        if not isinstance(r, dict) or type(r.get('id')) is not int or r['id'] <= 0:
            raise Unavailable('response_shape')
        stamp = r.get('created_at') or r.get('started_at')
        try:
            at = datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
        except (AttributeError, TypeError, ValueError):
            raise Unavailable('response_shape') from None
        if at < s['since']:
            continue
        if s['kind'] == 'github.pr_comment':
            sender = (r.get('user') or {}).get('login')
            text = r.get('body')
            expected = f"https://api.github.com/repos/{s['repository']}/issues/{s['thread']}"
            if r.get('issue_url') != expected:
                raise Unavailable('repository_mismatch')
        else:
            if r.get('run_id') != int(s['thread']):
                raise Unavailable('thread_mismatch')
            if r.get('conclusion') not in ('failure', 'timed_out', 'action_required'):
                continue
            sender, text = 'github-actions', r.get('name')
        if not isinstance(sender, str) or not isinstance(text, str) or len(text) > 65536:
            raise Unavailable('response_shape')
        if sender == s['sender']:
            events.append(dict(source='github', kind=s['kind'], thread=s['thread'], sender=sender,
                               id=s['repository'] + ':' + str(r['id']), text=text))
    return events, len(rows) == PAGE_SIZE
