"""P247: one owner's read watermark, private to this host and history epoch.

P321: a host that never kept a mark (the first P247+ start, or a fresh install) seeds it at the newest visible reply
already in the history — what was there before is read, as P207's first run on a remote — instead of 0, which painted
「99+」 on every remote until one of them caught up."""
import json


class ReadMark:
    def __init__(self, st, hist=None):
        self.st = st
        self.path = st.root / "history-read.json"
        self.epoch, self.mark = -1, 0
        try:
            v = json.loads(self.path.read_text())
            if type(v.get("epoch")) is int and type(v.get("read")) is int and v["read"] >= 0:
                self.epoch, self.mark = v["epoch"], v["read"]
        except (OSError, ValueError, AttributeError):
            pass
        if self.epoch < 0 and hist is not None:
            self.seed(hist)

    @staticmethod
    def visible(turn) -> bool:
        text = (turn or {}).get("reply", {}).get("text", "")
        return isinstance(text, str) and bool(text.strip()) and text.strip() != "〔不回群〕"

    def seed(self, hist) -> None:
        """P321: everything already in the history counts as read; a failed write keeps it for this run only."""
        tid = next((i for i, t in reversed(list(hist.turns.items())) if self.visible(t)), 0)
        self.epoch, self.mark = hist.epoch, tid
        try:
            self.st.write_private(self.path, json.dumps({"epoch": hist.epoch, "read": tid}).encode())
        except OSError:
            pass

    def value(self, hist):
        return self.mark if self.epoch == hist.epoch else 0

    def see(self, hist, epoch, tid):
        if type(epoch) is not int or epoch != hist.epoch or type(tid) is not int:
            return False
        if not self.visible(hist.get(tid)):
            return False
        old = self.value(hist)
        if tid <= old:
            return False
        # Persist before publishing: a failed disk write cannot announce a durable receipt.
        self.st.write_private(self.path, json.dumps({"epoch": epoch, "read": tid}).encode())
        self.epoch, self.mark = epoch, tid
        return True
