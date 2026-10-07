"""Owner installation-code capability: server credentials never go to a friend's computer.
Output only to a new owner-private file; caller's existing paired transport owns delivery.
"""
import json
import os
from pathlib import Path
import re
import urllib.request
import uuid

ORIGIN = 'https://install-assistant.agentj.app'
# The shared env store names it AGENTJ_INSTALLER_OWNER_ISSUER_TOKEN; the service-side
# name INSTALL_ASSISTANT_OWNER_ISSUER_TOKEN is still accepted (first non-empty wins).
OWNER_TOKEN_NAMES = ('AGENTJ_INSTALLER_OWNER_ISSUER_TOKEN', 'INSTALL_ASSISTANT_OWNER_ISSUER_TOKEN')

def owner_token(environ=None, read_file=None):
    """Environment first, then (when given) an env-file reader, each under both names."""
    environ = os.environ if environ is None else environ
    for name in OWNER_TOKEN_NAMES:
        value = (environ.get(name) or '').strip()
        if value: return value
    if read_file is not None:
        for name in OWNER_TOKEN_NAMES:
            value = (read_file(name) or '').strip()
            if value: return value
    return None

def issue_owner(output: Path, *, opener=None, request_id=None, token=None):
    from datetime import datetime, timezone
    token = token or owner_token()
    if not token: raise ValueError('owner_issuer_not_configured')
    if output.exists() or output.is_symlink(): raise ValueError('output_exists')
    if not output.parent.is_dir() or output.parent.is_symlink(): raise ValueError('private_output_directory_required')
    if output.parent.stat().st_mode & 0o077: raise ValueError('private_output_directory_required')
    if opener is None:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args): raise ValueError('redirect_refused')
        opener=urllib.request.build_opener(NoRedirect()).open
    pending=output.with_name(output.name+'.request')
    if request_id is None:
        if pending.exists():
            if pending.is_symlink() or pending.stat().st_mode & 0o077: raise ValueError('unsafe_pending_request')
            request_id=pending.read_text().strip()
        else:
            request_id=uuid.uuid4().hex
            fd=os.open(pending, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as f: f.write(request_id)
    if not re.fullmatch(r'[a-f0-9]{32}',request_id): raise ValueError('invalid_request_id')
    req = urllib.request.Request(ORIGIN+'/v1/install/codes', data=json.dumps({'kind':'owner','entitlement_id':request_id}).encode(), headers={'content-type':'application/json','authorization':'Bearer '+token})
    try:
        with opener(req, timeout=15) as r: value=json.loads(r.read(4097))
    except Exception: raise ValueError('owner_code_issue_failed') from None
    if not re.fullmatch(r'AJI-[a-f0-9]{32}', value.get('code','')) or value.get('quota_usd') != 10: raise ValueError('owner_code_issue_failed')
    try:
        expiry=datetime.fromisoformat(value['expires_at'].replace('Z','+00:00')).timestamp()
        now=datetime.now(timezone.utc).timestamp()
        if not now < expiry <= now+7260: raise ValueError()
    except (KeyError, ValueError, TypeError): raise ValueError('owner_code_issue_failed') from None
    value={k:value[k] for k in ('code','expires_at','quota_usd')}
    value['command']='curl -fsSL https://agentj.app/install-assistant.sh | sh -s -- '+value['code']
    fd=os.open(output, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'w') as f: json.dump(value,f)

def main(argv):
    import argparse
    p=argparse.ArgumentParser(description='Owner-only installation code; no master credential leaves this host')
    p.add_argument('action', choices=['issue-owner']);p.add_argument('--output',required=True,type=Path); p.add_argument('--request-id',help='Nonsecret retry id, 32 lowercase hex')
    a=p.parse_args(argv)
    try: issue_owner(a.output.expanduser(),request_id=a.request_id)
    except ValueError as e: p.exit(1, str(e)+'\n')
    print('一次性码已写入私有文件；仅通过现有已配对通道交付。/ Code written to private file; deliver through the existing paired channel only.')
