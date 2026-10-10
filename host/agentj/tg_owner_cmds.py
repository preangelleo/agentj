"""P118 (B4): `telegram.owner_commands` — owner-private Telegram commands answered by the owner's own local program, never
the model. Only the owner's private chat; stdout goes back there only (not to the Agent, history or log — the log keeps id,
outcome class, ms). `CODE: x` lines become one-tap code. Rate-limited per row (failures count), 30 s, ≤ 4 KiB, one run at a
time. The program: a regular executable owned by this user, not group/other-writable; stdin closed, its own folder, a
minimal environment (never the bot key or any key the host holds)."""
import asyncio, collections, html, os, re, stat, subprocess, time, unicodedata

RESERVED = {'start', 'help', 'stop', 'compact', 'approve', 'deny', 'pair', 'resume', 'config', 'model', 'my_agent_id',
            'add_friend', 'notification', 'notify'}
FIELDS = {'id', 'cmd', 'exec', 'args', 'choices', 'desc', 'per_hour'}
ENV_KEEP = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'TMPDIR')
SECRETS = re.compile(r'AJI-[a-f0-9]{8,}|\d{6,}:[\w-]{20,}|[\w+/=-]{24,}')   # on top of privacy.redact (named keys, bearer …)
_ok = lambda v, n: isinstance(v, str) and 0 < len(v) <= n and not any(ord(c) < 32 for c in v)


def problem(rows):
    """Config check: None or one line (never echoes a value)."""
    if not isinstance(rows, list) or len(rows) > 10: return 'at most 10 commands'
    ids, cmds = set(), set()
    for r in rows:
        if not isinstance(r, dict) or set(r) - FIELDS: return 'fields: ' + ', '.join(sorted(FIELDS))
        if not isinstance(r.get('id'), str) or not re.fullmatch(r'[\w.-]{1,64}', r['id']) or r['id'] in ids: return 'each command needs a unique plain id'
        c = r.get('cmd')
        if not isinstance(c, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', c) or c in cmds | RESERVED:
            return 'cmd: lowercase letters, digits and _ (≤ 32), unique, not a built-in command'
        ids.add(r['id']); cmds.add(c)
        if not _ok(r.get('exec'), 1000) or not os.path.isabs(r['exec']): return 'exec: an absolute path to your program'
        a, ch = r.get('args', []), r.get('choices', [])
        if not isinstance(a, list) or len(a) > 8 or not all(_ok(x, 200) for x in a): return 'args: at most 8 plain strings'
        if not isinstance(ch, list) or len(ch) > 8 or not all(isinstance(x, str) and re.fullmatch(r'[a-z0-9][\w-]{0,31}', x) for x in ch):
            return 'choices: at most 8 lowercase words'
        if 'desc' in r and not _ok(r['desc'], 100): return 'desc: plain text up to 100 characters'
        if type(r.get('per_hour', 10)) is not int or not 1 <= r.get('per_hour', 10) <= 60: return 'per_hour: 1..60'


def match(rows, text, username=None):
    """`/cmd[@ThisBot] [choice]` → (row, choice | None); anything else → None (ordinary text)."""
    m = re.fullmatch(r'/(\w{1,32})(?:@(\w+))?(?:\s+(\S+))?', (text or '').strip())
    if not m or m[2] and (not username or m[2].lower() != username.lower()): return None
    row = next((r for r in rows or [] if r.get('cmd') == m[1].lower()), None)
    ch = m[3] and m[3].lower()
    return (row, ch) if row and (ch is None or ch in row.get('choices', [])) else None


def commands(rows):
    return [{'command': r['cmd'], 'description': (r.get('desc') or r['cmd'])[:256]} for r in rows or []]


def _clean(s):
    return ''.join(' ' if unicodedata.category(c) in ('Cc', 'Cf') and c != '\t' else c for c in s)


def reason(raw):
    """The program's last stderr line, safe to show the owner."""
    from .privacy import redact
    lines = [x.strip() for x in str(raw or '').splitlines() if x.strip()]
    return re.sub(r'\s+', ' ', _clean(SECRETS.sub('[hidden]', redact(lines[-1]) if lines else ''))).strip()[:200] or 'failed'


def render(out):
    """stdout → (Telegram HTML, plain)."""
    h, p = [], []
    for line in out.splitlines():
        line = _clean(line).rstrip(); m = re.fullmatch(r'CODE:\s*(.+)', line)
        h.append('<code>' + html.escape(m[1].strip()) + '</code>' if m else html.escape(line)); p.append(m[1].strip() if m else line)
    return '\n'.join(h).strip(), '\n'.join(p).strip()


def trusted(path):
    try: st = os.stat(os.path.realpath(path))
    except OSError: return False
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o022 and os.access(path, os.X_OK)


class Runner:
    def __init__(self, clock=time.monotonic, run=subprocess.run):
        self.clock, self.run_fn, self.busy = clock, run, set()
        self.attempts = collections.defaultdict(collections.deque)

    async def run(self, row, choice=None):
        """→ (ok, html, plain, outcome class); only the class may be logged."""
        if row['id'] in self.busy: return False, None, '上一个还在生成，请稍等 / still running', 'busy'
        q, now, per = self.attempts[row['id']], self.clock(), row.get('per_hour', 10)
        while q and now - q[0] >= 3600: q.popleft()
        if len(q) >= per:
            return False, None, f'太频繁：每小时最多 {per} 次，约 {int(3600 - now + q[0]) // 60 + 1} 分钟后再试 / too often', 'rate-limited'
        q.append(now)
        if not trusted(row['exec']): return False, None, '找不到可信的本机程序 / local program missing or not trusted', 'untrusted'
        self.busy.add(row['id'])
        try:
            p = await asyncio.to_thread(self.run_fn, [row['exec'], *row.get('args', []), *([choice] if choice else [])],
                                        stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False,
                                        cwd=os.path.dirname(os.path.realpath(row['exec'])),
                                        env={k: os.environ[k] for k in ENV_KEEP if k in os.environ})
        except subprocess.TimeoutExpired: return False, None, '超时（30 秒）/ timed out', 'timeout'
        except OSError: return False, None, '无法启动本机程序 / could not start', 'spawn-error'
        finally: self.busy.discard(row['id'])
        if p.returncode: return False, None, '失败：' + reason(p.stderr.decode('utf-8', 'replace')), f'exit-{p.returncode}'
        try:
            if not p.stdout.strip() or len(p.stdout) > 4096: raise ValueError
            return (True, *render(p.stdout.decode('utf-8')), 'ok')
        except ValueError: return False, None, '程序没有给出可用的结果 / no usable output', 'bad-output'
