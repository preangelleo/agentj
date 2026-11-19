"""Visible outcomes for an input card created by the host-verified owner phone turn."""
from . import provenance
from .compose import Send


_TEXT = {
    'saved': ('已收到，密钥已保存。', 'Received. Your key has been saved.'),
    'check_failed': ('已收到，密钥已保存，但验证未通过。', 'Received. Your key was saved, but verification failed.'),
    'timeout': ('密钥卡已过期，本次没有保存。', 'The key card expired without saving a key.'),
    'denied': ('已取消填写，本次没有保存密钥。', 'You declined the key card. No key was saved.'),
    'failed': ('这次未能保存密钥。', 'The key could not be saved.'),
    'unknown': ('这次无法确认密钥是否已保存。', 'Saving the key could not be confirmed.'),
}


class Receipt:
    def __init__(self, host, proof):
        self.host, self.agent, self.origin = host, proof[0], proof[1]
        self.send = getattr(self.agent, 'cur_send', None)
        self.message = self.origin['message_id']
        self.epoch = str(host.hist.epoch)
        self.device = getattr(self.send, 'device', None)
        self.key = host.st.sign_key(self.device) if self.device else None
        self.directory = (host.agent_cfg or {}).get('dir')
        self.native_id = host.st.agent_session(self.agent.kind)
        host._task_id()
        self.task_epoch = host.task_epoch
        self.detached = self.reported = self.resumed = False
        self.saved = False

    def same_page(self):
        h = self.host
        src = (h.hist.get(self.message) or {}).get('src') or {}
        return (self.key and h.st.sign_key(self.device) == self.key and h.agent is self.agent
                and self.epoch == str(h.hist.epoch) and src.get('k') == 'phone' and src.get('dev') == self.device
                and self.origin.get('session') == self.epoch and self.origin.get('verified') is True
                and not self.origin.get('suspicious') and self.directory == (h.agent_cfg or {}).get('dir')
                and self.native_id == h.st.agent_session(self.agent.kind)
                and self.send is not None and self.send.turn == self.message
                and getattr(self.send, 'approval_origin', None) == self.origin)

    def current(self, continuation=None):
        h = self.host
        refs = h.__dict__.get('_approval_origins', {})
        current = getattr(h.agent, 'cur_send', None)
        return (self.same_page() and not h.stopped() and self.native_id
                and self.message == max(refs, default=0) and refs[self.message] == self.origin
                and self.task_epoch == getattr(h, 'task_epoch', None)
                and (current is None or current is self.send or current is continuation))

    def finished(self, card, result):
        if self.reported or result.get('elsewhere') or not self.same_page():
            return
        outcome = result.get('result')
        if outcome == 'saved':
            if not card.get('_chat_committed'):
                outcome = 'unknown'
            elif (result.get('receipt') or {}).get('verify') == 'fail':
                outcome = 'check_failed'
        elif outcome not in ('timeout', 'failed', 'denied'):
            outcome = 'unknown'
        self.reported = True
        self.saved = outcome == 'saved'
        text = _TEXT[outcome][self.host.lang == 'en']
        # A separate ordinary reply survives a silent model turn and reconnection.
        self.host.hist_add({'k':'agent', 'text':'', 'quote':{'id':self.message}}, text, 'done')
        self.host.st.log('secret_chat_receipt', id=card['id'], result=outcome, turn=self.message)
        self.resume()

    def detach(self):
        self.detached = True
        self.resume()

    def resume(self):
        if self.detached and self.saved and not self.resumed and self.current():
            self.resumed = True
            self.agent.submit(Continuation(self))


class Continuation(Send):
    def __init__(self, receipt):
        self.receipt = receipt
        text = ('密钥已保存。请继续本对话中原先等待密钥的任务，并回复结果。' if receipt.host.lang != 'en' else
                'The key has been saved. Continue the task that was waiting for it in this conversation and report the result.')
        super().__init__('agentj-secret', '', 0, text=text, by='Agent J', source=provenance.system())

    def delivery_allowed(self):
        return self.receipt.current(self)

    async def wait_ready(self):
        if not self.delivery_allowed():
            return False
        self.turn = self.receipt.host.hist_add({'k':'agent', 'text':'', 'quote':{'id':self.receipt.message}}, '', 'open')['id']
        return True
