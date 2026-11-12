"""#579 main-session work budget. Advisory only since 0.30.2 (#0302): it adds a reminder, never denies and never grants.

The chairman's rule: outside the UV fingerprint list Agent J does not block what the harness would allow."""
import asyncio
import os
import secrets
import time
from . import controls, danger, preferences

# Replies and status reads to the owner's other devices / friends are the main thread's job, not implementation work.
REPLY = frozenset(('tell','list','history','status','reports','asks'))
DELEGATE = frozenset(('Agent','Task','TaskOutput','TaskStop','AskUserQuestion','spawn_agent','send_message','followup_task','wait_agent','agent.dispatch','agent.session','file.read','web.search'))
WORK = frozenset(('Write','Edit','MultiEdit','Delete','NotebookEdit','apply_patch'))
MESSAGE = {
    'zh': '主线程工作已超过预算。请把实现、构建或开发测试派给 CEO / 子 Agent，主线程保留派发、监督、验收和回复主人。可先运行 `agentj busy run --help` 查看现成派活命令。',
    'en': 'Main-thread work exceeded its budget. Delegate implementation, builds or development tests to a CEO / sub-agent; keep dispatch, supervision, acceptance and owner replies here. Run `agentj busy run --help` for the existing dispatch command.',
}


def work_call(tool, inp):
    """Existing read classifier + bounded shell parsing; no free-text intent whitelist."""
    if tool in DELEGATE:
        return False
    if tool in ('exec_command','shell_command','execute_command','bash','shell'):
        tool, inp = 'Bash', {'command': (inp or {}).get('command',(inp or {}).get('cmd',''))}
    if tool == 'write_stdin' and not (inp or {}).get('chars'):
        return False
    if tool == 'exec' and 'code' in (inp or {}) and 'command' not in inp and 'cmd' not in inp:
        return False  # code-mode wrapper: nested actual tool hooks carry the work
    if tool == 'exec':
        tool, inp = 'Bash', {'command':(inp or {}).get('command',(inp or {}).get('cmd',''))}
    if tool in WORK:
        return True
    if danger.readonly(tool, inp)[0]:
        return False
    if tool != 'Bash':
        return True
    command = inp.get('command') if isinstance(inp, dict) else None
    if not isinstance(command, str) or not command.strip():
        return False
    parts = []
    commands = danger.lex(command)
    if not commands:
        return True
    for parsed in commands:
        words = parsed.words
        if (words and os.path.basename(words[0]) == 'agentj' and not parsed.redirs and not parsed.subst and not parsed.heredoc
                and (words[1:3] == ['busy','run'] or len(words) > 1 and words[1] in ('status','doctor','validate','handoff')
                     or len(words) > 2 and words[1] == 'friends' and words[2] in REPLY)):
            continue
        unpacked = danger.unwrap(parsed)
        if not unpacked:
            return True
        parts.extend(unpacked)
    for part in parts:
        if part.flags & {'inline','eval','piped_shell','remote'} or part.redirs:
            return True
        words = [part.name, *part.args]
        name = os.path.basename(part.name)
        # Dispatch is exempt, but shell side effects alongside it are not.
        if name == 'agentj' and len(words) > 2 and words[1:3] == ['busy','run']:
            continue
        if name == 'agentj' and len(words) > 1 and words[1] in ('status','doctor','validate','handoff'):
            continue
        if name == 'agentj' and len(words) > 2 and words[1] == 'friends' and words[2] in REPLY:
            continue
        if name == 'relay' and len(words) > 1 and words[1] in ('validate','qa','doctor','handoff'):
            continue
        from .peer_origin import NOTES
        if name in ('python','python3','python3.11','python3.13','python3.14') and len(words) > 1:
            target = words[1]
            if target in (NOTES + '/p44-gates.py', NOTES + '/precheck.py'):
                continue
        if part.raw and part.raw[0] in (NOTES + '/precheck.sh', './' + NOTES + '/precheck.sh'):
            continue
        # Test runners count as work; only the named acceptance entrypoints above are exempt.
        if not danger.readonly('Bash', {'command': __import__('shlex').join(words)})[0]:
            return True
    return False


def interrupt_route(adapter):
    """Only native routes whose current-turn target is bounded for this new control."""
    from .agent_codex import CodexAgent
    from .agent_opencode import OpenCodeAgent
    from .shared import SharedClaudeAgent
    if isinstance(adapter,CodexAgent):
        done=getattr(adapter,'turn_done',None)
        if (getattr(adapter,'proc',None) is not None and getattr(adapter,'tid',None)
                and getattr(adapter,'turn_id',None) and done is not None and not done.is_set()):
            return True,''
        return False,'native_turn_unavailable'
    if isinstance(adapter,SharedClaudeAgent):
        session=getattr(adapter,'session',None);native=getattr(adapter,'native',None)
        if (isinstance(session,dict) and native is not None and getattr(native,'pid',None)==session.get('pid')
                and getattr(adapter,'master',None) is not None):
            if (getattr(adapter.state,'status',None)=='waiting' or getattr(adapter,'control_name',None)
                    or getattr(getattr(adapter,'menu_desk',None),'current',None)):
                return False,'native_confirmation_pending'
            return True,''
        return False,'native_turn_binding_unavailable'
    if isinstance(adapter,OpenCodeAgent):
        return False,'native_turn_binding_unavailable'
    return False,'native_turn_binding_unavailable'


class Guard:
    def __init__(self, adapter, clock=time.monotonic):
        self.adapter, self.clock = adapter, clock
        self.reset()
        self.waiting_at = None

    def options(self):
        a = self.adapter
        cfg = getattr(a, 'cfg', {})
        prefs = getattr(a.host, 'preferences', {})
        enabled = preferences.get(prefs,'agent.main_thread_guard',cfg.get('main_thread_guard',True)) is not False
        calls = preferences.get(prefs,'agent.main_thread_work_calls',cfg.get('main_thread_work_calls',8))
        seconds = preferences.get(prefs,'agent.main_thread_seconds',cfg.get('main_thread_seconds',180))
        return enabled, calls if type(calls) is int and 2 <= calls <= 1000 else 8, seconds if type(seconds) is int and 30 <= seconds <= 3600 else 180

    def reset(self):
        self.session = secrets.token_hex(12)
        self.started = None
        self.calls = 0
        self.warned = self.blocked = False
        self.waiting_at = None

    def exempt(self, event=None):
        a = self.adapter
        return (bool((event or {}).get('agent_id')) or a.cfg.get('_workflow_ceo') is True
                or getattr(getattr(a,'cur_send',None),'by',None) == 'heartbeat'
                or getattr(a,'status',None) == 'compacting'
                or bool(getattr(getattr(a,'state',None),'compacting',False)))

    def background(self):
        a = self.adapter
        if getattr(a,'bg_live',None) or getattr(a,'sub_agents',None):
            return True
        if any(not row.get('done') for row in (getattr(a,'children',{}) or {}).values() if isinstance(row,dict)):
            return True
        busy = getattr(a.host,'busy',None)
        return bool(busy and any(row.get('status') in ('running','stalled') for row in busy.items()))

    def observe(self, event):
        if event.get('agent_id'):
            return
        if event.get('hook_event_name') in ('UserPromptSubmit','Stop','SessionEnd','PreCompact','PostCompact'):
            self.reset()

    def snapshot(self):
        enabled, limit, seconds = self.options()
        elapsed = max(0,int(self.clock()-self.started)) if self.started is not None else 0
        busy = enabled and self.started is not None and (self.calls >= limit or elapsed >= seconds)
        a = self.adapter
        available = not (getattr(a,'kind',None) == 'opencode' and getattr(a,'v2',False)) or getattr(a,'_main_dispatch_available',False) is True
        interrupt_ok,interrupt_reason=interrupt_route(a)
        return {'enabled':enabled,'available':available,'reason':'' if available else 'guard_pending',
                'busy':busy,'elapsed_s':elapsed,'work_calls':self.calls,
                'warned':self.warned,'blocked':self.blocked,'owner_waiting':self.waiting_at is not None,
                'waiting_s':max(0,int(self.clock()-self.waiting_at)) if self.waiting_at is not None else 0,
                'can_interrupt':bool(enabled and interrupt_ok and (busy or self.waiting_at is not None)),
                'interrupt_reason':interrupt_reason,
                'turn':getattr(a.host,'cur_turn',None),'session':self.session}

    def changed(self):
        fn = getattr(self.adapter.host,'status_changed',None)
        if callable(fn):
            fn()

    def check(self, tool, inp, event=None):
        enabled, limit, seconds = self.options()
        if not enabled or self.exempt(event) or not work_call(tool,inp) or self.background():
            return {}
        if self.started is None:
            self.started = self.clock()
        self.calls += 1
        if self.calls < limit and self.clock()-self.started < seconds:
            return {}
        # #0302: remind once at the threshold and again every `limit` work calls; never deny (blocked stays False).
        if self.warned and (self.calls - limit) % limit:
            return {}
        a = self.adapter
        message = MESSAGE['en' if str(getattr(a.host,'lang','zh')).startswith('en') else 'zh']
        self.warned = True
        emit = getattr(a.host,'emit',None)
        if callable(emit):
            emit('main_thread_guard', result='remind', calls=self.calls)
        self.changed()
        return {'hookSpecificOutput':{'hookEventName':'PreToolUse','additionalContext':message}}


def of(adapter):
    guard = getattr(adapter,'main_thread_guard',None)
    if not isinstance(guard, Guard):
        guard = adapter.main_thread_guard = Guard(adapter)
    return guard


def status(host):
    a = getattr(host,'agent',None)
    guard = getattr(a,'main_thread_guard',None)
    return guard.snapshot() if guard is not None else None


def completed(host):
    guard = getattr(getattr(host, "agent", None), "main_thread_guard", None)
    if isinstance(guard, Guard):
        guard.reset()
        guard.changed()


def owner_queued(host):
    a = getattr(host,'agent',None)
    if a is None or getattr(a,'status',None) not in ('working','waiting','compacting'):
        return
    guard = of(a)
    if guard.waiting_at is None:
        guard.waiting_at = guard.clock()
    guard.changed()


class InterruptTarget:
    """Host-held generation and native turn snapshot; never decoded from wire fields."""
    def __init__(self, host, adapter, guard, owner_session):
        self.host, self.adapter, self.guard = host, adapter, guard
        self.owner_session, self.state = owner_session, host.st
        self.signer = host.st.sign_key(owner_session.device)
        self.turn, self.generation = host.cur_turn, guard.session
        self.proc, self.tid, self.turn_id = (getattr(adapter,k,None) for k in ('proc','tid','turn_id'))
        self.done, self.queue = getattr(adapter,'turn_done',None), getattr(adapter,'q',None)
        self.native_session=getattr(adapter,'session',None)
        self.session_ids={k:self.native_session.get(k) for k in ('pid','sessionId')} if isinstance(self.native_session,dict) else None
        self.master,self.native,self.claude_done=(getattr(adapter,k,None) for k in ('master','native','done'))

    def live(self, adapter):
        h = self.host
        return (adapter is self.adapter and h.agent is adapter and h.st is self.state
                and getattr(adapter,'main_thread_guard',None) is self.guard and h.cur_turn == self.turn
                and self.guard.session == self.generation and h._paired_live(self.owner_session)
                and h.st.sign_key(self.owner_session.device) == self.signer and not h.stopped() and not h.seat_refusal()
                and getattr(adapter,'proc',None) is self.proc and getattr(adapter,'turn_done',None) is self.done
                and getattr(adapter,'q',None) is self.queue and getattr(adapter,'tid',None) == self.tid
                and getattr(adapter,'turn_id',None) == self.turn_id
                and getattr(adapter,'session',None) is self.native_session
                and (self.session_ids is None or {k:self.native_session.get(k) for k in ('pid','sessionId')}==self.session_ids)
                and getattr(adapter,'master',None)==self.master and getattr(adapter,'native',None) is self.native
                and getattr(adapter,'done',None) is self.claude_done)


async def interrupt(host, session, obj):
    req = obj.get('request')
    why = 'unpaired' if not host._paired_live(session) else controls.check(
        host.st,host.nonces,host.channel,session.device,obj,'main_thread_interrupt',{'request':req})
    controls.log(host.st,action='main_thread_interrupt',device=session.device,result=why or 'verified',obj={'request':req})
    a = getattr(host,'agent',None)
    guard = getattr(a,'main_thread_guard',None)
    if not why and (not isinstance(req,dict) or guard is None or req != {'turn':host.cur_turn,'session':guard.session}):
        why = 'stale'
    if not why and (host.stopped() or host.seat_refusal() or not guard.snapshot()['can_interrupt']):
        why = guard.snapshot().get('interrupt_reason') or 'unavailable'
    ok = False
    if not why:
        target = InterruptTarget(host,a,guard,session)
        async def halt_target():
            if not target.live(a):
                return False
            if not interrupt_route(a)[0]:return False
            return await a.halt(clear_queue=False,expected_target=target)
        try:
            # Do not call Host.stop_turn(): it stops scheduled/background jobs too.
            a.halt_quiet = True
            ok = bool(await asyncio.wait_for(halt_target(),5))
        except (Exception, asyncio.TimeoutError):
            why = 'failed'
        finally:
            a.halt_quiet = False
        if ok and target.live(a):
            guard.reset()
            host.status_changed()
    await host.send_app(session,{'t':'main_thread_interrupt_res','r':obj.get('r'),'ok':ok,
                                **({'why':why or 'native_unavailable'} if not ok else {})})
