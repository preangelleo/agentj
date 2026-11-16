"""Isolated Host/UDS with a real paired passkey phone for `agentj relay claude|codex --full` (0.30.29).

argv: <root> <mode>. Files <root>/trigger_estop | trigger_stop fire do_estop | stopping + stop_all once.
Modes: allow · deny · timeout · no_uv · tamper · reuse · nokey · nobin · hold (allow once <root>/release_card exists)
· race (allow; R-0006 r4 N1: <root>/plant_env = "<folder>\n<line>" writes <folder>/.env only while Host.ask runs —
the card's own index sees it — and removes it once the card is answered, so every relay-side check before and after passes).
<root>/agent_dir (optional) names the main Agent's folder (default <root>/work); <root>/move_after_answer moves it to
the path inside once Host.ask has returned (a move while the card is open is already refused by the core: authority_changed). Every SecretIndex.build / display_normalizer call is logged to <root>/index_builds.jsonl
with its phase: `card` inside Host.ask, `relay` inside relay_full.display_refusal (R-0006 r3 N1 spy; paths only).
The phone answers each card per mode; `reuse` replays the first signed allow for every later ask (a stolen / stale
approval). Fake harness binaries live in <root>/bin (PATH first); they never contact a network.
"""
import _hermetic  # noqa
import asyncio
import contextvars
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from agentj.state import State
from test_l1 import Phone, _owner_host, _ready
from agentj import diskq, keys


async def main():
    root, mode = Path(sys.argv[1]), sys.argv[2]
    st = State(root / 'state')
    st.init(relay='ws://127.0.0.1:1')
    agent_dir = (root / 'agent_dir').read_text().strip() if (root / 'agent_dir').exists() else str(root / 'work')
    st.set_agent_config('codex', agent_dir, record_root=False)
    if mode != 'nokey':
        fixture = json.loads((root / 'fixture_keys.json').read_text())
        path = Path(keys.env_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(f'{k}={v}\n' for k, v in fixture.items()))
        path.chmod(0o600)
    sent = []
    host = _owner_host(st, sent)
    host.ask_ttl = 15 if mode == 'hold' else 3
    host.agent.proc = SimpleNamespace(pid=os.getpid(), returncode=None)
    phone = Phone(st, with_passkey=True)
    session = _ready(host, phone)
    seen = set()
    from agentj import action_policy, peer_guard, relay_full
    phase = contextvars.ContextVar('phase', default=None)   # copied into to_thread / tasks started inside the phase

    def log_build(**row):
        if phase.get():
            with open(root / 'index_builds.jsonl', 'a') as f:
                f.write(json.dumps({'phase': phase.get(), **row}) + '\n')
    real_build = peer_guard.SecretIndex.build.__func__

    def build(cls, *a, **k):
        log_build(fn='SecretIndex.build', args=[str(x) for x in a], workdir=k.get('workdir'),
                  fragment_size=k.get('fragment_size'))
        return real_build(cls, *a, **k)
    peer_guard.SecretIndex.build = classmethod(build)
    real_norm = action_policy.display_normalizer

    def norm(**k):
        log_build(fn='display_normalizer', workdir=k.get('workdir'), lang=k.get('lang'),
                  index=k.get('index') is not None)
        return real_norm(**k)
    action_policy.display_normalizer = norm
    real_refusal = relay_full.display_refusal

    def refusal(*a, **k):
        phase.set('relay')                   # runs in its own to_thread context copy
        return real_refusal(*a, **k)
    relay_full.display_refusal = refusal
    spy_ask = host.ask

    async def asking(*a, **k):
        phase.set('card')
        plant = root / 'plant_env'
        planted = None
        if mode == 'race' and plant.exists():
            folder, line = plant.read_text().split('\n', 1)
            planted = Path(folder, '.env')
            planted.write_text(line)
        try:
            answer = await spy_ask(*a, **k)
        finally:
            if planted is not None:
                planted.unlink()
                (root / 'plant_removed').write_text('1')
        moved = root / 'move_after_answer'
        if moved.exists():                    # the agent folder moves after the owner allowed, before relay_full spawns
            host.agent_cfg['dir'] = moved.read_text().strip()
        return answer
    host.ask = asking
    if mode == 'reuse':
        real_ask, first = host.ask, {}

        async def replaying(tool, inp, *a, **k):
            if 'answer' in first:
                return first['answer']
            first['answer'] = await real_ask(tool, inp, *a, **k)
            return first['answer']
        host.ask = replaying

    fired = set()

    async def triggers():                    # R-0006 r1 H1 / L4: the test asks for stop-everything or serve stopping
        from agentj import relay_full
        while True:
            await asyncio.sleep(.05)
            try:
                if 'estop' not in fired and (root / 'trigger_estop').exists():
                    fired.add('estop')

                    async def no_turn(**_):        # the fixture's agent.proc is this process: nothing to halt
                        return False
                    host.agent.halt = no_turn
                    await host.do_estop('cli', 'test')
                if 'stop' not in fired and (root / 'trigger_stop').exists():
                    fired.add('stop')
                    host.stopping.set()      # what Host._run's shutdown does for relay jobs (source-checked in Units)
                    await relay_full.stop_all(host, 'serve_stop')
            except Exception:  # noqa: BLE001 — the test reads it
                import traceback
                (root / 'trigger_error').write_text(traceback.format_exc())

    held = set()

    async def answers():
        while True:
            await asyncio.sleep(.01)
            for _, card in sent:
                if card.get('t') != 'ask' or card['id'] in seen:
                    continue
                if mode == 'hold' and not (root / 'release_card').exists():
                    with open(root / 'cards.jsonl', 'a') as f:
                        if card['id'] not in held:
                            held.add(card['id'])
                            f.write(json.dumps({'tool': card.get('tool'), 'summary': card.get('summary'),
                                                'passkey_required': card.get('passkey_required'), 'held': True}) + '\n')
                    continue
                seen.add(card['id'])
                if card['id'] not in held:           # a held card was already recorded once
                    with open(root / 'cards.jsonl', 'a') as f:
                        f.write(json.dumps({'tool': card.get('tool'), 'summary': card.get('summary'),
                                            'passkey_required': card.get('passkey_required')}) + '\n')
                if mode == 'timeout':
                    continue
                answer = phone.answer(card, mode != 'deny')
                if mode == 'no_uv':
                    answer.pop('fa', None)
                if mode == 'tamper':
                    answer = phone.answer(card, True, summary=card['summary'] + ' changed')
                await host._app(session, answer)
    server = await asyncio.start_unix_server(host.on_ctl, path=str(st.sock_path))
    watcher = asyncio.create_task(answers())
    trig = asyncio.create_task(triggers())
    print('READY', flush=True)
    try:
        await server.serve_forever()
    finally:
        watcher.cancel()
        trig.cancel()
        await diskq.aflush()


if __name__ == '__main__':
    asyncio.run(main())
