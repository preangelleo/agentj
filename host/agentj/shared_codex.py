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
from . import danger, humane, shared_hook, shared_pin
from .shared_risk import RiskGuard

UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
DESKTOP_WAIT = 30*60        # s a phone message waits for a running desktop turn
KEEP_WAIT = 10              # P282: s a kept app-server waits for the queued next phone turn before it is released anyway
DISCOVER_LIMIT = 400        # newest rollouts inspected when no thread is selected
HEAL_GAP = 10*60            # P330: s the newest thread must lead the next one by to be followed without asking
HEAL_CHOICES = 4            # P330: threads offered on the 「接哪个」 card


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
    'desktop turn active; alternate turns': ('Agent J 主机上的 Codex 正在回答。', 'The desktop Codex is still answering.'),
    'unverified original permission profile': ('所选会话用了 Agent J 还没核对过的权限配置。', 'The selected thread uses a permission profile Agent J has not verified.'),
    'original permissions unavailable': ('读不到所选会话的权限。', 'The selected thread permissions are unreadable.'),
    'unsupported original sandbox': ('所选会话的沙箱类型 Agent J 不认识。', 'The selected thread uses an unknown sandbox type.'),
    'unverified resume sandbox': ('所选会话的沙箱类型 Agent J 不认识。', 'The selected thread uses an unknown sandbox type.'),
    'native resume changed original approval policy': ('Codex 接续后审批策略和原会话不一致。', 'Codex resumed with a different approval policy.'),
    'native resume changed original profile': ('Codex 接续后权限配置和原会话不一致。', 'Codex resumed with a different permission profile.'),
    'shared pre-execution hook unavailable': ('这个 Codex 版本没装上高危提醒钩子。', 'This Codex version did not load the high-risk warning hook.'),
    'desktop writer active': ('Agent J 主机上的 Codex App 正在使用这个会话，手机现在只读。完全退出 Codex App 后重发，就能接着同一个会话。', 'The Codex App on the Agent J host is using this session; the phone is read-only. Quit the Codex App completely, then resend to continue this same session.'),
    'desktop turn did not finish': ('Agent J 主机上的 Codex 回合 30 分钟还没结束。', 'The desktop Codex turn has not finished for 30 minutes.'),
    'shared session lost': ('共享会话找不到了：原来接着的那个 Codex 会话在这台Agent J 主机上已经没有记录。',
                            'The shared session is gone: the Codex thread it followed no longer exists on the Agent J host.'),
}

# P330 (FEEDBACK 26): the pinned thread's rollout is gone (Mac: cc99e6c3 deleted, the desktop on 01a13a75)
LOST = {
    'auto': ('共享会话找不到了（原来的 {old} 在这台Agent J 主机上已经没有记录），已改接Agent J 主机上最近在用的 Codex 会话 {new}。',
             'The shared session is gone ({old} no longer exists on the Agent J host); now following the Codex thread the '
             'computer used last, {new}.'),
    'chosen': ('好，共享会话改接 Codex 会话 {new}。', 'OK — the shared session now follows Codex thread {new}.'),
    'new': ('共享会话找不到了（原来的 {old} 在这台Agent J 主机上已经没有记录），这个目录里也没有别的 Codex 会话，已为你新开一个。',
            'The shared session is gone ({old} no longer exists on the Agent J host) and this folder has no other Codex '
            'thread, so a new one was started.'),
    'refused': ('共享会话找不到了（原来的 {old} 在这台Agent J 主机上已经没有记录），这条没有发出。重发一次会再让你选接哪个会话；'
                '也可以在Agent J 主机上运行 `agentj config set agent.shared_session_id <会话 ID>` 指定。',
                'The shared session is gone ({old} no longer exists on the Agent J host); this message was not sent. Resend to '
                'pick the thread to follow, or run `agentj config set agent.shared_session_id <thread ID>` on the Agent J host.'),
    'open_failed': ('共享会话找不到了（原来的 {old} 已经没有记录），Agent J 想接Agent J 主机上的会话或新开一个，但 Codex 没打开（{why}）。'
                    '先直接重发一次；还不行在Agent J 主机上运行 `agentj doctor`。',
                    'The shared session is gone ({old} no longer exists) and Codex did not open the replacement ({why}). '
                    'Resend once; if it fails again run `agentj doctor` on the Agent J host.'),
    'q': ('原来的共享会话 {old} 找不到了。这个目录里有几个 Codex 会话，手机接哪一个？',
          'The shared session {old} is gone. Several Codex threads are in this folder — which one should the phone follow?'),
    'h': ('接哪个会话', 'Which thread'),
    'opt': ('{sid} · {ago}更新', '{sid} · updated {ago} ago'),
    'new_opt': ('新开一个对话', 'Start a new conversation'),
    'new_desc': ('Agent J 主机上要接着它，运行 codex resume <新 ID>', 'continue it on the Agent J host with codex resume <new ID>'),
}


def ago(secs, zh):
    secs = max(0, int(secs))
    for unit, (z, e) in ((86400, ('天', 'd')), (3600, ('小时', 'h')), (60, ('分钟', 'min'))):
        if secs >= unit:
            return f'{secs // unit} {z}前' if zh else f'{secs // unit} {e}'
    return '刚刚' if zh else 'just now'
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


def effective_pin(st, pin):
    """P328: the owner's `agent.shared_session_id` unless a phone /clear superseded it (`codex.clear_from`): then the thread
    the phone moved to (per-harness state, `codex`), '' while the next message is still to open it. The global pin is never
    written for Codex — another harness must not follow a Codex thread id."""
    if isinstance(pin, str) and pin and pin == st.agent_session('codex.clear_from'):
        stored = st.agent_session('codex')
        return stored if isinstance(stored, str) and UUID.fullmatch(stored) else ''
    return pin if isinstance(pin, str) else ''


def clear_pending(st, pin=''):
    """P328: a phone /clear is waiting for the next message to open the new thread (nothing to discover or follow)."""
    prev = st.agent_session('codex.prev')
    stored = st.agent_session('codex')
    return (not effective_pin(st, pin) and isinstance(prev, str) and bool(prev)
            and not (isinstance(stored, str) and stored))


def sessions_root():
    return Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'sessions'


def first_meta(path):
    with contextlib.suppress(OSError, ValueError):
        with path.open('rb') as f:
            item = json.loads(f.readline(256*1024))
        if isinstance(item, dict) and item.get('type') == 'session_meta':
            return item.get('payload') or {}
    return None


def candidates(directory, exclude=(), keep=1, limit=DISCOVER_LIMIT, free=True, skip_pids=()):
    """P330: up to `keep` native Codex threads whose own cwd is exactly this project, newest first: [(id, mtime)].
    Reads first lines only. `free`: threads another Codex client has open (their rollout file, lsof / /proc) are left out
    — a thread a terminal codex is writing to would leave the phone read-only — unless every one is held."""
    root = sessions_root()
    try:
        files = sorted(((p, p.stat().st_mtime) for p in root.rglob('rollout-*.jsonl')), key=lambda x: x[1], reverse=True)[:limit]
    except OSError:
        return []
    want = os.path.realpath(directory)
    out = []
    room = keep + (HEAL_CHOICES if free else 0)          # look a little further: some of the newest may be held
    for path, mtime in files:
        meta = first_meta(path)
        sid = (meta or {}).get('id')
        if isinstance(sid, str) and UUID.fullmatch(sid) and path.name.endswith(f'-{sid}.jsonl') \
                and os.path.realpath(meta.get('cwd') or '') == want and sid not in exclude:
            out.append((sid, mtime, path))
            if len(out) >= room:
                break
    if free and out:
        from . import codex_procs
        try:
            busy = codex_procs.held([p for _, _, p in out], skip_pids)
        except Exception:  # noqa: BLE001 — not knowing never hides a thread
            busy = set()
        idle = [r for r in out if os.path.realpath(str(r[2])) not in busy]
        out = idle or out
    return [(sid, mtime) for sid, mtime, _ in out[:keep]]


def discover(directory, limit=DISCOVER_LIMIT, skip_pids=()):
    """Newest native Codex thread whose own cwd is exactly this project (P330: one no other client holds), else None."""
    rows = candidates(directory, keep=1, limit=limit, skip_pids=skip_pids)
    return rows[0][0] if rows else None


def heal_pick(rows):
    """P330: the thread to follow when the pinned one is gone → (id, how) — the only one, or the newest by a clear margin
    (HEAL_GAP); (None, 'ambiguous') = the owner picks on the phone; (None, 'none') = there is nothing to follow."""
    if not rows:
        return None, 'none'
    if len(rows) == 1:
        return rows[0][0], 'only'
    if rows[0][1] - rows[1][1] >= HEAL_GAP:
        return rows[0][0], 'newest'
    return None, 'ambiguous'


def selected_rollout(directory, sid):
    if not isinstance(sid, str) or not UUID.fullmatch(sid):
        raise Refusal('exact Codex thread ID required')
    matches = list(sessions_root().rglob(f'*-{sid}.jsonl'))
    if len(matches) != 1:
        r = Refusal('thread rollout unavailable or ambiguous')
        r.missing = not matches          # P330: gone (follow the desktop's thread) vs. two copies (never guessed)
        raise r
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
        self.kept = False                # P282: the app-server stayed up for the next queued phone turn (thread still loaded)
        self.desktop_wait = False        # P328: a phone message is waiting behind the owner's desktop turn (wait_desktop)
        self.owner_pin = self.cfg.get("shared_session_id") or ""     # P328: the owner's configured pin as serve read it
        st = self.host.st
        pin = effective_pin(st, self.owner_pin)
        if pin != self.owner_pin:
            self.cfg["shared_session_id"] = pin          # P328: a phone /clear moved off the owner's pin (restart-safe)
        if not self.cfg.get("shared_session_id"):
            stored=self.host.st.agent_session(self.kind)
            if isinstance(stored,str) and UUID.fullmatch(stored):
                self.cfg["shared_session_id"]=stored
        self.cleared = clear_pending(st, self.cfg.get("shared_session_id") or "")   # P328: next message opens a new thread
        self.lost_pin = None             # P330: the pinned thread this turn found gone (the error says so)
        self.heal_tried = set()          # P330: dead pins the observer already looked past (one rglob each)

    def _zh(self):
        return str(getattr(self.host, 'lang', None) or self.cfg.get('language', 'zh')).startswith('zh')

    def _lost_text(self, key, **kw):
        z, e = LOST[key]
        return (z if self._zh() else e).format(**kw)

    def _repin(self, dead, sid, how):
        """P330: follow `sid` from now on — this harness's own pin (agent.json `codex.pin`), never the global config."""
        self.cfg['shared_session_id'] = sid
        with contextlib.suppress(OSError):
            shared_pin.write(self.host.st, self.kind, sid)
        self.note('shared_repin', how)
        if ('repin', dead) not in self.told:
            self.told.add(('repin', dead))
            self.host.agent_notice(self._lost_text('chosen' if how == 'chosen' else 'auto', old=dead[:8], new=sid[:8]))

    async def _ask_rehome(self, dead, rows):
        """P330: several threads are equally likely: the owner picks on the phone (a signed §10.7 card).
        → the thread id, '' = start a new conversation, None = no answer (cancel / timeout / no phone)."""
        from .approvals import norm_questions
        import time
        i = 0 if self._zh() else 1
        now = time.time()
        opts = [{'label': LOST['opt'][i].format(sid=f'{sid[:8]}…{sid[-4:]}', ago=ago(now - m, i == 0)), 'description': ''}
                for sid, m in rows]
        opts.append({'label': LOST['new_opt'][i], 'description': LOST['new_desc'][i]})
        card = norm_questions([{'question': LOST['q'][i].format(old=dead[:8]), 'header': LOST['h'][i],
                                'multiSelect': False, 'options': opts}])
        ask = getattr(self.host, 'question', None)
        if card is None or ask is None:
            return None
        try:
            outcome, picks = await ask(card, None, task=getattr(self.host, 'task_label', None))
        except Exception:  # noqa: BLE001 — nobody answered
            return None
        if outcome != 'answer' or not picks or not picks[0]:
            return None
        n = picks[0][0]
        return rows[n - 1][0] if 1 <= n <= len(rows) else ''

    async def _rehome(self, dead):
        """P330 (FEEDBACK 26): the pinned thread's rollout is gone. Follow the desktop's current thread in this folder (the
        only one, or the newest by HEAL_GAP); several alike → the owner picks on a card; none → None (a new thread)."""
        self.lost_pin = dead
        rows = await asyncio.to_thread(candidates, self.cfg['dir'], {dead}, HEAL_CHOICES, DISCOVER_LIMIT, True, self._own_pids())
        sid, how = heal_pick(rows)
        if sid is None and how == 'ambiguous':
            sid = await self._ask_rehome(dead, rows)
            if sid is None:
                self.note('shared_pin_lost', 'unanswered')
                raise Refusal('shared session lost')
            how = 'chosen' if sid else 'new_chosen'
        if not sid:
            self.note('shared_pin_lost', how)
            return None, how
        self._repin(dead, sid, how)
        return sid, how

    async def _observe_heal(self, dead):
        """P330: the observer found the pinned rollout gone: follow an unambiguous successor right away (the phone shows
        the desktop again without a message); anything else waits for the next phone message (its card)."""
        if not dead or dead in self.heal_tried or dead == self.tid or self.phone_turn or self.proc is not None:
            return
        self.heal_tried.add(dead)
        rows = await asyncio.to_thread(candidates, self.cfg['dir'], {dead}, HEAL_CHOICES, DISCOVER_LIMIT, True, self._own_pids())
        sid, how = heal_pick(rows)
        if not sid:
            self.note('shared_pin_lost', how)
            return
        async with self.host.turn_lock:
            if not self.phone_turn and self.proc is None and self.cfg.get('shared_session_id') == dead:
                self._repin(dead, sid, how)

    def _own_pids(self):
        p = getattr(self.proc, 'pid', None) if self.proc is not None else None
        return {p} if isinstance(p, int) else set()

    async def _ready(self):
        """CodexAgent._ready; P330: when the pinned thread was gone, the failure says so (not an unnamed TimeoutError); a
        loaded machine that answers past START_WAIT / CALL_WAIT gets one more try on a fresh app-server (Mac 10-16: the
        TimeoutError, then it opened fine)."""
        self.lost_pin = None
        for attempt in (1, 2):
            if self.proc is None and not await self._spawn():
                return False
            try:
                return await self._thread()
            except asyncio.TimeoutError as e:
                if attempt == 1:
                    self.note('shared_open_retry', 'TimeoutError')
                    if self.proc is not None:
                        with contextlib.suppress(Exception):
                            await self._kill(self.proc)
                    self.proc = None
                    continue
                err = e
            except (RPCError, ConnectionError, OSError) as e:
                err = e
            why = brief(err) if not isinstance(err, asyncio.TimeoutError) else 'TimeoutError'
            if self.lost_pin:
                self.fail_notice(self._lost_text('open_failed', old=self.lost_pin[:8], why=why))
            else:
                self.fail_notice(f"Codex 没能打开这段对话（{why}）。")
            return False
        return False

    async def drop_conversation(self):
        """P328: forget the loaded thread; a kept app-server (P282) is released, never reused for another thread."""
        self.kept = False
        if self.proc is not None:
            await self._kill(self.proc)
        await super().drop_conversation()

    async def cmd_clear(self, arg):
        """P328: the phone opens a new Codex conversation. Codex app-server has no call that moves another client (the
        desktop App / TUI) to a thread, and a `thread/start` thread is not written to disk before its first message (no one
        else can resume it, it is gone with our app-server). So /clear unpins here (per-harness state only, `.prev` kept
        for 「撤销清空」); the next phone message starts the new thread in this folder and says its `codex resume` id."""
        from .slash import Result
        st = self.host.st
        old = self.cfg.get('shared_session_id') or ('' if self.cleared else await asyncio.to_thread(discover, self.cfg['dir']))
        if not (isinstance(old, str) and UUID.fullmatch(old)):
            return Result(humane.of(self, 'clear.noop'), 'info', sep=True)
        path = self.rollout if self.rollout is not None and self.rollout_sid == old else None
        if path is None:
            with contextlib.suppress(OSError, ValueError):
                path = await asyncio.to_thread(selected_rollout, self.cfg['dir'], old)
        active = False
        if path is not None:
            with contextlib.suppress(OSError):
                active = (await asyncio.to_thread(scan, path))[2]
        if active:
            # The desktop is mid-answer on this thread: nothing is changed (not half a clear); the same command works after.
            self.note('shared_clear_refused', 'desktop turn active; alternate turns')
            return Result(humane.of(self, 'codex.clear_busy'), 'info')
        await self.drop_conversation()
        if self.owner_pin:
            st.set_agent_session(self.kind + '.clear_from', self.owner_pin)
        st.set_agent_session(self.kind + '.prev', old)
        st.set_agent_session(self.kind, None)
        shared_pin.write(st, self.kind, None)        # P330: Codex's own pin; the next message pins the new thread
        self.cfg['shared_session_id'] = ''
        self.cleared = True
        self.rollout = self.rollout_sid = None
        self.offset = 0
        self.writer = {}             # a desktop writer on the old thread does not hold the new one
        self.follow_status(None)
        self.meter(ctx=None)
        self.note('shared_clear', None)
        return Result(humane.of(self, 'clear.kept') + '\n' + humane.of(self, 'codex.clear_next'), sep=True, undo=True)

    async def cmd_undo_clear(self, arg):
        """P328: 「撤销清空」 until the next message opened the new thread (same window as Agent.cmd_undo_clear)."""
        from .slash import Result
        st = self.host.st
        prev = st.agent_session(self.kind + '.prev')
        if not (isinstance(prev, str) and prev):
            return Result(humane.of(self, 'undo.none'), 'error')
        if not self.cleared or st.agent_session(self.kind):
            st.set_agent_session(self.kind + '.prev', None)
            return Result(humane.of(self, 'undo.late'), 'error')
        await self.drop_conversation()
        st.set_agent_session(self.kind, prev)
        st.set_agent_session(self.kind + '.prev', None)
        shared_pin.write(st, self.kind, prev)
        if self.owner_pin and self.owner_pin == prev:
            st.set_agent_session(self.kind + '.clear_from', None)     # the owner's own pin is the conversation again
        self.cfg['shared_session_id'] = prev
        self.cleared = False
        self.rollout = self.rollout_sid = None
        self.meter(ctx=None)
        self.note('shared_clear_undone', None)
        return Result(humane.of(self, 'undo.done'), sep=True)

    def start(self):
        super().start()
        self.observer = asyncio.create_task(self.observe())

    async def reload_identity(self):
        """The owner's own session: no injected identity, never restarted for it (A1)."""

    def follow_status(self, status):
        self.shared_status = status
        from . import codex_procs
        w = self.writer or {}
        notice = {l: codex_procs.text(w.get('kind') or 'unknown', w.get('pid'), l == 'zh', w.get('via'))
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
        return codex_procs.text(w.get('kind') or 'unknown', w.get('pid'), zh, w.get('via'))

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
        sid = self.cfg.get('shared_session_id') or (None if self.cleared else discover(self.cfg['dir'], skip_pids=self._own_pids()))
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
                try:
                    self.read_desktop()
                except Refusal as r:
                    if getattr(r, 'missing', False):
                        await self._observe_heal(self.cfg.get('shared_session_id'))
                    raise
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
        if not self.read_probe and danger.shared_guard(self.cfg) and not self.risk_channel:   # P198: on by default
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
        self.desktop_wait = active      # P328: the phone's message waits behind the owner's desktop turn
        while active:
            if not told:
                told = True
                self.note('shared_waiting', 'desktop turn active; alternate turns')
                self.host.agent_notice(humane.of(self, 'codex.waiting'))
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
            self.desktop_wait = active

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
        if why == 'phone clear':   # P328/P380: only /clear owns an existing greeting card to update
            self.host.clear_resume(self.kind, sid)
        elif why == 'shared session lost':               # P330: the pinned thread is gone and nothing else is here
            self.host.agent_notice(self._lost_text('new', old=(self.lost_pin or '?')[:8]) + f' `codex resume {sid}`')
        else:
            self.host.agent_notice(f'已为你新开一个 Codex 会话（Agent J 主机上 `codex resume {sid}` 可以接着聊）。 / '
                                   f'Started a new Codex session for you (continue it on the Agent J host with `codex resume {sid}`).')
        return res

    async def _thread(self):
        if self.kept and self.proc is not None and self.proc.returncode is None and self.tid:
            self.kept=False      # P282: the previous phone turn left this thread loaded for us — no second spawn + resume
            return True
        self.kept=False
        sid=self.cfg.get('shared_session_id')
        why=None
        if not (isinstance(sid,str) and UUID.fullmatch(sid)) and self.cleared:
            sid,why=None,'phone clear'                # P328: never back to the thread the phone just cleared away from
        elif not (isinstance(sid,str) and UUID.fullmatch(sid)):
            sid=discover(self.cfg['dir'],skip_pids=self._own_pids())
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
                if getattr(r,'missing',False):
                    sid,how=await self._rehome(sid)          # P330: follow the desktop's thread (or ask), never a blind new one
                    why=None if sid else ('owner chose new' if how=='new_chosen' else 'shared session lost')
                    if sid:
                        path=selected_rollout(self.cfg['dir'],sid)
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
        self.cleared=False
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
        if self.host.st.agent_session(shared_pin.key(self.kind)) != sid:
            with contextlib.suppress(OSError):
                shared_pin.write(self.host.st,self.kind,sid)     # P330: Codex's own pin follows (never the global one)
        self.lost_pin=None
        return True

    async def turn(self,text):
        try:
            self.turn_done=asyncio.Event()
            if not await self._ready(): return
            self.phone_turn=True
            self.risk.reset()
            self.turn_done,self.turn_proc=asyncio.Event(),self.proc
            self.turn_id,self.turn_status=None,{}
            self.tail_reset()
            choice = self.phone_choice()
            await self.remind(False)    # P159: the owner's own thread keeps its identity; only the language rides hidden
            res=await self.deliver(lambda:self.call('turn/start',{'threadId':self.tid,
                'input':[{'type':'text','text':text,'text_elements':[]}],**self.original, **choice}))
            self.revert = None
            if choice.get('model'): self.thread['model'] = choice['model']
            if choice.get('effort'): self.thread['reasoningEffort'] = choice['effort']
            self.meter(model=self.thread.get('model'), model_name=self.model_name(self.thread.get('model')),
                       effort=self.thread.get('reasoningEffort'))
            self.turn_id=(res.get('turn') or {}).get('id')
            await self.await_turn()     # P282: turn/completed, or the quiet-tail fallback
            self.read_desktop()
            # P282 (Mac 10-14: 46–65 s per spawn + resume on a busy machine): a phone message already waiting keeps this
            # app-server and its loaded thread for the very next turn; released after KEEP_WAIT s in any case.
            self.kept = not self.halting and not self.q.empty() and self.shared_status != 'desktop_writer'
            if self.kept: self._bg(self._release_kept(self.proc))
        except Stopped:
            pass
        except (ValueError,RPCError,OSError) as error:
            reason=brief(error)
            self.note('shared_refused', reason)
            zh, en = REASONS.get(reason, (None, None))
            if reason == 'desktop writer active':
                self.local_fail(self.writer_text())
            elif reason == 'shared session lost':
                self.local_fail(self._lost_text('refused', old=(self.lost_pin or '?')[:8]))
            elif zh:
                self.local_fail(f'Codex 没收到这条消息：{zh}在Agent J 主机上处理后重发；看原因跑 `agentj activity`。 / '
                                f'Not delivered to Codex: {en} Fix it on the Agent J host and resend; `agentj activity` shows why.')
            elif isinstance(error, RPCError):
                self.local_fail(pick(getattr(self.host, "lang", "zh"), f'Codex 拒绝了这条消息：{reason}。', f'Codex refused this message: {reason}.'))
            else:
                self.local_fail(f'Codex 没收到这条消息（{reason}）。跑 `agentj doctor` 看看。 / '
                                f'Not delivered to Codex ({reason}). Run `agentj doctor`.')
        finally:
            # Release this actor after its turn so the native desktop can resume
            # the same rollout. The observer stays alive between actors.
            if self.proc is not None and not self.kept:
                await self._kill(self.proc)
            self.phone_turn=False
            self.desktop_wait=False
            if self.rollout is not None and self.shared_status != 'desktop_writer':
                self.follow_status('following')

    async def _release_kept(self, p):
        await asyncio.sleep(KEEP_WAIT)
        if self.kept and self.proc is p and not self.phone_turn:
            self.kept = False
            await self._kill(p)

    def _emit(self,text):
        super()._emit(public_text(text))

    async def halt(self,clear_queue=True):
        """/stop · the stop switch. The phone's own turn: app-server `turn/interrupt` (confirmed by the caller). P328: a turn
        the owner started on the Agent J host runs in the desktop's own Codex — not our app-server's — so the phone cannot stop
        it: say so (Esc / Ctrl+C on the Agent J host); a phone message queued behind it (wait_desktop) is withdrawn, as before."""
        busy=self.status=='working'
        if clear_queue:
            while not self.q.empty():
                item=self.q.get_nowait()
                if getattr(self.host,'queue_dropped',None):self.host.queue_dropped(item)
        asked=clear_queue or self.halt_quiet      # a person asked (/stop, stop-everything) — not a task / compaction making room
        waiting=asked and busy and self.desktop_wait and not self.phone_turn
        desktop=waiting or (asked and not self.phone_turn and await self._desktop_running())
        if not desktop:
            await self.interrupt_request(self.proc)
        if self.turn_done:self.turn_done.set()
        if desktop:
            sep='' if str(getattr(self.host,'lang','zh')).startswith('zh') else ' '
            self.cannot_halt(humane.of(self,'codex.cant_stop')+(sep+humane.of(self,'codex.cant_stop_withdrawn') if waiting else ''))
            return False
        return busy

    async def _desktop_running(self):
        """P328: the followed rollout shows a desktop turn in progress (read off the event loop)."""
        path=self.rollout
        if path is None:
            return False
        try:
            return bool(await asyncio.to_thread(lambda:scan(path)[2]))
        except (OSError,ValueError):
            return False
