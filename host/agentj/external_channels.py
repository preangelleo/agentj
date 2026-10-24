"""P501b: authenticated owner mail / Telegram enter a separate conversation.

Authentication remains in each adapter. No received text becomes a main-session
Send. Held requests reuse WeChat's signed, expiring proposal/claim machinery.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
from .wechat import WeChat
from .channel_avatar import handoff, choice, choose, Reply, openrouter_bound
from . import provenance

LABELS = {'wechat': '微信', 'mail': '邮件', 'telegram': 'Telegram'}


class ExternalChannels(WeChat):
    def __init__(self, host, **kwargs):
        super().__init__(host, namespace='external', **kwargs)
        self.receive_lock = asyncio.Lock()

    async def receive_owner(self, source, message_id, text, paths=(), valid=lambda: True, *, admission=None, blobs=(), quote=None, quote_text=None, source_info=None):
        if source not in ('mail', 'telegram'):
            raise ValueError('channel_source')
        async with self.receive_lock:
            if self.closed or self.h.stopped() or not valid():
                return None
            rid = hashlib.sha256((source + ':' + str(message_id)).encode()).hexdigest()
            previous = self.store.data['records'].get(rid)
            if previous:
                return None  # ambiguous replies are not replayed after a crash
            wd = (self.h.agent_cfg or {}).get('dir')
            if not wd:
                return None
            if quote_text:
                text = quote_text + '\n\n' + text
            payload = json.dumps({'source': source, 'text': text, 'untrusted': True,
                                  'execute': False, 'attachments': list(paths)}, ensure_ascii=False).encode()
            manifest = await asyncio.to_thread(self.store.place, wd, payload, source + '-message.json', 'application/json')
            if self.closed or self.h.stopped() or not valid():
                return None
            record = self.store.put(rid, [manifest, *paths], text)
            record.update(source=source, state='processing', admission=admission)
            self.store.save()
            self.h.agent_notice('来自' + LABELS[source] + ' · ' + manifest)
            reply = None
            try:
                from .channel_inputs import inputs
                model_parts, images = await asyncio.to_thread(inputs, wd, paths, blobs)
                if self.closed or self.h.stopped() or not valid():
                    record['state'] = 'cancelled'
                    self.store.save()
                    return None
                reply = await self.assistant('\n'.join([text, *model_parts])[:32000], images)
            except Exception:
                self.assistant_waiting = True
                self.store.count('assistant_failed')
            if self.closed or self.h.stopped() or not valid():
                record['state'] = 'cancelled'
                self.store.save()
                return None
            waiting = reply is None and getattr(self, 'assistant_waiting', False)
            record['state'] = 'replied' if reply is not None else 'waiting_model' if waiting else 'pending'
            self.store.save()
            if reply is None and not waiting:
                self.open_card(record)
            if waiting:
                return self.waiting_reply()
            return Reply(reply, getattr(self, 'reply_images', ())) if reply is not None else handoff(self.h)

    async def offer_choice(self):
        from .channel_plans import inventory
        usable = await asyncio.to_thread(inventory)
        saved = self.store.data.setdefault('avatar', {})
        result = choice(self.h.agent_cfg or {}, saved, usable)
        result['options'] = usable
        if result['ask']:
            self.h.agent_notice('外部通道分身用哪个套餐？' + '、'.join(r['id'] if r.get('selectable') is not False else r['label']['zh'] for r in usable) +
                                ('；推荐 Codex。' if str(result['recommend']).startswith('codex') or any(r['id'] == result['recommend'] and r['kind'] == 'codex' for r in usable) else '。') + '默认沿用主对话；可以直接告诉 Agent J 更换。')
            saved['asked'] = True
            self.store.save()
        return result

    async def select(self, req):
        from .channel_plans import inventory
        source = getattr(getattr(getattr(self.h, 'agent', None), 'cur_send', None), 'source', None)
        if not isinstance(source, dict) or source.get('kind') != 'phone' or source.get('role') != 'owner':
            return {'ok': False, 'why': 'owner_phone_required'}
        usable = await asyncio.to_thread(inventory)
        wanted = req.get('id')
        free_model = None
        if wanted in ('free', '免费'):
            from .binaries import resolve
            exe = resolve('opencode')['path']
            from .channel_free import option
            return {'ok':False, **await asyncio.to_thread(option, exe)}
        # Merely having a global environment variable is not a bound route. The
        # existing native configuration and provider login must both be present.
        bound = await asyncio.to_thread(openrouter_bound, self.h)
        try:
            config = choose(wanted, self.h.agent_cfg or {}, usable, free_model=free_model,
                            router_bound=bound, model=req.get('model'))
        except ValueError as e:
            return {'ok': False, 'why': str(e)}
        if not await self.approval('更换外部通道分身套餐 / Change external-channel plan: ' + str(wanted), {'op':'avatar','config':config}):
            return {'ok':False, 'why':'denied'}
        await self.sessions.stop_all()
        if getattr(self.h, 'wechat', None):
            await self.h.wechat.sessions.stop_all()
            self.h.wechat.store.data['sessions'] = {}
            self.h.wechat.store.save()
        self.store.data['avatar'] = {'asked':True, **({} if wanted == 'shared' else {'config':config})}
        self.store.data['sessions'] = {}
        self.store.save()
        return {'ok': True, 'kind':config.get('kind'), 'model':config.get('model')}

    def admitted(self, record):
        admission = record.get('admission') or {}
        if record.get('source') == 'mail':
            from .mail import read_config, _same
            cfg = read_config(self.h.st)
            return (cfg['enabled'] and cfg['generation'] == admission.get('generation') and
                    any(_same(row['addr'], admission.get('addr', '')) and row['role'] == 'owner' for row in cfg['allow']))
        if record.get('source') == 'telegram':
            from .telegram import configuration
            cfg = configuration(self.h.st)
            return bool(cfg) and all(cfg.get(k) == admission.get(k) for k in ('owner_id','key_env','generation'))
        return False

    async def claim(self, rid, **kwargs):
        record = self.store.data['records'].get(rid)
        if not record or not self.admitted(record):
            return {'ok':False, 'why':'channel_revoked'}
        return await super().claim(rid, **kwargs)

    async def run(self):
        for record in self.store.data['records'].values():
            if record['state'] == 'processing':
                record['state'] = 'pending'
        self.store.save()
        while not self.closed:
            if not self.h.stopped():
                if any(r['state'] == 'pending' and r['until'] <= self.clock() for r in self.store.data['records'].values()):
                    self.store.expire()
                for record in self.store.data['records'].values():
                    self.open_card(record)
            await asyncio.sleep(1)
