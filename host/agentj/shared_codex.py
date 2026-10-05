"""Same-thread Codex continuation. Terminal and phone must take alternating turns.

An app-server launched here is owned here, outside the independent fence. Resume
never substitutes a new thread. Permissions come from the selected rollout's last
turn_context, not current config defaults. Unknown/new permission shapes fail closed.
"""
from __future__ import annotations
import asyncio
import contextlib
import json
import os
from pathlib import Path
import re

from .agent_codex import CodexAgent, RPCError
from .shared import public_text
from . import shared_hook
from .shared_risk import RiskGuard

UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')


def selected_rollout(directory, sid):
    if not isinstance(sid, str) or not UUID.fullmatch(sid):
        raise ValueError('exact Codex thread ID required')
    root = Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'sessions'
    matches = list(root.rglob(f'*-{sid}.jsonl'))
    if len(matches) != 1:
        raise ValueError('thread rollout unavailable or ambiguous')
    return matches[0]


def owner_context(path, directory, sid):
    meta = context = None
    active = False
    with path.open('rb') as f:
        while line := f.readline(2*1024*1024+1):
            if len(line)>2*1024*1024:
                while line and not line.endswith(b'\n'):
                    line=f.readline(2*1024*1024+1)
                continue
            try: item=json.loads(line)
            except ValueError: continue
            p=item.get('payload') or {}
            if item.get('type')=='session_meta': meta=p
            elif item.get('type')=='turn_context': context=p
            elif item.get('type')=='event_msg':
                if p.get('type')=='task_started': active=True
                elif p.get('type') in ('task_complete','turn_aborted'): active=False
    if not meta or meta.get('id')!=sid or os.path.realpath(meta.get('cwd',''))!=os.path.realpath(directory):
        raise ValueError('wrong thread/project')
    if not context or context.get('cwd')!=meta.get('cwd'):
        raise ValueError('no original turn permissions')
    if active: raise ValueError('desktop turn active; alternate turns')
    policy=context.get('approval_policy'); sandbox=context.get('sandbox_policy')
    reviewer=context.get('approvals_reviewer') or 'user'
    profile = context.get('permission_profile')
    active_profile = context.get('active_permission_profile') or {}
    # Only the exact built-in read-only profile has been verified against this
    # version's native experimental protocol. Never flatten a custom profile:
    # its deny entries may be absent from the legacy sandbox summary.
    profile_id = active_profile.get('id')
    disabled_full = profile == {'type':'disabled'} and not active_profile and sandbox == {'type':'danger-full-access'}
    if profile is not None and not disabled_full:
        expected = {'type':'managed', 'file_system':{'type':'restricted', 'entries':[
            {'path':{'type':'special','value':{'kind':'root'}},'access':'read'}]}, 'network':'restricted'}
        if profile_id != ':read-only' or profile != expected or sandbox != {'type':'read-only'}:
            raise ValueError('unverified original permission profile')
    if not isinstance(policy,(str,dict)) or not isinstance(sandbox,dict) or reviewer not in ('user','auto_review','guardian_subagent'):
        raise ValueError('original permissions unavailable')
    mapping={'read-only':'readOnly','workspace-write':'workspaceWrite','danger-full-access':'dangerFullAccess','external-sandbox':'externalSandbox'}
    native=dict(sandbox); native['type']=mapping.get(native.get('type'),native.get('type'))
    for old,new in [('writable_roots','writableRoots'),('network_access','networkAccess'),('exclude_tmpdir_env_var','excludeTmpdirEnvVar'),('exclude_slash_tmp','excludeSlashTmp')]:
        if old in native: native[new]=native.pop(old)
    if native.get('type') not in ('readOnly','workspaceWrite','dangerFullAccess','externalSandbox'):
        raise ValueError('unsupported original sandbox')
    original={'approvalPolicy':policy,'approvalsReviewer':reviewer}
    if profile is not None and not disabled_full:
        original['permissions']=profile_id
    else:
        original['sandboxPolicy']=native
    return original,context


class SharedCodexAgent(CodexAgent):
    def __init__(self, host, cfg):
        super().__init__(host, cfg)
        self.observer = None
        self.rollout = None
        self.offset = 0
        self.discard_line = False
        self.phone_turn = False
        self.risk = RiskGuard(self)
        self.risk_channel = None
        if not self.cfg.get("shared_session_id"):
            stored=self.host.st.agent_session(self.kind)
            if isinstance(stored,str) and UUID.fullmatch(stored):
                self.cfg["shared_session_id"]=stored

    def start(self):
        super().start()
        self.observer = asyncio.create_task(self.observe())

    def read_desktop(self):
        if self.rollout is None:
            sid = self.cfg.get('shared_session_id')
            self.rollout = selected_rollout(self.cfg['dir'], sid)
            owner_context(self.rollout, self.cfg['dir'], sid)
            self.offset = self.rollout.stat().st_size
            return
        with self.rollout.open('rb') as f:
            if f.seek(0, 2) < self.offset:
                raise ValueError('selected rollout truncated')
            f.seek(self.offset)
            for _ in range(100):
                line=f.readline(2*1024*1024+1)
                if not line: break
                if self.discard_line or len(line)>2*1024*1024:
                    self.offset=f.tell();self.discard_line=not line.endswith(b'\n');continue
                if not line.endswith(b'\n'): break
                self.offset=f.tell()
                try: item=json.loads(line)
                except ValueError: continue
                p=item.get('payload') or {}
                if self.phone_turn: continue
                if item.get('type')=='response_item' and p.get('type')=='message':
                    role=p.get('role')
                    text='\n'.join(b.get('text','') for b in p.get('content',[]) if isinstance(b,dict) and b.get('type') in ('input_text','output_text'))
                    if text and role in ('user','assistant'):
                        (self.host.desktop_input if role=='user' else self.host.desktop_text)(public_text(text))
                elif item.get('type')=='event_msg' and p.get('type') in ('task_complete','turn_aborted'):
                    self.host.desktop_end()

    async def observe(self):
        while True:
            try: self.read_desktop()
            except (OSError,ValueError): pass
            await asyncio.sleep(.25)

    async def stop(self):
        if self.observer:
            self.observer.cancel()
            with contextlib.suppress(asyncio.CancelledError): await self.observer
        await super().stop()
        if self.risk_channel:
            await self.risk_channel.stop()

    async def _spawn(self):
        if self.cfg.get('high_risk_warnings',True) and not self.risk_channel:
            self.risk_channel=shared_hook.Channel(self.risk,shared_hook.channel_path(self.host.st.root,'codex'))
            await self.risk_channel.start()
            shared_hook.install(self.cfg['dir'],self.risk_channel.path,family='codex')
        return await super()._spawn()

    def argv(self):
        argv=super().argv()
        if self.risk_channel:
            entry=shared_hook.settings(self.risk_channel.path,('PreToolUse',))['hooks']['PreToolUse'][0]['hooks'][0]
            # An explicit launch hook is a trusted owner-installed bridge; it is
            # additive to native user/system/project hook sources. No native
            # approval, sandbox or reviewer override is introduced here.
            command=json.dumps(entry['command'])
            value='[{ hooks = [{ type = "command", command = '+command+', timeout = 180 }] }]'
            argv += ['-c','hooks.PreToolUse='+value]
        return argv

    def launch_argv(self, argv):
        # Ordinary native process with original HOME/config. No fence, no injected
        # identity, no flags relaxing native permissions.
        return argv

    def policy(self): return {}

    def initialize_capabilities(self): return {"experimentalApi": True}


    async def _thread(self):
        sid=self.cfg.get('shared_session_id')
        path=selected_rollout(self.cfg['dir'],sid)
        original,ctx=owner_context(path,self.cfg['dir'],sid)
        params={'threadId':sid,'excludeTurns':True,'approvalPolicy':original['approvalPolicy'],
                'approvalsReviewer':original['approvalsReviewer']}
        if 'permissions' in original: params['permissions']=original['permissions']
        else:
            params['sandbox']={'readOnly':'read-only','workspaceWrite':'workspace-write','dangerFullAccess':'danger-full-access'}.get(original['sandboxPolicy']['type'])
            if params['sandbox'] is None: raise ValueError('unverified resume sandbox')
        if self.risk_channel:
            # Trust only this host-generated command at its current native hash,
            # scoped to the resumed thread. Never bypass trust for owner hooks.
            listing=await self.call('hooks/list',{'cwds':[self.cfg['dir']]})
            command=shared_hook.settings(self.risk_channel.path,('PreToolUse',))['hooks']['PreToolUse'][0]['hooks'][0]['command']
            trusted={}
            covered=False
            for entry in listing.get('data',[]):
                if os.path.realpath(entry.get('cwd','')) != os.path.realpath(self.cfg['dir']): continue
                for hook in entry.get('hooks',[]):
                    if hook.get('command') != command or hook.get('handlerType') != 'command': continue
                    if not hook.get('enabled') or not hook.get('currentHash') or not hook.get('key'): continue
                    trusted[hook['key']]={'trusted_hash':hook['currentHash']}
                    covered |= hook.get('eventName') == 'preToolUse'
            if not covered: raise ValueError('shared pre-execution hook unavailable')
            params['config']={'hooks.state':trusted}
            self.host.emit('shared_guard_ready',agent=self.kind,scope='thread',hooks=len(trusted))
        res=await self.call('thread/resume',params)
        if (res.get('thread') or {}).get('id')!=sid:
            raise RPCError({'message':'resume returned different thread'})
        self.tid=sid;self.original=original
        self.thread={k:res.get(k) for k in ('model','sandbox','approvalPolicy','approvalsReviewer','cwd','reasoningEffort')}
        if res.get('approvalPolicy') != original['approvalPolicy'] or res.get('approvalsReviewer') != original['approvalsReviewer']:
            raise ValueError('native resume changed original approval policy')
        if 'permissions' in original and (res.get('activePermissionProfile') or {}).get('id') != original['permissions']:
            raise ValueError('native resume changed original profile')
        self.thread['sandbox']=res.get('sandbox')
        self.host.st.set_agent_session(self.kind,sid)
        return True

    async def turn(self,text):
        try:
            if not await self._ready(): return
            self.phone_turn=True
            self.risk.reset()
            self.turn_done,self.turn_proc=asyncio.Event(),self.proc
            self.turn_id,self.turn_status=None,{}
            res=await self.deliver(lambda:self.call('turn/start',{'threadId':self.tid,
                'input':[{'type':'text','text':text,'text_elements':[]}],**self.original}))
            self.turn_id=(res.get('turn') or {}).get('id')
            await self.turn_done.wait()
            self.read_desktop()
        except (ValueError,RPCError,OSError) as error:
            known = {'exact Codex thread ID required','thread rollout unavailable or ambiguous','wrong thread/project','no original turn permissions','desktop turn active; alternate turns','unverified original permission profile','original permissions unavailable','unsupported original sandbox','native resume changed original approval policy','native resume changed original profile','unverified resume sandbox','shared pre-execution hook unavailable'}
            reason=str(error) if isinstance(error,ValueError) and str(error) in known else type(error).__name__
            self.host.emit("shared_refused", agent=self.kind, reason=reason)
            self.local_fail('Codex 同 thread 接续未投递：核对原会话权限，并等电脑回合结束。 / Wait for the desktop turn and verify the selected thread permissions.')
        finally:
            # Release this actor after its turn so the native desktop can resume
            # the same rollout. The observer stays alive between actors.
            if self.proc is not None:
                await self._kill(self.proc)
            self.phone_turn=False

    def _emit(self,text):
        super()._emit(public_text(text))

    async def halt(self,clear_queue=True):
        busy=self.status=='working'
        if clear_queue:
            while not self.q.empty():
                item=self.q.get_nowait()
                if getattr(self.host,'queue_dropped',None):self.host.queue_dropped(item)
        await self.interrupt_request(self.proc)
        if self.turn_done:self.turn_done.set()
        return busy
