"""Bridge browser synthetic transport to real Host._app/mail/Elevator admission."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'host/tests'))
import _hermetic  # noqa: E402
import asyncio
import json
from test_p352_keys import Base
from agentj import mail

fixture = Base(); fixture.setUp()
h = fixture.host
h.push_notify = lambda *_: None
h.mail = mail.Mail(h)
h.mail.set_config(mail.apply_preset(mail.blank_config(), 'gmail', 'demo@example.com'))
async def send(s, obj):
    print(json.dumps(obj), flush=True)
    return True
h.send_app = send
async def main():
    while line := await asyncio.to_thread(sys.stdin.readline):
        req = json.loads(line)
        await h._app(fixture.s, req)
    h.stopping.set()
    for task in list(h.__dict__.get("_bg_tasks", [])): task.cancel()
    await asyncio.sleep(.05)
try:
    asyncio.run(main())
finally:
    fixture.tearDown()
