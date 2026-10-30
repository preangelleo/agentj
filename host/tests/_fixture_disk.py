"""Fixture-owned disk worker barrier. Register immediately after the directory cleanup, before producer cleanups."""
from agentj import diskq


async def drain_async_disk(case):
    case.assertTrue(await diskq.aflush(),
                    "disk queue must settle before the fixture state directory is removed")
