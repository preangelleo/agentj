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


# R-0056: Codex native declines carry no reason field, so the model reads "rejected by user". Every decline Agent J sends
# is preceded by this developer note naming who decided: the owner on a paired device, or Agent J policy with its class.
def _owner_texts():
    """Both languages of serve's paired-device decline (sysmigrate.DECLINED) — security r1 F2."""
    try:
        from .sysmigrate import DECLINED
        return frozenset(DECLINED)
    except Exception:  # noqa: BLE001 — the literal pair below is the same text
        return frozenset(('用户在手机上拒绝了这个操作。', 'The owner declined this on the phone.'))


def model_note(result, why, cls=None):
    """The model-facing line for a declined native request. `result` is the host decision dict (its `reason`, if any)."""
    why = why.strip() if isinstance(why, str) else ''
    msg = result.get('message') if isinstance(result, dict) else None
    if cls is None and (owner_denied(result) or reason(result) == 'owner_denied_before'
                        or (isinstance(msg, str) and msg.strip() in _owner_texts())):
        return 'Agent J approval result: refused by the owner on a paired device. ' + why
    cls = cls or reason(result)
    if cls == 'unknown':     # security r1 F2: never claim who decided when the host did not say
        return 'Agent J approval result: declined; reason unknown; treat as refused, do not retry another way. ' + why
    text = ('Agent J approval result: NOT refused by the owner (ignore the native "rejected by user" wording); '
            f'refused by Agent J policy: {cls}. ' + why)
    if cls.startswith('off_list_write_'):
        # The frozen off-list rule names every request outside its closed read list "write" (find, grep, journalctl,
        # ls -t, a path outside the working folder, a pipe or redirect): say so instead of claiming the command writes.
        text += (' Note: this class covers every request outside the closed read list for this source (for example find, '
                 'grep, journalctl, a pipe or redirect, or a path outside the working folder), not only commands that write.')
    return text
