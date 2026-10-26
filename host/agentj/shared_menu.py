"""Claude native modals: local screen → existing signed question → freshly verified fixed keys.

P538 (#303): a TUI menu is shown on the owner's paired phones as it stands — title, numbered rows with their description
column, the ❯ row — and a tapped row is reached with ↑/↓ and confirmed, each step re-read from the screen first. The model
picker is offered with versioned names and confirmed with `s` (this session only, the same as the phone's model label).
A modal whose rows cannot be read is shown as its screen text with an ↑ ↓ Enter Esc keypad, never as Esc alone.
Menu and screen text go only into the end-to-end signed question (credentials redacted, privacy.owner_text); nothing of
it is logged (approvals.log keeps hashes). Only Rewind is auto-dismissed. Permissions/questions belong to the hooks,
never this reader: anything that looks like a permission box is not a menu. A native question box the hooks
stopped waiting on (host restart, hook timeout) can still be answered from a paired phone as an ordinary menu.
P539: the "Session paused" card names only model names that pass a strict pattern; 「改一改再发」 hands the owner's OWN
phone message back to that phone.
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
    # (P539: `(?!\s*N.)` — the cursor on a menu's last row is a row, not a prompt; `\s*(?!N.)` backtracked past it.)
    if any(re.match(r'^[❯>](?!\s*\d{1,2}\.)', line) for line in lines[-2:]): return None
    tail = '\n'.join(lines[-18:])
    if not re.search(r'Esc(?:ape)? to (?:cancel|exit|close)', tail, re.I): return None
    end = max(i for i, line in enumerate(lines) if _ESC.search(line))
    # P538: a live modal owns the bottom of the screen: its key hint is the last line (at most one line under it, never an
    # input prompt or a rule). A menu quoted in a reply sits above the idle input box and the status line — it is history.
    if end < len(lines) - 2 or any(_RULE.match(x) or re.match(r'^[❯>](?!\s*\d{1,2}\.)', x) for x in lines[end + 1:]):
        return None
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
    paused = _paused(lines)
    if paused:
        return paused
    # P538 (#303): any other anchored modal. A permission / trust / plan box is the hooks' (an Enter there would answer it).
    start = max((i for i in range(end) if _RULE.match(lines[i]) or re.match(r'^[❯>](?!\s*\d{1,2}\.)', lines[i])), default=-1)
    if end - start < 2: return None             # a hint right under a rule is a status line, not a modal
    region = lines[start + 1:end + 1]
    if _PERMISSION.search('\n'.join(region[-24:])) or any(_ANSWER.match(m[3]) for m in map(_MROW.match, region) if m):
        return None
    # P550 (P538 × P539): any other "Session paused" variant (usage-credits sibling, unknown rows) and any modal offering
    # paid usage stay at the computer — only the exact P539 refusal card above is offered.
    if _PAID.search('\n'.join(region[-24:])) or any(x.strip(_BOX) == 'Session paused' for x in region): return None
    return parse_menu(region) or screen_card(region)


_PERMISSION = re.compile(r"Do you want to|Would you like to proceed|Allow (?:once|always)|Don.t allow|Yes, and don.t ask|\(y/n\)|"
                         r"tell Claude what to do|auto-accept|approve edits|bypass permissions|Yes, I accept|"
                         r"trust (?:the files|this folder)|New MCP server|MCP servers? (?:found|in this project)", re.I)
# Rows that answer a permission-like question (Yes / No / Allow / Deny / Approve / Trust …) are never offered either —
# also the owner's own /permissions rules list (P538 review: rule edits stay at the computer).
_ANSWER = re.compile(r"(?:Yes|No|Allow|Deny|Approve|Reject|Trust|Don.t|Always|Never)\b", re.I)
_PAID = re.compile(r"usage credits|extra usage|Upgrade (?:your )?plan", re.I)
_MROW = re.compile(r'^(❯\s*)?(\d{1,2})\.\s+(\S.*?)(\s+✔)?(?:\s{2,}(\S.*?))?\s*$')
_RULE = re.compile(r'^[─━═—\-]{6,}$')
_ESC = re.compile(r'Esc(?:ape)? to (?:cancel|exit|close)', re.I)
MAX_ROWS = 15                      # the phone card holds 16 options: rows + Esc
_BOX = '│┃|╭╮╰╯─━┌┐└┘ \t'
_NAME = r'[A-Za-z0-9][A-Za-z0-9 .\-]{0,39}'


def _paused(lines):
    """P539 (#299): Claude Code's "Session paused" refusal modal (2.1.296 `Yke`): title, body, then exactly the rows
    `1. Switch to <Fallback>` / `2. Edit prompt and retry[ with <Model>]` and the Esc hint. Its usage-credits sibling
    (same title, other rows) is not this."""
    rows = [line.strip(_BOX) for line in lines[-24:]]
    rows = [r for r in rows if r]
    if 'Session paused' not in rows:
        return None
    rows = rows[len(rows) - 1 - rows[::-1].index('Session paused'):]
    cursor, found = None, {}
    for r in rows:
        m = re.fullmatch(r'(❯\s*)?([1-9])\.\s+(.+)', r)
        if not m:
            continue
        n, label = int(m[2]), m[3].strip()
        if m[1]: cursor = n
        if n == 1 and (k := re.fullmatch(r'Switch to (?:the fallback model|(' + _NAME + r'))', label)):
            found[1] = k[1]
        elif n == 2 and (k := re.fullmatch(r'Edit prompt and retry(?: with (' + _NAME + r'))?', label)):
            found[2] = k[1]
        else:
            return None
    if sorted(found) != [1, 2] or cursor not in (1, 2):
        return None
    return {'kind': 'paused', 'reset': '', 'keys': ['1', '2'], 'cursor': cursor,
            'model': found[2] or '', 'fallback': found[1] or ''}


def _clip(text, n):
    text = ' '.join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + '…'


def parse_menu(lines):
    """Title, rows (label, description, ✔), the ❯ row and the key hint of the modal ending at the last Esc hint; None when
    the rows are not one unbroken numbered list with exactly one ❯."""
    end = max((i for i, line in enumerate(lines) if _ESC.search(line)), default=None)
    if end is None: return None
    first = last = None
    rows = []
    for i in range(end - 1, max(-1, end - 40), -1):
        m = _MROW.match(lines[i])
        if m:
            rows.insert(0, {'n': int(m[2]), 'l': m[3].strip(), 'd': (m[5] or '').strip(), 'on': bool(m[4]), 'cur': bool(m[1])})
            first, last = i, last if last is not None else i
        elif rows and i < first - 3:
            break                                   # more than 3 unnumbered lines: the list is over
        elif not rows and end - i > 4:
            return None                             # the rows must sit right above the key hints
    if not rows or len(rows) > MAX_ROWS or sum(r['cur'] for r in rows) != 1: return None
    if [r['n'] for r in rows] != list(range(rows[0]['n'], rows[0]['n'] + len(rows))): return None
    # Unnumbered lines inside the list continue the row above them (wrapped descriptions).
    i, k = first, 0
    for j in range(first + 1, last + 1):
        if _MROW.match(lines[j]): k += 1
        else: rows[k]['d'] = (rows[k]['d'] + ' ' + lines[j]).strip()
    head = []
    for j in range(first - 1, max(-1, first - 5), -1):
        if _RULE.match(lines[j]) or re.match(r'^[❯>](?!\s*\d{1,2}\.)', lines[j]): break
        head.insert(0, lines[j])
    hint = ' · '.join(lines[last + 1:end + 1])
    title = head[0] if head else 'Claude Code'
    info = {'kind': 'menu', 'reset': '', 'keys': [], 'title': _clip(title, 120), 'sub': _clip(' '.join(head[1:]), 300),
            'rows': [{**r, 'l': _clip(r['l'], 80), 'd': _clip(r['d'], 200)} for r in rows], 'hint': _clip(hint, 200)}
    if title == 'Select model':
        from .shared_modelswitch import row_name
        info['model'] = True
        info['names'] = [row_name(r['l'], r['d']) for r in rows]
    return info


def screen_card(lines):
    """The bottom of the screen (≤ 20 lines × 100 columns) for a modal whose rows could not be read."""
    tail = [line[:100] for line in lines[-20:]]
    return {'kind': 'screen', 'reset': '', 'keys': [], 'lines': tail}


def row_keys(info, n):
    """The arrow keys from the ❯ row to row number n, then the confirming key; None for a row that is not there."""
    rows = info.get('rows') or []
    at = next((i for i, r in enumerate(rows) if r['cur']), None)
    to = next((i for i, r in enumerate(rows) if r['n'] == n), None)
    if at is None or to is None: return None
    return (['down'] * (to - at) if to > at else ['up'] * (at - to)), ('s' if info.get('model') else 'enter')


def _owner(text):
    """Owner-channel redaction, then no control / bidi-format characters (a label must read as it is on screen)."""
    from .privacy import owner_text
    return ''.join(c for c in owner_text(text) if c == '\n' or unicodedata.category(c) not in ('Cc', 'Cf'))


def options(info, lang):
    """What each option of the card does, in order: a key name ('esc', '1'…'4', 'up', 'down', 'enter') or ('row', n)."""
    if info['kind'] == 'menu':
        return [('row', r['n']) for r in info['rows']] + ['esc']
    if info['kind'] == 'screen':
        return ['up', 'down', 'enter', 'esc']
    if info['kind'] == 'paused':
        return ['1', 'retry', 'edit', 'new']
    return list(info['keys']) + ['esc']


def actions(info):
    """What each card option does, in order (the card's option n → actions(info)[n-1])."""
    return options(info, None)


def questions(info, lang):
    if info['kind'] == 'paused':
        from .refusal_flag import paused_question
        return [paused_question(info, lang)]
    if info['kind'] == 'menu':
        model = info.get('model')
        o, seen = [], set()
        for k, r in enumerate(info['rows']):
            label = info['names'][k] if model else r['l']
            if r['on']: label += pick(lang, '（当前）', ' (current)')
            if r['cur']: label = '❯ ' + label
            label = _owner(label)
            while label in seen: label += ' '
            seen.add(label)
            d = r['d']
            if model:      # the description column without the version the label now says
                d = re.sub(r'^(?:Opus|Sonnet|Fable|Haiku)\s+\d+(?:\.\d+)?\s*(?:·\s*)?', '', d)
            o.append({'l': label, 'd': _owner(d)})
        o.append({'l': pick(lang, '退出菜单（Esc）', 'Exit menu (Esc)'), 'd': ''})
        if model:
            q = pick(lang, 'Claude Code 停在选模型菜单（/model）', 'Claude Code is in the model menu (/model)')
            h = pick(lang, '点哪个就把本会话换成哪个（只改本会话，和手机模型标签一样）',
                     'Tap one to switch this session to it (this session only, like the phone’s model label)')
        else:
            q = _owner(pick(lang, 'Claude Code 停在菜单：', 'Claude Code is in a menu: ') + info['title'])
            h = _owner(_clip(' · '.join(x for x in (info['sub'], pick(lang, '点哪项就选哪项（终端里的 ↑↓ + Enter）',
                                                                       'Tap a row to choose it (↑↓ + Enter in the terminal)')) if x), 300))
        return [{'q': q, 'h': h, 'm': False, 'o': o}]
    if info['kind'] == 'screen':
        q = pick(lang, 'Claude Code 停在终端画面，按键操作：', 'Claude Code is on a terminal screen; use the keys:')
        o = [{'l': pick(lang, '↑ 上移', '↑ Up'), 'd': ''}, {'l': pick(lang, '↓ 下移', '↓ Down'), 'd': ''},
             {'l': pick(lang, 'Enter 确认', 'Enter'), 'd': pick(lang, '选中 ❯ 指着的那一项', 'Chooses the ❯ row')},
             {'l': pick(lang, '退出菜单（Esc）', 'Exit menu (Esc)'), 'd': ''}]
        return [{'q': q, 'h': _owner('\n'.join(info['lines']))[:2400] + '\n', 'm': False, 'o': o}]
    quota = info['kind']=='quota'
    text = pick(lang, 'Claude Code 额度已用完，恢复时间：', 'Claude Code limit reached; resets ') + info['reset'] if quota else pick(lang,'Claude Code 停在回退菜单。','Claude Code is in a menu; you can exit it.').replace('回退菜单', '菜单')
    labels = [pick(lang,'停止并等待恢复','Stop and wait'),pick(lang,'不再继续','Don’t continue'),
              pick(lang,'使用额外用量（可能收费）','Use extra credits (may cost money)'),
              pick(lang,'升级套餐（可能收费）','Upgrade (may cost money)')] if quota else []
    labels.append(pick(lang,'退出菜单（Esc）','Exit menu (Esc)'))
    return [{'q':text,'h':'Claude Code','m':False,'o':[{'l':s,'d':''} for s in labels]}]


def prompt_ready(text):
    """The terminal shows its input prompt with text in it (the prompt Claude Code put back), no menu row."""
    if not isinstance(text, str): return False
    for line in [x.strip(_BOX) for x in text.splitlines() if x.strip(_BOX)][-8:]:
        if re.match(r'^[❯>]\s+(?![1-9]\.\s)\S', line): return True
    return False


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

    def _native(self):
        a=self.agent
        return bool(a.session and a.native and a.native.pid==a.session.get('pid') and a.native.returncode is None and a.master is not None)

    async def screen_text(self):
        """(visible text | None when unreadable, route) — local only, never logged or sent."""
        a=self.agent
        if not a.session: return None,None
        if self._native():
            return (self.surface.text() if self.surface.valid and not self.surface.pending else None),None
        from .shared_interrupt import herdr_bin,_agents
        from .shared_modelswitch import screen
        def read():
            binary=herdr_bin()
            sid=a.session.get('sessionId')
            pane=next((x.get('pane_id') for x in _agents(binary) if sid and (x.get('agent_session') or {}).get('value')==sid),None) if binary else None
            if not pane: return None,None
            return screen(binary,pane),(binary,pane)
        return await asyncio.to_thread(read)

    async def snapshot(self):
        a=self.agent
        if not a.session: return None,None
        text,route=await self.screen_text()
        return (detect(text) if text is not None else False),route

    async def send(self, route, key, want=None):
        """want: 'screen' = Enter only on the P538 keypad screen; 'prompt' = Enter only on the prompt a P539 "Session
        paused" retry put back (no modal on screen); None = either."""
        a=self.agent
        if self.blocked() or a.state.status=='waiting': return False
        commands=['esc']
        if isinstance(key, tuple) and len(key)==2 and key[0]=='row' and isinstance(key[1], int):
            return await self._row(route, key[1])
        if key in ('up','down','enter'):
            # P538: the keypad only for a screen the phone could not read — re-read now; a permission box never counts.
            info,fresh_route=await self.snapshot()
            if fresh_route!=route: return False
            if info and info['kind']=='screen' and want in (None,'screen'): commands=[key]
            elif key=='enter' and info is None and want in (None,'prompt'):
                # P539: only the restored prompt of a "Session paused" retry, with no modal on screen
                text,fresh_route=await self.screen_text()
                if text is None or fresh_route!=route or detect(text) is not None or not prompt_ready(text): return False
                commands=['enter']
            else: return False
        elif key != 'esc':
            if key not in ('1','2','3','4'): return False
            info,fresh_route=await self.snapshot()
            if not info or info['kind'] not in ('quota','paused') or fresh_route!=route: return False
            n=len(info['keys'])
            if info.get('cursor') not in range(1,n+1) or not 1<=int(key)<=n: return False
            commands=['down'] * ((int(key)-info['cursor']) % n) + ['enter']
        return await self._keys(route, commands)

    async def _row(self, route, n):
        """P538: reach row n from the ❯ row, see the ❯ on it, then confirm (Enter; `s` in the model picker)."""
        info,fresh_route=await self.snapshot()
        if not info or info['kind']!='menu' or fresh_route!=route: return False
        plan=row_keys(info,n)
        if plan is None: return False
        moves,confirm=plan
        if moves:
            if not await self._keys(route, moves): return False
            for _ in range(8):
                await asyncio.sleep(.25)
                now,now_route=await self.snapshot()
                if now and now['kind']=='menu' and now_route==route and now['title']==info['title'] \
                        and [r['n'] for r in now['rows'] if r['cur']]==[n]:
                    break
            else:
                return False                            # the ❯ did not land where we aimed: no Enter
        if self.blocked() or self.agent.state.status=='waiting': return False
        if not await self._keys(route, [confirm]): return False
        if info.get('model'):
            # The one dialog answered here is Claude Code's own "Switch model?" warning this `s` raised.
            from .shared_modelswitch import confirm_open
            for _ in range(12):
                await asyncio.sleep(.25)
                raw=await self.raw(route)
                if raw is None or 'Select model' in raw and 'use this session only' in raw: continue
                bottom='\n'.join([x for x in raw.splitlines() if x.strip()][-8:])
                # Only the warning itself, at the bottom, nothing permission-like, nothing changed meanwhile.
                if confirm_open(bottom) and not _PERMISSION.search(bottom) \
                        and not self.blocked() and self.agent.state.status!='waiting':
                    return await self._keys(route, ['enter'])
                break
        return True

    async def raw(self, route):
        """The same visible screen snapshot() reads, as text (None when unreadable)."""
        a=self.agent
        if self._native():
            return self.surface.text() if self.surface.valid and not self.surface.pending else None
        if not (isinstance(route, tuple) and len(route)==2): return None
        from .shared_modelswitch import screen
        return await asyncio.to_thread(screen, *route)

    async def _keys(self, route, commands):
        a=self.agent
        if self._native():
            try: os.write(a.master,b''.join({'esc':b'\x1b','up':b'\x1b[A','down':b'\x1b[B','enter':b'\r','s':b's'}[k] for k in commands))
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
            if info['kind']=='menu' and info.get('model'):
                learn=getattr(a,'learn_picker_rows',None)
                if callable(learn): learn(info['names'])     # P538: the phone's model list learns the versioned rows
            if info['kind']=='paused':
                from .refusal_flag import phone_draft
                self.draft=phone_draft(a,a.host)      # the flagged turn's own words, taken before anything moves on
            decision,picks=await a.host.question(questions(info,lang),gone=gone)
            acts=options(info,lang)
            if decision=='answer' and picks and len(picks)==1 and isinstance(picks[0],list) and len(picks[0])==1:
                n=picks[0][0]
                if not isinstance(n,int) or not 1<=n<=len(acts): return
                key=acts[n-1]
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
            then=None
            if info['kind']=='paused':
                then={'1':'switch','retry':'retry','edit':'edit','new':'new','esc':'edit'}[key]
                key={'retry':'2','edit':'esc','new':'esc'}.get(key,key)
            if await self.send(route,key,want='screen' if info['kind']=='screen' else None):
                a.host.st.log('shared_menu_key',kind=info['kind'],key=key if isinstance(key,str) else 'row',
                              **({} if isinstance(key,str) else {'row':key[1]}))
                if key=='esc': a._stop_esc_at=time.monotonic()
                if then:
                    self.attempted=False;self.retry_at=time.monotonic()+2
                    await self._after_paused(then,info,route,identity)
                if info['kind']=='rewind':
                    a.host.agent_notice(pick(lang,'Claude Code 停在回退菜单，已发送 Esc 退出。','Claude Code was in Rewind; sent Esc to exit.'))
                    self.attempted=False;self.retry_at=time.monotonic()+2
                elif info['kind'] in ('menu','screen'):
                    self.attempted=False;self.retry_at=time.monotonic()+2   # an unchanged screen is offered again
            elif self.gone is gone:
                self.attempted=False;self.retry_at=time.monotonic()+30
                a.host.agent_notice(pick(lang,'菜单操作未确认，请重试；仍无响应可在紧急修复中重开 AI 会话。','Menu action unconfirmed. Retry, or use Emergency repair to reopen the AI session.'))

    async def _gone_from_screen(self, route, seconds=6.0):
        """Wait (bounded) until the screen is readable and shows no native modal; the fresh text, else None."""
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            await asyncio.sleep(.25)
            if self.blocked(): return None
            text,fresh_route=await self.screen_text()
            if text is not None and fresh_route==route and detect(text) is None: return text
        return None

    async def _after_paused(self, then, info, route, identity):
        """P539 (#299): what follows the key on the "Session paused" modal, then one notice saying what was done."""
        a=self.agent;lang=getattr(a.host,'lang','zh')
        from . import refusal_flag as rf
        if then=='retry':
            # Claude Code puts the prompt back in its input box; submit it only once the modal is gone and it is there
            ok=False
            text=await self._gone_from_screen(route)
            for _ in range(8):
                if text is not None and prompt_ready(text) and await self.send(route,'enter',want='prompt'):
                    ok=True;break
                await asyncio.sleep(.25)
                text,fresh_route=await self.screen_text()
                if fresh_route!=route: break
            now=tuple(a.session.get(k) for k in ('sessionId','pid','startedAt')) if a.session else None
            if ok and now==identity:
                a.host.st.log('shared_menu_key',kind='paused',key='enter')
                a.host.agent_notice(rf.paused_done('retry',info,lang))
            else:
                a.host.agent_notice(pick(lang,'已选「原话重试」，但没能确认原话重新发出去了：在手机上把原话再发一次就行；仍无响应可在紧急修复中重开 AI 会话。',
                                         'Chose "retry as is", but could not confirm it was sent again: send your words once more from the phone; if nothing answers, use Emergency repair to reopen the AI session.'))
            return
        if then=='edit':
            device,text=getattr(self,'draft',(None,''))
            n=rf.draft_restore(a.host,device,text)
            a.host.agent_notice(rf.paused_done('edit',info,lang,rf.restore_note(lang,bool(text) and n>0)))
            return
        if then=='new':
            await self._gone_from_screen(route,3.0)
            self.current=None;self.key=None;self.closed.set()
            a.host.agent_notice(rf.paused_done('new',info,lang))
            from .agent import Cmd
            a.submit(Cmd('clear','','Agent J',None))
            return
        a.host.agent_notice(rf.paused_done('switch',info,lang))

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
