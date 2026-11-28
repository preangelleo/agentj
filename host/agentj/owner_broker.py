
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
    if words[1] == 'file' and words[2] in ('show', 'send'):
        import argparse
        parser = argparse.ArgumentParser(exit_on_error=False, add_help=False)
        parser.add_argument('path')
        if words[2] == 'show':
            parser.add_argument('--label', default='')
        else:
            parser.add_argument('--to', required=True, choices=['wechat'])
            parser.set_defaults(label='')
        parser.add_argument('--json', action='store_true')
        try:
            args, unknown = parser.parse_known_args(words[3:])
        except (argparse.ArgumentError, SystemExit):
            return None
        if unknown or not args.path or len(args.path) > 512 or len(args.label) > 128:
            return None
        from .file_show import request
        return request(args.path, args.label, cwd, delivery='wechat' if words[2] == 'send' else None)
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


def codex_live(agent, native, proc):
    return (all(isinstance(value, str) and value for value in native)
            and agent.host.agent is agent and proc is not None and agent.proc is proc and proc.returncode is None
            and getattr(agent, 'turn_proc', None) is proc and native == (agent.tid, agent.turn_id))


async def codex_prehook(agent, event, tool, inp):
    """Use the real shell child hook. A code-mode program cannot describe a broker command."""
    if (tool != 'Bash' or not isinstance(inp, dict) or not isinstance(inp.get('command'), str)
            or not event.get('_agentj_path_authenticated') or event.get('_agentj_child')
            or event.get('agent_id') or event.get('parent_tool_use_id')
            or getattr(agent, 'research', False) or agent.cfg.get('_workflow_ceo')):
        return {}
    call, cwd = event.get('tool_use_id'), event.get('cwd')
    native = (event.get('session_id'), event.get('turn_id'))
    proc = getattr(agent, 'proc', None)
    peers = getattr(agent.host, 'peers', None)

    def live():
        return codex_live(agent, native, proc)

    if (peers is None or not all(isinstance(v, str) and v for v in (call, cwd, *native))
            or not os.path.isabs(cwd) or not live()):
        return {}
    cwd = os.path.abspath(cwd)
    req = parse(tool, inp, cwd)
    if not req or req['t'] != 'file_show':
        return {}
    from .approval_jev import with_cwd
    command = inp['command']
    policy_input = {'command': with_cwd(command, cwd, agent.cfg['dir'])}
    broker = peers.xfer.broker
    if not broker.register(call, tool, policy_input, cwd, raw=inp, native=native, prehook=True,
                           native_shell=(command, cwd), native_live=live, require_start=True):
        return {}
    answer = await broker.execute(tool, policy_input, call, event.get('_agentj_gone'))
    if not answer or answer['behavior'] != 'allow':
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                'permissionDecisionReason': (answer or {}).get('message') or 'No live native file-call admission'}}
    return {}  # Only the native harness can run or approve the shell call.


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

    def register(self, call, tool, inp, cwd, raw=None, native=None, *, prehook=False,
                 native_shell=None, native_live=None, require_start=False):
        x = self.xfer
        req = parse(tool, raw if raw is not None else inp, cwd)
        if req is None and self.feedback_applicable():
            from . import feedback_native
            req = feedback_native.command(tool, raw if raw is not None else inp, cwd)
        execution = getattr(x, 'execution', None)
        if not req or not isinstance(call, str) or not execution or not execution.live or execution.send is None:
            return
        if req['t'] == 'file_show' and native_shell is not None:
            old = self.calls.get(call)
            if old and old['execution'] is execution and old.get('native') == native:
                if old.get('native_shell') != native_shell or old['request'] != req:
                    old['invalid_native_shell'] = True
                elif not prehook:
                    old['rpc_input'] = copy.deepcopy(inp)
                if old.get('start_ready') is not None and (not prehook or old.get('invalid_native_shell')):
                    old['start_ready'].set()
                return
        if req['t'] == 'support_native' and call in self.calls:
            old = self.calls[call]
            if old.get('request') == req and old.get('native') == native and old.get('execution') is execution:
                if not prehook and (old.pop('awaiting_native', False)
                                    or old.get('observed_start') and 'rpc_input' not in old):
                    old['native_ready'].set()
                    # A genuine approval RPC can add its own description after
                    # item/started. The exact parsed command and native ID were
                    # matched above, and an in-flight decision stays pinned.
                    old['rpc_input'] = copy.deepcopy(inp)
                    if 'feedback_decision' not in old:
                        old['input'] = old['rpc_input']
                    return
                if old.get('input') == inp or not prehook and old.get('rpc_input') == inp:
                    return  # a repeated native admission never refreshes its source/timeout
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
        row = self.calls[call]
        if req['t'] == 'file_show' and native_shell is not None:
            row.update(native_shell=native_shell, native_live=native_live, decision_ready=asyncio.Event())
            if require_start:
                row['start_ready'] = asyncio.Event()
        if req['t'] == 'support_native':
            from .feedback_native import Binding
            row.update(input=copy.deepcopy(inp), registered_at=time.monotonic(), awaiting_native=prehook)
            if prehook:
                row['native_ready'] = asyncio.Event()
            try:
                row['feedback'] = Binding(self, row)
                self.feedback_seen.add((call, native))
            except (ValueError, AttributeError, OSError): self.calls.pop(call, None)
        while len(self.calls) > 64:
            self.calls.pop(next(iter(self.calls)))
        return self.calls.get(call)

    def observe_shell_start(self, call, native, shell):
        row = self.calls.get(call)
        if row is not None and row.get('native') == native and row.get('start_ready') is not None:
            if row['native_shell'] != shell:
                row['invalid_native_shell'] = True
            row['start_ready'].set()

    async def execute(self, tool, inp, call=None, gone=None):
        x = self.xfer
        candidates = [(cid, row) for cid, row in self.calls.items() if row['tool'] == tool
                      and (row['input'] == inp or row.get('rpc_input') == inp)]
        matches = [(cid, row) for cid, row in candidates if call == cid
                   or call is None and row.get('native_shell') is None and row['input'] == inp]
        if not matches:
            if any(row.get('native_shell') is not None for _, row in candidates):
                return {'behavior': 'deny', 'message': 'No matching native file-call admission'}
            missing = parse(tool, inp, (getattr(self.xfer.svc.host, 'agent_cfg', None) or {}).get('dir') or os.getcwd())
            if missing is None and self.feedback_applicable():
                from . import feedback_native
                missing = feedback_native.command(tool, inp, self.xfer.svc.host.agent_cfg.get('dir') or os.getcwd())
            if missing:
                host = self.xfer.svc.host
                if (missing['t'] == 'file_show' and 'delivery' not in missing
                        and getattr(host.agent, 'kind', None) == 'claude'
                        and (host.agent_cfg or {}).get('session_mode') != 'shared'):
                    from .file_show import HINT, OWNER_HINT
                    execution = getattr(x, 'execution', None)
                    owner = (execution and execution.live and execution.send is not None
                             and provenance.is_owner(provenance.turn_source(host.agent)))
                    return {'behavior': 'deny', 'message': HINT if owner else OWNER_HINT}
                return {'behavior':'deny', 'message':'No live native feedback admission' if missing['t'] == 'support_native' else 'No live native secret-call admission'}
            return None
        if len(matches) != 1:
            return {'behavior':'deny', 'message':'Ambiguous native secret call'}
        _, row = matches[0]
        if row.get('invalid_native_shell'):
            return {'behavior': 'deny', 'message': 'Conflicting native file-call evidence'}
        if row.get('awaiting_native'):
            return {'behavior':'deny', 'message':'No live native feedback admission'}
        if row['request']['t'] == 'support_native':
            return await self._approve_feedback(row, tool, inp, gone)
        if row.get('native_shell') is not None:
            return await self._decide_native_file(row, tool, inp, gone)
        return await self._execute_row(row, tool, inp, gone)

    async def _decide_native_file(self, row, tool, inp, gone):
        ready = row['decision_ready']
        if row.get('decision_started'):
            await ready.wait()
            answer = row['native_decision']
            if answer['behavior'] != 'allow':
                return answer
            if row['check']() and (gone is None or not gone.done()):
                return {'behavior': 'allow', 'updatedInput': inp}
            return {'behavior': 'deny', 'message': 'Native file call expired'}
        row['decision_started'] = True
        row['native_decision'] = {'behavior': 'deny', 'message': 'Native file call expired'}
        try:
            answer = await self._execute_row(row, tool, inp, gone)
            row['native_decision'] = answer
            return answer
        finally:
            ready.set()

    async def _execute_row(self, row, tool, inp, gone):
        x = self.xfer
        if row.get('admitted'):
            # PreToolUse observes the call even when native rules skip PermissionRequest.
            # A later PermissionRequest for the *same* file call must not invalidate it.
            if row['request']['t'] == 'file_show' and row['check']():
                return {'behavior':'allow', 'updatedInput':inp}
            return {'behavior':'deny', 'message':'Native secret call already admitted'}
        execution = row['execution']
        host = x.svc.host
        origin_binding = None
        def live():
            native_live = row.get('native_live')
            return (not row.get('invalid_native_shell') and (native_live is None or native_live())
                    and execution.live and getattr(x, 'execution', None) is execution
                    and execution.send is getattr(host.agent, 'cur_send', None) and not host.stopped()
                    and (origin_binding is None
                         or file_show.source_refusal(host, req, row['source'], execution.send, origin_binding) is None))
        if not live() or (gone is not None and gone.done()):
            return {'behavior':'deny', 'message':'Native secret call expired'}
        from . import policy_registry
        if policy_registry.main_requirement(tool, inp) == 'deny':
            return {'behavior':'deny', 'message':'Native secret call blocked by policy'}
        req = dict(row['request'])
        if req['t'] == 'file_show':
            from . import file_show
            page = host.hist.get(getattr(host, 'cur_turn', None))
            ids = next((ids for ids in host.hist.parts.values() if page and page['id'] in ids), [page['id']] if page else [])
            if (os.path.realpath((host.agent_cfg or {}).get('dir') or '') != row['project_root']
                    or not page or page.get('end') != 'open' or execution.send.turn not in ids):
                return {'behavior': 'deny', 'message': file_show.OWNER_HINT if 'delivery' not in req else file_show.SEND_FAILURE_HINTS['no_owner_session']}
            if provenance.owner_channel(row['source']) == 'wechat':
                origin_binding = getattr(execution.send, 'wechat_file_binding', None)
            if why := file_show.source_refusal(host, req, row['source'], execution.send, origin_binding):
                fallback = file_show.OWNER_HINT if 'delivery' not in req else file_show.SEND_FAILURE_HINTS['no_owner_session']
                return {'behavior': 'deny', 'message': file_show.WECHAT_FILE_HINTS.get(why) or file_show.SEND_FAILURE_HINTS.get(why, fallback)}
            path = os.path.abspath(os.path.join(req['cwd'], os.path.expanduser(req['path'])))
            if hint := file_show.generated_image_hint(host, row['project_root'], path):
                return {'behavior': 'deny', 'message': hint}
            if req.get('delivery') == 'wechat':
                from . import wechat_forward
                binding = origin_binding if origin_binding is not None else wechat_forward.file_binding(host)
                if binding is None:
                    return {'behavior': 'deny', 'message': '请先绑定微信，再在私聊中发一条消息。 / Connect WeChat, then send a message in the private chat first.'}
                try:
                    await asyncio.to_thread(wechat_forward.file_secret_check, row['project_root'], path)
                except (OSError, ValueError):
                    return {'behavior': 'deny', 'message': 'File delivery privacy check refused.'}
                if origin_binding is not None and not live():
                    return {'behavior': 'deny', 'message': file_show.SEND_FAILURE_HINTS['binding_changed']}
                with owner_call.request(live), provenance.request_source(row['source']):
                    answer = await host.ask(tool, inp, gone, batch=False)
                if answer.get('behavior') != 'allow':
                    return answer
                if not live() or not wechat_forward.file_binding_live(host, binding):
                    return {'behavior': 'deny', 'message': file_show.SEND_FAILURE_HINTS['binding_changed']}
                row['file_delivery'] = binding
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

    async def _approve_feedback(self, row, tool, inp, gone):
        from . import approval_review, feedback_native, policy_registry
        binding = row.get('feedback')
        stale = {'behavior': 'deny', 'message': 'Original native feedback call expired.'}

        def live():
            return bool(binding and binding.live() and (gone is None or not gone.done()))

        if not live():
            return stale
        if policy_registry.main_requirement(tool, inp) == 'deny':
            return {'behavior': 'deny', 'message': 'Native feedback call blocked by policy.'}
        previous = row.get('feedback_decision')
        if previous is not None:
            if not row.get('observed_start') or 'rpc_input' not in row:
                return {'behavior': 'deny', 'message': 'Native feedback call already evaluated.'}
            answer = await asyncio.shield(previous)
            return answer if live() else stale
        if row.get('issued'):
            return stale
        decision = row['feedback_decision'] = asyncio.get_running_loop().create_future()
        answer = stale
        try:
            try:
                binding.bind_body(await asyncio.to_thread(feedback_native.payload, row['request'], binding.root))
            except (OSError, ValueError, UnicodeError):
                answer = {'behavior': 'deny', 'message': 'Cannot bind the exact feedback draft.'}
                return answer
            if not live():
                return stale
            with owner_call.request(binding.live), provenance.request_source(binding.source), approval_review.origin_scope(binding.origin):
                answer = await binding.host.ask(tool, inp, gone, batch=False)
            if answer.get('behavior') == 'allow':
                if not live():
                    answer = stale
                else:
                    row.update(admitted=True, ticket=secrets.token_hex(16), check=binding.live)
            return answer
        finally:
            decision.set_result(answer)

    def complete(self, call, success, output, tool=None, inp=None, cwd=None, native=None, child=False, *, native_shell=None):
        row = self.calls.get(call)
        if child:
            return  # a sub-agent result never completes the main thread's call
        if row and row.get('native_ready') and row.get('native') == native and not row.get('issued'):
            row['feedback'].closed = True
            row['native_ready'].set()
            self.calls.pop(call)
            return
        if row and row.get('native_shell') is not None and row.get('native') == native and not row.get('issued'):
            row['invalid_native_shell'] = True
            if row.get('start_ready') is not None:
                row['start_ready'].set()
            self.calls.pop(call)
            return
        if not row or not row.get('admitted') or not row.get('issued') or row.get('native') != native:
            return
        if tool is not None and (tool != row['tool'] or inp != row['input'] or os.path.abspath(cwd or '') != row['request']['cwd']):
            return
        self.calls.pop(call)
        if not success or (row.get('native_shell') is not None and row['native_shell'] != native_shell):
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
                    await asyncio.to_thread(host_diagnostics.execute, x.svc.host.st, request, _support_binding=binding)
            finally:
                try:
                    binding.report()
                finally:
                    binding.closed = True
            return
        with owner_call.request(row['check']), provenance.request_source(row['source']):
            from . import file_show
            if req['t'] == 'file_show':
                result = await file_show.attach(x.svc.host, req, row['check'], row['project_root'], row.get('file_delivery'))
            elif req['t'] == 'tell':
                result = await x.svc.tell(req['friend'], req['text'], grant=req['grant'])
            else:
                result = await x.cli(req, req['cwd'])
        if req['t'] != 'file_show':
            self.receipts[key(req)] = (time.monotonic() + 120, result)
        elif req.get('delivery') == 'wechat':
            why = result.get('why')
            if hint := file_show.SEND_FAILURE_HINTS.get(why) or file_show.WECHAT_FILE_HINTS.get(why):
                from .text import one
                result.setdefault('hint', one(x.svc.host.lang, hint))
            self._record_wechat_result(row, result)
        while len(self.receipts) > 64:
            self.receipts.pop(next(iter(self.receipts)))
        x.svc.host.agent_notice(('FILE_SEND_RESULT: ' if req.get('delivery') == 'wechat' else 'FILE_SHOW_RESULT: ' if req['t'] == 'file_show' else 'SECRET_TASK_RESULT: ') + json.dumps(result, ensure_ascii=False))

    def feedback_receipt(self, req, peer):
        """Only an exact admitted payload produces a pending result; never HTTP here."""
        from . import feedback_native
        value = feedback_native.canonical(req)
        rows = [r for r in self.calls.values() if r.get('feedback') and r.get('admitted')
                and r['feedback'].body == value and r['feedback'].live()]
        if len(rows) != 1:
            return feedback_native.not_sent('draft_invalid' if value is None else 'admission_ambiguous' if rows else 'admission_missing')
        row = rows[0]
        if not row['feedback'].peer(peer):
            return feedback_native.not_sent('caller_unverified')
        if row.get('issued'):
            return feedback_native.not_sent('already_pending')
        row['issued'] = True
        return {'exit':0, 'output':'SUPPORT_TASK_PENDING: ' + json.dumps(_pending(row),sort_keys=True) + '\n'}

    async def drain(self):
        if self.jobs:
            await asyncio.gather(*list(self.jobs))

    async def file_receipt(self, req):
        """Wait only on exact pending calls already observed on a native channel."""
        async def ready(row):
            await row['decision_ready'].wait()
            if not row.get('admitted'):
                return False
            if row.get('start_ready') is not None:
                await row['start_ready'].wait()
            return row['check']()

        execution = getattr(self.xfer, 'execution', None)
        pending = {asyncio.create_task(ready(row)) for row in self.calls.values()
                   if row.get('decision_ready') is not None and row['execution'] is execution
                   and execution.live and row['request'] == req and not row.get('invalid_native_shell')}
        try:
            async with asyncio.timeout(25):
                # A native call that was never started must not hold up a later valid call.
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    if any(task.result() for task in done):
                        break
        except TimeoutError:
            from .file_show import failure
            return failure('native_admission_expired')
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        if req.get('delivery') != 'wechat':
            return self.receipt(req)
        from . import file_show, wechat_forward
        row, denied = self._receipt_row(req)
        if denied:
            return denied
        host = self.xfer.svc.host
        result = await asyncio.to_thread(file_show.send_preflight, host, req, row['project_root'])
        current, denied = self._receipt_row(req)
        if denied or current is not row:
            return denied or file_show.failure('native_admission_expired')
        if not wechat_forward.file_binding_live(host, row['file_delivery']):
            return file_show.failure('binding_changed')
        if result:
            self._record_wechat_result(row, result)
            return result
        row['issued'] = True
        result = _pending(row)
        self._record_wechat_result(row, result)
        return result

    @staticmethod
    def _record_wechat_result(row, result):
        binding = row['file_delivery']
        if binding.get('inbound'):
            from .file_show import request_path
            binding['adapter'].record_file_result(row['execution'].send, binding, row['ticket'], request_path(row['request']), result)

    def receipt(self, req):
        if req.get('op') == 'grants' and not req.get('revoke'):
            return self.xfer._list(req)
        receipt = self.receipts.pop(key(req), None)
        if receipt and receipt[0] > time.monotonic():
            return receipt[1]
        row, denied = self._receipt_row(req)
        if denied:
            return denied
        row['issued'] = True
        return _pending(row)

    def _receipt_row(self, req):
        matching = [row for row in self.calls.values() if row.get('admitted') and key(row['request']) == key(req)]
        rows = [row for row in matching if row['check']() and (row.get('start_ready') is None or row['start_ready'].is_set())]
        if len(rows) != 1:
            if req.get('t') == 'file_show':
                execution = getattr(self.xfer, 'execution', None)
                candidates = [row for row in self.calls.values() if row.get('admitted')
                              and row['request'].get('t') == 'file_show']
                why = ('native_admission_ambiguous' if len(rows) > 1 else 'native_admission_expired' if matching
                       else 'native_admission_cwd_mismatch' if any(
                           {k: v for k, v in row['request'].items() if k != 'cwd'} ==
                           {k: v for k, v in req.items() if k != 'cwd'} for row in candidates)
                       else 'native_admission_request_mismatch' if candidates
                       else 'native_admission_missing' if execution and execution.live and execution.send is not None
                       else 'no_owner_session')
                return None, {'result': 'failure', 'why': why, 'client_downloaded': False}
            return None, {'result':'refused', 'why':'caller', **({'ok':False} if req.get('t') == 'tell' else {})}
        return rows[0], None


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
