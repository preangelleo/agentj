"""Order-local Host facade for the existing native adapters; launched only by core.ceo.

No native output supplies authority. The native login environment remains adapter-owned.
The guardian remains the final owner of the complete child tree and exit receipt.
"""
from __future__ import annotations
import asyncio
import contextlib
import copy
import hmac
import hashlib
import stat
import sys
import json
import math
import os
from pathlib import Path
import secrets
import time
from types import SimpleNamespace

from .. import danger, provenance
from ..state import State
from . import askbridge, dispatcher, orders, process_evidence


def frozen_source(order, source):
    parent = order.meta.get('parent') or {}
    return provenance.is_owner(source) or (order.meta.get('origin') == 'task'
        and parent.get('kind') == 'task' and parent.get('source_class') in ('system', 'same_host')
        and provenance.normalize(source)['kind'] == parent['source_class'])


def _namespace_digest(identity):
    # A 14-hex namespace plus a short socket leaf matches the ordinary master socket length.
    # The full identity below makes collisions fail closed rather than alias orders.
    return hashlib.sha256(identity).hexdigest()[:14]


def _owned(info, directory=False):
    return (info.st_uid == os.getuid() and not info.st_mode & 0o022
            and (stat.S_ISDIR(info.st_mode) if directory else
                 stat.S_ISREG(info.st_mode) and info.st_nlink == 1))


class NativeState(State):
    """Private order state, never main Agent session/configuration."""
    def __init__(self, order, cfg):
        super().__init__(orders._mk(order.path / 'native'))
        self._identity = os.fsencode(str(order.path.absolute()))
        self._perm = self.root.parent.parent.parent / ('n' + _namespace_digest(self._identity)) / 'p'
        self.prepare_perm()
        self.cfg = copy.deepcopy(cfg)
        self.sessions = {}  # fresh conversation for every CEO generation, including resume

    @property
    def perm_dir(self):
        return self._perm

    @property
    def perm_sock_path(self):
        return self.perm_dir / 'p'

    def prepare_perm(self):
        """Register identity outside the only directory exposed to the native agent.

        Unknown/incomplete namespaces are never repaired or cleaned. Directory-relative
        NOFOLLOW opens also reject symlink substitution of the marker or private mount.
        """
        ns = self._perm.parent
        try:
            created = False
            try:
                ns.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                pass
            fd = os.open(ns, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                if not _owned(os.fstat(fd), True):
                    raise ValueError('namespace owner/mode')
                if created:
                    marker = os.open('owner', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=fd)
                    with os.fdopen(marker, 'wb') as f:
                        f.write(self._identity)
                        f.flush()
                        os.fsync(f.fileno())
                marker = os.open('owner', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(marker, 'rb') as f:
                    if not _owned(os.fstat(f.fileno())) or f.read(len(self._identity)+1) != self._identity:
                        raise ValueError('namespace identity/owner')
                if created:
                    os.mkdir('p', mode=0o700, dir_fd=fd)
                private = os.open('p', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if not _owned(os.fstat(private), True):
                        raise ValueError('permission directory owner/mode')
                finally:
                    os.close(private)
            finally:
                os.close(fd)
        except (OSError, ValueError) as e:
            raise orders.OrderError('native_perm_namespace', str(e)) from e

    @property
    def fence_state(self):
        return FenceState(self, self.root.parent.parent.parent)

    def set_agent_model(self, *args, **kw):
        return False

    def agent_config(self):
        return copy.deepcopy(self.cfg)

    def agent_session(self, kind):
        return self.sessions.get(kind)

    def set_agent_session(self, kind, sid):
        if sid:
            self.sessions[kind] = sid
        else:
            self.sessions.pop(kind, None)


class FenceState:
    """Protection-only view. All State methods stay bound to the order-private State."""
    def __init__(self, private, master):
        self.root = Path(master)
        self.perm_dir = private.perm_dir
        self._private = private

    def __getattr__(self, name):
        return getattr(self._private, name)


class FencedAdapter:
    """Reuse the adapter launch policy with a protection view, never a writable master State."""
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name == 'proc':
            self._order_anchor = ((value, value.pid, process_evidence.codex_procs.start_token(value.pid))
                                  if value is not None else None)

    def launch_argv(self, argv):
        original = self.host
        self.host = copy.copy(original)
        self.host.st = original.st.fence_state
        try:
            return super().launch_argv(argv)
        finally:
            self.host = original

    async def authorized_launch_argv(self, argv):
        from .. import fence
        if self.cfg.get('fence', True):
            await asyncio.to_thread(fence.problem, self.host.st.fence_state, self.cfg['dir'])
        # Workflow CEOs fail closed; no one-launch isolation bypass.
        return self.launch_argv(argv)


class Usage:
    """Receives the adapters' existing heartbeat meter events (not model estimates)."""
    active = True
    codex_base = None

    def __init__(self):
        self.samples = {}

    def observe(self, key):
        pass

    def sample(self, key, inp, out):
        if all(type(x) is int and x >= 0 for x in (inp, out)):
            self.samples[str(key)] = (inp, out)

    def totals(self):
        # Claude result includes assistant usage; do not count both event families.
        rows = [self.samples['result']] if 'result' in self.samples else list(self.samples.values())
        return {'input': sum(x[0] for x in rows), 'output': sum(x[1] for x in rows)}


class Host:
    ask_ttl = 120
    lang = 'zh'
    preferences = {}
    peers = None
    goals = None
    task_label = None

    def __init__(self, order, client, st_root):
        self.order, self.client, self.st_root = order, client, st_root
        self.meta = copy.deepcopy(order.meta)
        m = self.meta
        self.cfg = {'dir': m['project'], 'model': m.get('model'), 'fence': m['fence'],
                    'danger_extra': m.get('danger_extra'), '_workflow_ceo': True,
                    'high_risk_warnings': False, 'follow_harness': False}
        self.st = NativeState(order, self.cfg)
        self.origin_source = copy.deepcopy(next((e.get('source') for e in reversed(order.events()) if e.get('ev') in ('origin', 'resume_source')), None))
        self.heartbeat = SimpleNamespace(usage=Usage())
        self.perm_claimed = asyncio.Event()
        self.perm_token = None
        self.perm_w = None
        self.srv = None
        self.agent = None
        from ..busy import Board
        self.busy = Board(self.st.root)
        self.busy_keys = {}
        self.closed = False
        self.text = []
        self.generation = secrets.token_hex(16)
        self.requests = set()
        hist = orders._jsonl(order.path / 'history.jsonl')
        self.prior_wall = sum(float(r.get('wall_s') or 0) for r in hist)
        self.prior_tokens = {k: sum(int((r.get('tokens') or {}).get(k) or 0) for r in hist)
                             for k in ('input', 'output')}
        self.budget_path = order.path / 'native-budget.json'
        if self.budget_path.exists():
            try:
                saved = json.loads(self.budget_path.read_text())
                if saved['active'] is not False:
                    raise orders.OrderError('native_budget_unconfirmed')
                wall, tokens = saved['wall_s'], saved['tokens']
                if type(wall) not in (int, float) or not math.isfinite(wall) or wall < 0 or any(type(tokens[k]) is not int or tokens[k] < 0 for k in ('input','output')):
                    raise ValueError('budget')
                self.prior_wall = max(self.prior_wall, wall)
                self.prior_tokens = {k:max(self.prior_tokens[k],tokens[k]) for k in ('input','output')}
            except orders.OrderError:
                raise
            except (KeyError, TypeError, ValueError) as e:
                raise orders.OrderError('native_budget_ledger') from e
        self.accounted = False
        self.launched = False
        self.started = time.monotonic()

    def stopped(self):
        return self.closed or dispatcher.paused(self.st_root) or self.order.control() == 'stop' or bool(self.order.result())

    def budget(self):
        u = self.heartbeat.usage.totals()
        limit = self.meta['budget'].get('tokens')
        return (self.prior_wall + time.monotonic() - self.started >= 60 * self.meta['budget']['wall_min']
                or (limit is not None and sum(u.values()) + sum(self.prior_tokens.values()) >= limit))

    def live(self, proc=None):
        p = getattr(self.agent, 'proc', None)
        return frozen_source(self.order, self.origin_source) and not self.stopped() and not self.budget() and p is not None and p.returncode is None and (proc is None or p is proc)

    def native_busy(self, event, params=None):
        from .. import busy
        scope = 'order:' + self.order.id
        if self.meta['harness'] == 'claude':
            self.busy.stream_event(event, scope) if event is not None else self.busy.session_gone(scope)
        else:
            self.busy.codex_event(event, params, scope) if event is not None else self.busy.session_gone(scope)
        current = self.busy.mem
        import hashlib
        for key in set(current) - set(self.busy_keys):
            item = current[key]
            wid = scope + ':' + hashlib.sha256(key.encode()).hexdigest()[:12]
            self.busy_keys[key] = wid
            self.order.event('subagent_start', native=key[:100], order=self.order.id)
            busy.write_item(self.st_root, busy.item(wid, item.get('who'), 'temp', item.get('label'),
                pid=os.getpid(), parent=self.order.id, harness=self.meta['harness'], cwd=self.meta['project']))
        for key in set(self.busy_keys) - set(current):
            busy.remove_item(self.st_root, self.busy_keys.pop(key))
            self.order.event('subagent_end', native=key[:100], order=self.order.id)

    def agent_text(self, text):
        if isinstance(text, str):
            self.text.append(text)

    def agent_status(self, status):
        self.order.event('native_status', status=status, generation=self.generation)

    def agent_notice(self, text):
        # Diagnostic text is data, never a command or a provenance source.
        self.order.event('native_notice', text=str(text)[:1000])

    def policy_refused(self, tool, inp, why, notice=None, fix=None):
        self.order.event('native_refused', tool=tool, why=str(why)[:300])

    async def question(self, *args, **kw):
        return 'cancelled', None

    async def ask(self, tool, inp, gone=None, **kw):
        deny = {'behavior': 'deny', 'message': 'Order approval unavailable or expired.'}
        p = getattr(self.agent, 'proc', None)
        generation = self.generation
        rid = secrets.token_hex(16)
        self.requests.add(rid)
        try:
            if not isinstance(tool, str) or not isinstance(inp, dict) or not self.live(p):
                return deny
            if self.meta['mode'] == 'research' and not danger.readonly(tool, inp)[0]:
                return deny
            # Same order-wide denial key as core Gate. No batch grant is inherited.
            from .pipeline import digest
            if tool in ('Write', 'Edit'):
                from .pipeline import Gate
                from .tools import resolve
                try:
                    full = str(resolve(self.meta['project'], inp.get('file_path')))
                except (ValueError, TypeError):
                    return deny
                if not Gate(self.meta['project'], scope_write=(self.meta['brief'].get('scope') or {}).get('write'))._in_scope(full):
                    return deny
            key = digest(tool, inp)
            if key in self.order.denied():
                return deny
            pending = asyncio.create_task(self.client(tool, inp, None))
            try:
                until = time.monotonic() + self.ask_ttl
                while not pending.done():
                    if (not self.live(p) or generation != self.generation or rid not in self.requests
                            or (gone is not None and gone.done()) or time.monotonic() >= until):
                        return deny
                    await asyncio.wait({pending}, timeout=.05)
                ans = pending.result()
                if not self.live(p) or generation != self.generation or (gone is not None and gone.done()):
                    return deny
                if not isinstance(ans, dict) or ans.get('behavior') != 'allow':
                    self.order.add_denied(key)
                    return deny
                return {'behavior': 'allow', 'updatedInput': copy.deepcopy(inp)}
            finally:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        except (OSError, ValueError, TypeError, ConnectionError):
            return deny
        finally:
            self.requests.discard(rid)

    def save_budget(self, active):
        usage = self.heartbeat.usage.totals()
        tokens = {k:self.prior_tokens[k]+usage[k] for k in usage}
        limit = self.meta['budget'].get('tokens')
        if not active and self.launched and not self.accounted and limit is not None:
            # Native interruption has no final meter receipt: reserve the remaining
            # allowance rather than let repeated stop/resume erase unreported usage.
            tokens['input'] = max(tokens['input'], limit)
            self.order.event('native_budget_reserved', reason='no_final_meter')
        orders._write(self.budget_path, json.dumps({'active':active,'wall_s':self.prior_wall + time.monotonic()-self.started,
                        'tokens':tokens,'generation':self.generation}))

    async def start(self):
        self.st.prepare_perm()
        # No fallback: this namespace supports every path the ordinary master supports.
        limit = 103 if sys.platform == 'darwin' else 107
        if len(os.fsencode(self.st.perm_sock_path)) > limit:
            raise orders.OrderError('native_perm_socket_path_too_long', 'master state root cannot support AF_UNIX')
        self.save_budget(True)
        self.st.perm_sock_path.unlink(missing_ok=True)
        for claim in self.st.perm_dir.glob('task-claim-*'):
            claim.unlink()
        self.srv = await asyncio.start_unix_server(self.on_perm, path=str(self.st.perm_sock_path))

    def new_perm_env(self):
        self.st.prepare_perm()
        for claim in self.st.perm_dir.glob('task-claim-*'):
            claim.unlink()
        self.generation = secrets.token_hex(16)
        if self.perm_w:
            self.perm_w.close()
        self.perm_token = secrets.token_urlsafe(32)
        self.perm_claimed = asyncio.Event()
        claim = self.st.perm_dir / ('task-claim-' + secrets.token_hex(8))
        orders._write(claim, self.perm_token)
        return {'AGENTJ_PERM_SOCK': str(self.st.perm_sock_path), 'AGENTJ_PERM_CLAIM': str(claim),
                'AGENTJ_PERM_WAIT': str(self.ask_ttl + 10)}

    def descendant(self, pid):
        proc = getattr(self.agent, 'proc', None)
        if proc is None or proc.returncode is not None:
            return False
        anchor = getattr(self.agent, '_order_anchor', None)
        return (anchor is not None and anchor[0] is proc and anchor[1] == proc.pid
                and process_evidence.descendant(pid, anchor[1], anchor[2])
                and proc is getattr(self.agent, 'proc', None) and proc.returncode is None)

    async def on_perm(self, r, w):
        claimed = False
        p = None
        try:
            req = json.loads(await asyncio.wait_for(r.readline(), 10) or b'null')
            p = getattr(self.agent, 'proc', None)
            if (not isinstance(req, dict) or req.get('t') != 'claim' or not self.perm_token
                    or not hmac.compare_digest(str(req.get('token', '')), self.perm_token)
                    or not self.descendant(askbridge._peer_pid(w))):
                return
            self.perm_token = None
            self.perm_w = w
            claimed = True
            w.write(b'{"t":"claimed"}\n'); await w.drain()
            self.perm_claimed.set()
            seen, requests, tasks = set(), {}, set()
            async def one(m):
                rid = m['id']
                try:
                    ans = await self.ask(m.get('tool'), m.get('input'), requests[rid]) if self.live(p) else {'behavior': 'deny'}
                    w.write((json.dumps({'t': 'answer', 'id': rid, **ans}) + '\n').encode())
                    await w.drain()
                finally:
                    requests.pop(rid, None)
            try:
                while line := await r.readline():
                    m = json.loads(line)
                    if not isinstance(m, dict) or type(m.get('id')) is not int:
                        continue
                    rid = m['id']
                    if m.get('t') == 'cancel':
                        f = requests.get(rid)
                        if f is not None and not f.done():
                            f.set_result(True)
                    elif m.get('t') == 'ask':
                        if rid in seen or len(seen) >= 4096:
                            w.write((json.dumps({'t': 'answer', 'id': rid, 'behavior': 'deny'}) + '\n').encode())
                            await w.drain()
                            continue
                        seen.add(rid)
                        requests[rid] = asyncio.get_running_loop().create_future()
                        task = asyncio.create_task(one(m)); tasks.add(task)
                        task.add_done_callback(tasks.discard)
            finally:
                for f in requests.values():
                    if not f.done(): f.set_result(True)
                for task in tasks: task.cancel()
                if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, ValueError, asyncio.TimeoutError):
            pass
        finally:
            w.close()
            if claimed and self.perm_w is w:
                self.perm_w = None
                if not self.closed and self.agent is not None:
                    self.agent.perm_lost()

    async def close(self):
        self.closed = True
        self.generation = ''
        self.requests.clear()
        if self.perm_w:
            self.perm_w.close()
        if self.srv:
            self.srv.close()
            await self.srv.wait_closed()
        if self.agent:
            proc = getattr(self.agent, 'proc', None)
            await self.agent.stop()
            if proc is not None:
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except asyncio.TimeoutError as e:
                    raise orders.OrderError('stop_unconfirmed') from e
        self.save_budget(False)
        self.st.perm_sock_path.unlink(missing_ok=True)
        from .. import busy
        for wid in self.busy_keys.values():
            busy.remove_item(self.st_root, wid)
        for p in self.st.perm_dir.glob('task-claim-*'):
            p.unlink(missing_ok=True)


async def run(o, client, st_root, opening):
    from ..agent import ClaudeAgent
    from ..agent_codex import CodexAgent
    class OrderClaude(FencedAdapter, ClaudeAgent):
        def argv(self, resume, research=False):
            return super().argv(resume, research=self.host.meta['mode'] == 'research')

        def _unsupported_model(self, event):
            return False  # pinned order model cannot silently fall back to a different model

        def _busy(self, ev):
            self.host.launched = True
            self.host.native_busy(ev)

    class OrderCodex(FencedAdapter, CodexAgent):
        def _busy(self, method, params):
            self.host.launched = True
            self.host.native_busy(method, params)

        # Preserve the real adapter protocol, adding only order-local request binding.
        def _on_request(self, m):
            p = m.get('params') or {}
            rid = m.get('id')
            if 'requestApproval' in str(m.get('method')):
                if (p.get('threadId') != self.tid or not self.turn_id or p.get('turnId') != self.turn_id):
                    self._answer(rid, {'decision': 'decline'})
                    return
            self.__dict__.setdefault('_order_requests', {})[rid] = self.proc
            super()._on_request(m)

        def _answer(self, rid, result=None, error=None):
            bound = self.__dict__.setdefault('_order_requests', {}).pop(rid, self.proc)
            if bound is self.proc:
                super()._answer(rid, result, error)

    h = Host(o, client, st_root)
    h.agent = OrderClaude(h, h.cfg) if o.meta['harness'] == 'claude' else OrderCodex(h, h.cfg, persist=False, research=o.meta['mode'] == 'research')
    h.agent.native_source = copy.deepcopy(next((e.get('source') for e in reversed(o.events()) if e.get('ev') in ('origin', 'resume_source')), None))
    reason, code = None, 0
    turn = None
    try:
        await h.start()
        turn = asyncio.create_task(h.agent.turn(opening))
        while not turn.done():
            if h.stopped() or h.budget():
                reason = 'budget' if h.budget() else 'stopped'
                proc = getattr(h.agent, 'proc', None)
                await h.agent.halt()
                if proc is not None:
                    try:
                        await asyncio.wait_for(proc.wait(), 5)
                    except asyncio.TimeoutError as e:
                        raise orders.OrderError('stop_unconfirmed') from e
                break
            await asyncio.wait({turn}, timeout=.05)
        if reason is None:
            await turn
            if h.agent.is_down():
                code = 2
            elif o.meta['harness'] == 'codex' and h.agent.turn_status.get('status') != 'completed':
                code = 1
            elif o.meta['harness'] == 'claude' and (not h.agent.last_result or h.agent.last_result.get('is_error')):
                code = 1
        # A denied pending approval can finish the native turn between polls.
        # The accepted stop still owns the result, even if that turn returned a head.
        if reason is None and h.stopped():
            reason = 'stopped'
        if reason is None and code == 0 and h.meta['budget'].get('tokens') is not None and not h.heartbeat.usage.samples:
            code = 1
            o.event('native_meter_missing')
        h.accounted = reason is None and bool(h.heartbeat.usage.samples)
        if reason is None and h.budget():
            reason = 'budget'
        return {'text': '\n\n'.join(h.text), 'code': code, 'reason': reason,
                'usage': h.heartbeat.usage.totals()}
    finally:
        await h.close()
        if turn and not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn
