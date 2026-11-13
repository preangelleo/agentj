"""Explicit-list approval adapter; existing wire signatures and UV verifier remain owners."""
import asyncio
import copy
import json
import os
from dataclasses import dataclass
import secrets
import time
from . import approval_review, approvals, policy_registry, uv_list


@dataclass(frozen=True)
class SettingsContext:
    """Host-held AuthRescue settings subject, never a field accepted from a tool."""
    host: object
    owner: object
    session: object
    input_sha: str
    nonce: str
    until: float
    binding: object
    page: tuple = ('AI 账户登录', 'AI account login')   # 0.30.7: the Settings page this context belongs to
    @classmethod
    def capture(cls, host, owner, session, inp):
        if owner is not getattr(host, 'auth_rescue', None) or not host._paired_live(session):
            raise ValueError('settings session changed')
        item = cls(host, owner, session, approvals.input_digest(inp), secrets.token_hex(16), time.monotonic() + host.ask_ttl, None)
        object.__setattr__(item, 'binding', item.current(host))
        book = host.__dict__.setdefault('_auth_settings_requests', {})
        for key, old in list(book.items()):
            if time.monotonic() >= old.until:
                book.pop(key, None)
        if len(book) >= 64:
            raise ValueError('settings requests full')
        book[item.nonce] = item
        return item

    def current(self, host):
        session = self.session
        if (host is not self.host or getattr(host, 'auth_rescue', None) is not self.owner
                or not host._paired_live(session) or host.stopped()
                or host.stopping.is_set()):
            return None
        pk = host.st.passkey_of_device(session.device)
        public = {key: pk.get(key) for key in ('id', 'alg', 'spki', 'rp')} if pk else None
        return (host.st, host.channel, str(host.hist.epoch), host.kp.pub, session.cid, session.device, session.pub,
                host.st.sign_key(session.device), approval_review.digest(public))

    def live(self, host):
        return (time.monotonic() < self.until and self.binding is not None
                and self.current(host) == self.binding
                and host.__dict__.get('_auth_settings_requests', {}).get(self.nonce) is self)

    def discard(self):
        self.host.__dict__.get('_auth_settings_requests', {}).pop(self.nonce, None)

    def origin(self, summary):
        return {'session': self.binding[2], 'message_id': None, 'root_message_id': None,
                'channel': 'phone', 'sender': '', 'text': summary, 'verified': True,
                'suspicious': False, 'initiator': '遥控器设置 / Remote settings', 'chain': []}


@dataclass(frozen=True)
class NativeContext:
    """In-process capability; never deserialized from a tool input or wire field."""
    adapter: object
    process: object
    cwd: str
    session: str

    @classmethod
    def capture(cls, adapter):
        return cls(adapter, getattr(adapter, 'proc', None), os.path.realpath(adapter.cfg['dir']),
                   str(getattr(adapter, 'tid', None) if adapter.kind == 'codex' else getattr(adapter, 'sid', None)))

    def live(self, host):
        a = self.adapter
        return (getattr(a, 'host', None) is host and getattr(a, 'proc', None) is self.process
                and os.path.realpath(a.cfg['dir']) == self.cwd
                and str(getattr(a, 'tid', None) if a.kind == 'codex' else getattr(a, 'sid', None)) == self.session)


@dataclass(frozen=True)
class PermissionContext:
    """A claimed local permission connection with a launch-time cwd, not wire cwd."""
    host: object
    writer: object
    cwd: str
    process: object = None

    @property
    def session(self):
        return 'permission-connection:' + str(id(self.writer))

    def live(self, host):
        return host is self.host and host.perm_w is self.writer


async def ask(host, Ask, tool, inp, *, gone=None, ttl=None, grant_info=False,
              receipt=None, only=None, host_card=False, settings_session=None, bounded_context=None, harness_permission=False, task_actions=None, task_target=None, native_context=None, settings_context=None, front=None, background=None, order_owner=False):
    # JSON from a native transport must normalize before inventory/path checks,
    # settings-input hashes or display hashes. Never echo exception repr/values.
    try:
        if not isinstance(tool, str) or not isinstance(inp, dict):
            raise TypeError('shape')
        tool.encode('utf-8')
        input_json = approval_review.canonical(inp)
        input_json.encode('utf-8')
        for value in (task_actions, task_target):
            if value is not None:
                approval_review.canonical(value).encode('utf-8')
    except (ValueError, TypeError, RecursionError, OverflowError):
        if isinstance(receipt, dict):
            receipt.update(reason='invalid_input', approval_card_shown=False)
        host.st.log('ask_done', decision='deny', reason='invalid_input')
        return {'behavior':'deny', 'reason':'invalid_input',
                'message':'请求内容格式无效，未执行 / Invalid request data; no operation executed.'}
    if settings_context is not None and (type(settings_context) is not SettingsContext
            or tool != 'AuthRescue' or not host_card or settings_context.session is not settings_session
            or only != settings_session.device or settings_context.input_sha != approvals.input_digest(inp)
            or not settings_context.live(host)):
        return {'behavior': 'deny', 'reason': 'session_changed'}
    if native_context is not None and (not isinstance(native_context, (NativeContext, PermissionContext)) or not native_context.live(host)):
        return {'behavior':'deny', 'reason':'native_context_changed', 'message':'Native session context changed; no operation executed.'}
    cwd = native_context.cwd if native_context is not None else (getattr(host, 'agent_cfg', None) or {}).get('dir')
    uv = uv_list.required(tool, inp, cwd=cwd, env=uv_list.host_environment(host))
    fingerprint = headless_fingerprint = headless_src = None
    if (not uv and settings_context is None and task_actions is None
            and (harness_permission or tool not in policy_registry.HOST_TOOLS)):
        # Parsed argv + a few stat calls on the closed credential inventory, like charge_command / required() above.
        fingerprint = uv_list.fingerprint_command(tool, inp, cwd=cwd, env=uv_list.host_environment(host), host=host)
    if fingerprint:
        # #537② + ADR-0308-fingerprint-scope (chairman 2026-11-11): an off-list call that moves money
        # (uv_list.CHARGE_COMMANDS) or cannot be undone (uv_list.IRREVERSIBLE) gets the fingerprint card — on the MAIN
        # thread only (owner turns: uv_list.FINGERPRINT_SOURCES) and only where the off-list decision
        # itself would allow it (credential / private-state reads and unresolved launchers stay refused).
        # 0.30.11: host-held background jobs, scheduled runs and heartbeat have no runtime fingerprint gate,
        # irrespective of how they were enabled or resumed. Existing automations must not be re-approved.
        # host_read / external / unknown retain the ordinary off-list write refusal.
        from . import approval_offlist
        typed = approval_offlist.source(host, settings_context=settings_context, background=background,
                                        order_owner=order_owner)
        if typed.get('type') in uv_list.FINGERPRINT_SOURCES and typed.get('trust') in ('owner', 'host'):
            decided = await asyncio.to_thread(approval_offlist.decide, host, tool, inp, cwd, typed)
            uv = decided[0] == 'allow'
        else:
            headless_fingerprint, headless_src = fingerprint, typed
    def refused(reason, message):
        # No card was displayed. Keep the original request's metadata-only
        # audit linkage without collecting source text or inventing a signer.
        rid = secrets.token_hex(16)
        agent = getattr(host.agent, 'kind', '?')
        approvals.record(host.st, rid=rid, agent=agent, tool=tool,
            input_sha256=approvals.input_digest(inp),
            shown_sha256=approvals.shown_digest(tool, '[not displayed]'),
            decision='deny', reason=reason, cats=[], task_id=host._task_id())
        if isinstance(receipt, dict):
            receipt.update(id=rid, reason=reason, approval_card_shown=False)
        host.st.log('ask_done', id=rid, agent=agent, tool=tool, decision='deny', reason=reason)
        return {'behavior':'deny', 'message':message, 'reason':reason}
    if host.stopping.is_set():
        return refused('serve_stop', 'Agent J serve has stopped; no operation executed.')
    if host.stopped():
        return refused('estop', '已急停，未执行 / Stop everything is on; no operation executed.')
    resume_context = approval_review.digest({'only':only,'host_card':bool(host_card),
        'harness_permission':bool(harness_permission),'task_actions':task_actions,'task_target':task_target,
        'cwd':os.path.realpath(cwd) if cwd else None,
        'native_session':native_context.session if native_context else None,
        'native_process':getattr(native_context.process,'pid',None) if native_context else None})
    resumed = take_resume(host,tool,inp,uv,resume_context,settings_session)
    if resumed is not None:
        if isinstance(receipt,dict):
            receipt.update(reason='request_continuation',approval_card_shown=False)
        return {'behavior':'allow','updatedInput':inp,**({'grant_info':resumed} if grant_info else {})}
    # 0.30.7 ADR-0307-offlist: the card list is uv_list.required() + approval_offlist.CARD_LIST (host executors'
    # own explicit cards; a native request may only name AskUserQuestion) + legacy exact-action plans. Anything
    # else never opens a card from any source: answered here, audited, with the reason when refused.
    from . import approval_offlist
    listed = bool(uv) or task_actions is not None or tool in (approval_offlist.NATIVE_CARDS if harness_permission
                                                              else approval_offlist.CARD_LIST)
    if not listed and (harness_permission or tool not in policy_registry.HOST_TOOLS):
        return await approval_offlist.answer(host, tool, inp, cwd, receipt=receipt, settings_context=settings_context,
                                             background=background, fingerprint=headless_fingerprint, src=headless_src)
    # Host commands that are not on the list add no second permission layer.
    # Native permission requests reach this adapter only after the harness asks.
    if not harness_permission and not listed and tool in policy_registry.HOST_TOOLS and task_actions is None:
        from . import application_ingress
        result = application_ingress.ordinary(host, tool, inp)
        if result['behavior'] != 'allow':
            return refused(result['reason'], 'No live authenticated application request; no operation executed.')
        if isinstance(receipt, dict):
            receipt.update(reason='harness', approval_card_shown=False)
        return result
    if not host._approvers():
        return refused('no_device', '没有能批准的已配对手机 / No paired remote can approve this request.')
    # Only concrete Host executors own these historical display/proof strings.
    # Native tool parameters may contain a model-authored summary: always show
    # their actual canonical arguments instead of trusting that field.
    host_summary = (not harness_permission and tool in ('agentj mail workflow-grant','agentj automation grant','agentj secret grant','AuthRescue','AgentJBoundedPlan')
                    and isinstance(inp.get('summary'),str))
    # Privacy scanning is awaited I/O. Pin the request's real subject before
    # yielding; an old native request cannot bind to the next owner turn.
    if settings_context is None:
        host._task_id()  # initialize the existing task epoch before its snapshot
    request_agent = host.agent
    request_epoch = str(host.hist.epoch)
    request_authority = settings_context.binding if settings_context else host._approval_live_authority()
    request_origin = None if settings_context else copy.deepcopy(approval_review.snapshot(host))
    request_typed = approval_offlist.source(host, settings_context=settings_context, background=background)
    def subject_live():
        return (not host.stopped() and not host.stopping.is_set()
                and (gone is None or not gone.done())
                and (native_context is None or native_context.live(host))
                and (settings_context.live(host) if settings_context else
                     host.agent is request_agent and str(host.hist.epoch) == request_epoch
                     and host._approval_live_authority() == request_authority))
    if not subject_live():
        return refused('authority_changed', 'Request authority changed; no operation executed.')
    from . import action_policy
    try:
        # This is display-only: the untouched inp still feeds inventory, frozen
        # executor inputs and input_sha. Never redact an already signed frame.
        display = await asyncio.to_thread(action_policy.display_normalizer, lang=host.lang, workdir=cwd)
        if not harness_permission and tool == 'agentj secret grant':
            from .owner_grant import typed_plan_display
            display = typed_plan_display(inp, display)
        safe_input = display(inp)
        raw_summary = safe_input['summary'] if host_summary else tool + '\n' + (('Working directory: '+cwd+'\n') if native_context else '') + approval_review.canonical(safe_input)
    except (ValueError, TypeError, RecursionError, OSError):
        return refused('invalid_display', '无法安全显示请求，未执行 / Request cannot be displayed safely; no operation executed.')
    if not subject_live():
        return refused('authority_changed', 'Request authority changed; no operation executed.')
    if len(raw_summary.encode()) > 24000:
        return refused('too_large', '审批内容过长，无法完整显示 / Approval is too large to display.')
    # JSON serialization already quotes controls. Avoid a second regex pass
    # that can eat unrelated query/form effects such as &scope=write.
    summary = raw_summary
    if len(summary.encode()) > 24000:
        return refused('too_large', '审批内容过长，无法完整显示 / Approval is too large to display.')
    plan_scope = None
    if task_actions is not None:
        try:
            plan_scope, lines = task_scope(host,tool,inp,task_actions,task_target)
            summary += '\n具体动作 / Exact actions:\n' + '\n'.join(str(i+1)+'. '+line for i,line in enumerate(lines))
        except (ValueError, TypeError, KeyError, RecursionError):
            return refused('invalid_plan','任务动作清单无效 / Invalid task action plan.')
    if len(summary.encode()) > 24000 or (plan_scope is not None and len(summary.encode())+len(plan_scope.encode()) > 14000):
        return refused('too_large','审批清单过长，无法完整显示 / Approval plan is too large to display.')
    # Preserve the Host-owned Settings source record independently of display.
    origin_summary = summary
    try:
        # Freeze the exact complete wire text before either signature digest.
        # bind must not project it again or truncate executable input details.
        summary = approval_review.display_text(summary, display)
    except (ValueError, TypeError, RecursionError, OSError):
        return refused('invalid_display', '无法安全显示请求，未执行 / Request cannot be displayed safely; no operation executed.')
    if len(summary.encode('utf-8')) > 24000 or (plan_scope is not None and len(summary.encode('utf-8'))+len(plan_scope.encode('utf-8')) > 14000):
        return refused('too_large','审批清单过长，无法完整显示 / Approval plan is too large to display.')
    rid = secrets.token_hex(16)
    digest = approvals.shown_digest(tool, summary)
    isha = approvals.input_digest(inp)
    a = Ask(rid, tool, summary, digest, inp, isha,
            time.monotonic() + (ttl if ttl else host.ask_ttl),
            asyncio.get_running_loop().create_future(), require_passkey=uv,
            only=only, host_card=host_card, settings_session=settings_session,
            reason=tool)
    a.list_model = True
    a._list_display_snapshot = summary
    a.display_normalizer = display
    a.settings_context = settings_context
    a.list_resume_context = resume_context
    a.task_scope = plan_scope
    a.tg_plan_only = task_actions is not None
    a.bounded_plan = bounded_context
    # 0304 QA scene 3: a host-summary card (host-authored text, verbatim) may carry a host-built one-line front
    # (title / reason, zh + en). Display only: it joins the signed envelope; the details stay the exact summary.
    a.front = ({k: v for k, v in front.items() if k in ('title', 'title_en', 'reason', 'reason_en') and isinstance(v, str)}
               if (host_summary or (tool == 'agentj mail send' and inp.get('friend_handoff'))) and isinstance(front, dict) else None)
    if bounded_context is not None:
        bounded_context.rid = a.rid
    # A source label never grants authority. Unknown/external labels are retained
    # honestly; the paired signature and (for list entries) fresh UV decide.
    approval_review.bind(host, a, origin=settings_context.origin(origin_summary) if settings_context else request_origin,
                         typed=request_typed)
    a.review_live_authority = request_authority
    a.review_request_source = approval_review.digest(request_origin) if request_origin is not None else approval_review.digest(approval_review.snapshot(host))
    host.asks[rid] = a
    base = dict(rid=rid, agent=getattr(host.agent,'kind','?'), tool=tool,
                input_sha256=isha, shown_sha256=digest, cats=[])
    host.st.log('ask', id=rid, tool=tool, kind='danger' if uv else 'low', tier='uv' if uv else 'hold',
                source_type=request_typed['type'], trust=request_typed['trust'], card=True)
    host.activity('ask', id=rid, tool=tool, cats=[], summary=summary, task=host.task_label)
    host.emit('ask', id=rid, tool=tool, summary=summary)
    host._post(lambda fn: host._send_ready(lambda s: fn(s) if not only or only == s.device else None),
               lambda s: host._ask_msg(a,s))
    host.status_changed()
    host.push_notify('ask')
    if isinstance(receipt,dict):
        receipt.update(id=rid, reason='pending', approval_card_shown=None)
    try:
        waits = {a.fut, a.review_pause} | ({gone} if gone is not None else set())
        done,_ = await asyncio.wait(waits, timeout=max(0,a.deadline-time.monotonic()), return_when=asyncio.FIRST_COMPLETED)
        if a.review_pause in done and not a.fut.done():
            return host._park(a,base,summary,host._park_key(tool,isha),None,why='question')
        if a.fut in done:
            decision,did,sk,sig = a.fut.result()
            reason = 'device' if did else 'estop' if host.stopped() else getattr(a,'withdrawn',None) or 'serve_stop'
        else:
            decision,did,sk,sig = 'deny',None,None,None
            reason = 'agent_gone' if gone is not None and gone in done else 'timeout'
            if not a.fut.done():
                a.fut.set_result((decision,did,sk,sig))
        # Never turn a task grant, parked old grant, or a simulated answer into UV.
        if decision == 'allow' and (reason != 'device' or not sk or host.st.sign_key(did) != sk
                or not subject_live() or time.monotonic() > a.deadline
                or (uv and not a.uv_ok) or (native_context is not None and not native_context.live(host))
                or (settings_context is not None and not settings_context.live(host))):
            decision,reason = 'deny','authority_changed'
    finally:
        if not a.parked:
            host.asks.pop(rid,None)
    approval_review.finished(host,a,decision,reason)
    if getattr(a,'answer_v',None) == 2:
        base.update(shown_sha256=a.answer_digest, approval_v=2)
    approvals.record(host.st,**base,decision=decision,reason=reason,device=did,sign_pub=sk,sig=sig,
        task_scope=a.task_scope,task_uv=bool(uv and a.uv_ok) if a.task_scope else None,
        task_id=host._task_id() if a.task_scope else None)
    host.st.log('ask_done',id=rid,tool=tool,decision=decision,reason=reason,device=did)
    host.activity('decision',id=rid,tool=tool,result=decision,reason=reason,cats=[],summary=summary)
    host.emit('ask_done',id=rid,result=decision,reason=reason,device=did)
    host._post(host._send_ready,lambda s:{'t':'ask_done','id':rid,'result':decision,'reason':reason})
    host.status_changed()
    if isinstance(receipt,dict):
        receipt.update(reason=reason,approval_card_shown=None)
    if decision == 'allow':
        if a.settings_session is not None:
            from . import auth_settings_uv
            auth_settings_uv.mint(host,a.settings_session,a)
        result = {'behavior':'allow','updatedInput':inp}
        if grant_info:
            result['grant_info'] = proof(host,a,did,sk,sig)
        return result
    messages = {'device':'主人在手机上拒绝了 / The owner denied this request.',
                'timeout':'确认已过期，未执行 / Approval expired; no operation executed.',
                'agent_gone':'原请求已结束，未执行 / The original request ended; no operation executed.'}
    return {'behavior':'deny','reason':reason,'message':messages.get(reason,'确认已失效，未执行 / Approval is no longer valid; no operation executed.')}


async def security(host, req):
    """CLI account unlink consent, bound to the exact current private record hash."""
    from . import cloud
    if req.get('command') != 'agentj unlink':
        return {'ok':False,'error':'shape'}
    before = cloud.read_cloud(host.st)
    target = approval_review.digest(before)
    if req.get('target_sha256') != target:
        return {'ok':False,'error':'target_changed'}
    answer = await host.ask('agentj unlink', {'target_sha256':target,
        'account':str((before or {}).get('tenant',{}).get('slug','unlinked'))}, grant_info=True)
    proof = answer.get('grant_info') or {}
    allowed = (answer.get('behavior') == 'allow' and proof.get('uv') is True
               and proof.get('sign_pub') and host.st.sign_key(proof.get('device')) == proof.get('sign_pub')
               and approval_review.digest(cloud.read_cloud(host.st)) == target
               and not host.stopped() and not host.stopping.is_set())
    return {'ok':bool(allowed),'error':None if allowed else 'uv_or_target_changed'}


def task_scope(host, tool, inp, steps, target):
    """Legacy exact-action plans keep their signed scope and object seals, no effect UV."""
    from . import bounded_plan, danger
    if not isinstance(steps,list) or not 1 <= len(steps) <= 12:
        raise ValueError('plan size')
    actions,lines,bindings = [],[],{}
    for step in steps:
        if not isinstance(step,dict) or set(step) != {'tool','input'} or not isinstance(step['input'],dict):
            raise ValueError('plan shape')
        name, data = step['tool'],step['input']
        if not isinstance(name,str) or uv_list.required(name,data):
            raise ValueError('listed commands require their own UV')
        if name == 'Bash':
            command=data.get('command')
            if not isinstance(command,str) or any(char in command for char in ('$', '`', '*', '?', '[', '{', '\n')):
                raise ValueError('dynamic plan operand')
            parsed=danger.lex(command)
            forbidden=set(danger._SHELLS)|{'python','python3','node','ruby','perl','php','eval','source','.','xargs','find'}
            if not parsed or any(c.subst or any(p.name in forbidden for p in danger.unwrap(c)) for c in parsed):
                raise ValueError('unbounded plan program')
        sha=approvals.input_digest(data)
        seal=bounded_plan.shell_seal(name,data,(host.agent_cfg or {}).get('dir'),host.__dict__.setdefault('_plan_seal_key',secrets.token_bytes(32)))
        bindings[name+':'+sha]=seal
        actions.append({'tool':name,'input_sha256':sha,'class':host._task_class(name,danger.Verdict())})
        raw_line=name+' '+approval_review.canonical(data)
        if len(raw_line.encode()) > 1000:
            raise ValueError('plan display too large')
        line=approval_review.safe_text(raw_line,1000)
        lines.append(line)
    if not any(a['tool']==tool and a['input_sha256']==approvals.input_digest(inp) for a in actions):
        raise ValueError('first action absent')
    if len({(a['tool'],a['input_sha256']) for a in actions}) != len(actions):
        raise ValueError('duplicate step')
    scope=json.dumps({'task':host._task_id(),'actions':actions,'target':target,'uv':False,'expires':int(time.time())+600,
        'display_steps':lines,'object_bindings':bindings},sort_keys=True,separators=(',',':'))
    if len(scope.encode())+sum(len(line.encode()) for line in lines)>14000:
        raise ValueError('plan too large')
    return scope,lines


def proof(host, ask, device, signing_key, signature):
    """Exact original signed result, also used by a one-use question continuation."""
    from . import wire
    info={'rid':ask.rid,'device':device,'sign_pub':signing_key,'uv':bool(ask.require_passkey and ask.uv_ok),
          **({'approval_v':2,'envelope':copy.deepcopy(ask.envelope)} if getattr(ask,'answer_v',None)==2 else {})}
    if getattr(ask,'fa',None):
        info.update(fa=copy.deepcopy(ask.fa),sig=wire.b64u(signature),summary=ask.summary,kind=ask.pk_kind,channel=host.channel)
    return info


def bind_resume(host, ask, send, device, signing_key, signature):
    """A Host-created retry Send is an object capability, never a client field."""
    if (not getattr(ask,'list_model',False) or getattr(ask,'bounded_plan',None) is not None
            or not getattr(ask,'review_event',None) or not signing_key
            or host.st.sign_key(device)!=signing_key or (ask.require_passkey and not ask.uv_ok)):
        return
    records=host.__dict__.setdefault('_uv_list_resumes',{})
    records[ask.rid]={'ask':ask,'send':send,'state':host.st,'agent':host.agent,'channel':host.channel,
        'session':str(host.hist.epoch),'task_epoch':host.__dict__.get('task_epoch'),'deadline':ask.deadline,'device':device,'key':signing_key,
        'context':ask.list_resume_context,'proof':proof(host,ask,device,signing_key,signature)}
    while len(records)>64:
        records.pop(next(iter(records)))


def take_resume(host, tool, inp, uv, context, settings_session):
    records=host.__dict__.setdefault('_uv_list_resumes',{})
    current=getattr(host.agent,'cur_send',None)
    now=time.monotonic()
    for rid,record in list(records.items()):
        ask=record['ask']
        if (now>=record['deadline'] or record['state'] is not host.st or record['agent'] is not host.agent
                or record['channel']!=host.channel or record['session']!=str(host.hist.epoch)
                or record['task_epoch']!=host.__dict__.get('task_epoch')
                or host.st.sign_key(record['device'])!=record['key']):
            records.pop(rid,None)
            continue
        if current is not record['send'] or tool!=ask.tool or approvals.input_digest(inp)!=ask.input_sha:
            continue
        if (context!=record['context'] or settings_session is not ask.settings_session
                or uv!=ask.require_passkey or rid!=ask.rid or not ask.fut.done()
                or ask.fut.result()[:3]!=('allow',record['device'],record['key'])
                or (uv and (not ask.uv_ok or record['proof'].get('uv') is not True))):
            records.pop(rid,None)
            continue
        records.pop(rid,None)  # Consume before returning; no await can race a second invocation.
        host.st.log('approval_continuation',id=rid,tool=tool,device=record['device'],input_sha256=ask.input_sha)
        return copy.deepcopy(record['proof'])
    return None
