"""P247: one owner's read watermark, private to this host and history epoch."""
import json


class ReadMark:
    def __init__(self, st):
        self.st = st
        self.path = st.root / "history-read.json"
        self.epoch, self.mark = -1, 0
        try:
            v = json.loads(self.path.read_text())
            if type(v.get("epoch")) is int and type(v.get("read")) is int and v["read"] >= 0:
                self.epoch, self.mark = v["epoch"], v["read"]
        except (OSError, ValueError, AttributeError):
            pass

    def value(self, hist):
        return self.mark if self.epoch == hist.epoch else 0

    def see(self, hist, epoch, tid):
        if type(epoch) is not int or epoch != hist.epoch or type(tid) is not int:
            return False
        turn = hist.get(tid)
        text = (turn or {}).get("reply", {}).get("text", "")
        if not isinstance(text, str) or not text.strip() or text.strip() == "〔不回群〕":
            return False
        old = self.value(hist)
        if tid <= old:
            return False
        # Persist before publishing: a failed disk write cannot announce a durable receipt.
        self.st.write_private(self.path, json.dumps({"epoch": epoch, "read": tid}).encode())
        self.epoch, self.mark = epoch, tid
        return True
