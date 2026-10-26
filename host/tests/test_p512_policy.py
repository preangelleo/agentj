"""Shared registry matrix and real policy consumers; all fixtures stay local."""
import _hermetic  # noqa: F401
import asyncio
import dataclasses
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from agentj import approval_level, channel_policy, policy_registry as p


def legacy_canonical_floor(layer, action):
    # Frozen pre-P512 policy, independent of current table values.
    free = {'answer', 'translate', 'summarize', 'rewrite'}
    native = {'image.generate', 'image.edit', 'web.search'}
    hold = {'file.write', 'file.trash', 'settings.write', 'memory.restore'}
    uv = {'file.delete', 'mail.send', 'calendar.write', 'secrets.read', 'secrets.write',
          'payment', 'admin', 'device.pair', 'device.revoke', 'publish'}
    if layer == 'main':
        return ('allow' if action in free | native | {'file.read', 'company.read'} else
                'hold' if action in hold | {'company.write'} else 'uv' if action in uv else 'deny')
    if layer == 'channel':
        return 'allow' if action in free | native else 'deny'
    if layer == 'friend':
        return 'allow' if action in free else 'deny'
    return 'allow' if action in free | {'company.read'} else 'hold' if action == 'company.write' else 'deny'


# P539 (#300): canonical actions added after P512, each explicitly reviewed: main = UV card, other layers no wider.
P539_REVIEWED = frozenset({'computer.control'})


class Matrix(unittest.TestCase):
    def test_four_layers_representative_actions(self):
        # Columns: main, owner external channel, friend, customer service.
        rows = {
            'answer': ('allow','allow','allow','allow'),
            'web.search': ('allow','allow','handoff','deny'),
            'image.generate': ('allow','allow','handoff','deny'),
            'file.write': ('allow','handoff','handoff','deny'),
            'mail.send': ('hold','handoff','handoff','deny'),   # P551 (#318): ② reversible — long-press for the Agent
            'secrets.read': ('uv','handoff','handoff','deny'),
            'admin': ('uv','handoff','handoff','deny'),
            'payment': ('uv','handoff','handoff','deny'),
            'company.read': ('allow','deny','handoff','allow'),
            'company.write': ('uv','handoff','handoff','uv'),
            'unknown': ('deny','deny','deny','deny'),
        }
        for action, expected in rows.items():
            for layer, result in zip(p.LAYERS, expected):
                with self.subTest(layer=layer, action=action):
                    self.assertEqual(p.decision(layer,action), result)
        for value in (None, {}, [], 1, True, '', 'Answer', 'answer ', 'answer;admin'):
            for layer in p.LAYERS:
                self.assertEqual(p.decision(layer,value),'deny')
        self.assertEqual(p.decision('invented','answer'),'deny')

    def test_channel_has_no_independent_auto_list(self):
        for action, policy in p.REGISTRY.items():
            self.assertEqual(channel_policy.channel_forbidden(action),policy.confirmation!='none' or not policy.channel)
        updated={**p.REGISTRY,'new.read':p.NATIVE,'answer':p.UV}
        with patch.object(p,'REGISTRY',updated):
            self.assertFalse(channel_policy.channel_forbidden('new.read'))
            self.assertTrue(channel_policy.channel_forbidden('answer'))
            self.assertEqual(p.decision('friend','answer'),'handoff')
        self.assertFalse(hasattr(channel_policy,'AUTO'))

    def test_low_risk_guard_consults_table_before_classifier(self):
        from agentj.approval_jev import low_risk_pass
        from test_p198_bdg import _Host, HRW
        for policy in (p.HOLD, p.UV, p.UNKNOWN):
            host = _Host(HRW)
            with patch.object(p, 'REGISTRY', {**p.REGISTRY, 'file.read': policy}), \
                 patch.object(host, '_classify', side_effect=AssertionError('floor must run first')) as classify:
                self.assertFalse(low_risk_pass(host, HRW, 'Read', {'file_path': 'fixture.txt'}))
                classify.assert_not_called()
            self.assertEqual(host.st.logs, [])

    def test_export_and_immutable_defaults(self):
        export=p.export()
        self.assertEqual(json.loads(json.dumps(export)),export)
        from pathlib import Path
        doc=(Path(__file__).resolve().parents[2]/'docs/policy-registry.md').read_text()
        for name,row in export['actions'].items():
            line='| '+' | '.join([name,row['effect'],row['confirmation'],*[row['layers'][k] for k in p.LAYERS]])+' |'
            self.assertIn(line,doc)
        self.assertEqual(set(export['actions']),set(p.REGISTRY))
        for name, row in export['actions'].items():
            self.assertEqual(row['layers'],{x:p.decision(x,name) for x in p.LAYERS})
        with self.assertRaises(TypeError):p.REGISTRY['injected']=p.FREE
        with self.assertRaises(dataclasses.FrozenInstanceError):p.UV.confirmation='none'

    def test_main_existing_card_boundary_uses_same_table(self):
        for tool in approval_level.HOST_CARDS:
            self.assertEqual(approval_level.host_card(tool), not p.dispatch(tool), tool)   # #321: OpenCode `task` is ①
        for tool in ('agentj mail','agentj new-unregistered-action',None,'mail.send'):
            self.assertTrue(approval_level.host_card(tool),tool)
        self.assertFalse(approval_level.host_card('Read'))
        with patch.object(p,'REGISTRY',{**p.REGISTRY,'mail.send':p.NATIVE}):
            self.assertFalse(approval_level.host_card('agentj mail'))
        self.assertEqual(p.main_requirement('invented.action'),'deny')
        self.assertEqual(p.main_requirement('unknown'),'deny')
        self.assertEqual(p.main_requirement('agentj new-unregistered-action'),'deny')

    def test_host_card_preserves_real_arguments_and_precomputed_floor(self):
        for tool, inp, floor in (
            ('Bash', {'command': '$PY -m tests'}, 'hold'),
            ('Bash', {'command': 'bash -c "$CMD"'}, 'hold'),
            ('Bash', {'command': 'sudo ls'}, 'uv'),
            ('invented.action', {}, 'deny'),
            ('Bash', {'command': 'printf hello'}, 'allow'),
            ('Bash', {'command': 'ls'}, 'allow'),
            ('Bash', {'command': 'git status'}, 'allow'),
        ):
            with self.subTest(tool=tool, inp=inp):
                self.assertEqual(p.main_requirement(tool, inp), floor)
                self.assertEqual(approval_level.host_card(tool, inp), floor != 'allow')
                with patch.object(p, 'main_requirement', side_effect=AssertionError('floor recomputed')):
                    self.assertEqual(approval_level.host_card(tool, inp, requirement=floor), floor != 'allow')

    def test_known_parameters_and_external_floor(self):
        for tool in ('Bash','exec_command','shell_command','mcp__fixture__execute_command'):
            for cmd in ('printf hello','ls','git status','echo done > notes.md'):
                self.assertEqual(p.main_requirement(tool, {'command':cmd, 'cmd':cmd}), 'allow')
                self.assertEqual(p.channel_tool_decision(tool), 'handoff')
            for cmd in ('rm -rf ~','agentj secret get fixture','sudo ls','stripe payment_intents create', 'env sudo ls', "bash -c 'sudo ls'", 'X=1 sudo ls'):
                self.assertEqual(p.main_requirement(tool, {'command':cmd, 'cmd':cmd}), 'uv')
        for tool in ('Write','Edit','MultiEdit','file.write','mcp__fixture__write_file'):
            self.assertEqual(p.main_requirement(tool, {'file_path':'notes.md'}), 'allow')
            self.assertEqual(p.main_requirement(tool, {'file_path':'.env'}), 'uv')
            self.assertEqual(p.channel_tool_decision(tool), 'handoff')
        self.assertEqual(p.main_requirement('NotebookEdit', {'notebook_path':'notes.ipynb', 'edit_mode':'delete'}), 'uv')
        self.assertEqual(p.main_requirement('Read', {'file_path':'.env'}), 'uv')
        self.assertEqual(p.main_requirement('apply_patch', {'patch':'*** Begin Patch\n*** Delete File: notes.md\n*** End Patch'}), 'uv')
        self.assertEqual(p.main_requirement('apply_patch', {'patch':'*** Begin Patch\n*** Add File: notes.md\n+hello\n*** End Patch'}), 'allow')


class MainExecution(unittest.IsolatedAsyncioTestCase):
    async def test_real_ask_enforces_hold_uv_and_unknown(self):
        import tempfile
        from test_l1 import _state,_owner_host,Phone,_ready
        with tempfile.TemporaryDirectory() as directory:
            st=_state(directory);h=_owner_host(st,[]);ph=Phone(st);_ready(h,ph)
            h.ask_ttl=.1
            h.goals.current=lambda: self.fail('registry floor bypassed via goal')
            h._grant_for=lambda *args: self.fail('registry floor bypassed via batch')
            for action, uv in (('settings.write',False),('Delete',True),('Bash',True),('memory.restore',False),('mcp__mail__send_email',True),('company.write',True)):
                with self.subTest(action=action):
                    with patch('agentj.approval_level.level',return_value='full'):
                        task=asyncio.create_task(h.ask(action,{'path':'fixture.txt', 'command':'rm -rf ~'},batch=True))
                        await asyncio.sleep(.001)
                    for _ in range(100):
                        cards=[a for a in h.asks.values() if not a.fut.done()]
                        if cards:break
                        await asyncio.sleep(.001)
                    self.assertTrue(cards)
                    card=cards[-1]
                    self.assertEqual(card.require_passkey,uv)
                    self.assertIsNone(card.scope)
                    card.fut.set_result(('deny','device',None,None))
                    self.assertEqual((await task)['behavior'],'deny')
            for unknown in ('invented.action','mcp__fs__unregistered_read','mcp__execute'):
                self.assertEqual((await h.ask(unknown,{}))['behavior'],'deny')

    async def test_real_ask_keeps_argument_floor_when_jev_allows(self):
        import tempfile
        from agentj import approval_jev
        from test_l1 import _state, _owner_host, Phone, _ready
        cases = [('$PY -m tests', 'hold'), ('bash -c "$CMD"', 'hold'),
                 ('sudo ls', 'uv'), ('invented.action', 'deny'),
                 ('printf hello', 'allow'), ('ls', 'allow'), ('git status', 'allow')]
        for level in ('standard', 'full'):
            for command, floor in cases:
                if level == 'standard' and floor == 'allow':
                    continue  # ordinary calls bypass Host.ask at the native hook/guard
                with self.subTest(level=level, command=command), tempfile.TemporaryDirectory() as directory:
                    st = _state(directory); sent = []; h = _owner_host(st, sent)
                    _ready(h, Phone(st)); h.ask_ttl = .05
                    tool = 'invented.action' if floor == 'deny' else 'Bash'
                    inp = {'command': command}
                    self.assertEqual(p.main_requirement(tool, inp), floor)
                    # Real table, classifier and Host.ask. Only the external Jev
                    # response and signed approval-level setting are fixtures.
                    with patch.object(approval_jev, 'available', return_value=True), \
                         patch.object(approval_jev, 'judge', return_value=approval_jev.Result('allow', .99, 'fixture')) as judge, \
                         patch.object(approval_level, 'level', return_value=level), \
                         patch.object(p, 'main_requirement', wraps=p.main_requirement) as lookup:
                        result = await h.ask(tool, inp)
                        await asyncio.sleep(.01)
                    cards = [obj for _, obj in sent if obj['t'] == 'ask']
                    self.assertEqual(result['behavior'], 'allow' if floor == 'allow' and level == 'full' else 'deny')
                    self.assertEqual(len(cards), 0 if floor == 'deny' or (floor == 'allow' and level == 'full') else 1)
                    if floor in ('hold', 'uv', 'deny'):
                        judge.assert_not_called()
                    lookup.assert_called_once_with(tool, inp)
                    if cards:
                        self.assertEqual(cards[0].get('passkey_required', False), floor == 'uv' or approval_jev.gray(h._classify(tool, inp)))

    async def test_real_uv_proof_rejects_missing_uv_and_replay(self):
        import tempfile
        from test_l1 import _state,_owner_host,Phone,_ready
        from test_p57_passkey import Authenticator,pk_record
        from agentj import passkey
        with tempfile.TemporaryDirectory() as directory:
            st=_state(directory);h=_owner_host(st,[]);ph=Phone(st);session=_ready(h,ph)
            h.agent=None  # no main owner turn: the registered company proposes a UV card only
            auth=Authenticator();st.set_passkey(ph.pub,pk_record(auth))
            h.ask_ttl=2
            task=asyncio.create_task(h.ask('company.write',{'digest':'a'*64,'summary':'sample'},_company_proposal=p.COMPANY_PROPOSAL))
            try:
                for _ in range(200):
                    cards=[a for a in h.asks.values() if not a.fut.done()]
                    if cards:break
                    await asyncio.sleep(.001)
                self.assertTrue(cards);a=cards[-1]
                obj=ph.answer(h._ask_msg(a,session),True)
                await h._answer(session,obj)
                self.assertFalse(a.fut.done())
                challenge=passkey.elevate_challenge(h.channel,ph.did,a.rid,a.pk_kind,a.rid,a.digest)
                await h._answer(session,{**obj,'fa':auth.assert_(challenge,b'0',nonce=b'0',flags=1)})
                self.assertFalse(a.fut.done())
                good={**obj,'fa':auth.assert_(challenge,b'0',nonce=b'0')}
                await h._answer(session,good)
                self.assertEqual((await task)['behavior'],'allow')
                await h._answer(session,good)
                self.assertEqual(len(h.asks),0)
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)

    async def test_support_owner_approval_uses_bounded_redacted_uv_card(self):
        from agentj.bots.owner import Manager
        host=NS(ask=AsyncMock(return_value={'behavior':'allow'}))
        manager=Manager.__new__(Manager);manager.host=host
        import time
        request={'id':'r','tool':'lookup','digest':'a'*64,'expires':time.time()+120}
        self.assertTrue(await manager.approve('bot',request,{'order':'sample','nested':{'password':'tiny','pin':'12'}}))
        self.assertEqual(host.ask.call_args.args[0],'company.write')
        self.assertTrue(host.ask.call_args.kwargs['require_passkey'])
        inp=host.ask.call_args.args[1]
        self.assertNotIn('args',inp)
        self.assertEqual(inp['digest'],request['digest'])
        self.assertNotIn('tiny',inp['summary'])
        self.assertNotIn('12',inp['summary'])
        self.assertIn('sample',inp['summary'])
        host.lang='zh'
        self.assertTrue(await manager.approve('bot',request,{}))
        self.assertEqual(host.ask.call_args.kwargs['origin'],'客服')
        host.lang='en-US'
        self.assertTrue(await manager.approve('bot',request,{}))
        self.assertEqual(host.ask.call_args.kwargs['origin'],'Customer service')
        host.ask.reset_mock()
        self.assertFalse(await manager.approve('bot',request,{'text':'x'*13000}))
        host.ask.assert_not_awaited()


class SupportExecution(unittest.TestCase):
    def test_final_executor_denies_without_approval_and_rechecks_table(self):
        from test_p82_bots import BotTest
        from agentj.bots.store import BotError
        fixture=BotTest();fixture.setUp()
        try:
            calls=[]
            reg=fixture.registry(lambda *a,**kw:calls.append((a,kw)) or {'status':'ok'})
            fixture.identity();reg.save({**fixture.tool('write'),'auth':None})
            with self.assertRaisesRegex(BotError,'owner_approval_required'):
                reg.execute(fixture.vid,fixture.rid,'lookup',{})
            self.assertFalse(calls)
            request=reg.approval(fixture.vid,fixture.rid,'lookup',{})
            reg.decide(request['id'],request['digest'],True)
            reg.execute(fixture.vid,fixture.rid,'lookup',{},request['id'])
            self.assertEqual(len(calls),1)
            with self.assertRaisesRegex(BotError,'owner_approval_required'):
                reg.execute(fixture.vid,fixture.rid,'lookup',{},request['id'])
            with patch.object(p,'REGISTRY',{**p.REGISTRY,'company.write':p.UNKNOWN}):
                with self.assertRaisesRegex(BotError,'unknown_tool'):
                    reg.execute(fixture.vid,fixture.rid,'lookup',{},request['id'])
            self.assertEqual(len(calls),1)
        finally:fixture.tearDown()


class LegacyFloors(unittest.TestCase):
    def test_every_action_against_pre_registry_layer_floors(self):
        # Frozen semantic floors from pre-P512: channel_policy.AUTO,
        # peer_session's no-tool contract, bots.tools read/write levels and
        # main danger/approval rules. New actions get the old unknown floor.
        free = {'answer','translate','summarize','rewrite'}
        native = {'image.generate','image.edit','web.search'}
        reversible = {'file.write','file.trash','settings.write','memory.restore'}
        uv = {'file.delete','mail.send','calendar.write','secrets.read','secrets.write',
              'payment','admin','device.pair','device.revoke','publish','company.write'}
        uv |= P539_REVIEWED    # reviewed new action: unknown-deny → mandatory passkey card (never automatic)
        p551 = {'mail.send','calendar.write','publish'}   # chairman #318: ② reversible, main layer uv → hold only
        dispatch = {'agent.dispatch'}                      # chairman #321 (P551): sending a sub-agent off is ①
        covered = free | native | reversible | uv | dispatch | {'file.read','company.read'}
        self.assertEqual(set(p.REGISTRY),covered)
        rank = {'allow':0,'hold':1,'uv':2,'handoff':3,'deny':3}
        for action in set(p.REGISTRY) | {'unknown','new.read'}:
            old = {
                'main': 'allow' if action in free|native|dispatch|{'file.read','company.read'} else
                        'hold' if action in reversible|{'company.write'} else 'uv' if action in uv else 'deny',
                'channel': 'allow' if action in free|native else 'deny',
                'friend': 'allow' if action in free else 'deny',
                'support': 'allow' if action in free|{'company.read'} else
                           'hold' if action=='company.write' else 'deny',
            }
            for layer in p.LAYERS:
                with self.subTest(layer=layer,action=action):
                    if layer == 'main' and action == 'file.write':
                        self.assertEqual(p.decision(layer, action), 'allow')  # chairman #274 / F14
                    elif layer == 'main' and action in p551:
                        self.assertEqual(p.decision(layer, action), 'hold')   # chairman #318 (P551)
                    else:
                        self.assertGreaterEqual(rank[p.decision(layer,action)],rank[old[layer]])

    def test_all_native_mappings_against_legacy_floors(self):
        # Exact intentional differences; a newly registered native spelling is
        # denied by the frozen old channel rule until explicitly reviewed here.
        differences = {'WebSearch', 'WebFetch', 'mcp__fixture__web_search'}
        from agentj import danger
        automatic = {'answer', 'translate', 'summarize', 'rewrite',
                     'image.generate', 'image.edit', 'web.search'}
        samples = {**{name: name for name in p.REGISTRY}, **p.NATIVE_TOOLS,
                   **p.HOST_TOOLS, **{'mcp__fixture__' + name: action for name, action in p.MCP_TOOLS.items()},
                   'unknown': None, 'mcp__fixture__unknown': None}
        rank = {'allow': 0, 'hold': 1, 'uv': 2, 'handoff': 3, 'deny': 3}
        seen = set()
        for tool, action in samples.items():
            for layer in p.LAYERS:
                old = (legacy_canonical_floor(layer, action) if tool == action else
                       'hold' if layer == 'main' and danger.classify(tool, {}).danger else
                       'allow' if layer == 'main' else
                       'allow' if layer == 'channel' and tool in automatic else 'deny')
                actual = p.channel_tool_decision(tool) if layer == 'channel' else p.decision(layer, p.tool_action(tool))
                # Friend/support native tools never gain their canonical answer/API permission.
                if layer in ('friend', 'support') and tool != action:
                    self.assertNotEqual(actual, 'allow', (layer, tool))
                if layer == 'main' and action == 'file.write':
                    self.assertEqual(actual, 'allow')  # #274/F14 intentional owner-only change
                elif layer == 'main' and action in P539_REVIEWED:
                    self.assertEqual(actual, 'uv')     # P539 #300: a reviewed new action asks with a passkey
                elif layer == 'main' and action in ('mail.send', 'calendar.write', 'publish'):
                    self.assertEqual(actual, 'hold')   # #318 (P551): reversible — long-press, never silent
                elif layer == 'main' and action == 'agent.dispatch':
                    self.assertEqual(actual, 'allow')  # #321 (P551): a sub-agent dispatch is ①; its own calls are checked
                elif rank[actual] < rank[old]:
                    self.assertEqual(layer, 'channel')
                    self.assertIn(tool, differences)
                    self.assertEqual((old, actual, action), ('deny', 'allow', 'web.search'))
                    seen.add(tool)
        self.assertEqual(seen, differences)

    def test_exact_native_mapping_and_no_tool_masquerading(self):
        for tool,action in {**p.NATIVE_TOOLS,**p.HOST_TOOLS}.items():
            self.assertEqual(p.main_requirement(tool), 'allow' if tool in ('Bash','exec_command','shell_command') else p.decision('main',action),tool)
        for tool,action in p.MCP_TOOLS.items():
            self.assertEqual(p.main_requirement('mcp__fixture__'+tool), 'allow' if tool in ('execute_command','bash') else p.decision('main',action))
        for tool in ('mcp__fixture__unregistered_read','mcp__fixture__read_file__delete',
                     'mcp____read_file', 'answer'):
            self.assertIsNone(p.tool_action(tool,canonical=False))


class EntryMatrix(unittest.IsolatedAsyncioTestCase):
    async def test_main_shared_hook_real_native_events(self):
        from test_shared_hooks import ClaudeHooks
        f=ClaudeHooks();await f.asyncSetUp()
        try:
            for tool,want in (('Read',None),('Write',None),('Delete','ask'),
                              ('Bash',None),('mcp__fs__write_file',None),
                              ('mcp__fs__delete_file','ask'),('mcp__fs__unknown','deny')):
                with self.subTest(tool=tool):
                    result=await f.event('PreToolUse',tool,{'file_path':'fixture.txt'})
                    self.assertEqual(result.get('hookSpecificOutput',{}).get('permissionDecision'),want)
            for lang,fragment in (('zh','需要主人确认'),('en-US','owner confirmation required')):
                f.host.lang=lang
                result=await f.event('PreToolUse','Write',{'file_path':'.env'})
                self.assertIn(fragment,result['hookSpecificOutput']['permissionDecisionReason'])
        finally:f.tmp.cleanup();f.doCleanups()

    async def test_codex_opencode_hook_events_never_reuse_registry_cards(self):
        from agentj.shared_risk import RiskGuard
        for kind in ('codex','opencode'):
            host=NS(ask=AsyncMock(return_value={'behavior':'allow'}),stopped=lambda:False,emit=lambda *a,**k:None)
            guard=RiskGuard(NS(host=host,kind=kind,cfg={'shared_session_id':'fixture'}))
            for tool in ('Read','Write','Delete','exec_command','mcp__fs__unknown'):
                host.ask.reset_mock()
                result=await guard.hook_event({'hook_event_name':'PreToolUse','session_id':'fixture',
                                             'tool_name':tool,'tool_input':{'path':'fixture.txt'}})
                if tool=='mcp__fs__unknown':
                    self.assertEqual(result['hookSpecificOutput']['permissionDecision'],'deny')
                    host.ask.assert_not_awaited()
                elif tool in ('Read','Write','exec_command'):host.ask.assert_not_awaited()
                else:
                    host.ask.assert_awaited_once()
                    self.assertEqual(host.ask.call_args.kwargs['require_passkey'],tool!='Write')
                    await guard.hook_event({'hook_event_name':'PreToolUse','session_id':'fixture',
                                           'tool_name':tool,'tool_input':{}})
                    self.assertEqual(host.ask.await_count,2)

    async def test_independent_claude_hook_native_events(self):
        from test_danger import Hook
        hook = Hook()
        for tool, want in (('Read', None), ('Write', None), ('Bash', None),
                           ('Delete', 'ask'), ('mcp__fs__unknown', 'deny')):
            with self.subTest(tool=tool):
                result = hook.run_hook({'tool_name': tool, 'tool_input': {'command': 'ls', 'file_path': 'fixture.txt'}})
                self.assertEqual(result.returncode, 0)
                output = json.loads(result.stdout)['hookSpecificOutput'] if result.stdout else {}
                self.assertEqual(output.get('permissionDecision'), want)

    async def test_independent_claude_default_argv_enforces_policy(self):
        import os
        import subprocess
        from pathlib import Path
        from agentj.agent import ClaudeAgent
        from agentj.preferences import defaults
        cfg = {**defaults()['agent'], 'kind': 'claude', 'dir': '/tmp'}
        self.assertFalse(cfg['high_risk_warnings'])
        for warnings, research in ((False, False), (True, False), (False, True), (True, True)):
            cfg['high_risk_warnings'] = warnings
            argv = ClaudeAgent(None, cfg).argv(None, research=research)
            settings = json.loads(argv[argv.index('--settings') + 1])
            self.assertIs(settings['disableAllHooks'], False)
            hooks = settings['hooks']['PreToolUse']
            self.assertEqual(len(hooks), 1)
            self.assertEqual(hooks[0]['matcher'], '*')
            command = hooks[0]['hooks'][0]['command']
            cases = [('Read', {'file_path':'fixture.txt'}, None),
                     ('Write', {'file_path':'fixture.txt'}, None),
                     *[('Bash', {'command':cmd}, None) for cmd in ('printf hello','ls','git status')],
                     ('Bash', {'command':'rm -rf ~'}, 'ask'),
                     ('Write', {'file_path':'.env'}, 'ask'),
                     ('Write', {'file_path':'~/.aws/credentials'}, 'ask'),
                     ('Bash', {'command':'agentj secret get fixture'}, 'ask'),
                     ('Bash', {'command':'stripe payment_intents create'}, 'ask'),
                     ('Bash', {'command': '$PY -m tests'}, 'ask'),
                     ('Bash', {'command': 'bash -c "$CMD"'}, 'ask'),
                     ('settings.write', {}, 'ask'), ('mcp__fs__unknown', {}, 'deny')]
            for tool, inp, want in cases:
                if research and tool != 'Read' and not (tool == 'Bash' and inp.get('command') in ('printf hello','ls','git status')):
                    want = 'deny'
                with self.subTest(warnings=warnings, research=research, tool=tool, inp=inp):
                    result = subprocess.run(command, shell=True, text=True, capture_output=True,
                        input=json.dumps({'tool_name': tool, 'tool_input': inp}),
                        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])), timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    output = json.loads(result.stdout)['hookSpecificOutput'] if result.stdout else {}
                    self.assertEqual(output.get('permissionDecision'), want)

    async def test_independent_claude_allowed_owner_actions_stay_silent(self):
        import io
        import shlex
        from contextlib import redirect_stdout
        from agentj import agent, danger
        from agentj.preferences import defaults
        cfg = {**defaults()['agent'], 'kind': 'claude', 'dir': '/tmp'}
        # Real factory argv and real registry: never substitute the policy result.
        for warnings in (False, True):
            cfg['high_risk_warnings'] = warnings
            argv = agent.ClaudeAgent(None, cfg).argv(None)
            settings = json.loads(argv[argv.index('--settings') + 1])
            command = shlex.split(settings['hooks']['PreToolUse'][0]['hooks'][0]['command'])
            args = command[command.index('hook') + 1:command.index('||')]
            for tool, inp in (('Write', {'file_path': 'fixture.txt'}),
                              *[('Bash', {'command': cmd}) for cmd in ('printf hello', 'ls', 'git status')]):
                out = io.StringIO()
                with patch('sys.stdin', io.StringIO(json.dumps({'tool_name': tool, 'tool_input': inp}))), \
                     redirect_stdout(out):
                    self.assertEqual(danger.hook_main(args), 0)
                self.assertEqual(out.getvalue(), '', (warnings, tool))
            for tool, inp, requirement in (
                ('Bash', {'command': 'rm -rf ~'}, 'uv'),
                ('Write', {'file_path': '.env'}, 'uv'),
                ('Write', {'file_path': '~/.aws/credentials'}, 'uv'),
                ('Bash', {'command': 'agentj secret get fixture'}, 'uv'),
                ('Bash', {'command': 'sudo ls'}, 'uv'),
                ('Bash', {'command': 'stripe payment_intents create'}, 'uv'),
                ('settings.write', {}, 'hold'),
            ):
                self.assertEqual(p.main_requirement(tool, inp), requirement)
                out = io.StringIO()
                with patch('sys.stdin', io.StringIO(json.dumps({'tool_name': tool, 'tool_input': inp}))), redirect_stdout(out):
                    self.assertEqual(danger.hook_main(args), 0)
                self.assertEqual(json.loads(out.getvalue())['hookSpecificOutput']['permissionDecision'], 'ask')
                self.assertNotEqual(p.channel_tool_decision(tool), 'allow')

    async def test_independent_codex_and_opencode_real_decisions(self):
        from test_p198_bdg import _Host, HRW
        from agentj.agent_codex import CodexAgent
        from agentj.agent_opencode import OpenCodeAgent
        for kind, cls in (('codex', CodexAgent), ('opencode', OpenCodeAgent)):
            cfg = {**HRW, 'kind': kind}
            host = _Host(cfg)
            adapter = cls(host, cfg)
            host.agent = adapter
            adapter.cur_send = NS(source={'kind': 'phone', 'role': 'owner'})
            replies = []
            adapter._answer = lambda rid, res=None, error=None: replies.append(res)
            adapter.client = NS(request=AsyncMock(return_value=(200, {})))
            for tool, permission in (('Read', 'read'), ('Write', 'edit'), ('Bash', 'bash'),
                                     ('mcp__fs__unknown', 'fs__unknown')):
                host.asked.clear()
                inp = {'command': 'ls', 'file_path': 'fixture.txt'}
                if kind == 'codex':
                    await adapter._decide(tool, tool, inp, {'decision': 'accept'}, {'decision': 'decline'}, True)
                    accepted = replies[-1] == {'decision': 'accept'}
                else:
                    request = {'permission': permission, 'patterns': ['fixture.txt'],
                               'metadata': {'command': 'ls', 'diff': '--- /dev/null\n+++ fixture.txt\n@@ -0,0 +1 @@\n+x'}}
                    await adapter._decide(tool, request, asyncio.get_running_loop().create_future())
                    accepted = adapter.client.request.call_args.args[2]['reply'] == 'once'
                with self.subTest(kind=kind, tool=tool):
                    self.assertEqual(accepted, tool in ('Read', 'Write', 'Bash'))
                    self.assertEqual(len(host.asked), 0 if tool in ('Read', 'Write', 'Bash') else 1)
                    if host.asked:
                        self.assertEqual(p.main_requirement(host.asked[0][0]), p.main_requirement(tool))

    async def test_friend_real_session_turn_answer_and_tool_matrix(self):
        import tempfile
        from pathlib import Path
        from agentj.peer_session import PeerSessions
        from agentj.state import State
        from agentj.friends import FriendStore
        with tempfile.TemporaryDirectory() as tmp:
            st=State(Path(tmp)/'state');st.root.mkdir()
            store=FriendStore(st)
            session=PeerSessions(NS(st=st,agent_cfg={'kind':'claude'}),store)
            # Substitute only the harness boundary; parsing and turn policy stay real.
            session._exe=lambda kind:'fixture'
            session._claude_argv=lambda *args:['fixture']
            session.execution_env=lambda *args:{}
            session._execute=AsyncMock(return_value=('', '', 0))
            friend={'id':'fixture'}
            for tool in (None,'Read','Write','Delete','Bash','mcp__fs__unknown','answer'):
                parsed={'sid':None,'error':None,'text':'{"decision":"reply","text":"hello","topic":"sample"}',
                        'tokens':1,'tools':[] if tool is None else [tool]}
                with patch('agentj.peer_session.fence.problem',return_value=None), \
                     patch('agentj.peer_session.fence.protected_paths',return_value=[]), \
                     patch.object(session,'parse_claude',return_value=parsed):
                    result=await session.turn(friend,{'auto':{'mode':'on'}},'hello')
                    self.assertEqual(result.decision,'reply' if tool is None else 'ask_owner')
                    if tool is None:
                        with patch.object(p,'REGISTRY',{**p.REGISTRY,'answer':p.UNKNOWN}):
                            result=await session.turn(friend,{'auto':{'mode':'on'}},'hello')
                            self.assertEqual(result.decision,'ask_owner')

    async def test_channel_real_turn_matrix_including_company_read(self):
        from agentj.wechat import Sessions
        from agentj.peer_session import TurnResult
        from agentj.channel_native import NativeCodex
        session=Sessions(NS(st=NS(),agent_cfg={'kind':'codex'}),NS(data={}))
        for action in set(p.REGISTRY) | set(p.NATIVE_TOOLS) | set(p.HOST_TOOLS) | {'mcp__fixture__'+name for name in p.MCP_TOOLS} | {'unknown'}:
            res=TurnResult('reply','hello','sample',1,None,tools=[] if action=='answer' else [action])
            with patch.object(NativeCodex,'turn',AsyncMock(return_value=res)):
                result=await session.turn({}, {}, 'hello')
            self.assertEqual(result.decision,'reply' if p.channel_tool_decision(action)=='allow' else 'ask_owner', action)
        with patch.object(p,'REGISTRY',{**p.REGISTRY,'answer':p.UNKNOWN}), \
             patch.object(NativeCodex,'turn',AsyncMock(return_value=TurnResult('reply','hello','sample',1,None))):
            self.assertEqual((await session.turn({}, {}, 'hello')).decision,'ask_owner')

    async def test_support_real_prepare_matrix_and_answer_gate(self):
        from test_p82_bots import BotTest
        from agentj.bots.store import BotError
        from agentj.bots.engine import Engine
        f=BotTest();f.setUp()
        try:
            f.identity();calls=[]
            reg=f.registry(lambda *a,**k:calls.append(1) or {'status':'ok'})
            for action in ('company.read','file.write','file.delete','company.write','unknown'):
                reg.save({**f.tool('read' if action=='company.read' else 'write'),'auth':None})
                with patch.object(p,'company_action',return_value=action):
                    if action=='company.read':reg.execute(f.vid,f.rid,'lookup',{})
                    else:
                        with self.assertRaises(BotError):reg.execute(f.vid,f.rid,'lookup',{})
            self.assertEqual(calls,[1])
            engine=Engine(f.store,'codex')
            with patch.object(p,'REGISTRY',{**p.REGISTRY,'answer':p.UNKNOWN}):
                self.assertEqual((await engine.reply(f.bot,f.vid,f.cap,f.rid,'hello'))['status'],'refused')
        finally:f.tearDown()
