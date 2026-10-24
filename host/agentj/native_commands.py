"""P495 #175 fixed native headless commands. Never convert unsupported commands into model instructions.

Contracts: Codex 0.162.1 generated ReviewStartParams; OpenCode v1 server API /session/:id/init
and /command; v2 API /api/session/:id/command (name/text). The adapter's normal serialized
turn, delivery receipt, permission events and completion/interrupt handling remain the owners.
"""
from __future__ import annotations
import json
import secrets
from urllib.parse import quote
from . import slash


def opencode_request(text: str, sid: str, model: str | None, v2: bool):
    parsed = slash.parse(text)
    if not parsed or parsed[0] not in ('init', 'review'):
        return None
    name, arg = parsed
    prefix = ('/api' if v2 else '') + '/session/' + quote(sid, safe='')
    if v2:
        return prefix + '/command', {'name': name, 'text': arg}
    if name == 'init':
        if arg:
            raise ValueError('OpenCode v1 /init does not accept arguments; use /init on its own.')
        provider, sep, mid = (model or '').partition('/')
        if not sep or not provider or not mid:
            raise ValueError('OpenCode v1 /init needs a selected model; choose one with /model first.')
        return prefix + '/init', {'messageID': 'msg_' + secrets.token_hex(16), 'providerID': provider, 'modelID': mid}
    body = {'command': name, 'arguments': arg}
    if model:
        body['model'] = model
    return prefix + '/command', body


NATIVE_AGENT = "agentj-native-main"


def main_config(content: str, cfg: dict) -> str:
    """v1 command has no system field: keep the same core via a child-only primary agent.

    No permissions/tools are added; the same session permission rules still decide every tool.
    The owner's config file is never edited. Full prompt + hidden reminder come from the existing
    identity owner, not a copied constitution or new packaged identity asset.
    """
    from . import main_identity
    data = json.loads(content)
    agents = data.setdefault("agent", {})
    agents[NATIVE_AGENT] = {"mode": "primary", "hidden": True,
                            "prompt": main_identity.prompt(cfg) + main_identity.turn_reminder(cfg, True)}
    return json.dumps(data, ensure_ascii=False)


async def request_v1(agent, client, path, body):
    """v1 command/init replies are synchronous. Hold the say lock only through the write.

    A drained write is uncertain, not proof of native acceptance. Keep that state until
    the response confirms success; never replay, and never block the relay receive lane
    on a Withdraw while a model/approval is still running.
    """
    import asyncio
    from .agent import UNCERTAIN
    written = asyncio.Event()
    task = waiter = None
    send = getattr(agent, "cur_send", None)

    async def submitted():
        nonlocal task, waiter
        # deliver owns the send lock and checks withdrawal before any request starts.
        task = asyncio.create_task(client.request("POST", path, body, timeout=600, written=written))
        waiter = asyncio.create_task(written.wait())
        await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            status, _ = task.result()
            return True if status in (200, 204) else False if 400 <= status < 500 else UNCERTAIN
        return UNCERTAIN

    try:
        await agent.deliver(submitted)
        result = await task
        if result[0] in (200, 204) and send is not None and agent.cur_send is send:
            async with send.lock:
                if send.state == "uncertain":
                    send.state = "delivered"
                    notify = getattr(agent.host, "say_delivered", None)
                    if notify:
                        notify(send)
        return result
    finally:
        pending = [t for t in (task, waiter) if t is not None]
        for t in pending:
            if not t.done():
                t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
