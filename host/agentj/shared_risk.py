"""Per-turn shared warning layer, before native execution; native rules still decide.

A high-risk approval here never grants OS/network permissions and never answers
an independent native ask. Same-category grants are tied to the signed phone
request and rechecked against device revocation and the emergency stop.
"""
import asyncio
import hashlib
import json
from . import danger, policy_registry
from .text import pick

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
        a=self.adapter
        from . import handoff
        if not a.host.stopped() and handoff.owner_side(a) and handoff.own_read(tool, inp, a.cfg):
            # P539 (#301): reading Agent J's own handoff file needs no card — still one approvals.log line (L2)
            rid=await asyncio.to_thread(handoff.record_own, a.host.st, getattr(a,'kind',None), tool, inp)
            a.host.st.log('ask_done', id=rid, tool=tool, decision='allow', reason='own_handoff')
            return True
        requirement=policy_registry.main_requirement(tool, inp)
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
                    return self._deny('denied_before')
                epoch=self.epoch
                answer=await a.host.ask(tool,inp,batch=False,require_passkey=requirement == 'uv')
                if answer.get('behavior') != 'allow':
                    if epoch == self.epoch:
                        self.denied.update(keys)
                    return self._deny('host',answer.get('message'))
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
            answer=await a.host.ask(tool,inp,batch=False,risk_scope=True)
            if answer.get('behavior') != 'allow':
                if self.epoch == epoch:
                    self.denied.update(keys)
                return self._deny('host',answer.get('message'))
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
            return {}
        if isinstance(event,dict) and event.get('hook_event_name') == 'PreToolUse':
            a.host.emit('shared_pretool', agent=a.kind, tool=event.get('tool_name'), input_keys=sorted((event.get('tool_input') or {}).keys()), matching=event.get('session_id') == a.cfg.get('shared_session_id'))
        if not isinstance(event,dict) or event.get('session_id') != a.cfg.get('shared_session_id'):
            return {}
        if event.get('hook_event_name') == 'UserPromptSubmit':
            self.reset(event.get('turn_id'))
        if event.get('hook_event_name') != 'PreToolUse':
            return {}
        tool=event.get('tool_name','?');inp=event.get('tool_input') or {}
        if await self.check(tool,inp):
            return {}  # no allow: preserve every native hook/permission decision
        return {'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny',
                                     'permissionDecisionReason':self.reason()}}
