"""Per-turn shared warning layer, before native execution; native rules still decide.

A high-risk approval here never grants OS/network permissions and never answers
an independent native ask. Same-category grants are tied to the signed phone
request and rechecked against device revocation and the emergency stop.
"""
import asyncio
import hashlib
import json
from . import danger

class RiskGuard:
    def __init__(self, adapter):
        self.adapter=adapter
        self.grants={}
        self.denied=set()
        self.lock=asyncio.Lock()
        self.turn_id=None
        self.epoch=0
        self.ready=asyncio.Event()

    def reset(self, turn_id=None):
        self.turn_id=turn_id
        self.epoch+=1
        self.grants.clear()
        self.denied.clear()

    async def check(self, tool, inp):
        a=self.adapter
        if not a.cfg.get('high_risk_warnings',True):
            return True
        verdict=danger.classify_shared(tool,inp,a.cfg.get('danger_extra'))
        if not verdict.danger:
            return True
        async with self.lock:
            if set(verdict.cats) & self.denied:
                return False
            for cat,grant in list(self.grants.items()):
                if a.host.stopped() or a.host.st.sign_key(grant['device']) != grant['sign_pub']:
                    self.grants.pop(cat,None)
            if set(verdict.cats) <= self.grants.keys():
                for cat in verdict.cats:
                    a.host.st.log('shared_risk_batch',category=cat,grant=self.grants[cat]['rid'],
                        turn=self.turn_id,input_sha256=hashlib.sha256(json.dumps(inp,sort_keys=True).encode()).hexdigest())
                return True
            epoch=self.epoch
            answer=await a.host.ask(tool,inp,batch=False,risk_scope=True)
            if answer.get('behavior') != 'allow':
                if self.epoch == epoch:
                    self.denied.update(verdict.cats)
                return False
            grant=answer.get('risk_grant')
            if a.host.stopped() or (grant and a.host.st.sign_key(grant['device']) != grant['sign_pub']):
                return False
            if grant and self.epoch == epoch:
                self.grants.update({cat:grant for cat in verdict.cats})
            return True

    async def hook_event(self, event):
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
                                     'permissionDecisionReason':'Paired phone denied or timed out.'}}
