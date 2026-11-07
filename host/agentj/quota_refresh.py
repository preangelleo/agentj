"""#398: bounded typed refusal recovery and owner-controlled phone device-code entry."""
def owner_bound(host, remote, agent):
    """Recovery UI binds the current owner connection, independent of a finished Send."""
    mark = binding(agent)
    key = host.st.sign_key(remote.device)
    def live():
        from . import controls
        return bool(key and host.agent is agent and agent.owns_harness() and not agent.halting
            and any(r is remote for r in host.sessions.values()) and host._paired_live(remote)
            and host.st.sign_key(remote.device) == key and binding(agent) == mark
            and not controls.estop_state(host.st).get('on'))
    return live


async def recheck(host, remote, rid, *, _bound=None):
    if _bound is not None and not _bound():
        return {'ok': False, 'why': 'changed'}
    if not host._paired_live(remote):
        return {'ok': False, 'why': 'not_ready'}
    seen = host.__dict__.setdefault('_quota_recheck_seen', set())
    identity = (host.channel, remote.device, rid)
    if identity in seen or len(seen) >= 256:
        return {'ok': False, 'why': 'replay'}
    seen.add(identity)
    agent = getattr(host, 'agent', None)
    if agent is None or getattr(agent, 'kind', None) != 'codex' or not agent.owns_harness():
        return {'ok': False, 'why': 'unsupported'}
    done = getattr(agent, 'turn_done', None)
    if getattr(agent, 'working', False) or (done is not None and not done.is_set()):
        return {'ok': False, 'why': 'busy'}
    live = _bound or owner_bound(host, remote, agent)
    if not live():
        return {'ok': False, 'why': 'changed'}
    try:
        before = account_identity(await agent.call('account/read', {}, 20))
    except Exception:
        return {'ok': False, 'why': 'NOT_VERIFIED'}
    if not live() or before is None:
        return {'ok': False, 'why': 'NOT_VERIFIED'}
    async def verify_account():
        if not live():
            return False
        try:
            current = account_identity(await agent.call('account/read', {}, 20))
        except Exception:
            return False
        return live() and current == before
    live.verify_account = verify_account
    # Existing requester-only phone login UI and passkey confirmation. No logout,
    # automatic switch or task replay. Bound flow uses a new isolated account slot.
    started = await host.auth_rescue.login._start(remote, rid, 'codex', bound_live=live)
    return {'ok': bool(started), 'why': None if started else 'not_started',
            'entitlements': 'NOT_VERIFIED'}


def upgrade_intent(host, text):
    """Only exact phone intent; ordinary prose is never treated as a credential action."""
    import re
    if not isinstance(text, str) or len(text) > 500:
        return False
    value = text.strip()
    completed = value in ('升级了', '我升级了', '已升级', '升級了', '我升級了', '已升級',
                          "I've upgraded", 'I upgraded')
    if not completed:
        # Match the owner's completed first-person clause, including the actual
        # traditional-Chinese quota report; never a future/third-person quote.
        completed = re.search(r'(?:^|[，。！？,.!?\s])我(?:们|們)?(?:刚刚|剛剛|刚|剛|已经|已經|已)?(?:升级|升級)了(?=$|[，。！？,.!?\s])', value) is not None
    if not completed:
        return False
    a = getattr(host, 'agent', None)
    state = getattr(host.auth_rescue, 'state', {}).get('codex', {})
    if not a or getattr(a, 'kind', None) != 'codex' or state.get('cls') not in ('balance', 'rate_limit'):
        return False
    return True


async def upgraded(host, remote, text, rid, *, _bound=None):
    if _bound is not None and not _bound():
        return {'ok': False, 'why': 'changed'}
    if not upgrade_intent(host, text):
        return None
    return await recheck(host, remote, rid, _bound=_bound)


def offer_phone(agent):
    """A typed failure supplies the quota intent state without parsing error prose."""
    h = agent.host
    if h.agent is not agent or not agent.owns_harness() or agent.halting:
        return
    rescue = getattr(h, 'auth_rescue', None)
    if rescue is not None:
        rescue.signal('codex', 'rate_limit')
    h.agent_notice('权益同步 NOT_VERIFIED。升级后可在手机发送「升级了」，或点 AI 登录救援的额度卡，通过设备码继续；旧请求不会自动重放。')


def rejected(error):
    """Only typed RPC refusal, never error prose or a bare HTTP 429."""
    from .agent_codex import RPCError
    if not isinstance(error, RPCError):
        return False
    e = error.err
    data = e.get('data') if isinstance(e.get('data'), dict) else {}
    if any(k in e or k in data for k in ('turn', 'turnId', 'turn_id')):
        return False
    return any(isinstance(v, str) and v in ('usage_limit_reached', 'usageLimitExceeded')
               for v in (e.get('code'), e.get('codexErrorInfo'), data.get('code'), data.get('codexErrorInfo')))


def binding(agent):
    """Memory-only snapshot of actual request provenance; Send has no public key."""
    import json
    from . import accounts
    h = agent.host
    meta = accounts.load(h.st.root)['harness']['codex']
    s = getattr(agent, 'cur_send', None)
    device = getattr(s, 'device', None)
    key = h.st.sign_key(device) if device and hasattr(h.st, 'sign_key') else None
    cfg = getattr(agent, 'cfg', {}) or {}
    host_cfg = getattr(h, 'agent_cfg', {}) or {}
    task = h._task_id() if hasattr(h, '_task_id') else None
    return (key, device, getattr(s, 'sid', None), getattr(s, 'turn', None),
            json.dumps(getattr(s, 'source', None), sort_keys=True), id(h), id(h.agent),
            h.channel, getattr(h, '_pairing_epoch', None),
            getattr(getattr(h, 'hist', None), 'epoch', None), getattr(h, 'task_epoch', None), task,
            meta.get('active'), meta.get('switched'), agent.tid, id(agent.proc), id(s),
            getattr(s, 'state', None), getattr(agent, '_quota_stop', 0), id(getattr(agent, 'task', None)),
            tuple(cfg.get(k) for k in ('dir', 'profile', 'provider_profile')),
            tuple(host_cfg.get(k) for k in ('dir', 'profile', 'provider_profile')))


def bound_live(agent, remote=None):
    """The exact paired connection must remain registered, even on same-device reconnect."""
    mark = binding(agent)
    h = agent.host
    if remote is None:
        remote = next((r for r in getattr(h, 'sessions', {}).values()
                       if getattr(r, 'device', None) == mark[1]), None)
    def live():
        from . import controls
        s = getattr(agent, 'cur_send', None)
        guard = getattr(s, 'delivery_allowed', None)
        source = getattr(s, 'source', None) or {}
        return bool(source.get('kind') == 'phone' and source.get('role') == 'owner'
            and h.agent is agent and remote is not None
            and any(r is remote for r in getattr(h, 'sessions', {}).values())
            and h._paired_live(remote) and mark[0] and not agent.halting
            and agent.owns_harness() and mark == binding(agent)
            and getattr(s, 'state', None) != 'cancelled'
            and (not callable(guard) or guard())
            and not controls.estop_state(h.st).get('on'))
    return live


def account_identity(result):
    """Supported GetAccountResponse only. Plan changes are intentionally allowed."""
    a = result.get('account') if isinstance(result, dict) else None
    if not isinstance(a, dict) or a.get('type') != 'chatgpt':
        return None
    email, workspace = a.get('email'), a.get('workspaceName')
    if not isinstance(email, str) or not email.strip() or 'workspaceName' not in a:
        return None
    if workspace is not None and not isinstance(workspace, str):
        return None
    return email, workspace


def requalify_request(host):
    """Expire this task's short admission only; retain used sets and OperationOnce.

    New tool requests still pass the ordinary signed approval decision path. Never
    revoke persistent approval.level, owner/goal grants, or unrelated task records.
    """
    task = host._task_id() if hasattr(host, '_task_id') else None
    if task is None:
        return
    for key, record in host.__dict__.get('task_grants', {}).items():
        if isinstance(key, tuple) and key and key[0] == task:
            record['until'] = 0


async def refresh(agent, live=None):
    """Bounded read → managed refresh → identity check → limits; no entitlement claim."""
    if not agent.owns_harness() or agent.halting or getattr(agent, '_quota_refreshing', False):
        return False
    agent._quota_refreshing = True
    try:
        live = live or bound_live(agent)
        if not live():
            return False
        try:
            before = account_identity(await agent.call('account/read', {}, 20))
            if not live() or before is None:
                return False
            after = account_identity(await agent.call('account/read', {'refreshToken': True}, 20))
            if not live() or after is None or before != after:
                return False
            limits = await agent.call('account/rateLimits/read', {}, 20)
            if not live() or not isinstance(limits, dict):
                return False
            if isinstance(limits.get('rateLimits'), dict):
                agent.rate = limits['rateLimits']
                meter = getattr(agent, 'rate_meter', None)
                if callable(meter):
                    meter(agent.rate)
            requalify_request(agent.host)
            return live()
        except Exception:
            # Unsupported / external auth / disconnect are NOT_VERIFIED. An owned
            # restart is not a successful refresh and must never permit replay.
            return False
    finally:
        agent._quota_refreshing = False


async def start(agent, method, params):
    """Bound recovery stays inside deliver's lock and before delivery acknowledgement."""
    from .agent_codex import RPCError
    live = bound_live(agent)
    try:
        return await agent.call(method, params)
    except RPCError as error:
        if (method != 'turn/start' or not rejected(error) or agent._quota_evidence
                or agent.turn_id or agent.turn_status or not live()):
            raise
        if not await refresh(agent, live):
            raise
        if not live() or agent._quota_evidence or agent.turn_id or agent.turn_status:
            raise
        # Deliberately outside a second catch: quota again cannot loop.
        return await agent.call(method, params)
