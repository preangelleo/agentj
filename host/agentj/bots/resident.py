"""Shared resident response kernel; adapters own sessions, budgets and channels.

Only an explicitly approved complete candidate is returned. Official F18 retains
its existing queue/snapshot/account; customer adapters retain private host storage.
No implicit HOME, credentials, network, logging or owner capability lives here.
"""
import inspect

async def _value(call,*args):
    value=call(*args)
    return await value if inspect.isawaitable(value) else value

async def guarded_reply(model,outbound,*,attempts=1,held=None):
    """Bound generation/rewrite attempts; unknown gates propagate without release."""
    if type(attempts) is not int or not 1<=attempts<=4:raise ValueError('invalid_attempts')
    reasons=[]
    for index in range(attempts):
        candidate=await _value(model,reasons)
        reasons=await _value(outbound,candidate)
        if not isinstance(reasons,list) or any(not isinstance(x,str) for x in reasons):raise ValueError('invalid_gate')
        if not reasons:return candidate
        if held is not None:await _value(held,index,reasons)
    return None
