"""P467: bounded phone commands for OpenCode and ACP, independent of a stalled turn.

Only an explicit read-only allowlist bypasses the turn queue. Mutations keep the
queue/turn lock and expire before starting; an expired command never runs later.
"""
from __future__ import annotations

import asyncio
from .slash import Result

QUERY_WAIT = 5.0
QUEUE_WAIT = 10.0
COMMAND_WAIT = 60.0
KINDS = ("opencode", "kimi", "qwen", "dsh")


def applies(agent) -> bool:
    return agent.kind in KINDS


def readonly(name: str, arg: str) -> bool:
    return name in ("status", "context", "cost", "help") or (name == "model" and not arg)


def unavailable(agent) -> Result:
    en = getattr(agent.host, "lang", "zh").startswith("en")
    return Result("Command timed out. The current turn may still be running; you can press Stop first."
                  if en else "命令等待超时，当前一轮可能还没结束，可先 Stop，再重试。", "info")


async def execute(agent, name: str, arg: str) -> Result:
    try:
        return await asyncio.wait_for(agent.command(name, arg), 600 if name == "compact" else COMMAND_WAIT)
    except asyncio.TimeoutError:
        return unavailable(agent)


async def query(agent, name: str, arg: str) -> Result:
    # Do not create/reopen a process or session in parallel with startup/another turn.
    if agent.status in ("working", "compacting") and getattr(agent, "proc", None) is None:
        return unavailable(agent)
    try:
        if getattr(agent, "proc", None) is None:
            # A cold query may initialize the harness; serialize only that startup.
            import contextlib
            async def cold_query():
                async with getattr(agent.host, "turn_lock", None) or contextlib.nullcontext():
                    return await agent.command(name, arg)
            return await asyncio.wait_for(cold_query(), QUERY_WAIT)
        return await asyncio.wait_for(agent.command(name, arg), QUERY_WAIT)
    except asyncio.TimeoutError:
        return unavailable(agent)
    except Exception as e:  # a query bug costs one card, never the phone receiver
        return Result(f"命令出错（{type(e).__name__}）。", "error")


def expire_queued(agent, cmd) -> asyncio.TimerHandle:
    def expire():
        if not cmd.started and not cmd.cancelled:
            agent.host.cmd_done(cmd, unavailable(agent))
            cmd.cancelled = True
    return asyncio.get_running_loop().call_later(QUEUE_WAIT, expire)
