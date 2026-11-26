"""One generic, opted-in reminder via a currently verified owner channel."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from email.message import EmailMessage
from email.policy import SMTP
import smtplib
import time

from . import mail, preferences, support_fault_mail, telegram, wechat_forward
from .text import pick

# Enable only after the verified-owner provider journeys pass.
ENABLED = False
CHANNELS = ('wechat', 'telegram', 'mail')
CLOSED = 'unavailable'
ACCEPTED = 'accepted'
UNKNOWN = 'unconfirmed'


def channels(prefs):
    if not ENABLED:
        return ()
    order = preferences.get(prefs, 'notify.card_reminders', [])
    if not isinstance(order, list) or any(not isinstance(item, str) or item not in CHANNELS for item in order):
        return ()
    return tuple(order) if len(set(order)) == len(order) else ()


async def _wechat(host, _subject, text, live):
    wc = getattr(host, 'wechat', None)
    if not wc or wechat_forward._why(wc) or not live():
        return CLOSED
    data = wc.store.data
    generation = data['generation']
    account = dict(data['account'])
    if not generation or not account.get('ilink_user_id'):
        return CLOSED
    api = wc.api_factory(account)
    if not live() or wechat_forward._why(wc) or data['generation'] != generation or data['account'] != account:
        return CLOSED
    try:
        await wc.send(api, text, generation)
    except Exception as exc:  # only explicit rejection may advance to the next channel
        if getattr(exc, 'code', None) in ('context_rejected', 'no_context', 'stopped'):
            return CLOSED
        return UNKNOWN
    return ACCEPTED


async def _telegram(host, _subject, text, live):
    tg = getattr(host, 'telegram', None)
    config = telegram.configuration(host.st) if tg and tg.enabled() else None
    if not config or not config.get('generation') or tg.enrollment != config or not tg.bot_id or not tg.last_poll:
        return CLOSED
    bot = tg.bot_id

    def send():
        if (not live() or not tg.enabled() or tg.bot_id != bot or tg.enrollment != config
                or telegram.configuration(host.st) != config):
            return CLOSED
        try:
            telegram.api(config, 'sendMessage', {'chat_id': config['owner_id'], 'text': text})
        except telegram.TelegramError as exc:
            return CLOSED if exc.kind in ('missing_key', 'http_400', 'http_401', 'http_403', 'http_429') else UNKNOWN
        return ACCEPTED

    return await asyncio.to_thread(send)


def _mail_send(host, subject, text, live):
    consent = support_fault_mail.configured(host.st)
    if not consent or not live():
        return CLOSED
    try:
        scope, binding = consent._bound()
        config = mail.read_config(host.st)
        if (not consent._read().get('active', {}).get('desk_active') or config['address'] != scope.address):
            return CLOSED
        password = mail.load_secret(host.st, scope.address)
        if not password:
            return CLOSED
        client = mail.smtp_open(config, password)
    except (support_fault_mail.MailConsentError, mail.MailError, OSError):
        return CLOSED
    try:
        message = EmailMessage(policy=SMTP)
        message['From'] = message['To'] = scope.address
        message['Subject'] = subject
        message['Auto-Submitted'] = 'auto-generated'
        message['X-Auto-Response-Suppress'] = 'All'
        message[mail.MARK] = mail.mark(config, 'notes')
        message.set_content(text)
        with host.st.file_lock('support-fault-mail'):
            current, generation = consent._bound()
            if (not live() or current != scope or generation != binding or mail.read_config(host.st) != config
                    or not consent._read().get('active', {}).get('desk_active')):
                return CLOSED
            try:
                rejected = client.send_message(message, from_addr=scope.address, to_addrs=[scope.address])
            except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
                return CLOSED
            except (OSError, smtplib.SMTPException):
                return UNKNOWN
        return CLOSED if rejected else ACCEPTED
    except (support_fault_mail.MailConsentError, mail.MailError):
        return CLOSED
    finally:
        with suppress(OSError, smtplib.SMTPException):
            client.quit()


async def _mail(host, subject, text, live):
    return await asyncio.to_thread(_mail_send, host, subject, text, live)


async def deliver(host, event, pending):
    order = channels(host.preferences)
    if not order or not pending():
        return
    now = time.monotonic()
    history = host.__dict__.setdefault('_card_reminder_attempts', {})
    for rid in list(history):
        if now - history[rid] >= 3600:
            history.pop(rid)
    if event in history or len(history) >= 6:
        return
    history[event] = now
    subject = pick(host.lang, 'Agent J 有一张卡片等你处理', 'An Agent J card is waiting')
    text = pick(host.lang, '请在已配对的遥控器上打开 Agent J 处理。', 'Open Agent J on your paired remote to review the card.')
    providers = {'wechat': _wechat, 'telegram': _telegram, 'mail': _mail}
    for channel in order:
        def live(channel=channel):
            return pending() and channel in channels(host.preferences) and not host.stopped()

        if not live():
            continue
        result = await providers[channel](host, subject, text, live)
        host.st.log('card_reminder', id=event, via=channel, result=result)
        if result != CLOSED:
            return
