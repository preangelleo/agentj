
from __future__ import annotations

import contextlib
import contextvars

_CURRENT = contextvars.ContextVar('owner_call', default=None)
_CONT = contextvars.ContextVar('owner_continuation', default=None)


class Execution:
    def __init__(self, send):
        self.send, self.live = send, True


def live():
    check = _CURRENT.get()
    return check is None or check()


@contextlib.contextmanager
def request(check):
    token = _CURRENT.set(check)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def continuation():
    row = _CONT.get()
    return row if row and row[2]() else None


@contextlib.contextmanager
def supplement(scope, device, check):
    token = _CONT.set((scope, device, check))
    try:
        yield
    finally:
        _CONT.reset(token)


def bound():
    check = _CURRENT.get()
    return check is not None and check()
