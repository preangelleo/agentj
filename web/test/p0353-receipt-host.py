"""Generate actual /update history cards from a synthetic resolver failure."""
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    if "--legacy" in sys.argv:
        wheel = (Path(__file__).resolve().parents[2] / "site" / "public" / "dl"
                 / "agentj-0.30.52-py3-none-any.whl")
        expected = "c15cfbacf8af3d8756ab2e4c0b589a0fb29409f9e36c9736ace2e05a816b5c3e"
        assert hashlib.sha256(wheel.read_bytes()).hexdigest() == expected
        with zipfile.ZipFile(wheel) as package:
            package.extractall(root / "legacy")
        sys.path.insert(0, str(root / "legacy"))
    os.environ.update(
        HOME=tmp, AGENTJ_STATE_DIR=str(root / "state"), XDG_CONFIG_HOME=str(root / "config"),
        AGENTJ_TEST="1",
    )
    from agentj import phone_update, serve, slash, state

    st = state.State()
    st.init(relay="ws://127.0.0.1:1")
    host = serve.Host(st, events="jsonl", read_stdin=False)
    host._post = lambda *args: None
    host.emit = lambda *args, **kwargs: None
    rec = {
        "from": "0.30.50", "to": "0.30.52", "result": "failed", "step": "install",
        "reason": "uv_failed", "dependency_source": "pypi", "dependency_phase": "resolve",
        "uv_failure_class": "other", "retry_count": 0, "tell_owner": True,
        "support": "sent (st_" + "x" * 22 + ") metadata only",
    }
    cards = {}
    for lang in ("zh", "en"):
        host.lang = lang
        host.cmd_card("update", slash.Result(phone_update.text(rec, lang), "error"))
        cards[lang] = host.hist.get(host.hist.meta()["last"])
    print(json.dumps(cards, ensure_ascii=False))
