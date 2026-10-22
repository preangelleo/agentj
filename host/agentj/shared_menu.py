"""Claude native modals: local screen → existing signed question → freshly verified fixed keys.

No screen, draft, conversation or arbitrary menu text is logged or sent to a remote.
Only Rewind is auto-dismissed. Permissions/questions belong to the hooks, never this reader.
"""
from __future__ import annotations
import asyncio
import codecs
import os
import re
import time
import unicodedata
from .text import pick


class Screen:
    """Bounded visible PTY surface, not scrollback. Incremental UTF-8/CSI/OSC; unknown commands fail closed."""
    def __init__(self, rows=40, cols=120):
        self.rows, self.cols = rows, cols
        self.grid = [[' '] * cols for _ in range(rows)]
        self.y = self.x = 0
        self.saved = (0, 0)
        self.pending = ''
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.valid = True

    def feed(self, data):
        s = self.pending + self.decoder.decode(data)
        self.pending = ''
        i = 0
        while i < len(s):
            c = s[i]
            if c == '\x1b':
                m = re.match(r'\x1b\[([0-9;?]*)([ -/]*)([@-~])', s[i:])
                osc = re.match(r'\x1b\].*?(?:\x07|\x1b\\)', s[i:], re.S)
                if m:
                    self._csi(m[1], m[3]); i += len(m[0]); continue
                if osc:
                    i += len(osc[0]); continue
                if i + 1 >= len(s) or s[i+1] in '[]':
                    self.pending = s[i:][-4096:]; break
                if s[i+1] == '7': self.saved = (self.y, self.x)
                elif s[i+1] == '8': self.y, self.x = self.saved
                else: self.valid = False
                i += 2; continue
            if c == '\r': self.x = 0
            elif c == '\n':
                self.y += 1
                if self.y >= self.rows:
                    self.grid.pop(0); self.grid.append([' '] * self.cols); self.y = self.rows - 1
            elif c == '\b': self.x = max(0, self.x - 1)
            elif c == '\t': self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
            elif c >= ' ' and c != '\x7f' and not unicodedata.combining(c):
                if self.x >= self.cols:
                    self.x = 0; self.y = min(self.rows - 1, self.y + 1)
                self.grid[self.y][self.x] = c
                w = 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1
                if w == 2 and self.x+1 < self.cols: self.grid[self.y][self.x+1] = ''
                self.x += w
            i += 1

    def _csi(self, args, cmd):
        a = [int(n or 0) for n in args.lstrip('?').split(';')]
        n = a[0] or 1
        if cmd in ('H', 'f'): self.y, self.x = min(self.rows-1,n-1), min(self.cols-1,(a[1] or 1)-1 if len(a)>1 else 0)
        elif cmd == 'A': self.y = max(0,self.y-n)
        elif cmd == 'B': self.y = min(self.rows-1,self.y+n)
        elif cmd == 'C': self.x = min(self.cols-1,self.x+n)
        elif cmd == 'D': self.x = max(0,self.x-n)
        elif cmd == 'G': self.x = min(self.cols-1,n-1)
        elif cmd == 'd': self.y = min(self.rows-1,n-1)
        elif cmd in ('J','K'):
            mode=a[0]
            for y in range(self.rows) if cmd=='J' else [self.y]:
                for x in range(self.cols):
                    if mode in (2,3) or (mode==0 and (y,x)>=(self.y,self.x)) or (mode==1 and (y,x)<=(self.y,self.x)):
                        self.grid[y][x]=' '
            if cmd=='J' and mode in (2,3): self.valid=True
        elif cmd == 's': self.saved=(self.y,self.x)
        elif cmd == 'u': self.y,self.x=self.saved
        elif cmd not in ('m','h','l','q'): self.valid=False

    def text(self):
        return '\n'.join(''.join(row).rstrip() for row in self.grid) if self.valid and not self.pending else ''


def detect(text):
    """Only active, anchored native dialogs. Never a historical quote above a new input prompt."""
    if not isinstance(text, str): return None
    text = text[-12000:]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines: return None
    # A native input prompt below a quoted menu proves that menu is history.
    if any(re.match(r'^[❯>]\s*(?![1-4]\.)', line) for line in lines[-2:]): return None
    tail = '\n'.join(lines[-18:])
    if not re.search(r'Esc(?:ape)? to (?:cancel|exit|close)', tail, re.I): return None
    if re.search(r'^(?:❯\s*)?Rewind\s*$', tail, re.M) and 'Restore the code and/or conversation' in tail:
        return {'kind':'rewind','reset':'','keys':[]}
    quota = re.search(r"You[’']ve hit your (?:(?:session|weekly|usage) )?limit[^\n]*?resets\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\d{1,2}:\d{2}\b)",tail,re.I)
    if quota and 'What do you want to do?' in tail:
        labels = []
        cursor = None
        known = (r'Stop and wait', r"Don[’']t continue", r'(?:Use )?(?:extra )?usage credits', r'Upgrade(?: your plan)?')
        for line in lines[-12:]:
            m = re.fullmatch(r'(?:❯\s*)?([1-4])\.\s+(.+)',line)
            if m:
                num=int(m[1])
                if line.startswith('❯'): cursor = num
                if re.fullmatch(known[num-1],m[2],re.I): labels.append(m[1])
                else: return None
        if labels == ['1','2','3','4']:
            return {'kind':'quota','reset':quota[1].strip(),'keys':labels,'cursor':cursor}
    # Other known native menus expose only Escape, never arbitrary rows or Enter.
    if re.search(r'^(?:Select model|Resume Session|Settings|Manage MCP servers)\s*$',tail,re.M):
        return {'kind':'menu','reset':'','keys':[]}
    return None


def questions(info, lang):
    quota = info['kind']=='quota'
    text = pick(lang, 'Claude Code 额度已用完，恢复时间：', 'Claude Code limit reached; resets ') + info['reset'] if quota else pick(lang,'Claude Code 停在回退菜单。','Claude Code is in a menu; you can exit it.').replace('回退菜单', '菜单')
    labels = [pick(lang,'停止并等待恢复','Stop and wait'),pick(lang,'不再继续','Don’t continue'),
              pick(lang,'使用额外用量（可能收费）','Use extra credits (may cost money)'),
              pick(lang,'升级套餐（可能收费）','Upgrade (may cost money)')] if quota else []
    labels.append(pick(lang,'退出菜单（Esc）','Exit menu (Esc)'))
    return [{'q':text,'h':'Claude Code','m':False,'o':[{'l':s,'d':''} for s in labels]}]


class Desk:
    def __init__(self, agent):
        self.agent=agent
        self.stopping=False
        self.rewind_auto=False
        self.surface=Screen()
        self.current=None
        self.task=None
        self.gone=None
        self.poll_lock=asyncio.Lock()
        self.last_poll=0
        self.key=None
        self.attempted=False
        self.retry_at=0
        self.closed=asyncio.Event(); self.closed.set()

    def blocked(self):
        stopped=getattr(self.agent.host,"stopped",None)
        return self.stopping or not callable(stopped) or bool(stopped())

    async def snapshot(self):
        a=self.agent
        if not a.session: return None,None
        if a.native and a.native.pid==a.session.get('pid') and a.native.returncode is None and a.master is not None:
            return (detect(self.surface.text()) if self.surface.valid and not self.surface.pending else False),None
        from .shared_interrupt import herdr_bin,_agents
        from .shared_modelswitch import screen
        def read():
            binary=herdr_bin()
            sid=a.session.get('sessionId')
            pane=next((x.get('pane_id') for x in _agents(binary) if sid and (x.get('agent_session') or {}).get('value')==sid),None) if binary else None
            if not pane: return False,None
            text=screen(binary,pane)
            return (detect(text) if text is not None else False),(binary,pane)
        return await asyncio.to_thread(read)

    async def send(self, route, key):
        a=self.agent
        if self.blocked() or a.state.status=='waiting' or key not in ('esc','1','2','3','4'): return False
        commands=['esc']
        if key != 'esc':
            info,fresh_route=await self.snapshot()
            if not info or info['kind']!='quota' or fresh_route!=route or info.get('cursor') not in (1,2,3,4): return False
            commands=['down'] * ((int(key)-info['cursor']) % 4) + ['enter']
        if a.native and a.native.pid==a.session.get('pid') and a.native.returncode is None and a.master is not None:
            try: os.write(a.master,b''.join({'esc':b'\x1b','down':b'\x1b[B','enter':b'\r'}[k] for k in commands))
            except OSError: return False
            return True
        if not route: return False
        from .shared_interrupt import _run
        binary,pane=route
        # The pane may have been reused since the snapshot; require the session-id match too.
        from .shared_interrupt import _agents
        def write():
            if self.blocked() or a.state.status=='waiting': return False
            if not any(x.get('pane_id')==pane and (x.get('agent_session') or {}).get('value')==a.session.get('sessionId') for x in _agents(binary)):
                return False
            return _run(binary,'agent','send-keys',pane,*commands) is not None
        return await asyncio.to_thread(write)

    def withdraw(self):
        if self.gone and not self.gone.done(): self.gone.set_result(None)

    async def poll(self, force=True):
        async with self.poll_lock:
            now=time.monotonic()
            if not force and now-self.last_poll<2: return
            self.last_poll=now
            await self._poll()

    async def _poll(self):
        a=self.agent
        if self.stopping or not a.session or not isinstance(a.session.get("pid"), int):
            self.withdraw();self.current=None;self.key=None;self.closed.set()
            return
        if a.native_lock.locked() or a.state.status=='waiting' or self.blocked():
            self.withdraw()
            self.current=None;self.key=None;self.closed.set()
            return
        info,route=await self.snapshot()
        identity=tuple(a.session.get(k) for k in ('sessionId','pid','startedAt')) if a.session else None
        if info is False:
            if self.key and self.key[0]!=identity:
                self.withdraw();self.current=None;self.key=None;self.closed.set()
            return  # unreadable is not proof that a previously seen menu closed
        key=(identity,info)
        if key != self.key:
            self.withdraw()
            self.key=key;self.current=info;self.attempted=False;self.retry_at=0;self.rewind_auto=False
            if info: self.closed.clear()
            else: self.closed.set()
        if self.task and self.task.done() and not self.task.cancelled():
            error=self.task.exception()
            if error:
                a.host.st.log('shared_menu_error',error=type(error).__name__)
                self.retry_at=time.monotonic()+30;self.attempted=False
            self.task=None
        if not info or (self.task and not self.task.done()) or self.attempted or time.monotonic()<self.retry_at: return
        self.attempted=True
        self.gone=asyncio.get_running_loop().create_future()
        from . import bg
        self.task=bg.spawn(self._act(info,route,identity,self.gone), "shared_menu", bg.host_log(a.host))

    async def _act(self, info, route, identity, gone):
        a=self.agent;lang=getattr(a.host,'lang','zh')
        if info['kind']=='rewind' and not self.rewind_auto:
            self.rewind_auto=True
            key='esc'
        else:
            a.host.turn_queued_note(False)
            if a.phone_turn:
                a.host.agent_notice(questions(info,lang)[0]['q'])
                a.phone_reply_seen=True
                a.done.set()  # End waiting for this blocked turn; no fake Stop or delivery receipt.
            decision,picks=await a.host.question(questions(info,lang),gone=gone)
            if decision=='answer' and picks and len(picks)==1 and isinstance(picks[0],list) and len(picks[0])==1:
                n=picks[0][0]
                if not isinstance(n,int) or not 1<=n<=len(info['keys'])+1: return
                key=(info['keys']+['esc'])[n-1]
            elif decision=='cancel': key='esc'
            else:
                if self.gone is gone and decision in ('timeout','no_device','answer'):
                    self.retry_at=time.monotonic()+(2 if decision=='answer' else 30);self.attempted=False
                    if decision=='answer':
                        a.host.agent_notice(pick(lang,'请点菜单按钮；自填文字不会送到终端，请重新选择。','Choose a menu button; text replies do not control the terminal.'))
                return
        if gone.done() or self.blocked() or a.state.status=='waiting': return
        async with a.native_lock:
            fresh,fresh_route=await self.snapshot()
            now=tuple(a.session.get(k) for k in ('sessionId','pid','startedAt')) if a.session else None
            if gone.done() or self.blocked() or a.state.status=='waiting' or now != identity or fresh != info or fresh_route != route:
                if self.gone is gone:
                    self.attempted=False;self.retry_at=time.monotonic()+2
                return
            if await self.send(route,key):
                a.host.st.log('shared_menu_key',kind=info['kind'],key=key)
                if key=='esc': a._stop_esc_at=time.monotonic()
                if info['kind']=='rewind':
                    a.host.agent_notice(pick(lang,'Claude Code 停在回退菜单，已发送 Esc 退出。','Claude Code was in Rewind; sent Esc to exit.'))
                    self.attempted=False;self.retry_at=time.monotonic()+2
            elif self.gone is gone:
                self.attempted=False;self.retry_at=time.monotonic()+30
                a.host.agent_notice(pick(lang,'菜单操作未确认，请重试；仍无响应可在紧急修复中重开 AI 会话。','Menu action unconfirmed. Retry, or use Emergency repair to reopen the AI session.'))

    async def wait_clear(self):
        await self.poll()
        while self.current:
            if self.agent.halting or self.blocked(): return False
            await asyncio.sleep(.25)
            await self.poll(force=False)
        return True

    async def stop(self):
        self.stopping=True
        self.withdraw()
        if self.task and not self.task.done():
            # Let Host.question broadcast question_done before cancelling the old actor.
            try: await asyncio.wait_for(asyncio.shield(self.task), 2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.task.cancel()
                try: await self.task
                except asyncio.CancelledError: pass
        self.current=None;self.closed.set()
