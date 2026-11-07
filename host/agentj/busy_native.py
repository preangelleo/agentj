"""Host-owned native busy dispatch; control-socket kernel identity is the authority.

Run the existing Codex app-server adapter (including Host approvals), never raw
exec outside its launching fence. No user configuration is written. Ambiguous
IPC outcomes must be inspected by key, never submitted a second time.
"""
from __future__ import annotations
import asyncio
import copy
import os
from pathlib import Path
import re
import time
import threading


def parse(argv):
    from . import background
    if (not isinstance(argv, list) or not 2 <= len(argv) <= 64
            or any(not isinstance(v, str) or '\x00' in v or len(v) > 65536 for v in argv)):
        raise ValueError('invalid native argv')
    if background._harness(argv) != 'codex' or argv[1:2] not in (['exec'], ['e']):
        raise ValueError('use trusted Host entry: busy run --detach -- codex exec [--sandbox MODE] [--model MODEL] PROMPT; other native plans use the Host workflow dispatcher')
    prompt, opts = [], {}
    i = 2
    while i < len(argv):
        value = argv[i]
        name, eq, inline = value.partition('=')
        if name in ('--sandbox', '-s', '--model', '-m'):
            i += 1
            if not eq and i >= len(argv):
                raise ValueError('missing native option value')
            opts['sandbox' if name in ('--sandbox', '-s') else 'model'] = inline if eq else argv[i]
            i += 0 if eq else 1
        elif value in ('--json', '--ephemeral', '--skip-git-repo-check'):
            i += 1
        elif value.startswith('-'):
            raise ValueError('native option cannot preserve Host policy: ' + name)
        else:
            prompt.append(value)
            i += 1
    if len(prompt) != 1 or not prompt[0].strip() or len(prompt[0]) > 65536:
        raise ValueError('one explicit native prompt is required')
    from .codex_perm import MODES
    if 'sandbox' in opts and opts['sandbox'] not in MODES:
        raise ValueError('unknown sandbox')
    from . import slash
    if slash.parse(prompt[0]) is not None:
        raise ValueError('native slash commands use the current Host session entry')
    return prompt[0], opts


def policy(host, req, pid, uid):
    from . import approval_material, busy_launch, codex_perm, ctl_auth
    if uid != os.getuid() or type(pid) is not int or pid <= 1:
        raise ValueError('unverified caller; use the paired Host session')
    table = busy_launch.procs()
    if pid not in table:
        raise ValueError('caller process is not live')
    text, opts = parse(req.get('argv', []))
    agent = host.agent
    if agent is None or getattr(agent, 'kind', None) != 'codex' or not isinstance(getattr(agent, 'cfg', None), dict):
        raise ValueError('native plan must match the verified current Codex session')
    cfg = copy.deepcopy(agent.cfg)
    origin = approval_material.verified_caller_origin(host, pid)
    if not origin or origin.get('verified') is not True or origin.get('suspicious'):
        if (origin is not None or ctl_auth.refusal(pid, os.getpid(), host._agent_pids())
                or not ctl_auth.has_tty(pid) or 'sandbox' not in opts):
            raise ValueError('unknown source; dispatch inside the paired Host owner session, or use an independent owner terminal with explicit --sandbox')
        # An independent terminal is authenticated by the existing kernel/TTY gate;
        # it still runs through Host approvals and the configured Agent fence.
        origin = {'verified': True, 'suspicious': False, 'channel': 'terminal',
                  'session': str(host.hist.epoch), 'text': '', 'chain': []}
    thread = getattr(agent, 'thread', None)
    if not isinstance(thread, dict):
        raise ValueError('current session sandbox is unverified; attach via the Host first')
    sb = thread.get('sandbox')
    ceiling = codex_perm.mode_of(sb)
    modes = codex_perm.MODES
    if ceiling not in modes:
        raise ValueError('current session sandbox is unverified; attach via the Host first')
    requested = opts.get('sandbox', ceiling)
    effective = modes[min(modes.index(requested), modes.index(ceiling))]
    if not isinstance(req.get('cwd'), str) or not Path(req['cwd']).is_absolute() or not cfg.get('dir'):
        raise ValueError('dispatch cwd must be the verified session working directory')
    cwd = str(Path(req['cwd']).absolute())
    if cwd != str(Path(cfg['dir']).absolute()):
        raise ValueError('dispatch cwd must be the verified session working directory')
    # A simple workspace sandbox cannot reproduce arbitrary extra-root policies.
    # Narrow such a policy rather than broadening its roots/network defaults.
    if effective == 'workspace-write' and isinstance(sb, dict) and any(
            sb.get(k) for k in ('writableRoots', 'writable_roots', 'excludeTmpdirEnvVar', 'excludeSlashTmp')):
        effective = 'read-only'
    cfg.update(_workflow_ceo=True, session_mode='independent', follow_harness=False)
    if opts.get('model'):
        cfg['model'] = opts['model']
    return text, cfg, origin, effective, table[pid][2]


async def start(host, req, pid, uid):
    from . import busy, approval_review
    if host.stopped():
        raise ValueError('Stop everything is on; native dispatch was not started')
    key = req.get('key')
    if not isinstance(key, str) or not re.fullmatch(r'run:[a-f0-9]{12}', key):
        raise ValueError('invalid busy key')
    text, cfg, origin, mode, proc_start = policy(host, req, pid, uid)
    rows = host.__dict__.setdefault('_busy_native', {})
    digest = approval_review.digest(req)
    if key in rows:
        if rows[key]['binding'] != (pid, proc_start, digest):
            raise ValueError('busy key already bound')
        return rows[key]['receipt']
    if len(rows) >= 128:
        raise ValueError('native dispatch book full; inspect existing work')
    it = busy.item(key, req.get('who'), 'ceo', req.get('label', ''), report=req.get('report'), cwd=cfg['dir'], harness='codex')
    it.update(started=time.time(), status='queued')
    busy.write_item(host.st.root, it)
    # Host-held source, without pinning the Host PID as a worker ancestor.
    launch = host.busy.launch
    with launch.lock:
        launch.jobs[key] = {f: str(it.get(f) or '')[:400] for f in ('who', 'label', 'report')} | busy.restart_meta(it)
        chain = launch.open[2] if launch.open and launch.open[1] and len(launch.open) > 2 else None
        if chain:
            launch.jobs[key]['chain'] = chain
        launch.dirty = True
    launch.save()
    receipt = {'ok': True, 'key': key, 'manager': 'host-native-adapter', 'harness': 'codex app-server',
               'sandbox': mode, 'model': cfg.get('model') or getattr(host.agent, 'thread', {}).get('model'), 'approval': 'untrusted / Host review', 'fence': bool(cfg.get('fence', True)),
               'network': 'not checked: no approved probe target'}
    rows[key] = {'binding': (pid, proc_start, digest), 'receipt': receipt}
    token = approval_review.ORIGIN.set(copy.deepcopy(origin))
    try:
        task = asyncio.create_task(run(host, cfg, text, mode, it))
    finally:
        approval_review.ORIGIN.reset(token)
    rows[key]['task'] = task
    return receipt


async def run(host, cfg, text, mode, it):
    from . import busy, owner_avatar
    from .agent_codex import CodexAgent
    from .privacy import redact
    sandbox = ({'type': 'readOnly'} if mode == 'read-only' else
               {'type': 'workspaceWrite', 'writableRoots': [], 'networkAccess': False,
                'excludeTmpdirEnvVar': True, 'excludeSlashTmp': True} if mode == 'workspace-write' else
               {'type': 'dangerFullAccess'})
    class BusyCodex(CodexAgent):
        def _on_note(self, method, params):
            progress.output(time.monotonic())
            if it.get('status') == 'stalled':
                it.update(status='running', stalled_at=None)
            return super()._on_note(method, params)

        async def _retry_rate(self, why):
            # The first native turn may already have produced effects. Never
            # turn a quota/transport failure into a second dispatched turn.
            self.rate_retry = False
            self.turn_status = {'status': 'failed'}

        async def call(self, method, params=None, *args, **kw):
            if method == 'turn/start':
                params = {**(params or {}), 'sandboxPolicy': sandbox,
                          'approvalPolicy': 'untrusted', 'approvalsReviewer': 'user'}
                # Keep the approval adapter's own ceiling consistent with the turn.
                self.thread['sandbox'] = sandbox
            return await super().call(method, params, *args, **kw)

        def policy(self):
            value = super().policy()
            value.update(sandbox=mode, approvalPolicy='untrusted', approvalsReviewer='user')
            return value
    proxy = owner_avatar._Host(host, 'Busy native dispatch', None)
    runner = BusyCodex(proxy, cfg, persist=False)
    code = None
    stop = threading.Event()
    admitted = False
    from .busy_dispatch import Progress, cpu_snapshot, codex_session_snapshot
    progress = Progress(time.monotonic(), None)
    pin = None
    worker = asyncio.current_task()
    async def heartbeat():
        nonlocal pin

        while True:
            if host.stopped():
                stop.set()
                worker.cancel()
                return
            proc = getattr(runner, 'proc', None)
            if proc is not None:
                from .busy_probe import start_id
                it.update(pid=proc.pid, proc_start=start_id(proc.pid), child_pid=proc.pid)
                if pin is None:
                    pin = cpu_snapshot(proc.pid)
                snapshot = (cpu_snapshot(proc.pid, pin[0]) if pin else None,
                            len(getattr(runner, 'collect', []) or []),
                            codex_session_snapshot(runner.image_home, getattr(runner, 'tid', None))
                            if getattr(runner, 'image_home', None) else ())
                stalled = progress.stalled
                if progress.observe(time.monotonic(), snapshot):
                    it.update(status='stalled', stalled_at=time.time())
                elif stalled and not progress.stalled:
                    it.update(status='running', stalled_at=None)
            elif admitted:
                it['status'] = 'running'
            busy.write_item(host.st.root, it)
            await asyncio.sleep(busy.HEARTBEAT)
    beat = asyncio.create_task(heartbeat())
    try:
        path = busy.items_dir(host.st.root) / (busy._safe(it['key']) + '.json')
        if not await asyncio.to_thread(busy.wait_capacity, stop, path):
            code = 130
            return
        if host.stopped():
            code = 130
            return
        admitted = True
        it["delivery_started"] = time.time()
        out, result = await runner.run_once(text)
        code = result if type(result) is int else None
        it['log_tail'] = redact((out or '') + '\n' + (getattr(runner, 'err_tail', '') or ''), paths=True)[-2048:]
    except asyncio.CancelledError:
        code = 130
        raise
    except (OSError, ValueError, RuntimeError):
        it['log_tail'] = 'native adapter failed; inspect delivery; outcome unknown; never replay automatically'
    finally:
        stop.set()
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        busy.write_finished(host.st.root, it, code)
        busy.remove_item(host.st.root, it['key'])
