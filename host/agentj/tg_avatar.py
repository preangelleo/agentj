"""P275: the official Agent J photo for every Telegram bot bound through Agent J (owner bot and customer-service bots).

Bot API (core.telegram.org/bots/api, checked 2026-10-14):
  getUserProfilePhotos(user_id, limit)  → UserProfilePhotos {total_count, photos}; the bot asks about its own user id
  setMyProfilePhoto(photo: InputProfilePhoto) → True; InputProfilePhotoStatic = {"type": "static", "photo": "attach://<name>"}
      with a .JPG uploaded as multipart/form-data under <name> (profile photos can't be reused: always a fresh upload)
  removeMyProfilePhoto() → True, no parameters

Automatic only once per Telegram bot and only when it has no photo: an owner's own photo is never replaced. The result is
remembered in <state>/telegram-avatar.json (bot user id → set / kept); a failed check is not remembered and retries at the
next polling retry (owner bot) or bind (customer-service bot). The bot key never enters this module: callers pass `call(method, values)` and
`upload(method, fields, field, filename, mime, data)` that already carry it, and no Bot API body or URL is logged.
"""
import json
import os
import threading
import time
from pathlib import Path

ASSET = Path(__file__).resolve().parent / 'assets' / 'agentj-telegram-avatar-640.jpg'   # site/tools/brand-kit.py
FIELD = 'agentj_avatar'
LEDGER = 'telegram-avatar.json'
_lock = threading.Lock()


def has_photo(call, bot_id):
    r = call('getUserProfilePhotos', {'user_id': bot_id, 'limit': 1})
    if not isinstance(r, dict) or type(r.get('total_count')) is not int:
        raise ValueError('bad_answer')
    return r['total_count'] > 0


def set_official(upload):
    r = upload('setMyProfilePhoto', {'photo': json.dumps({'type': 'static', 'photo': 'attach://' + FIELD})},
               FIELD, 'agentj-avatar.jpg', 'image/jpeg', ASSET.read_bytes())
    if r is not True:
        raise ValueError('bad_answer')


def remove(call):
    if call('removeMyProfilePhoto', {}) is not True:
        raise ValueError('bad_answer')


def _load(root):
    try:
        d = json.loads((Path(root) / LEDGER).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(root, bot_id, result):
    with _lock:
        d = _load(root)
        d[str(bot_id)] = {'result': result, 'at': int(time.time())}
        p = Path(root) / LEDGER
        tmp = p.with_name(p.name + '.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(d, f)
        os.replace(tmp, p)


def recorded(root, bot_id):
    """'set' (Agent J gave it the official photo) / 'kept' (it had its own) / None (never checked)."""
    v = _load(root).get(str(bot_id))
    return v.get('result') if isinstance(v, dict) else None


def ensure(root, call, upload, bot_id):
    """→ 'skipped' (checked before) / 'kept' (has a photo) / 'set'. Raises on a Bot API failure (nothing remembered)."""
    if recorded(root, bot_id):
        return 'skipped'
    if has_photo(call, bot_id):
        result = 'kept'
    else:
        set_official(upload)
        result = 'set'
    remember(root, bot_id, result)
    return result


def act(root, action, call, upload, bot_id):
    """CLI / phone card: status | set (replace with the official photo) | reset (remove the current photo)."""
    if action == 'status':
        return {'has_photo': has_photo(call, bot_id), 'agentj': recorded(root, bot_id) == 'set'}
    if action == 'set':
        set_official(upload)
        remember(root, bot_id, 'set')
        return {'has_photo': True, 'agentj': True}
    if action == 'reset':
        remove(call)
        remember(root, bot_id, 'removed')
        return {'has_photo': has_photo(call, bot_id), 'agentj': False}
    raise ValueError('unknown_action')
