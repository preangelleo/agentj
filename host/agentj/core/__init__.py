"""Agent J Harness (internal code name agentj-core; P359 = first slice of AGENTJ-CORE-SPEC §7 M0 + the §12 orchestration path).

Our own coding-agent kernel, ported module by module from Pi's design (UPSTREAM.md) in Python, so the approval card, the
danger list, the fence and orchestration live inside the kernel instead of behind another harness's protocol:

    llm        OpenAI-compatible Chat (Volcano Ark / BytePlus ModelArk / AgentsRelay …), retries, fallback chain
    tools      read · write · edit · bash · grep · find · ls — executed by a toolbox child process inside the fence
    pipeline   the pre-execute gate, fail closed: stop → research → scope → refused-before → danger → card
    loop       model ↔ tools; step-boundary inbox (append) and control (stop); budgets; parallel `task` sub-agents
    orders     agentj.order.v1 order directories: brief, standing orders, report head, verdict, retry table
    dispatcher CEO processes (systemd-run --user unit agentj-o<id>, else a detached process), stop / resume / reconcile
    ceo        the CEO runner (`python -m agentj.core.ceo <id>`); askbridge: its approval line to serve's phone card
    adapter    CoreAgent — the main Agent inside serve (kind "core"), with the `dispatch` tool

Off by default: visible to detect / switch only with AGENTJ_CORE=1 in the service environment (P359; M3 decides defaults).
The loop and the model key stay in the host process; only the toolbox runs inside the fence, with an environment that
never contains a key. Standard library only.
"""
from __future__ import annotations

import os

LABEL = "Agent J Harness"
ENV = "AGENTJ_CORE"


def enabled(environ=None) -> bool:
    """P359: the harness is offered (detect / switch / `agentj order`) only when the owner turned it on."""
    return (os.environ if environ is None else environ).get(ENV, "") == "1"
