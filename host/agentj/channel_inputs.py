"""Bounded owner-channel attachments; only received inbox files, never model paths.

Images use WeChat's byte input and NativeCodex's private input directory. Text
and PDF extraction happen on the host, without giving an avatar file tools.
"""
import contextlib
import mimetypes
import os
from pathlib import Path
import stat
from . import inbox
from .channel_native import MAX_IMAGE, image_type
from .bots.store import BotError


def read_received(workdir, path):
    relative = Path(path).relative_to(Path(workdir).resolve() / inbox.DOT)
    if len(relative.parts) != 3 or relative.parts[0] != inbox.INBOX or any(x in ('.', '..') for x in relative.parts):
        raise ValueError('attachment_path')
    with contextlib.ExitStack() as stack:
        fd = stack.enter_context(inbox.dot_dir(workdir, create=False))
        for name in relative.parts[:-1]:
            fd = inbox._open_dir(name, fd, False)
            stack.callback(os.close, fd)
        fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(fd, 'rb') as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or not 0 < info.st_size <= MAX_IMAGE:
                raise ValueError('attachment_size')
            data = f.read(MAX_IMAGE + 1)
            if len(data) > MAX_IMAGE:
                raise ValueError('attachment_size')
            return data


def inputs(workdir, paths, blobs=()):
    """Paths come from authenticated adapters; blob MIME is only a display hint."""
    hints = {str(b.path): b.mime for b in blobs}
    parts, images = [], []
    for path in list(dict.fromkeys(map(str, paths)))[:10]:
        try:
            data = read_received(workdir, path)
            if image_type(data):
                if len(images) < 4:
                    images.append(data)
                continue
            mime = hints.get(path) or mimetypes.guess_type(path)[0] or ''
            if len(data) > 1024 * 1024:
                raise ValueError('attachment_size')
            if mime == 'application/pdf' and data.startswith(b'%PDF-'):
                from .bots.knowledge import extract
                text = extract('attachment.pdf', data)
            elif mime.startswith('text/') or mime in ('application/json', 'application/xml', 'application/x-yaml'):
                text = data.decode('utf-8-sig')
                if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
                    raise ValueError('attachment_binary')
            else:
                raise ValueError('attachment_format')
            parts.append('[Untrusted attachment content]\n' + text[:24000])
        except (OSError, ValueError, inbox.Unsafe, BotError):
            parts.append('[Attachment unavailable to this avatar; review in Agent J Web App.]')
    return parts, images
