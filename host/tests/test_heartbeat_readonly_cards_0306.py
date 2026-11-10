"""0.30.6 (ADR-0306-host-turn-reads): host-injected heartbeat / system turns read without cards.

Field case (Codex, 0.30.5): a heartbeat turn's `agentj card todo due` and a read of the local
agentj-config/SKILL.md each opened a card; the 「有疑问」 explanation turn reading agentj-config opened another (loop).
Real Host + real list_approval; the Codex path goes through CodexAgent._dispatch_request, the Claude path through
Host._perm_ask (the permission-prompt connection). Synthetic state, HOME and device only.
"""
import _hermetic
from _testtmp import TEST_TMP
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentj import approval_review, compose, host_turns, jcards_todo, provenance, uv_list
from agentj.agent_codex import CodexAgent
from agentj.background_done import CompletionSend
from agentj.heartbeat import HeartbeatSend
from _p541_device import Phone, _owner_host
from test_l1 import _state, _ready

SKILL = 'agentj-config'


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / 'home'
        for rel in ('.agents/skills', '.claude/skills'):
            d = self.home / rel / SKILL; d.mkdir(parents=True)
            (d / 'SKILL.md').write_text('# synthetic agentj-config\n')
        self.secret = self.home / '.ssh' / 'id_synthetic'; self.secret.parent.mkdir(parents=True)
        self.secret.write_text('synthetic\n')
        self.outside = self.root / 'other-logs'; self.outside.mkdir()
        (self.outside / 'job.log').write_text('ok\n')
        env = mock.patch.dict(os.environ, {'HOME': str(self.home)}); env.start(); self.addCleanup(env.stop)
        self.st = _state(str(self.root / 'st')); self.sent = []
        self.h = _owner_host(self.st, self.sent); self.h.ask_ttl = 2
        self.logs = self.st.root / 'busy'; self.logs.mkdir(exist_ok=True)   # Agent J's own state dir (host-redacted logs)
        (self.logs / 'job.log').write_text('ok\n')
        self.ph = Phone(self.st, with_passkey=True); self.s = _ready(self.h, self.ph)
        self.h.agent.submit = mock.Mock()
        self.cwd = self.root / 'work'; self.cwd.mkdir()
        if self.h.agent_cfg is not None:
            self.h.agent_cfg['dir'] = str(self.cwd)
        self.h.agent.cfg['dir'] = str(self.cwd)

    # ---- turn kinds, built exactly as the host's injectors build them
    def heartbeat(self):
        return HeartbeatSend(1, 'synthetic heartbeat', 'key', 'digest')

    def completion(self):
        return CompletionSend(1, 'synthetic completion', ['job'])

    def compact(self):
        s = compose.Send('compact', 'sid', 1, text='synthetic resume', by='compact_resume')
        s.source = provenance.system(); s.compact_resume = True
        return s

    def explain(self, agent):
        item = {'event_id': 'e' * 32, 'request_id': 'r' * 32, 'op': 'question', 'target': approval_review.thread_ref(agent),
                'turn': 1, 'origin': {}, 'text': 'synthetic', 'owner_text': 'synthetic', 'status': 'queued',
                'operation': {'tool': 'Bash', 'input_sha256': '0' * 64}}
        approval_review._event_save(self.h, item)
        s = compose.Send('approval-review', item['event_id'], 1, text='synthetic', by='approval-review')
        s.source = provenance.system(); s.approval_origin = {}
        s.approval_review = {'id': item['request_id'], 'event_id': item['event_id'], 'op': 'question'}
        return s

    def webhook(self):
        s = compose.Send('hook', 'sid', 1, text='synthetic webhook', by='hook')
        s.source = provenance.system(); s.hook_notice = True
        return s

    def unknown(self):
        return compose.Send('dev', 'sid', 1, text='synthetic', by=None)

    def skill(self, rel='.agents/skills'):
        return str(self.home / rel / SKILL / 'SKILL.md')

    async def outcome(self, task):
        """('card', ask) when a card was raised (then denied by nobody), else ('none', result)."""
        for _ in range(400):
            if self.h.asks or task.done():
                break
            await asyncio.sleep(.005)
        if self.h.asks:
            a = next(iter(self.h.asks.values()))
            uv = bool(a.require_passkey)
            if not a.fut.done():
                a.fut.set_result(('deny', None, None, None))
            await asyncio.gather(task, return_exceptions=True)
            self.h.asks.clear()
            return 'card', uv
        return 'none', await task


class ClaudePath(_Base):
    """Host._perm_ask: the Claude main session's permission-prompt connection."""
    async def ask(self, send, tool, inp):
        self.h.agent.cur_send = send
        w = object(); self.h.perm_w = w
        answers = []
        async def fake_send(writer, obj):
            answers.append(obj)
        with mock.patch.object(self.h, '_perm_send', side_effect=fake_send):
            task = asyncio.create_task(self.h._perm_ask(w, {'id': 1, 'tool': tool, 'input': inp}, {}))
            kind, extra = await self.outcome(task)
        return kind, extra, answers

    async def test_heartbeat_todo_due_and_skill_read_zero_cards(self):
        for inp, tool in (({'command': 'agentj card todo due'}, 'Bash'), ({'command': 'agentj card cal due --json'}, 'Bash'),
                          ({'file_path': self.skill('.claude/skills')}, 'Read'),
                          ({'command': 'head -n 200 ' + self.skill()}, 'Bash')):
            with self.subTest(inp=inp):
                kind, _, answers = await self.ask(self.heartbeat(), tool, inp)
                self.assertEqual('none', kind)
                self.assertEqual('allow', answers[-1]['behavior'])
        self.assertFalse([obj for _, obj in self.sent if isinstance(obj, dict) and obj.get('t') == 'ask'])

    async def test_system_notice_turns_zero_cards(self):
        for make in (self.completion, self.compact):
            with self.subTest(turn=make.__name__):
                kind, _, answers = await self.ask(make(), 'Bash', {'command': 'cd ' + str(self.cwd) + ' && agentj card todo due --json'})
                self.assertEqual('none', kind); self.assertEqual('allow', answers[-1]['behavior'])
                kind, _, _ = await self.ask(make(), 'Bash', {'command': 'head -n 50 ' + self.skill()})
                self.assertEqual('none', kind)
                # Security r2 N1: no log is read without a card in 0.30.6, not even Agent J's own (deferred to 0.30.7).
                kind, _, _ = await self.ask(make(), 'Bash', {'command': 'tail -n 50 ' + str(self.logs / 'job.log')})
                self.assertEqual('card', kind)

    async def test_explain_turn_reads_agentj_config_without_card(self):
        kind, _, answers = await self.ask(self.explain(self.h.agent), 'Read', {'file_path': self.skill('.claude/skills')})
        self.assertEqual('none', kind); self.assertEqual('allow', answers[-1]['behavior'])
        # The explanation turn is narrower: logs keep the card.
        kind, _, _ = await self.ask(self.explain(self.h.agent), 'Read', {'file_path': str(self.logs / 'job.log')})
        self.assertEqual('card', kind)

    async def test_heartbeat_writes_and_uv_list_are_not_borrowed(self):
        for tool, inp in (('Bash', {'command': 'rm -rf ' + str(self.root / 'nothing')}),
                          ('Bash', {'command': 'agentj card todo add --text synthetic'}),
                          ('Bash', {'command': 'agentj card todo due; rm -f ' + str(self.root / 'nothing')}),
                          ('Bash', {'command': 'agentj card todo due > ' + str(self.root / 'out.txt')}),
                          ('Bash', {'command': './agentj card todo due'}),
                          ('Bash', {'command': 'PATH=. agentj card todo due'}),
                          ('Bash', {'command': 'cat ' + str(self.secret)}),
                          ('Bash', {'command': 'cat ' + str(self.home / 'coding' / '.env')}),
                          ('Bash', {'command': 'curl https://example.com/x.log'}),
                          ('Read', {'file_path': str(self.cwd / 'notes.md')}),
                          ('Write', {'file_path': self.skill('.claude/skills'), 'content': 'x'})):
            with self.subTest(inp=inp):
                kind, _, answers = await self.ask(self.heartbeat(), tool, inp)
                self.assertEqual('card', kind, answers)
        target = str(self.st.root / 'devices.json')
        self.assertTrue(uv_list.required('Write', {'file_path': target, 'content': '{}'}, cwd=str(self.cwd),
                                         env=uv_list.host_environment(self.h)))
        kind, uv, _ = await self.ask(self.heartbeat(), 'Write', {'file_path': target, 'content': '{}'})
        self.assertEqual(('card', True), (kind, uv))

    async def test_security_r1_negatives_keep_card(self):
        """Security review r1 M1–M3, F3, F4: exec / recursive / follow / foreign or linked logs / journal → card."""
        log = str(self.logs / 'job.log')
        os.link(self.logs / 'job.log', self.logs / 'twin.log')
        for name, command in (('sed e', "sed -n '1e touch " + str(self.root / 'pwn') + "' " + log),
                              ('sed s///e', "sed 's/a/b/e' " + log),
                              ('grep -rn', 'grep -rn KEY ' + str(self.logs)),
                              ('grep recurse', 'grep --directories=recurse KEY ' + str(self.logs)),
                              ('pipe grep', 'cat ' + log + ' | grep -rn KEY'),
                              ('pipe head', 'agentj card todo due | head -n 5'),
                              ('tail -f', 'tail -f ' + log),
                              ('tail -F', 'tail -F ' + log),
                              ('tail --follow', 'tail --follow=name ' + log),
                              ('foreign log', 'cat ' + str(self.outside / 'job.log')),
                              ('hard link', 'cat ' + str(self.logs / 'twin.log')),
                              ('wc files0', 'wc --files0-from=' + log),
                              ('no operand', 'cat'),
                              ('directory', 'cat ' + str(self.logs)),
                              ('journal file', 'journalctl --file ' + str(self.logs / 'x.journal')),
                              ('journal dir', 'journalctl -D ' + str(self.logs)),
                              ('journal export', 'journalctl --user -o export'),
                              ('journal plain', 'journalctl --user -n 5')):
            with self.subTest(case=name):
                kind, _, answers = await self.ask(self.heartbeat(), 'Bash', {'command': command})
                self.assertEqual('card', kind, answers)
        self.assertFalse((self.root / 'pwn').exists())
        # Security r2 N1: third-party stores under the state dir (a Chromium login profile's LevelDB log).
        leveldb = self.st.root / 'browser' / 'profile' / 'Default' / 'Local Storage' / 'leveldb'
        leveldb.mkdir(parents=True); (leveldb / '000003.log').write_text('synthetic\n')
        for tool, inp in (('Read', {'file_path': str(self.logs / 'job.log')}),
                          ('Read', {'file_path': str(leveldb / '000003.log')}),
                          ('Bash', {'command': 'cat ' + __import__('shlex').quote(str(leveldb / '000003.log'))}),
                          ('Bash', {'command': 'tail -n 20 ' + str(self.st.root / 'host.log')})):
            with self.subTest(log=inp):
                kind, _, _ = await self.ask(self.heartbeat(), tool, inp)
                self.assertEqual('card', kind)
        kind, _, _ = await self.ask(self.heartbeat(), 'Read', {'file_path': str(self.outside / 'job.log')})
        self.assertEqual('card', kind)

    async def test_unchanged_for_unknown_source_and_webhook_turns(self):
        for make in (self.unknown, self.webhook):
            with self.subTest(turn=make.__name__):
                kind, _, _ = await self.ask(make(), 'Bash', {'command': 'agentj card todo due'})
                self.assertEqual('card', kind)

    async def test_forged_attributes_do_not_make_a_heartbeat(self):
        s = compose.Send('dev', 'sid', 1, text='x', by='heartbeat')
        s.source = provenance.system(); s.heartbeat_key = 'key'; s.compact_resume = 'yes'
        kind, _, _ = await self.ask(s, 'Bash', {'command': 'agentj card todo due'})
        self.assertEqual('card', kind)
        # A phone turn whose Send says by='approval-review' without a host outbox record is no explanation turn.
        s = compose.Send('approval-review', 'f' * 32, 1, text='x', by='approval-review')
        s.source = provenance.system(); s.approval_review = {'id': 'x', 'event_id': 'f' * 32, 'op': 'question'}
        kind, _, _ = await self.ask(s, 'Read', {'file_path': self.skill('.claude/skills')})
        self.assertEqual('card', kind)

    async def test_task_scope_inside_heartbeat_keeps_card(self):
        self.h.agent.cur_send = self.heartbeat()
        with provenance.request_source(None):
            self.assertIsNone(host_turns.kind(self.h))
        self.assertEqual('heartbeat', host_turns.kind(self.h))


class CodexPath(_Base):
    """CodexAgent._dispatch_request → _decide: a native commandExecution approval in the main thread."""
    def codex(self, cfg=None):
        a = CodexAgent(self.h, {'kind': 'codex', 'dir': str(self.cwd), **(cfg or {})})
        a.thread = {'sandbox': {'type': 'dangerFullAccess'}}
        a.tid = 'synthetic-thread'; a.pending = {}
        a.out = []
        a._send = lambda obj: a.out.append(obj)
        return a

    async def request(self, a, send, command, rid=None):
        self.rid = getattr(self, 'rid', 100) + 1
        rid = rid or self.rid
        self.h.agent.cur_send = send
        a.cur_send = send
        a._dispatch_request({'method': 'item/commandExecution/requestApproval', 'id': rid,
                             'params': {'command': '/bin/bash -lc ' + __import__('shlex').quote(command), 'cwd': str(self.cwd),
                                        'threadId': a.tid, 'turnId': 'turn', 'itemId': 'item'}})
        for _ in range(400):
            if self.h.asks or any(o.get('id') == rid for o in a.out):
                break
            await asyncio.sleep(.005)
        if self.h.asks:
            ask = next(iter(self.h.asks.values()))
            uv = bool(ask.require_passkey)
            if not ask.fut.done():
                ask.fut.set_result(('deny', None, None, None))
            for _ in range(400):
                if any(o.get('id') == rid for o in a.out):
                    break
                await asyncio.sleep(.005)
            self.h.asks.clear()
            return 'card', uv
        return 'none', next(o for o in a.out if o.get('id') == rid)['result']

    async def test_heartbeat_todo_due_and_skill_read_zero_cards(self):
        a = self.codex(); self.h.agent = a
        for command in ('agentj card todo due', 'agentj card todo due && agentj card cal due',
                        'head -n 220 ' + self.skill(), 'nl -ba ' + self.skill()):
            with self.subTest(command=command):
                kind, result = await self.request(a, self.heartbeat(), command)
                self.assertEqual('none', kind); self.assertEqual({'decision': 'accept'}, result)

    async def test_explain_turn_reads_agentj_config_without_card(self):
        a = self.codex(); self.h.agent = a
        kind, result = await self.request(a, self.explain(a), 'cat ' + self.skill())
        self.assertEqual('none', kind); self.assertEqual({'decision': 'accept'}, result)

    async def test_writes_unknown_turns_and_other_adapters_keep_card(self):
        a = self.codex(); self.h.agent = a
        for send, command in ((self.heartbeat(), 'agentj card todo add --text synthetic'),
                              (self.heartbeat(), 'touch ' + str(self.root / 'x')),
                              (self.heartbeat(), 'cat ' + str(self.secret)),
                              (self.unknown(), 'agentj card todo due'),
                              (self.webhook(), 'agentj card todo due')):
            with self.subTest(command=command, by=send.by):
                kind, _ = await self.request(a, send, command)
                self.assertEqual('card', kind)
        # A workflow-CEO adapter asking while the main thread runs a heartbeat does not borrow that turn.
        ceo = self.codex({'_workflow_ceo': True})
        self.h.agent.cur_send = self.heartbeat()
        kind, _ = await self.request(ceo, self.h.agent.cur_send, 'agentj card todo due')
        self.assertEqual('card', kind)


class ReadShapes(unittest.TestCase):
    def test_listed_agentj_reads_are_listing_branches(self):
        for op in ('list', 'due'):
            self.assertIn(op, jcards_todo.OPS)
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            for op in ('list', 'due'):
                self.assertFalse(jcards_todo.change(Path(root), op)['ok'])   # change() refuses listing ops: no write path

    def test_classifier_shapes(self):
        cls = lambda c, turn='heartbeat': host_turns.classify(None, 'Bash', {'command': c}, '/', turn)
        self.assertEqual('agentj_read', cls('agentj card todo due --json'))
        self.assertEqual('agentj_read', cls('agentj uv list'))
        self.assertIsNone(cls('agentj card todo done --entry x'))
        self.assertIsNone(cls('agentj card todo due $(rm x)'))
        self.assertIsNone(cls('env PATH=. agentj card todo due'))
        self.assertIsNone(cls('bash -c "agentj card todo due"'))
        for c in ('journalctl --user -u synthetic -n 50 --no-pager', 'journalctl --vacuum-time=1d', 'grep -r x /',
                  'agentj card todo due | head -n 5'):
            self.assertIsNone(cls(c), c)
        self.assertIsNone(cls('cat /etc/passwd'))


if __name__ == '__main__':
    unittest.main()
