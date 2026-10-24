"""Transport-only avatar stand-in; the real avatar is covered in P501b."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from agentj import provenance


class CaptureAvatar:
    def __init__(self, reply='分身答复'):
        self.calls=[]
        self.reply=reply
        self.sessions=NS(stop_all=AsyncMock())
        self.offer_choice=AsyncMock()
        self.receive_owner=AsyncMock(side_effect=self.receive)

    async def receive(self, source, mid, text, paths=(), valid=lambda:True, *, admission=None, blobs=(), quote=None, quote_text=None, source_info=None):
        admission=admission or {}
        session=NS(source_kind='email' if source=='mail' else 'telegram',
                   provenance=provenance.email_owner(admission.get('addr','owner@example.test')) if source=='mail' else provenance.telegram_owner(admission.get('owner_id',1)))
        self.calls.append((session,text,list(blobs)))
        return self.reply if valid() else None


def legacy_transport_avatar(case, host, get_channel):
    """Adapt old transport fixtures, NOT admission evidence.

    Their _accept is the existing synthetic serializer/completion queue. Reuse it
    only inside this explicitly installed model stand-in, so cursor/enrollment,
    quote and outbound tests retain their lower-level checks. P501b.Routing tests
    the real ingress separately and proves it never calls the main _accept.
    """
    from agentj import telegram as tg, tg_cursor
    from unittest.mock import patch
    quiet = 'fixture-avatar-completion-is-driven-by-the-test'
    original_api = tg.api
    def fixture_api(cfg, method, values, *args, **kwargs):
        if method == 'sendMessage' and values.get('text') == quiet:
            return {}
        return original_api(cfg, method, values, *args, **kwargs)
    patcher=patch.object(tg,'api',fixture_api)
    patcher.start();case.addCleanup(patcher.stop)
    async def receive(source, mid, text, paths=(), valid=lambda:True, *, admission=None, blobs=(), quote=None, quote_text=None, source_info=None):
        admission=admission or {}
        uid=admission.get('owner_id',1)
        session=NS(device='telegram:'+str(uid)+':'+str(uid),name='Telegram owner',cid=0,
                   source_kind='telegram',provenance=source_info or provenance.telegram_owner(uid))
        qkw={'quote':quote,'quote_text':quote_text} if quote_text and quote else {}
        send=await host._accept(session,text,None,blobs=list(blobs),**qkw) if blobs else await host._accept(session,text,None,**qkw)
        channel=get_channel()
        if send is not None and isinstance(getattr(send,'turn',None),int):
            cfg=tg.configuration(host.st)
            channel.turn_enrollment[send.turn]=dict(cfg)
            channel.turn_routes[send.turn]=(uid,uid,session.device)
            tg_cursor.outbox_put(channel.root,send.turn,uid,uid,session.device,cfg.get('generation'))
        return quiet
    host.external_channels=NS(receive_owner=AsyncMock(side_effect=receive),sessions=NS(stop_all=AsyncMock()),offer_choice=AsyncMock())
