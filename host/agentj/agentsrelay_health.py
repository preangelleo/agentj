"""One bounded fixed-text provider check; no user input, tools, retry or redirects."""
from __future__ import annotations
import copy
import http.client
import json
import os
import urllib.error
import urllib.request

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def snapshot(host, req, pid, uid, policy=None):
    from . import busy_native, provenance
    facts = policy if policy is not None else busy_native.policy(host, req, pid, uid)
    agent = host.agent
    return (agent, id(agent), getattr(agent, 'cur_send', None), id(getattr(agent, 'cur_send', None)),
            id(getattr(agent, 'session', None)), id(getattr(agent, 'thread', None)),
            copy.deepcopy(getattr(agent, 'session', None)),
            copy.deepcopy(getattr(agent, 'thread', None)), copy.deepcopy(facts),
            copy.deepcopy({name: getattr(agent, name, None) for name in
                           ('native_source', 'pending_source', 'out_src', 'phone_turn', 'phone_seen', 'research')}),
            getattr(getattr(host, 'hist', None), 'epoch', None),
            getattr(host, 'desktop_turn', None), copy.deepcopy(provenance.turn_source(agent)))


def valid(host, req, pid, uid, captured):
    try:
        return not host.stopped() and snapshot(host, req, pid, uid) == captured
    except (ValueError, AttributeError, KeyError, OSError):
        return False


def prepare(profile):
    from .agent_codex import primary_environment
    from .agentsrelay_dispatch import pinned_profile
    p = pinned_profile(profile)
    try:
        key = primary_environment(os.environ).get('AGENTSRELAY_OPENAI_KEY')
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(key, str) or not key or len(key) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in key):
        return None
    data = json.dumps({'model': p.model, 'input': 'Reply OK.', 'max_output_tokens': 8,
                       'tools': [], 'tool_choice': 'none', 'store': False}).encode()
    req = urllib.request.Request(p.base_url + '/responses', data=data, method='POST',
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    return req


def check(req, still_valid):
    if req is None:
        return {'ok': False, 'health': 'key_unavailable'}
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        # Last synchronous disclosure check after key preparation/opener setup.
        # Nothing from the current visible Host can replace the captured subject.
        if not still_valid():
            return {'ok': False, 'health': 'source_changed'}
        with opener.open(req, timeout=8) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                return {'ok': False, 'health': 'response_invalid'}
            body = json.loads(raw)
            healthy = (response.status == 200 and isinstance(body, dict)
                       and body.get('status') == 'completed' and isinstance(body.get('output'), list)
                       and not body.get('error') and any(
                           isinstance(item, dict) and item.get('type') == 'message'
                           and item.get('role') == 'assistant' and isinstance(item.get('content'), list)
                           and any(isinstance(part, dict) and part.get('type') == 'output_text'
                                   and isinstance(part.get('text'), str) and part['text'].strip() == 'OK'
                                   for part in item['content']) for item in body['output']))
            return {'ok': healthy, 'health': 'verified_now' if healthy else 'response_invalid'}
    except urllib.error.HTTPError as exc:
        exc.close()
        return {'ok': False, 'health': 'provider_refused'}
    except (OSError, ValueError, UnicodeError, RecursionError, http.client.HTTPException):
        return {'ok': False, 'health': 'outcome_unknown'}
