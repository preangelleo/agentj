"""Host-held refusal provenance for diagnostics/caching; never an approval decision."""
from .text import pick

def reason(receipt):
    value=receipt.get('reason') if isinstance(receipt,dict) else None
    return value if isinstance(value,str) and value else 'unknown'

def owner_denied(receipt):
    return reason(receipt)=='device'

def message(why,lang='zh',detail=None):
    messages={
        'device':('主人在已配对设备上拒绝了这次操作。','The owner denied this operation on a paired device.'),
        'owner_denied_before':('这一轮同一发起方的同类操作已被主人拒绝，本次未再弹卡。','The owner already denied this actor’s same category in this turn; no new card was shown.'),
        'timeout':('等待确认超时，主人没有作出拒绝。','Confirmation timed out; the owner did not deny it.'),
        'agent_gone':('发起会话已结束，请求已取消；这不是主人拒绝。','The initiating session ended and the request was cancelled; the owner did not deny it.'),
        'superseded':('旧请求已被替换并取消；这不是主人拒绝。','The old request was replaced and cancelled; the owner did not deny it.'),
        'serve_stop':('主机已停止，请求未执行；这不是主人拒绝。','The host stopped without executing the request; the owner did not deny it.'),
        'estop':('已急停，请求未执行。','Emergency stop is active; the request was not executed.'),
    }
    text=pick(lang,*messages.get(why,('主机未批准本次操作，未确认是主人拒绝。','The host did not approve this operation; an owner refusal is not established.')))
    if isinstance(detail,str) and detail.strip() and detail.strip() not in ('已拒绝。','Denied by paired phone or timeout.'):
        text+=' '+detail.strip()[:600]
    return text
