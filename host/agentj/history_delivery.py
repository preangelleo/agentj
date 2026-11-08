"""Coalesced history data delivery. Never queues approval, pairing or control messages."""
from __future__ import annotations
import sys

MAX_IDS, MAX_BYTES, BATCH = 500, 65536, 16


class Delivery:
    def __init__(self, host):
        self.host = host
        self.epoch = host.hist.epoch
        self.pending = {}  # int turn ids only; values are None, never a turn/blob/closure
        self.meta = self.scheduled = False
        self.replay_after = None

    def descriptor_bytes(self):
        return sys.getsizeof(self.pending) + sum(sys.getsizeof(k) for k in self.pending)

    def _sync(self):
        hist = self.host.hist
        if self.epoch != hist.epoch:
            self.epoch = hist.epoch
            self.pending.clear()
            self.replay_after = None
        for tid in tuple(self.pending):
            if hist.get(tid) is None:
                self.pending.pop(tid, None)

    def enqueue(self, turns=(), *, meta=False):
        self._sync()
        self.meta |= meta
        for turn in turns:
            tid = turn.get("id")
            if type(tid) is int and self.host.hist.get(tid) is not None:
                # Unvisited replay ids are fetched from current History, not stored twice.
                if self.replay_after is None or tid <= self.replay_after:
                    if tid not in self.pending and len(self.pending) >= MAX_IDS:
                        self.pending.clear()
                        self.replay_after = 0
                        self.meta = True
                        continue
                    self.pending[tid] = None
        if len(self.pending) > MAX_IDS or self.descriptor_bytes() > MAX_BYTES:
            self.pending.clear()
            self.replay_after = 0
            self.meta = True
        self._schedule()

    def _schedule(self):
        if not self.scheduled and (self.pending or self.meta or self.replay_after is not None):
            self.scheduled = True
            self.host._post(self.drain)

    async def drain(self):
        try:
            self._sync()
            ep = self.epoch
            count = BATCH
            if self.meta:
                self.meta = False
                count -= 1
                await self.host._send_p33(lambda s: {"t": "hist_meta", **self.host.hist_metadata()}
                                          if self.host.hist.epoch == ep else None)
            for _ in range(count):
                if self.host.hist.epoch != ep:
                    self._sync()
                    break
                if self.pending:
                    tid = next(iter(self.pending))
                    self.pending.pop(tid)  # a concurrent update while awaiting re-adds this id
                elif self.replay_after is not None:
                    tid = next((i for i in self.host.hist.turns if i > self.replay_after), None)
                    if tid is None:
                        self.replay_after = None
                        break
                    self.replay_after = tid
                else:
                    break
                def current(s):
                    if self.host.hist.epoch != ep:
                        return None
                    turn = self.host.hist.get(tid)
                    return {"t": "hist_turn", "epoch": ep, "turn": turn} if turn is not None else None
                await self.host._send_p33(current)
        finally:
            self.scheduled = False
            self._schedule()  # yield between batches; existing control messages retain FIFO order


def for_host(host):
    delivery = host.__dict__.get("_history_delivery")
    if delivery is None:
        delivery = host._history_delivery = Delivery(host)
    return delivery
