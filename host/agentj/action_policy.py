"""P503d mail authorization utilities. Global executor policy remains follow-up work."""
import json
import hashlib


def summary(tool: str, inp: dict) -> str:
    from .privacy import redact
    text = redact(tool + "\n" + json.dumps(inp, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    if tool == "agentj calendar":
        # Visible fields may be redacted to the same text. Bind the signed card to
        # the exact destination and payload that the executor will recheck.
        raw = json.dumps(inp, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        text += "\nRequest SHA-256: " + hashlib.sha256(raw.encode()).hexdigest()
    return text


def confirm_cli(action: str, params: dict, *, st=None) -> bool:
    from .state import State
    from . import elevate, names
    st = st if st is not None else State()
    res = elevate.client_request(st, {"t":"policy_cli", "action":action, "params":params}, timeout=620,
                                 path=names.elevate_sock(st))
    return res.get("result") == "approved"
