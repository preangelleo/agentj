
import asyncio
import hashlib
import secrets
import stat
import json
import os
import pathlib
import shlex
import shutil
import time
import copy

from . import owner_call, provenance


def parse(tool, inp, cwd):
    if tool != 'Bash' or not isinstance(inp, dict) or not isinstance(inp.get('command'), str):
        return None
    from .static_shell import _statements
    statements = _statements(inp['command'])
    if not statements or len(statements) != 1 or statements[0][1] or any(w[3] for w in statements[0][0]):
        return None
    try:
        words = shlex.split(inp['command'])
    except ValueError:
        return None
    if len(words) < 3 or not (words[0] == 'agentj' or (shutil.which('agentj') and os.path.realpath(words[0]) == os.path.realpath(shutil.which('agentj')))):
        return None
    if words[1:3] == ['file', 'show']:
        import argparse
        parser = argparse.ArgumentParser(exit_on_error=False, add_help=False)
        parser.add_argument('path')
        parser.add_argument('--label', default='')
        parser.add_argument('--json', action='store_true')
        try:
            args, unknown = parser.parse_known_args(words[3:])
        except (argparse.ArgumentError, SystemExit):
            return None
        if unknown or not args.path or len(args.path) > 512 or len(args.label) > 128:
            return None
        from .file_show import request
        return request(args.path, args.label, cwd)
    if words[1:3] == ['friends','tell']:
        args = words[3:]
        if args.count('--grant') != 1:
            return None
        at = args.index('--grant')
        if at+1 >= len(args):
            return None
        grant = args[at+1]
        args = [word for word in args[:at]+args[at+2:] if word != '--json']
        if len(args) < 2 or any(word.startswith('--') for word in args):
            return None
        return {'t':'tell', 'friend':args[0], 'text':' '.join(args[1:]), 'grant':grant, 'cwd':os.path.abspath(cwd)}
    if words[1] != 'secret' or words[2] not in ('ask', 'give', 'grant', 'grants'):
        return None
    req = {'t':'sx', 'op':words[2], 'cwd':os.path.abspath(cwd)}
    keys = {'--peer':'peer', '--name':'name', '--why':'why', '--dest':'dest', '--src':'src', '--file':'file', '--revoke':'revoke'}
    at = 3
    while at < len(words):
        word = words[at]
        if word == '--json':
            at += 1
            continue
        if word not in keys or at + 1 == len(words) or keys[word] in req:
            return None
        req[keys[word]] = words[at+1]
        at += 2
    if req['op'] in ('ask', 'give'):
        if not all(req.get(k) for k in ('peer', 'name', 'why')):
            return None
        req.setdefault('dest', '')
        req.setdefault('src', '')
    elif req['op'] == 'grant' and not req.get('file'):
        return None
    return req


def key(req):
    return hashlib.sha256(json.dumps(req, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Broker:
    def __init__(self, xfer):
        self.xfer, self.calls, self.receipts, self.jobs = xfer, {}, {}, set()
        self.feedback_proc, self.feedback_seen = None, set()

    def feedback_applicable(self):
        import sys
        a = getattr(self.xfer.svc.host, 'agent', None)
        cfg = getattr(a, 'cfg', None)
        return (sys.platform == 'linux' and isinstance(cfg, dict) and cfg.get('fence') is True
                and cfg.get('session_mode') != 'shared' and getattr(a, 'proc', None) is not None
                and getattr(a, 'kind', None) in ('claude', 'codex'))

    def register(self, call, tool, inp, cwd, raw=None, native=None):
        x = self.xfer
        req = parse(tool, raw if raw is not None else inp, cwd)
        if req is None and self.feedback_applicable():
            from . import feedback_native
            req = feedback_native.command(tool, raw if raw is not None else inp, cwd)
        execution = getattr(x, 'execution', None)
        if not req or not isinstance(call, str) or not execution or not execution.live or execution.send is None:
            return
        if req['t'] == 'support_native' and call in self.calls:
            old = self.calls[call]
            if old.get('request') == req and old.get('native') == native and old.get('execution') is execution and old.get('input') == inp:
                return  # repeated native admission never refreshes its original source/timeout
            if old.get('feedback'):
                old['feedback'].closed = True
            return
        if req['t'] == 'support_native':
            proc = getattr(x.svc.host.agent, 'proc', None)
            if proc is not self.feedback_proc:
                self.feedback_proc, self.feedback_seen = proc, set()
            if (call, native) in self.feedback_seen or len(self.feedback_seen) >= 1024:
                return
        self.calls[call] = {'request':req, 'tool':tool, 'input':inp, 'execution':execution,
                            'source':provenance.turn_source(x.svc.host.agent), 'native':native,
                            'project_root': os.path.realpath((x.svc.host.agent_cfg or {}).get('dir') or '')}
        if req['t'] == 'support_native':
            from .feedback_native import Binding
            row = self.calls[call]
            row.update(input=copy.deepcopy(inp), registered_at=time.monotonic())
            try:
                row['feedback'] = Binding(self, row)
                self.feedback_seen.add((call, native))
            except (ValueError, AttributeError, OSError): self.calls.pop(call, None)
        while len(self.calls) > 64:
            self.calls.pop(next(iter(self.calls)))

    async def execute(self, tool, inp, call=None, gone=None):
        x = self.xfer
        matches = [(cid, row) for cid,row in self.calls.items() if row['tool'] == tool and row['input'] == inp
                   and (call is None or call == cid)]
        if not matches:
            missing = parse(tool, inp, (getattr(self.xfer.svc.host, 'agent_cfg', None) or {}).get('dir') or os.getcwd())
            if missing is None and self.feedback_applicable():
                from . import feedback_native
                missing = feedback_native.command(tool, inp, self.xfer.svc.host.agent_cfg.get('dir') or os.getcwd())
            if missing:
                return {'behavior':'deny', 'message':'No live native feedback admission' if missing['t'] == 'support_native' else 'No live native secret-call admission'}
            return None
        if len(matches) != 1:
            return {'behavior':'deny', 'message':'Ambiguous native secret call'}
        cid, row = matches[0]
        if row.get('admitted'):
            return {'behavior':'deny', 'message':'Native secret call already admitted'}
        execution = row['execution']
        def live():
            return (execution.live and getattr(x, 'execution', None) is execution
                    and execution.send is getattr(x.svc.host.agent, 'cur_send', None)
                    and not x.svc.host.stopped())
        if not live() or (gone is not None and gone.done()):
            return {'behavior':'deny', 'message':'Native secret call expired'}
        from . import policy_registry
        if policy_registry.main_requirement(tool, inp) == 'deny':
            return {'behavior':'deny', 'message':'Native secret call blocked by policy'}
        req = dict(row['request'])
        if req['t'] == 'support_native':
            from . import feedback_native, approval_review
            binding = row.get('feedback')
            if binding is None or not binding.live():
                return {'behavior':'deny', 'message':'Original native feedback call expired.'}
            try: binding.bind_body(await asyncio.to_thread(feedback_native.payload, req, binding.root))
            except (OSError, ValueError, UnicodeError):
                return {'behavior':'deny', 'message':'Cannot bind the exact feedback draft.'}
            with owner_call.request(binding.live), provenance.request_source(binding.source), approval_review.origin_scope(binding.origin):
                answer = await x.svc.host.ask(tool, inp, gone, batch=False)
            if answer.get('behavior') != 'allow' or not binding.live():
                return answer if answer.get('behavior') != 'allow' else {'behavior':'deny','message':'Original native feedback call expired.'}
            row.update(admitted=True, ticket=secrets.token_hex(16), check=binding.live)
            return answer
        if req['t'] == 'file_show':
            host = x.svc.host
            page = host.hist.get(getattr(host, 'cur_turn', None))
            ids = next((ids for ids in host.hist.parts.values() if page and page['id'] in ids), [page['id']] if page else [])
            if (os.path.realpath((host.agent_cfg or {}).get('dir') or '') != row['project_root']
                    or not provenance.is_owner(row['source']) or not page or page.get('end') != 'open'
                    or execution.send.turn not in ids):
                return {'behavior': 'deny', 'message': 'No active owner page'}
        if req.get('op') == 'grant':
            path = pathlib.Path(req.pop('file')).expanduser()
            if not path.is_absolute():
                path = pathlib.Path(req['cwd']) / path
            try:
                root = pathlib.Path((x.svc.host.agent_cfg or {})['dir']).resolve()
                parts = path.absolute().relative_to(root).parts
                if not parts or any(part in ('.','..') for part in parts) or path.suffix != '.json':
                    raise ValueError('plan path')
                fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    for part in parts[:-1]:
                        nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        os.close(fd)
                        fd = nxt
                    planfd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    with os.fdopen(planfd, 'rb') as stream:
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
                            raise ValueError('plan file')
                        raw = stream.read(65537)
                    if len(raw) > 65536:
                        raise ValueError('plan size')
                    req['plan'] = json.loads(raw)
                finally:
                    os.close(fd)
            except (OSError, ValueError):
                return {'behavior':'deny', 'message':'Cannot read grant plan inside session folder'}
        row.update(request=req, admitted=True, ticket=secrets.token_hex(16), check=live)
        return {'behavior':'allow', 'updatedInput':inp}

    def complete(self, call, success, output, tool=None, inp=None, cwd=None, native=None, child=False):
        row = self.calls.get(call)
        if row and row.get('feedback') and child:
            return  # a nested native result never completes a root's feedback call
        if not row or not row.get('admitted') or not row.get('issued') or row.get('native') != native:
            return
        if tool is not None and (tool != row['tool'] or inp != row['input'] or os.path.abspath(cwd or '') != row['request']['cwd']):
            return
        self.calls.pop(call)
        if not success:
            return
        pending = _pending(row)
        if not _contains(output, pending):
            return
        if not row['check']():
            return
        job = asyncio.create_task(self._finish(row))
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)

    async def _finish(self, row):
        x = self.xfer
        req = row['request']
        if req['t'] == 'support_native':
            from . import host_diagnostics, approval_review
            binding = row['feedback']
            if not binding.live():
                return
            request = {'t':'host_diagnostics', 'op':'support', **json.loads(binding.body)}
            try:
                with owner_call.request(binding.live), provenance.request_source(binding.source), approval_review.origin_scope(binding.origin), binding.review_scope():
                    result = await asyncio.to_thread(host_diagnostics.execute, x.svc.host.st, request, _support_binding=binding)
                x.svc.host.agent_notice('SUPPORT_TASK_RESULT: ' + json.dumps(result, ensure_ascii=False))
            finally:
                binding.closed = True
            return
        with owner_call.request(row['check']), provenance.request_source(row['source']):
            from . import file_show
            if req['t'] == 'file_show':
                result = await file_show.attach(x.svc.host, req, row['check'], row['project_root'])
            elif req['t'] == 'tell':
                result = await x.svc.tell(req['friend'], req['text'], grant=req['grant'])
            else:
                result = await x.cli(req, req['cwd'])
        if req['t'] != 'file_show':
            self.receipts[key(req)] = (time.monotonic() + 120, result)
        while len(self.receipts) > 64:
            self.receipts.pop(next(iter(self.receipts)))
        x.svc.host.agent_notice(('FILE_SHOW_RESULT: ' if req['t'] == 'file_show' else 'SECRET_TASK_RESULT: ') + json.dumps(result, ensure_ascii=False))

    def feedback_receipt(self, req, peer):
        """Only an exact admitted payload produces a pending result; never HTTP here."""
        from . import feedback_native
        value = feedback_native.canonical(req)
        rows = [r for r in self.calls.values() if r.get('feedback') and r.get('admitted')
                and r['feedback'].body == value and r['feedback'].live()]
        if value is None or len(rows) != 1:
            return {'exit':3, 'output':'NOT SENT — no unique exact native feedback admission.\n'}
        row = rows[0]
        if not row['feedback'].peer(peer) or row.get('issued'):
            return {'exit':3, 'output':'NOT SENT — original native feedback caller is unverified or already issued.\n'}
        row['issued'] = True
        return {'exit':0, 'output':'SUPPORT_TASK_PENDING: ' + json.dumps(_pending(row),sort_keys=True) + '\n'}

    async def drain(self):
        if self.jobs:
            await asyncio.gather(*list(self.jobs))

    def receipt(self, req):
        if req.get('op') == 'grants' and not req.get('revoke'):
            return self.xfer._list(req)
        receipt = self.receipts.pop(key(req), None)
        if receipt and receipt[0] > time.monotonic():
            return receipt[1]
        matching = [row for row in self.calls.values() if row.get('admitted') and key(row['request']) == key(req)]
        rows = [row for row in matching if row['check']()]
        if len(rows) != 1:
            if req.get('t') == 'file_show':
                execution = getattr(self.xfer, 'execution', None)
                why = ('native_admission_ambiguous' if len(rows) > 1 else 'native_admission_expired' if matching
                       else 'native_admission_missing' if execution and execution.live and execution.send is not None
                       else 'no_owner_session')
                return {'result': 'failure', 'why': why, 'client_downloaded': False}
            return {'ok':False, 'result':'refused', 'why':'caller'} if req.get('t') == 'tell' else {'result':'refused', 'why':'caller'}
        row = rows[0]
        row['issued'] = True
        return _pending(row)


def _contains(output, pending, depth=0):
    if depth > 6:
        return False
    if isinstance(output, dict):
        return output == pending or any(_contains(v, pending, depth+1) for v in output.values())
    if isinstance(output, list):
        return any(_contains(v, pending, depth+1) for v in output[:100])
    if isinstance(output, str) and len(output) <= 1048576:
        try:
            if json.loads(output) == pending:
                return True
        except ValueError:
            pass
        for line in output.splitlines()[:100]:
            try:
                value = json.loads(line.removeprefix('SECRET_TASK_PENDING: ').removeprefix('SUPPORT_TASK_PENDING: '))
            except ValueError:
                continue
            if value == pending:
                return True
    return False


def _pending(row):
    result = {'result':'pending', 'ticket':row['ticket'], 'request_sha256':key(row['request'])}
    if row['request']['t'] == 'support_native':
        result['payload_sha256'] = row['feedback'].body_sha256
    if row['request']['t'] == 'file_show':
        result.update(result='queued', client_downloaded=False)
    if row['request']['t'] == 'tell':
        result['ok'] = True
    return result
