"""An admitted switch can request one existing phone key card and retry its exact job once."""
import asyncio
from dataclasses import dataclass
import os
import time

from . import ark, provider_runtime, switch_harness


def explanation(rec, lang):
    model = rec.get('model') or ''
    provider = model.partition('/')[0]
    plan = ark.plan_of(provider)
    english = lang == 'en'
    label = (('Volcano Ark ' if english else '火山方舟 ') +
             {'agent':'Agent Plan', 'coding':'Coding Plan', 'payg':'API'}[plan]) if plan else provider or 'OpenCode'
    message = f'The {label} key is missing or invalid.' if english else f'{label} 的密钥缺失或无效。'
    if rec.get('reason') == 'owner_provider_key':
        return message + (' Update it in the external OpenCode server’s provider settings.' if english else '请在外部 OpenCode 服务器的提供商设置中更新密钥。')
    if provider in ('volcengine','volcengine-coding-plan','byteplus','byteplus-modelark','byteplus-coding-plan') or plan in ('coding','payg'):
        message += (' The dedicated Ark Agent Plan key only works with the Beijing ark-agent-plan provider. Choose Volcano Ark Agent Plan in Agent J.'
                    if english else '方舟 Agent Plan 专属密钥不能用于内置 Volcengine Ark、Coding Plan 或 BytePlus 国际站；请在掌枢选择「火山方舟 Agent Plan」。')
    if plan == 'agent':
        message += (' Use the dedicated key from Ark console → Subscription → Agent Plan → Usage configuration in the phone key card.'
                    if english else '请在手机密钥卡填写方舟控制台「订阅 → Agent Plan → 使用配置」里的专属 API Key。')
    return message


def _official(provider, project, model):
    from . import opencode_free, opencode_probe_route
    plan = ark.plan_of(provider)
    if not plan:
        return False
    preset = ark.plan_data(plan)
    try:
        route = opencode_probe_route.selected_route(os.environ, project, model)
    except opencode_free.ProbeError:
        return False
    if plan == 'agent':
        from . import ark_connect
        try:
            ark_connect.official(os.environ,project,model)
            return True
        except opencode_free.ProbeError:
            return False
    block = route.get('provider', {}).get(provider, {})
    return (block.get('npm') == preset['block']['npm']
            and block.get('options') == preset['block']['options']
            and not block.get('env') and not block.get('id'))


@dataclass
class Origin:
    agent: object
    send: object
    source: dict
    device: str
    signing_key: bytes
    config: dict
    epoch: object
    latest: int
    route_stamp: str
    stored_thread: str | None
    native_thread: str | None

    def same_thread(self, host):
        if self.agent is None:
            return host.agent is None
        current = getattr(self.agent, 'tid', None) or self.stored_thread
        return (host.agent is self.agent and host.st.agent_session(self.agent.kind) == self.stored_thread
                and (not self.native_thread or current == self.native_thread))

    def report(self, host, zh, en):
        """Only the original signed phone turn or verified terminal setup page owns the result."""
        origins = host.__dict__.get('_approval_origins', {})
        message = self.source.get('message_id')
        if (not self.signing_key or host.st.sign_key(self.device) != self.signing_key
                or host.stopped() or host.agent is not self.agent or host.hist.epoch != self.epoch
                or self.source.get('session') != str(self.epoch) or message != self.latest
                or max(origins, default=None) != message or origins.get(message) != self.source
                or not self.same_thread(host)):
            return
        src = (host.hist.get(message) or {}).get('src') or {}
        if src.get('k') == 'phone':
            if (self.source.get('channel') != 'phone' or src.get('dev') != self.device or self.send is None
                    or self.send.turn != message or self.send.approval_origin != self.source):
                return
        elif (src.get('k') != 'host' or self.source.get('channel') != 'host'
              or set(host.st.devices()) != {self.device}):
            return
        host.hist_add({'k':'agent', 'text':'', 'quote':{'id':message}}, en if host.lang == 'en' else zh, 'done')

    def live(self, host):
        from . import provider_setup
        current = getattr(host.agent, 'cur_send', None)
        origins = host.__dict__.get('_approval_origins', {})
        return (not host.stopped() and self.same_thread(host) and host.hist.epoch == self.epoch
                and (current is None or current is self.send)
                and max(origins, default=0) == self.latest
                and host.st.sign_key(self.device) == self.signing_key
                and (self.agent is not None or set(host.st.devices()) == {self.device})
                and host.st.agent_config() == self.config
                and provider_setup.scope_stamp(host) == self.route_stamp)


def capture(host, req, pid):
    if switch_harness.check_spec(req)[1] or req.get('kind') != 'switch' or req.get('target') != 'opencode' or req.get('dry_run') or req.get('undo'):
        return None
    from . import approval_material, approval_review, ctl_auth, provenance, provider_setup
    proof = approval_material.verified_phone_chat(host, pid)
    agent = host.agent
    send = getattr(agent, 'cur_send', None)
    if proof and proof[0] is agent:
        source = proof[1]
        device = getattr(send, 'device', None)
    elif (type(pid) is int and ctl_auth.refusal(pid, os.getpid(), host._agent_pids()) is None
          and ctl_auth.has_tty(pid) and len(host.st.devices()) == 1):
        row = host.hist_add({'k':'host', 'name':'Agent J', 'text':'OpenCode switch'}, '', 'done')
        source = approval_review.remember(host, row, provenance.terminal())
        device = next(iter(host.st.devices()), None)
    else:
        return None
    key = host.st.sign_key(device) if device else None
    if not key or (not agent and host.st.agent_config()):
        return None
    return Origin(agent, send, source, device, key, host.st.agent_config(), host.hist.epoch,
                  max(host.__dict__.get('_approval_origins', {}), default=0), provider_setup.scope_stamp(host),
                  host.st.agent_session(agent.kind) if agent else None, getattr(agent, 'tid', None))


def admitted(host, request, result, origin):
    if result.get('ok') and origin:
        host._bg(_follow(host, result['job'], request.get('force', False), origin,request.get('model')), 'switch_key')


async def _follow(host, job, force, origin, expected_model=None):
    st = host.st
    end = time.monotonic() + switch_harness.PROBE_WAIT
    while time.monotonic() < end:
        rec = await asyncio.to_thread(switch_harness.read_receipt, st) or {}
        if rec.get('job') != job:
            return
        if rec.get('status') != 'probing':
            break
        await asyncio.sleep(0.5)
    else:
        return
    if (rec.get('status') != 'failed' or rec.get('reason') != 'provider_key' or rec.get('ark_plan_fix')
            or rec.get('target') != 'opencode'):
        return
    model = rec.get('model') or ''
    if expected_model and model != expected_model:
        return
    provider = model.partition('/')[0]
    cfg = await asyncio.to_thread(st.agent_config)
    if not cfg and rec.get('from') is None and origin.agent is None:
        from . import working_root
        cfg = {'dir':str(working_root.select(st))}
    if not cfg or cfg.get('kind') != rec.get('from') or (cfg.get('session_mode') == 'shared' and cfg.get('shared_opencode_port')):
        return
    purpose = (f'保存密钥并重试切换到 {model}' if host.lang == 'zh' else f'Save key and retry switching to {model}')
    def live():
        current = switch_harness.read_receipt(st) or {}
        return (current == rec and 0 <= time.time() - current['at'] < switch_harness.JOB_MAX_AGE
                and origin.live(host) and _official(provider, cfg['dir'], model))
    if not live():
        return
    active = host.__dict__.setdefault('_switch_key_guards', {})
    active[job] = live
    try:
        from . import approval_review
        card = None
        async def opened(shown):
            nonlocal card
            card = shown
        with approval_review.origin_scope(origin.source):
            res = await provider_runtime.request_key(host, provider, purpose=purpose, switch=(job,origin.device), on_card=opened)
            if res.get('result') == 'questioned' and card is not None:
                res = await asyncio.shield(card['fut'])
    except Exception as exc:
        from . import bg
        bg.swallowed(st.log, 'switch_key_card_failed', exc)
        res = {'result':'unknown'}
    finally:
        active.pop(job, None)
    if res.get('elsewhere'):
        return
    outcome = res.get('result')
    if outcome != 'saved' or not card or not card.get('_chat_committed'):
        if outcome == 'denied':
            origin.report(host, '已取消填写，本次没有保存密钥。要继续切换，请重新选择 OpenCode 模型。',
                          'You declined the key card. No key was saved. Select the OpenCode model again to continue.')
        elif outcome == 'timeout':
            origin.report(host, '密钥卡已过期，本次没有保存。要重新发卡，请再次选择 OpenCode 模型。',
                          'The key card expired without saving a key. Select the OpenCode model again for a new card.')
        elif outcome == 'busy':
            origin.report(host, '已有一张密钥卡正在处理。请先完成该卡，再选择 OpenCode 模型。',
                          'A key card is already open. Finish that card, then select the OpenCode model again.')
        else:
            origin.report(host, '这次未能确认保存密钥。请重新选择 OpenCode 模型后再试。',
                          'Saving the key could not be confirmed. Select the OpenCode model again to retry.')
        return
    retry_failed = ('密钥已保存，未能启动这次切换。请重新选择 OpenCode 模型。',
                    'Your key was saved, but the switch could not start. Select the OpenCode model again.')
    if not live():
        origin.report(host, *retry_failed)
        return
    # The new worker can restart the service immediately. Persist this receipt before submitting the retry.
    origin.report(host, '已收到，密钥已保存。接下来将重新验证所选 OpenCode 模型。',
                  'Received. Your key has been saved. Agent J will check the selected OpenCode model again.')
    req = {'kind':'switch', 'target':'opencode', 'model':model, 'force':force}
    try:
        result = await asyncio.to_thread(switch_harness.host_job, st, req, after=job, live=live, origin=origin)
    except Exception as exc:
        from . import bg
        bg.swallowed(st.log, 'switch_key_retry_unconfirmed', exc)
        origin.report(host, '密钥已保存，暂时无法确认切换是否已启动。请查看当前模型。',
                      'Your key was saved. The switch could not be confirmed. Check the current model.')
        return
    if not result.get('ok'):
        origin.report(host, *retry_failed)
