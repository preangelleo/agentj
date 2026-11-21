"""Dispatch one exact package request from a receiver-claimed same-owner task."""
import re

from . import application_ingress, plaza_host
from .owner_group import ALL_WORDS
from .same_owner import ForwardSend

_COMMAND = re.compile(r"agentj plaza request ([a-z0-9][a-z0-9-]{1,39})", re.ASCII)


def requested(send):
    if type(send) is not ForwardSend or send.approved or send.data.get("irt") or send.data.get("end"):
        return None
    if getattr(send, "action_proof", None) or any(send.data.get(k) for k in ("otg", "task_plan", "task_proof")):
        return None  # Signed plans and grants use their existing task executor.
    relay = send.owner.svc.guards.envelope(send.data.get("relay"))
    if not relay or relay["origin"] != send.fid or relay["hops"] != 1:
        return None
    text = send.data.get("text")
    if not isinstance(text, str) or len(text) > 512:
        return None
    text = text.strip()
    group = send.data.get("grp")
    if group is not None:
        svc = send.owner.svc
        if not svc.devices._grp_ok(group) or svc.keys.id not in group["to"]:
            return None
        names = (svc.keys.id, svc.st.agent_name(), *ALL_WORDS)
        prefix = next(("@" + name + " " for name in names if name and text[:len(name) + 2].casefold() == ("@" + name + " ").casefold()), None)
        if prefix:
            text = text[len(prefix):].lstrip(" ")
        elif group["to"] == [svc.keys.id]:
            # The sender can use its own single-word nickname for this one addressed device.
            alias, space, text = text.partition(" ")
            if not space or not re.fullmatch(r"@[\w.-]{1,64}", alias):
                return None
            text = text.lstrip(" ")
        else:
            return None
    match = _COMMAND.fullmatch(text)
    return match.group(1) if match else None


class PackageRequest:
    """Host-local per-dispatch binding. Never installed as an ordinary application frame."""
    def __init__(self, host, send, name):
        self.host, self.send, self.name = host, send, name
        self.agent, self.st = host.agent, host.st
        self.channel, self.epoch = host.channel, host.hist.epoch
        self.binding = application_ingress._session_binding(host, self.agent)
        self.closed = False

    def live(self):
        host, agent = self.host, self.agent
        return (not self.closed and host.agent is agent and host.st is self.st
                and host.peers is self.send.owner.svc and host.peers.same_owner is self.send.owner
                and host.channel == self.channel and host.hist.epoch == self.epoch
                and not host.stopping.is_set() and not agent.halting
                and agent.cur_send is self.send and host.cur_turn == self.send.turn
                and application_ingress._session_binding(host, agent) == self.binding
                and application_ingress._live_same_owner_task(agent)
                and not application_ingress._sandboxed(agent)
                and requested(self.send) == self.name)

    def card_live(self, host, inp):
        return (host is self.host and inp.get('package') == self.name
                and 'packages' not in inp and self.live())


async def dispatch(host, send):
    name = requested(send)
    if name is None:
        return False
    request = PackageRequest(host, send, name)
    try:
        result = await plaza_host.handle(host, {"name": name}, _device_task=request)
    finally:
        request.closed = True
    # Package output can include private installation details. Only a fixed status returns to the group.
    ok = result.get("ok") is True
    if getattr(host, "lang", "zh").startswith("en"):
        line = f"Installed {name}." if ok else f"{name} was not installed."
    else:
        line = f"已安装 {name}。" if ok else f"没有安装 {name}。"
    host.turn_shown = True
    host.hist_update(send.turn, text=line, end="done" if ok else "failed")
    host.cur_failed = not ok
    return True
