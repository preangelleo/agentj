"""P283: the owner binds Telegram with a one-time pairing code — no third-party bot, and never "whoever writes first".

The host makes a 6-digit code (10 minutes, one use) and shows it only on the paired phone's card: 「在 Telegram 里给 @<bot>
发：/start 123456」. The private chat that sends the right code is the owner's: enrolled, then 「Telegram 接通了」. A wrong,
expired or replayed code never binds; a code in a group is ignored (private chats only). Too many wrong tries burn the
code (the owner makes a new one). Anyone who is not the owner and writes to the bot in private gets one fixed answer —
their own numeric ID and how to pair — never a model reply; only a count is kept.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

TTL = 600                 # seconds a code is valid
WRONG_MAX = 10            # wrong tries (all senders together) before the code is burnt
WRONG_PER_SENDER = 3      # wrong tries one sender may make
ID_REPLY_GAP = 60         # seconds between two fixed ID answers to the same stranger
ID_REPLY_KEEP = 512       # strangers remembered for that gap
_CODE = re.compile(r'^(?:/start(?:@(\w+))?[ \t]+)?(\d{6})$')


def _hash(code: str) -> bytes:
    return hashlib.sha256(code.encode()).digest()


class Pairing:
    """One live code, in serve's memory only (a restart ends it: the owner makes a new one)."""

    def __init__(self, key_env: str, username: str | None, now: float | None = None):
        self.key_env, self.username = key_env, username
        self.code = f'{secrets.randbelow(10 ** 6):06d}'
        self.digest = _hash(self.code)
        self.started = time.time() if now is None else now
        self.expires = self.started + TTL
        self.wrong = 0
        self.per_sender: dict[int, int] = {}
        self.done = False           # used (bound) or burnt: never accepted again

    def live(self, now: float | None = None) -> bool:
        return not self.done and (time.time() if now is None else now) < self.expires

    def check(self, code: str, sender: int, now: float | None = None) -> str:
        """ok | expired | wrong | locked. `ok` consumes the code (one use)."""
        now = time.time() if now is None else now
        if self.done:
            return 'expired'
        if now >= self.expires:
            self.done = True
            return 'expired'
        if self.per_sender.get(sender, 0) >= WRONG_PER_SENDER:
            return 'locked'
        if hmac.compare_digest(_hash(code), self.digest):
            self.done = True
            return 'ok'
        self.wrong += 1
        self.per_sender[sender] = self.per_sender.get(sender, 0) + 1
        if self.wrong >= WRONG_MAX:
            self.done = True
        return 'wrong'


def parse(text, username: str | None = None) -> str | None:
    """`/start 123456`, `/start@ThisBot 123456` or a bare `123456` → the code; anything else → None."""
    if not isinstance(text, str):
        return None
    m = _CODE.match(text.strip())
    if not m:
        return None
    if m[1] and username and m[1].lower() != username.lower():
        return None
    return m[2]


def is_start(text) -> bool:
    return isinstance(text, str) and re.match(r'^/start(?:@\w+)?(?:\s|$)', text.strip()) is not None


def lang_of(sender: dict, fallback: str = 'zh') -> str:
    code = sender.get('language_code') if isinstance(sender, dict) else None
    if isinstance(code, str) and code:
        return 'zh' if code.lower().startswith('zh') else 'en'
    return 'en' if fallback == 'en' else 'zh'


def _clean(s, cap=64) -> str:
    s = re.sub(r'[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]', ' ', str(s or ''))
    return re.sub(r'\s+', ' ', s).strip()[:cap]


def id_card(sender: dict, lang: str) -> str:
    """The fixed answer to a sender who is not the owner: their numeric ID (plus @username and name), how to pair."""
    uid = sender.get('id')
    user = _clean(sender.get('username'), 32)
    name = _clean(' '.join(x for x in (sender.get('first_name'), sender.get('last_name')) if isinstance(x, str)))
    if lang == 'en':
        lines = [f'Your Telegram numeric ID: {uid}']
        if user:
            lines.append(f'Username: @{user}')
        if name:
            lines.append(f'Name: {name}')
        lines += ['', "If you own this Agent J, pair in Agent J: your paired phone shows a 6-digit code — send it to me as "
                      '/start <code>. Nothing else you send here is read.']
        return '\n'.join(lines)
    lines = [f'你的 Telegram 数字 ID：{uid}']
    if user:
        lines.append(f'用户名：@{user}')
    if name:
        lines.append(f'名字：{name}')
    lines += ['', '如果你是这台 Agent J 的主人，请在 Agent J 里完成配对：已配对的手机上会显示 6 位配对码，'
                  '把「/start 配对码」发给我就行。这里发的其它内容不会被读取。']
    return '\n'.join(lines)


WRONG = {'zh': '配对码不对或已经过期，没有绑定。请在 Agent J 里重新生成配对码再发一次。',
         'en': 'That pairing code is wrong or has expired; nothing was bound. Make a new code in Agent J and send it again.'}
LOCKED = {'zh': '错误次数太多，这个配对码已作废。请在 Agent J 里重新生成配对码。',
          'en': 'Too many wrong tries: this pairing code is no longer valid. Make a new one in Agent J.'}
CONNECTED = {'zh': 'Telegram 接通了。从现在起，您在 Telegram 给我发消息我都能收到，和在这里说是同一个对话，两边随便用。',
             'en': "Telegram is connected. From now on, anything you send me on Telegram reaches me — it's the same "
                   'conversation as here, so use whichever is handy.'}



def connected(lang: str, address: str = "") -> str:
    """P283 + Jarvis ruling (P290): the connected line opens with the owner's current form of address (owner.address);
    none set → no address at all (never a hard-coded 「董事长」)."""
    t = CONNECTED["en" if lang == "en" else "zh"]
    a = " ".join(str(address or "").split())[:16]
    return (a + ("，" if lang != "en" else ", ") + t) if a else t

def card(lang: str, username: str | None, code: str) -> str:
    """The phone card. The code is shown here and nowhere else (never the CLI answer, a log or Telegram)."""
    mins = TTL // 60
    bot = '@' + username if username else ('your bot' if lang == 'en' else '你的 bot')
    link = f'https://t.me/{username}?start={code}' if username else ''
    code_block, command = f'\n```text\n{code}\n```\n', f'\n```text\n/start {code}\n```\n'
    if lang == 'en':
        return (f'Telegram pairing code (valid {mins} minutes, one use):' + code_block +
                f'In Telegram, send {bot}:' + command +
                (f'\n[Open Telegram and tap Start]({link})' if link else '') +
                '\nOnly a private chat is accepted. Whoever sends this code becomes the owner, so do not share it.')
    return (f'Telegram 配对码（{mins} 分钟内有效，只能用一次）：' + code_block +
            f'在 Telegram 里给 {bot} 发：' + command +
            (f'\n[打开 Telegram，再点「开始」]({link})' if link else '') +
            '\n只认私聊。谁发了这个码谁就是主人，别转给别人。')



def phone_bound(lang: str, username: str | None, uid: int) -> str:
    bot = '@' + username if username else 'Telegram'
    return (f'Telegram is now bound: {bot} answers only your private chat (ID {uid}).' if lang == 'en'
            else f'Telegram 已绑定：{bot} 只认您的私聊（ID {uid}）。')


def phone_burnt(lang: str) -> str:
    return ('Too many wrong Telegram pairing codes were tried, so the code was cancelled. Make a new one if you still want '
            'to pair.' if lang == 'en' else 'Telegram 配对码被试错太多次，已作废。还要绑定的话请重新生成。')
