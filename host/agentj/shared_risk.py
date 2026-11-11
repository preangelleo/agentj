"""Per-turn shared warning layer, before native execution; native rules still decide.

A high-risk approval here never grants OS/network permissions and never answers
an independent native ask. Same-category grants are tied to the signed phone
request and rechecked against device revocation and the emergency stop.
"""
import asyncio
import hashlib
import json
from . import danger, policy_registry, refusal_intent
from .text import pick


def credential_read_refusal(host, tool, inp, cwd):
    from . import uv_list
    if not uv_list.credential_reads(tool, inp, host=host, cwd=cwd):
        return None
    host.st.log('credential_read_refused', tool=tool, reason=uv_list.CREDENTIAL_READ_REASON)
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
            'permissionDecisionReason': uv_list.CREDENTIAL_READ_REASON + ': ' + uv_list.CREDENTIAL_READ_HINT}}

# P539 (#301): the deny reason the agent sees says what happened and what it can do next (never a bare "denied").
WHY = {
    'unknown': ('Agent J 的规则里没有这种动作，没有给主人弹确认卡就直接拒绝了（不是主人拒绝的）。换一种常见做法再试，或用一句话告诉主人你想做什么、请他在手机上确认。',
                "This action type is not in Agent J's policy, so it was denied without showing the owner a card (the owner did not deny it). Try a common way, or tell the owner in one sentence what you want to do and ask on the phone."),
    'denied_before': ('这一轮里主人已经拒绝过同一类操作，所以这次没再弹卡。不要再试同样的操作；需要的话先问主人。',
                      'The owner already denied this kind of step earlier in this turn, so no new card was shown. Do not retry it; ask the owner first if it is needed.'),
    'stopped': ('Agent J 处于「全部停下」状态，什么都不执行。等主人在手机上点「恢复」。',
                'Agent J is in emergency stop and runs nothing. Wait for the owner to tap Resume on the phone.'),
    'revoked': ('批准它的那台手机已经取消配对，这个批准作废了。请主人在手机上再确认一次。',
                'The phone that approved it was unpaired, so the approval no longer counts. Ask the owner to confirm again on the phone.'),
    'host': ('Agent J：{msg} 如果这一步确实需要，用一句话告诉主人为什么，请他在手机上再批准一次。',
             'Agent J: {msg} If this step is really needed, tell the owner why in one sentence and ask them to approve it on the phone again.'),
}

class RiskGuard:
    def __init__(self, adapter):
        self.adapter=adapter
        self.grants={}
        self.denied=set()
        self.lock=asyncio.Lock()
        self.turn_id=None
        self.epoch=0
        self.ready=asyncio.Event()
        self.why=None       # P539: (kind, host message) of the last refusal

    def reset(self, turn_id=None):
        self.turn_id=turn_id
        from . import main_thread
        main_thread.of(self.adapter).reset()
        self.epoch+=1
        self.grants.clear()
        self.denied.clear()

    async def check(self, tool, inp):
        self.why=None
        ok=await self._check(tool, inp)
        if not ok and self.why is None:
            self.why=('host','')
        return ok

    def reason(self) -> str:
        """The deny reason for the agent (P539): which of unknown / denied before / stopped / revoked / the host's own."""
        kind,msg=self.why or ('host','')
        from . import uv_list
        if kind == uv_list.CREDENTIAL_READ_REASON:
            return kind + ': ' + uv_list.CREDENTIAL_READ_HINT
        lang=getattr(getattr(self.adapter,'host',None),'lang','zh')
        if kind=='host':
            msg=(msg or '').strip() or pick(lang,'主人没有批准（拒绝了，或没在时限内回答）。','The owner did not approve it (denied, or no answer in time).')
            return pick(lang,*WHY['host']).format(msg=msg[:600])
        if kind=='unknown' and msg:
            return policy_registry.unknown_text(msg, lang)+' '+pick(lang,*WHY[kind])   # P553 (#326): names the tool
        return pick(lang,*WHY[kind])

    def _deny(self, kind, msg=''):
        self.why=(kind,msg if isinstance(msg,str) else '')
        return False

    async def _check(self, tool, inp):
        # Pre-tool observer: no extra permission layer. Native permission
        # requests still reach Host.ask. Explicit Agent J executors own UV.
        if self.adapter.host.stopped():
            return self._deny('stopped')
        from . import uv_list
        a = self.adapter
        if credential_read_refusal(a.host, tool, inp, getattr(a, 'cfg', {}).get('dir')):
            return self._deny(uv_list.CREDENTIAL_READ_REASON)
        # #537② (Round 2 R2): a listed money-moving call in a shared session goes to Host.ask too — the owner / host
        # turn's fingerprint card there, every other source's off-list refusal (list_approval.ask decides, not here).
        if (uv_list.protected_targets(tool, inp, cwd=getattr(a, 'cfg', {}).get('dir'), env=uv_list.host_environment(a.host))
                or uv_list.charge_command(tool, inp)):
            from .list_approval import NativeContext
            answer = await a.host.ask(tool, inp, batch=False, _harness_permission=True, _native_context=NativeContext.capture(a))
            if answer.get('behavior') != 'allow':
                return self._deny('host', answer.get('message', 'Owner verification required.'))
        return True

        a=self.adapter
        from . import handoff
        if not a.host.stopped() and handoff.owner_side(a) and handoff.own_read(tool, inp, a.cfg):
            # P539 (#301): reading Agent J's own handoff file needs no card — still one approvals.log line (L2)
            rid=await asyncio.to_thread(handoff.record_own, a.host.st, getattr(a,'kind',None), tool, inp)
            a.host.st.log('ask_done', id=rid, tool=tool, decision='allow', reason='own_handoff')
            return True
        requirement=policy_registry.main_requirement(tool, inp, cwd=a.cfg.get('dir'))
        if requirement == 'deny':
            policy_registry.log_unknown(getattr(a.host,'st',None), tool, inp, agent=getattr(a,'kind','?'))   # P553: support can find it
            return self._deny('unknown', tool)
        if requirement in ('hold','uv'):
            # Denials may suppress repeat requests; approvals never become a
            # broad category grant for a registry-mandated card.
            verdict=danger.classify_shared(tool,inp,a.cfg.get('danger_extra'),a.cfg.get('approvals','standard'),a.cfg.get('dir'))
            keys=danger.shared_keys(verdict,a.cfg)
            async with self.lock:
                if set(keys) & self.denied:
                    a.host.st.log('shared_risk_denied',tool=tool,reason='owner_denied_before',agent=a.kind)
                    return self._deny('denied_before')
                epoch=self.epoch
                receipt={}
                answer=await a.host.ask(policy_registry.harness_tool(tool),inp,batch=False,require_passkey=requirement == 'uv',_receipt=receipt,_harness_permission=True)
                if answer.get('behavior') != 'allow':
                    if epoch == self.epoch and refusal_intent.owner_denied(receipt):
                        self.denied.update(keys)
                    return self._deny('host',refusal_intent.message(refusal_intent.reason(receipt),getattr(a.host,'lang','zh'),answer.get('message')))
                grant=answer.get('risk_grant')
                if a.host.stopped():
                    return self._deny('stopped')
                if grant and a.host.st.sign_key(grant['device']) != grant['sign_pub']:
                    return self._deny('revoked')
                return True
        verdict=danger.classify_shared(tool,inp,a.cfg.get('danger_extra'),a.cfg.get('approvals','standard'),a.cfg.get('dir'))
        if not verdict.danger:
            return True
        keys=danger.shared_keys(verdict,a.cfg)   # P198: the five clear dangers ask (was: only with high_risk_warnings)
        if not keys:
            return True
        async with self.lock:
            if set(keys) & self.denied:
                a.host.st.log('shared_risk_denied',tool=tool,reason='owner_denied_before',agent=a.kind)
                return self._deny('denied_before')
            for cat,grant in list(self.grants.items()):
                if a.host.stopped() or a.host.st.sign_key(grant['device']) != grant['sign_pub']:
                    self.grants.pop(cat,None)
            if set(keys) <= self.grants.keys():
                for cat in keys:
                    a.host.st.log('shared_risk_batch',category=cat,grant=self.grants[cat]['rid'],
                        turn=self.turn_id,input_sha256=hashlib.sha256(json.dumps(inp,sort_keys=True).encode()).hexdigest())
                return True
            epoch=self.epoch
            receipt={}
            answer=await a.host.ask(policy_registry.harness_tool(tool),inp,batch=False,risk_scope=True,_receipt=receipt,_harness_permission=True)
            if answer.get('behavior') != 'allow':
                if self.epoch == epoch and refusal_intent.owner_denied(receipt):
                    self.denied.update(keys)
                return self._deny('host',refusal_intent.message(refusal_intent.reason(receipt),getattr(a.host,'lang','zh'),answer.get('message')))
            grant=answer.get('risk_grant')
            if a.host.stopped():
                return self._deny('stopped')
            if grant and a.host.st.sign_key(grant['device']) != grant['sign_pub']:
                return self._deny('revoked')
            if grant and self.epoch == epoch:
                self.grants.update({cat:grant for cat in keys if cat not in danger.ALWAYS_CARD})
            return True

    async def hook_event(self, event):
        if isinstance(event,dict):
            event.pop('_agentj_gone',None)
        a=self.adapter
        if isinstance(event,dict) and event.get('hook_event_name') == 'SharedGuardReady':
            import os
            if os.path.realpath(event.get('cwd','')) == os.path.realpath(a.cfg['dir']):
                self.ready.set()
                if getattr(a,'kind',None) == 'opencode' and getattr(a,'v2',False):
                    a._main_dispatch_available=True
                    from . import main_thread
                    main_thread.of(a).changed()
            return {}
        if isinstance(event,dict) and event.get('hook_event_name') == 'PreToolUse':
            a.host.emit('shared_pretool', agent=a.kind, tool=event.get('tool_name'), input_keys=sorted((event.get('tool_input') or {}).keys()), matching=event.get('session_id') == a.cfg.get('shared_session_id'))
        if not isinstance(event,dict) or event.get('session_id') != a.cfg.get('shared_session_id'):
            return {}
        if event.get('hook_event_name') == 'UserPromptSubmit':
            self.reset(event.get('turn_id'))
        elif event.get('hook_event_name') in ('Stop','SessionEnd','PreCompact','PostCompact'):
            from . import main_thread
            main_thread.of(a).observe(event)
        if event.get('hook_event_name') != 'PreToolUse':
            return {}
        tool=event.get('tool_name','?');inp=event.get('tool_input') or {}
        if getattr(a,'kind',None) == 'codex':
            tool=policy_registry.codex_hook_tool(tool)   # P559: namespace+tool names from Codex's own hook
        from . import main_thread
        dispatch=main_thread.of(a).check(tool,inp,event)
        dispatch_output=dispatch.get('hookSpecificOutput',{})
        if dispatch_output.get('permissionDecision') == 'deny':
            return dispatch
        if await self.check(tool,inp):
            return dispatch  # no allow: preserve every native hook/permission decision
        return {'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny',
                                     'permissionDecisionReason':self.reason(),
                                     **({'additionalContext':dispatch_output['additionalContext']} if dispatch_output.get('additionalContext') else {})}}


class NativePathGuard:
    """Protected paths only for an Agent J-owned native server process.

    Kernel ancestry authenticates the channel; the native session/cwd still binds
    each protected operation. No native permission or sandbox decision is allowed.
    """
    def __init__(self, adapter):
        self.adapter = adapter
        self.ready = asyncio.Event()

    @property
    def session(self):
        return {'pid': getattr(getattr(self.adapter, 'proc', None), 'pid', None)}

    def authentic(self, event):
        pid = self.session['pid']
        event['_agentj_path_authenticated'] = isinstance(pid, int) and pid in (event.get('_agentj_chain') or [])
        # Keep a protected request visible to hook_event even when authentication
        # fails: returning False here would make Channel emit an empty decision.
        return True

    async def hook_event(self, event):
        import os
        from . import uv_list
        a = self.adapter
        if isinstance(event, dict) and event.get('hook_event_name') == 'SharedGuardReady':
            if (event.get('_agentj_path_authenticated') and isinstance(event.get('cwd'), str)
                    and os.path.realpath(event['cwd']) == os.path.realpath(a.cfg['dir'])):
                self.ready.set()
            return {}
        if not isinstance(event, dict) or event.get('hook_event_name') != 'PreToolUse':
            return {}
        tool, inp = event.get('tool_name', '?'), event.get('tool_input') or {}
        if a.kind == 'codex':
            tool = policy_registry.codex_hook_tool(tool)
        if (refusal := credential_read_refusal(a.host, tool, inp, a.cfg.get('dir'))) is not None:
            return refusal
        # #537② (S2): a full-access thread runs most commands without a native approval request; a listed money-moving
        # call (uv_list.CHARGE_COMMANDS) is sent to Host.ask here so list_approval can show the owner's fingerprint card.
        if (not uv_list.protected_targets(tool, inp, cwd=event.get('cwd') or a.cfg.get('dir'), env=uv_list.host_environment(a.host))
                and not uv_list.charge_command(tool, inp)):
            return {}
        sid = getattr(a, 'tid', None) if a.kind == 'codex' else getattr(a, 'sid', None)
        same_cwd = isinstance(event.get('cwd'), str) and os.path.realpath(event['cwd']) == os.path.realpath(a.cfg['dir'])
        if not event.get('_agentj_path_authenticated') or not sid or event.get('session_id') != sid or not same_cwd:
            answer = {'behavior': 'deny', 'message': 'Protected control file: native session binding unavailable.'}
        else:
            from .list_approval import NativeContext
            answer = await a.host.ask(tool, inp, event.get('_agentj_gone'), batch=False, _harness_permission=True, _native_context=NativeContext.capture(a))
        if answer.get('behavior') == 'allow':
            return {}
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                'permissionDecisionReason': answer.get('message') or 'Owner verification required for protected control files.'}}
