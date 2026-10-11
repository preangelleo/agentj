"""Same-thread Codex continuation. Terminal and phone must take alternating turns.

An app-server launched here is owned here, outside the independent fence. Permissions
come from the selected rollout's last turn_context when that shape is verified.

F14 (P51): a continuation that cannot be established is degraded, never a dead end:
no resumable thread (none selected, rollout missing, other project, resume refused by
Codex, except an active writer) -> a new native thread in the same folder with the owner's own Codex defaults;
desktop turn running -> wait for it, then attempt delivery; an active native writer ->
read-only follow, preserve the selected thread and refuse delivery with a next step; original permissions unreadable or
an unverified profile shape -> resume with the owner's Codex config defaults (never a
flattened copy of a custom profile, never extra overrides). Only Codex's own refusal
of the turn is reported, with the concrete reason.
"""
from __future__ import annotations
import asyncio
import contextlib
import json
import os
from pathlib import Path
import re

from .text import pick
from .agent_codex import CodexAgent, RPCError
from .shared import public_text
from . import shared_hook
from .shared_risk import RiskGuard

UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
DESKTOP_WAIT = 30*60        # s a phone message waits for a running desktop turn
DISCOVER_LIMIT = 400        # newest rollouts inspected when no thread is selected


class Refusal(ValueError):
    """A named reason; str() stays the stable English code used in activity logs."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# code -> (小白能懂的一句 + 下一步, plain English + next step)
REASONS = {
    'exact Codex thread ID required': ('还没选要接续的 Codex 会话。', 'No Codex thread is selected yet.'),
    'thread rollout unavailable or ambiguous': ('找不到所选 Codex 会话的记录（可能已删除）。', 'The selected Codex thread record was not found (maybe deleted).'),
    'wrong thread/project': ('所选 Codex 会话属于别的项目目录。', 'The selected Codex thread belongs to another project folder.'),
    'no original turn permissions': ('所选 Codex 会话还没有完整的一轮对话，读不到它的权限。', 'The selected Codex thread has no completed turn, so its permissions are unknown.'),
    'desktop turn active; alternate turns': ('电脑上的 Codex 正在回答。', 'The desktop Codex is still answering.'),
    'unverified original permission profile': ('所选会话用了 Agent J 还没核对过的权限配置。', 'The selected thread uses a permission profile Agent J has not verified.'),
    'original permissions unavailable': ('读不到所选会话的权限。', 'The selected thread permissions are unreadable.'),
    'unsupported original sandbox': ('所选会话的沙箱类型 Agent J 不认识。', 'The selected thread uses an unknown sandbox type.'),
    'unverified resume sandbox': ('所选会话的沙箱类型 Agent J 不认识。', 'The selected thread uses an unknown sandbox type.'),
    'native resume changed original approval policy': ('Codex 接续后审批策略和原会话不一致。', 'Codex resumed with a different approval policy.'),
    'native resume changed original profile': ('Codex 接续后权限配置和原会话不一致。', 'Codex resumed with a different permission profile.'),
    'shared pre-execution hook unavailable': ('这个 Codex 版本没装上高危提醒钩子。', 'This Codex version did not load the high-risk warning hook.'),
    'desktop writer active': ('电脑上的 Codex App 正在使用这个会话，手机现在只读。完全退出 Codex App 后重发，就能接着同一个会话。', 'The Codex App on your computer is using this session; the phone is read-only. Quit the Codex App completely, then resend to continue this same session.'),
    'desktop turn did not finish': ('电脑上的 Codex 回合 30 分钟还没结束。', 'The desktop Codex turn has not finished for 30 minutes.'),
}
class Stopped(Exception):
    """/stop while the phone message waited for the desktop turn."""


_PATHISH = re.compile(r'(?:~|/)[^\s\'"]*')


def brief(error):
    """Activity-log reason: the named code, else exception type + short text without paths."""
    if isinstance(error, Refusal):
        return error.code
    text = str(error.err.get('message') or error) if isinstance(error, RPCError) else str(error)
    if isinstance(error, ValueError) and text in REASONS:
        return text
    text = _PATHISH.sub('<path>', ' '.join(text.split()))[:120]
    return type(error).__name__ + (': ' + text if text else '')


def active_writer(error):
    # Native -32600 has other meanings too. Match only the concrete writer conflict.
    return isinstance(error, RPCError) and 'already has an active writer' in str(error.err.get('message') or '').lower()

def scan(path):
    """(session_meta, last turn_context, desktop turn running) of one rollout, bounded lines."""
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
            if not isinstance(item, dict): continue
            p=item.get('payload') or {}
            if item.get('type')=='session_meta': meta=p
            elif item.get('type')=='turn_context': context=p
            elif item.get('type')=='event_msg':
                if p.get('type')=='task_started': active=True
                elif p.get('type') in ('task_complete','turn_aborted'): active=False
    return meta, context, active


def sessions_root():
    return Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'sessions'


def first_meta(path):
    with contextlib.suppress(OSError, ValueError):
        with path.open('rb') as f:
            item = json.loads(f.readline(256*1024))
        if isinstance(item, dict) and item.get('type') == 'session_meta':
            return item.get('payload') or {}
    return None


def discover(directory, limit=DISCOVER_LIMIT):
    """Newest native Codex thread whose own cwd is exactly this project, else None. Reads first lines only."""
    root = sessions_root()
    try:
        files = sorted(root.rglob('rollout-*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    except OSError:
        return None
    want = os.path.realpath(directory)
    for path in files:
        meta = first_meta(path)
        sid = (meta or {}).get('id')
        if isinstance(sid, str) and UUID.fullmatch(sid) and path.name.endswith(f'-{sid}.jsonl') \
                and os.path.realpath(meta.get('cwd') or '') == want:
            return sid
    return None


def selected_rollout(directory, sid):
    if not isinstance(sid, str) or not UUID.fullmatch(sid):
        raise Refusal('exact Codex thread ID required')
    matches = list(sessions_root().rglob(f'*-{sid}.jsonl'))
    if len(matches) != 1:
        raise Refusal('thread rollout unavailable or ambiguous')
    return matches[0]


def owner_context(path, directory, sid):
    meta, context, active = scan(path)
    if not meta or meta.get('id')!=sid or os.path.realpath(meta.get('cwd',''))!=os.path.realpath(directory):
        raise Refusal('wrong thread/project')
    if not context or context.get('cwd')!=meta.get('cwd'):
        raise Refusal('no original turn permissions')
    if active: raise Refusal('desktop turn active; alternate turns')
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
            raise Refusal('unverified original permission profile')
    if not isinstance(policy,(str,dict)) or not isinstance(sandbox,dict) or reviewer not in ('user','auto_review','guardian_subagent'):
        raise Refusal('original permissions unavailable')
    mapping={'read-only':'readOnly','workspace-write':'workspaceWrite','danger-full-access':'dangerFullAccess','external-sandbox':'externalSandbox'}
    native=dict(sandbox); native['type']=mapping.get(native.get('type'),native.get('type'))
    for old,new in [('writable_roots','writableRoots'),('network_access','networkAccess'),('exclude_tmpdir_env_var','excludeTmpdirEnvVar'),('exclude_slash_tmp','excludeSlashTmp')]:
        if old in native: native[new]=native.pop(old)
    if native.get('type') not in ('readOnly','workspaceWrite','dangerFullAccess','externalSandbox'):
        raise Refusal('unsupported original sandbox')
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
        self.rollout_sid = None
        self.original = {}
        self.told = set()
        self.shared_status = None
        self.read_probe = False
        self.writer = {}                 # ADR-A178: who held the writer at the last refusal ({"kind", "pid", "how"})
        self.perm_source = "pending"     # F30: desktop / config / agentj / codex, known once the thread is resumed
        if not self.cfg.get("shared_session_id"):
            stored=self.host.st.agent_session(self.kind)
            if isinstance(stored,str) and UUID.fullmatch(stored):
                self.cfg["shared_session_id"]=stored

    async def cmd_clear(self, arg):
        from .slash import Result
        return Result(pick(getattr(self.host, "lang", "zh"), "没清成：Codex app-server 只能新建另一线程，不能替桌面切换当前线程。请在桌面 /new 后指定新线程；手机历史和水位保留。", "Not cleared: app-server cannot switch the desktop's active thread. Use desktop /new and select it; phone history and meter are unchanged."), "refused")

    async def cmd_undo_clear(self, arg):
        from .slash import Result
        return Result(pick(getattr(self.host, "lang", "zh"), "没有执行：共享 Codex 请在桌面 /resume 恢复原线程。", "Not executed: use desktop /resume for the original thread."), "refused")

    def start(self):
        super().start()
        self.observer = asyncio.create_task(self.observe())

    async def reload_identity(self):
        """The owner's own session: no injected identity, never restarted for it (A1)."""

    def follow_status(self, status):
        self.shared_status = status
        from . import codex_procs
        w = self.writer or {}
        notice = {l: codex_procs.text(w.get('kind') or 'unknown', w.get('pid'), l == 'zh')
                  for l in ('zh', 'en')} if status == 'desktop_writer' else None
        self.meter(shared_status=status, shared_writer=notice)

    def desktop_model(self, context):
        from . import slash
        model = context.get('model') if isinstance(context, dict) else None
        if isinstance(model, str) and slash.MODEL_RE.fullmatch(model):
            self.thread['model'] = model
            if 'effort' in context:
                self.thread['reasoningEffort'] = context['effort'] if isinstance(context['effort'], str) else None
            # A configured phone model is a next-turn choice, not the App's current model.
            self.meter(model=model, model_name=self.model_name(model),
                       **({'effort': self.thread.get('reasoningEffort')} if 'effort' in context else {}))

    def cur_model(self):
        if getattr(self, 'shared_status', None) in ('following', 'desktop_writer') and self.thread.get('model'):
            return self.thread['model']
        return super().cur_model()

    def cur_effort(self):
        if getattr(self, 'shared_status', None) in ('following', 'desktop_writer'):
            return self.thread.get('reasoningEffort')
        return super().cur_effort()

    def phone_choice(self):
        """Only explicit phone/host choices override a native thread; desktop choices remain native."""
        rev = getattr(self, 'revert', None) or {}
        return {k: v for k in ('model', 'effort')
                if isinstance(v := self.cfg.get(k) or rev.get(k), str) and v}

    async def probe_writer(self):
        """A short native resume probe; no hooks/config changes, no turn and no retained writer."""
        if not self.rollout or scan(self.rollout)[2]:
            return
        saved_thread = dict(self.thread)
        self.read_probe = True
        try:
            if await self._spawn():
                try:
                    await self.call('thread/resume', {'threadId': self.rollout_sid, 'excludeTurns': True})
                except RPCError as error:
                    if active_writer(error):
                        self.writer = await self.find_writer(self.rollout)
                        if self.writer.get('kind') == 'agentj' and await self.end_leftover(self.writer):
                            return               # our own leftover: ended; the next phone message resumes normally
                        self.follow_status('desktop_writer')
                        self.writer_notice()
        except (OSError, ConnectionError, asyncio.TimeoutError, RPCError):
            pass                         # an unavailable probe never prevents local read-only following
        finally:
            if self.proc is not None:
                await self._kill(self.proc)
            self.thread = saved_thread
            self.read_probe = False

    def writer_text(self):
        """Who holds the writer (ADR-A178); unknown holders never assume an App."""
        zh = str(self.cfg.get('language', 'en')).startswith('zh')
        w = getattr(self, 'writer', None) or {}
        from . import codex_procs
        return codex_procs.text(w.get('kind') or 'unknown', w.get('pid'), zh)

    async def find_writer(self, path):
        from . import codex_procs
        current = self.proc.pid if self.proc is not None and isinstance(getattr(self.proc, 'pid', None), int) else None
        try:
            w = await asyncio.to_thread(codex_procs.who, self.host.st, path, current)
        except Exception:  # noqa: BLE001 — not knowing is said as such
            w = {'kind': 'unknown', 'pid': None, 'how': None}
        self.note('shared_writer', w.get('kind'))
        return w

    async def end_leftover(self, w):
        """Our own leftover app-server holds the thread: end it (process group), tell the phone, retry the resume once."""
        from . import codex_procs
        pid = w.get('pid')
        if not isinstance(pid, int) or pid not in await asyncio.to_thread(codex_procs.ours_alive, self.host.st,
                                                                          {self.proc.pid} if self.proc is not None and isinstance(getattr(self.proc, 'pid', None), int) else set()):
            return False
        gone = await asyncio.to_thread(codex_procs._end, pid)
        if not gone:
            return False
        codex_procs.forget(self.host.st, pid)
        self.note('shared_writer_cleaned', 'agentj')
        zh = str(self.cfg.get('language', 'en')).startswith('zh')
        self.host.agent_notice(codex_procs.text('agentj_cleaned', None, zh))
        self.writer = {}
        return True

    def writer_notice(self):
        self.note('shared_read_only', 'desktop writer active')
        key = (self.cfg.get('shared_session_id'), 'desktop writer active')
        if key not in self.told:
            self.told.add(key)
            self.host.agent_notice(self.writer_text())

    def read_desktop(self):
        sid = self.cfg.get('shared_session_id') or discover(self.cfg['dir'])
        if not sid:
            return
        self.cfg['shared_session_id'] = sid
        if self.rollout is None or self.rollout_sid != sid:
            path = selected_rollout(self.cfg['dir'], sid)
            meta = first_meta(path)
            if not meta or meta.get('id') != sid or os.path.realpath(meta.get('cwd') or '') != os.path.realpath(self.cfg['dir']):
                raise Refusal('wrong thread/project')
            self.rollout, self.rollout_sid = path, sid
            # Capture the size before scanning: any concurrent append is reread, never skipped.
            self.offset = path.stat().st_size
            self.follow_status('following')
            self.desktop_model(scan(path)[1])
            self.host.st.set_agent_session(self.kind, sid)
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
                if item.get('type')=='turn_context' and isinstance(p.get('model'),str):
                    self.desktop_model(p)
                if self.phone_turn: continue
                if item.get('type')=='response_item' and p.get('type')=='message':
                    role=p.get('role')
                    text='\n'.join(b.get('text','') for b in p.get('content',[]) if isinstance(b,dict) and b.get('type') in ('input_text','output_text'))
                    from .transcript_input import human_input
                    if role == 'user' and not human_input(p, p.get('content'), text):
                        continue
                    if text and role in ('user','assistant'):
                        (self.host.desktop_input if role=='user' else self.host.desktop_text)(public_text(text))
                elif item.get('type')=='event_msg' and p.get('type') in ('task_complete','turn_aborted'):
                    self.host.desktop_end()

    async def observe(self):
        probed = None
        while True:
            try:
                self.read_desktop()
                if self.rollout_sid and probed != self.rollout_sid and not self.phone_turn and self.proc is None and not scan(self.rollout)[2]:
                    # Serialize against message/model commands using the same host turn lock.
                    async with self.host.turn_lock:
                        if not self.phone_turn and self.proc is None:
                            await self.probe_writer()
                            probed = self.rollout_sid
            except (OSError,ValueError): pass
            await asyncio.sleep(.25)

    async def stop(self):
        if self.observer:
            self.observer.cancel()
            with contextlib.suppress(asyncio.CancelledError): await self.observer
        await super().stop()
        self.follow_status(None)
        if self.risk_channel:
            await self.risk_channel.stop()

    async def _spawn(self):
        if not self.read_probe and self.cfg.get('high_risk_warnings',True) and not self.risk_channel:
            self.risk_channel=shared_hook.Channel(self.risk,shared_hook.channel_path(self.host.st.root,'codex'))
            await self.risk_channel.start()
            shared_hook.install(self.cfg['dir'],self.risk_channel.path,family='codex')
        return await super()._spawn()

    def argv(self):
        argv=super().argv()
        if self.risk_channel and not self.read_probe:
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


    async def _hook_config(self):
        """Trust only this host-generated command at its current native hash. Never
        bypass trust for owner hooks. None when Codex did not load it (degraded, told)."""
        if not self.risk_channel:
            return None
        try:
            listing=await self.call('hooks/list',{'cwds':[self.cfg['dir']]})
        except RPCError:
            listing={}
        command=shared_hook.settings(self.risk_channel.path,('PreToolUse',))['hooks']['PreToolUse'][0]['hooks'][0]['command']
        trusted={}
        covered=False
        for entry in (listing or {}).get('data',[]):
            if os.path.realpath(entry.get('cwd','')) != os.path.realpath(self.cfg['dir']): continue
            for hook in entry.get('hooks',[]):
                if hook.get('command') != command or hook.get('handlerType') != 'command': continue
                if not hook.get('enabled') or not hook.get('currentHash') or not hook.get('key'): continue
                trusted[hook['key']]={'trusted_hash':hook['currentHash']}
                covered |= hook.get('eventName') == 'preToolUse'
        if not covered:
            self.degraded('shared pre-execution hook unavailable',
                          '这次没有高危提醒，Codex 自己的审批和沙箱照常生效。', 'Codex native approvals and sandbox still apply.')
            return None
        self.host.emit('shared_guard_ready',agent=self.kind,scope='thread',hooks=len(trusted))
        return {'hooks.state':trusted}

    def note(self, ev, reason=None, **kw):
        """Why a continuation degraded or was refused: event + host.log + `agentj activity` (metadata only)."""
        self.host.emit(ev, agent=self.kind, reason=reason, **kw)
        with contextlib.suppress(Exception):
            self.host.st.log(ev, agent=self.kind, reason=reason)
        with contextlib.suppress(Exception):
            from . import activity
            activity.record(self.host.st, 'shared', ev=ev, agent=self.kind, reason=reason)

    def degraded(self, code, zh, en):
        a, b = REASONS.get(code, (code, code))
        self.note('shared_degraded', code)
        key = (self.cfg.get('shared_session_id'), code)
        if key in self.told:
            return
        self.told.add(key)
        self.host.agent_notice(f'{a}{zh} / {b} {en}')

    async def wait_desktop(self, path):
        """Queue behind a running desktop turn instead of refusing. /stop ends the wait."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + DESKTOP_WAIT
        told = False
        active = scan(path)[2]
        pos = path.stat().st_size
        while active:
            if not told:
                told = True
                self.note('shared_waiting', 'desktop turn active; alternate turns')
                self.host.agent_notice('电脑上的 Codex 正在回答，等它这一轮结束后自动发出你的消息。 / '
                                       'The desktop Codex is answering; your message is sent when that turn ends.')
            if self.turn_done is not None and self.turn_done.is_set():
                raise Stopped()
            if loop.time() > deadline:
                raise Refusal('desktop turn did not finish')
            await asyncio.sleep(1)
            with path.open('rb') as f:                     # only the new complete lines
                f.seek(pos)
                for line in f:
                    if not line.endswith(b'\n'): break
                    pos += len(line)
                    if b'"event_msg"' not in line: continue
                    try: kind=(json.loads(line).get('payload') or {}).get('type')
                    except (ValueError, AttributeError): continue
                    if kind == 'task_started': active = True
                    elif kind in ('task_complete', 'turn_aborted'): active = False

    async def _resume(self, sid, path):
        """Resume the owner's thread: verified original permissions, else the owner's Codex defaults."""
        deferred = None
        from . import codex_perm
        self.perm_source = 'codex'
        try:
            original,context=owner_context(path,self.cfg['dir'],sid)
            self.perm_source = 'desktop'
        except Refusal as why:
            if why.code in ('wrong thread/project', 'desktop turn active; alternate turns'): raise
            original=None
            context = scan(path)[1] if why.code == 'unverified original permission profile' else None
            deferred = (why.code, '已按你 Codex 自己的默认权限接着这个会话。',
                          "Continuing it with your own Codex default permissions.")
        params={'threadId':sid,'excludeTurns':True}
        provider = self.human.get('model_provider')
        if isinstance(provider, str) and provider:
            params['modelProvider'] = provider
        if codex_perm.default_desktop_record(context):
            # F30 (ADR-A175): the thread only ever had Codex's built-in default permissions — nobody chose them. The owner's
            # explicit config.toml sandbox_mode applies, else Agent J's default (directly on the system). Approvals stay
            # the thread's own (on-request); the high-risk hook and the danger list are unchanged.
            mode, self.perm_source = codex_perm.main_sandbox(self.human.get('sandbox_mode'))
            native = {'read-only':'readOnly','workspace-write':'workspaceWrite','danger-full-access':'dangerFullAccess'}[mode]
            original = {'approvalPolicy': context.get('approval_policy'), 'approvalsReviewer': context.get('approvals_reviewer') or 'user',
                        'sandboxPolicy': {'type': native}}
            deferred = None
        if original:
            params.update(approvalPolicy=original['approvalPolicy'],approvalsReviewer=original['approvalsReviewer'])
            if 'permissions' in original: params['permissions']=original['permissions']
            else:
                params['sandbox']={'readOnly':'read-only','workspaceWrite':'workspace-write','dangerFullAccess':'danger-full-access'}.get(original['sandboxPolicy']['type'])
                if params['sandbox'] is None:
                    # externalSandbox has no resume override: the owner's defaults decide.
                    for k in ('approvalPolicy','approvalsReviewer','sandbox'): params.pop(k,None)
                    original=None
                    self.perm_source = 'codex'
                    deferred = ('unverified resume sandbox', '已按你 Codex 自己的默认权限接着这个会话。',
                                  "Continuing it with your own Codex default permissions.")
        hooks=await self._hook_config()
        if hooks: params['config']=hooks
        res=await self.call('thread/resume',params)
        if isinstance(provider, str) and provider and res.get('modelProvider') != provider:
            raise RPCError({'message': 'resume did not apply current model provider'})
        if deferred:
            self.degraded(*deferred)
        if (res.get('thread') or {}).get('id')!=sid:
            raise RPCError({'message':'resume returned different thread'})
        if original and (res.get('approvalPolicy') != original['approvalPolicy'] or res.get('approvalsReviewer') != original['approvalsReviewer']
                         or ('permissions' in original and (res.get('activePermissionProfile') or {}).get('id') != original['permissions'])):
            # Codex's own answer wins: deliver under what it actually resumed with.
            self.note('shared_degraded', 'native resume changed original policy/profile')
            original=None
            self.perm_source = 'codex'
        self.original=original or {}
        return res

    async def _new_thread(self, why):
        """No resumable owner thread: an ordinary native thread, same folder, the owner's own Codex defaults."""
        params={'cwd':self.cfg['dir']}
        hooks=await self._hook_config()
        if hooks: params['config']=hooks
        try:
            res=await self.call('thread/start',params)
        except RPCError as e:
            self.note('shared_refused', brief(e))
            raise
        sid=((res or {}).get('thread') or {}).get('id')
        if not isinstance(sid,str) or not UUID.fullmatch(sid):
            raise RPCError({'message':'no thread'})
        self.original={}
        self.perm_source='codex'
        self.cfg['shared_session_id']=sid
        self.note('shared_new_thread', why)
        self.host.agent_notice(f'已为你新开一个 Codex 会话（电脑上 `codex resume {sid}` 可以接着聊）。 / '
                               f'Started a new Codex session for you (continue it on the computer with `codex resume {sid}`).')
        return res

    async def _thread(self):
        sid=self.cfg.get('shared_session_id')
        why=None
        if not (isinstance(sid,str) and UUID.fullmatch(sid)):
            sid=discover(self.cfg['dir'])
            why=None if sid else 'exact Codex thread ID required'
        path=None
        if sid:
            try:
                path=selected_rollout(self.cfg['dir'],sid)
                meta=first_meta(path)
                if not meta or meta.get('id')!=sid or os.path.realpath(meta.get('cwd') or '')!=os.path.realpath(self.cfg['dir']):
                    raise Refusal('wrong thread/project')
            except Refusal as r:
                path,why=None,r.code
        res=None
        healed=False
        while path and res is None:
            await self.wait_desktop(path)
            try:
                res=await self._resume(sid,path)
            except Refusal as r:
                if r.code == 'desktop turn active; alternate turns': continue    # it started again: wait again
                why,path=r.code,None
            except RPCError as e:
                if active_writer(e):
                    # ADR-A178: who holds it; our own leftover app-server is ended and the resume retried once
                    self.writer = await self.find_writer(path)
                    if self.writer.get('kind') == 'agentj' and not healed:
                        healed = True
                        if await self.end_leftover(self.writer):
                            continue
                    self.cfg['shared_session_id'] = sid
                    self.read_desktop()
                    self.desktop_model(scan(path)[1])
                    self.follow_status('desktop_writer')
                    self.writer_notice()
                    raise Refusal('desktop writer active') from e
                why,path=brief(e),None
        if res is None:
            res=await self._new_thread(why or 'thread rollout unavailable or ambiguous')
        sid=res['thread']['id']
        self.cfg['shared_session_id']=sid
        self.tid=sid
        self.follow_status(None)
        self.told.discard((sid, 'desktop writer active'))
        self.thread={k:res.get(k) for k in ('model','sandbox','approvalPolicy','approvalsReviewer','cwd','reasoningEffort','modelProvider')}
        if path and not self.thread.get('model'):
            _, latest, _ = scan(path)
            from . import slash
            if isinstance(latest,dict) and isinstance(latest.get('model'),str) and slash.MODEL_RE.fullmatch(latest['model']):
                self.thread['model']=latest['model']
        mid=self.thread.get('model');self.meter(model=mid,model_name=self.model_name(mid), effort=self.thread.get('reasoningEffort'))
        self.host.st.set_agent_session(self.kind,sid)
        return True

    async def turn(self,text):
        try:
            self.turn_done=asyncio.Event()
            if not await self._ready(): return
            self.phone_turn=True
            self.risk.reset()
            self.turn_done,self.turn_proc=asyncio.Event(),self.proc
            self.turn_id,self.turn_status=None,{}
            choice = self.phone_choice()
            res=await self.deliver(lambda:self.call('turn/start',{'threadId':self.tid,
                'input':[{'type':'text','text':text,'text_elements':[]}],**self.original, **choice}))
            self.revert = None
            if choice.get('model'): self.thread['model'] = choice['model']
            if choice.get('effort'): self.thread['reasoningEffort'] = choice['effort']
            self.meter(model=self.thread.get('model'), model_name=self.model_name(self.thread.get('model')),
                       effort=self.thread.get('reasoningEffort'))
            self.turn_id=(res.get('turn') or {}).get('id')
            await self.turn_done.wait()
            self.read_desktop()
        except Stopped:
            pass
        except (ValueError,RPCError,OSError) as error:
            reason=brief(error)
            self.note('shared_refused', reason)
            zh, en = REASONS.get(reason, (None, None))
            if reason == 'desktop writer active':
                self.local_fail(self.writer_text())
            elif zh:
                self.local_fail(f'Codex 没收到这条消息：{zh}在电脑上处理后重发；看原因跑 `agentj activity`。 / '
                                f'Not delivered to Codex: {en} Fix it on the computer and resend; `agentj activity` shows why.')
            elif isinstance(error, RPCError):
                self.local_fail(pick(getattr(self.host, "lang", "zh"), f'Codex 拒绝了这条消息：{reason}。', f'Codex refused this message: {reason}.'))
            else:
                self.local_fail(f'Codex 没收到这条消息（{reason}）。跑 `agentj doctor` 看看。 / '
                                f'Not delivered to Codex ({reason}). Run `agentj doctor`.')
        finally:
            # Release this actor after its turn so the native desktop can resume
            # the same rollout. The observer stays alive between actors.
            if self.proc is not None:
                await self._kill(self.proc)
            self.phone_turn=False
            if self.rollout is not None and self.shared_status != 'desktop_writer':
                self.follow_status('following')

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
