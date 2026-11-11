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
# P573 (#386 ⑤, chairman 10-29, REPORT-573-draft §三 「降为按钮」): exactly these rows drop from Face ID to one button in the
# owner window (main_confirmation="hold"); every other layer keeps its row. Reviewed here, never by a looser rank check.
P573_BUTTON = frozenset({'secrets.read', 'secrets.write', 'admin', 'computer.control'})
# P553 (#326): native names that were unknown (deny) until reviewed into the P551 tiers; never lower than ① on main
P553_UNKNOWN_BEFORE = frozenset({
    'ToolSearch', 'ListMcpResourcesTool', 'ReadMcpResourceTool', 'NotebookRead', 'BashOutput', 'TaskOutput', 'TaskGet',
    'TaskList', 'ListAgents', 'CronList', 'LSP', 'Skill', 'tool_search', 'list_mcp_resources', 'list_mcp_resource_templates',
    'read_mcp_resource', 'list_agents', 'glob', 'grep', 'list', 'lsp', 'skill', 'codesearch', 'ExternalDirectory',
    'TaskStop', 'KillShell', 'KillBash', 'ScheduleWakeup', 'EnterPlanMode', 'ExitPlanMode', 'TaskCreate', 'TaskUpdate',
    'CronCreate', 'CronDelete', 'PushNotification', 'SendUserMessage', 'StructuredOutput', 'ReportFindings', 'Sleep',
    'TeamCreate', 'TeamDelete', 'wait_agent', 'close_agent', 'plan_enter', 'plan_exit', 'SendMessage', 'Workflow',
    'spawn_agent', 'send_input', 'resume_agent', 'followup_task', 'EnterWorktree', 'ExitWorktree', 'todowrite', 'question',
    'Config', 'DesignSync', 'Monitor', 'write_stdin', 'js_repl', 'PowerShell', 'RemoteTrigger'})


# Reviewed semantic Host entries, independent of the registry's historical
# category projection. Native spellings/command text do not become executors.
P0300_UV_HOST = frozenset({'pair_device','AuthRescue','agentj mail workflow-grant',
                         'agentj goal grant','agentj secret grant',
                         'agentj automation grant'})   # 0304 A3: automation outbound-send grant (UV list)
P0300_REVIEWED_ACTIONS = {'plan.bounded': {'main':'uv','channel':'handoff','friend':'handoff','support':'deny'}}


async def native_tap(host, phone, session, tool, inp, *, approve=True, **kwargs):
    from _p541_device import Phone as V2Phone
    task = asyncio.create_task(host.ask(tool, inp, _harness_permission=True, **kwargs))
    try:
        for _ in range(100):
            if host.asks or task.done(): break
            await asyncio.sleep(.001)
        if task.done():
            raise AssertionError('native permission did not wait for its signed tap')
        ask = next(iter(host.asks.values()))
        card = host._ask_msg(ask, session)
        await host._answer(session, V2Phone.answer(phone, card, approve))
        return await task, card
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class Matrix(unittest.TestCase):
    def test_four_layers_representative_actions(self):
        # Columns: main, owner external channel, friend, customer service.
        rows = {
            'answer': ('allow','allow','allow','allow'),
            'web.search': ('allow','allow','handoff','deny'),
            'image.generate': ('allow','allow','handoff','deny'),
            'file.write': ('allow','handoff','handoff','deny'),
            'mail.send': ('hold','handoff','handoff','deny'),   # P551 (#318): ② reversible — long-press for the Agent
            'secrets.read': ('hold','handoff','handoff','deny'),   # P573: main window one button; other layers unchanged
            'admin': ('hold','handoff','handoff','deny'),
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
            self.assertEqual(approval_level.host_card(tool), tool in P0300_UV_HOST, tool)   # #321: OpenCode `task` is ①
        for tool in ('agentj mail','agentj new-unregistered-action',None,'mail.send'):
            self.assertFalse(approval_level.host_card(tool),tool)  # not an execution authorization
        self.assertFalse(approval_level.host_card('Read'))
        with patch.object(p,'REGISTRY',{**p.REGISTRY,'mail.send':p.NATIVE}):
            self.assertFalse(approval_level.host_card('agentj mail'))
        self.assertEqual(p.main_requirement('invented.action'),'allow')
        self.assertEqual(p.main_requirement('unknown'),'allow')
        self.assertEqual(p.main_requirement('agentj new-unregistered-action'),'allow')

    def test_host_card_preserves_real_arguments_and_precomputed_floor(self):
        for tool, inp, floor in (
            ('Bash', {'command': '$PY -m tests'}, 'hold'),
            ('Bash', {'command': 'bash -c "$CMD"'}, 'hold'),
            ('Bash', {'command': 'sudo ls'}, 'hold'),          # P573: sudo is one button (+ its password card)
            ('invented.action', {}, 'deny'),
            ('Bash', {'command': 'printf hello'}, 'allow'),
            ('Bash', {'command': 'ls'}, 'allow'),
            ('Bash', {'command': 'git status'}, 'allow'),
        ):
            with self.subTest(tool=tool, inp=inp):
                # Keep each legacy input/floor as an audit vector; effect/opaque
                # categories no longer add an Agent J permission layer.
                self.assertEqual(p.main_requirement(tool, inp), 'allow')
                self.assertFalse(approval_level.host_card(tool, inp))
                with patch.object(p, 'main_requirement', side_effect=AssertionError('floor recomputed')):
                    self.assertFalse(approval_level.host_card(tool, inp, requirement='allow'))

    def test_known_parameters_and_external_floor(self):
        for tool in ('Bash','exec_command','shell_command','mcp__fixture__execute_command'):
            for cmd in ('printf hello','ls','git status','echo done > notes.md'):
                self.assertEqual(p.main_requirement(tool, {'command':cmd, 'cmd':cmd}), 'allow')
                self.assertEqual(p.channel_tool_decision(tool), 'handoff')
            for cmd in ('rm -rf ~','agentj secret get fixture','stripe payment_intents create'):
                # Home is an ancestor of the closed approval-control inventory.
                wanted = 'uv' if cmd == 'rm -rf ~' else 'allow'
                self.assertEqual(p.main_requirement(tool, {'command':cmd, 'cmd':cmd}), wanted)
            for cmd in ('sudo ls', 'env sudo ls', "bash -c 'sudo ls'", 'X=1 sudo ls'):   # P573: sudo → one button, every spelling
                self.assertEqual(p.main_requirement(tool, {'command':cmd, 'cmd':cmd}), 'allow')
            self.assertEqual(p.main_requirement(tool, {'command':'sudo rm -rf ~', 'cmd':'sudo rm -rf ~'}), 'uv')
        for tool in ('Write','Edit','MultiEdit','file.write','mcp__fixture__write_file'):
            self.assertEqual(p.main_requirement(tool, {'file_path':'notes.md'}), 'allow')
            self.assertEqual(p.main_requirement(tool, {'file_path':'.env'}), 'allow')   # P573: a credential write is one button
            self.assertEqual(p.channel_tool_decision(tool), 'handoff')
        self.assertEqual(p.main_requirement('NotebookEdit', {'notebook_path':'notes.ipynb', 'edit_mode':'delete'}), 'allow')
        self.assertEqual(p.main_requirement('Read', {'file_path':'.env'}), 'allow')   # P573: a key read for the Agent
        self.assertEqual(p.main_requirement('apply_patch', {'patch':'*** Begin Patch\n*** Delete File: notes.md\n*** End Patch'}), 'allow')
        self.assertEqual(p.main_requirement('apply_patch', {'patch':'*** Begin Patch\n*** Add File: notes.md\n+hello\n*** End Patch'}), 'allow')

        # Actual closed control-file inventory and semantic commands retain UV.
        from pathlib import Path
        for path in ('.claude/settings.json','.codex/hooks.json','.opencode/plugins/agentj-dispatch/index.js'):
            with self.subTest(protected_path=path):
                self.assertEqual(p.main_requirement('Write', {'file_path':path,'content':'fixture'},
                                                    cwd=str(Path.cwd())), 'uv')
        for tool in P0300_UV_HOST:
            self.assertEqual(p.main_requirement(tool, {}), 'uv')


class MainExecution(unittest.IsolatedAsyncioTestCase):
    async def test_real_ask_enforces_hold_uv_and_unknown(self):
        import tempfile
        from test_l1 import _state,_owner_host,Phone,_ready
        with tempfile.TemporaryDirectory() as directory:
            st=_state(directory);h=_owner_host(st,[]);ph=Phone(st);session=_ready(h,ph)
            h.ask_ttl=2
            h.goals.current=lambda: None
            h._grant_for=lambda *args: self.fail('legacy batch grant answered a native request')
            for action, legacy_uv in (('settings.write',False),('Delete',True),('Bash',True),('memory.restore',False),('mcp__mail__send_email',True),('company.write',True)):
                with self.subTest(action=action), patch('agentj.approval_level.level',return_value='full'):
                    if action != 'Bash':
                        # 0.30.7 ADR-0307-offlist: a native request off the card list (a native request may only name
                        # AskUserQuestion; these are not UV-listed) is answered at once on the owner turn — no card.
                        before = len(st.log_path.read_text()) if st.log_path.exists() else 0
                        result = await asyncio.wait_for(h.ask(action,{'path':'fixture.txt','command':'rm -rf ~'},
                                                              _harness_permission=True,batch=True),3)
                        self.assertFalse(h.asks)
                        self.assertEqual(result['behavior'],'allow')
                        self.assertIn('"reason": "off_list"', st.log_path.read_text()[before:])
                        continue
                    result, card = await native_tap(h,ph,session,action,{'path':'fixture.txt','command':'rm -rf ~'},approve=False,batch=True)
                    # The exact Bash operand ~ includes protected control files (UV-listed: still a card).
                    self.assertEqual(bool(card.get('passkey_required')), action == 'Bash')
                    self.assertNotIn('batch',card)
                    self.assertEqual(result['behavior'],'deny')
            for unknown in ('invented.action','mcp__fs__unregistered_read','mcp__execute'):
                # 0.30.7 ADR-0307-offlist: an unknown native tool is off the card list → answered at once, no card.
                result=await asyncio.wait_for(h.ask(unknown,{},_harness_permission=True),3)
                self.assertFalse(h.asks)
                self.assertEqual(result['behavior'],'allow')

    async def test_real_ask_keeps_argument_floor_when_jev_allows(self):
        import tempfile
        from agentj import approval_jev
        from test_l1 import _state, _owner_host, Phone, _ready
        cases = [('$PY -m tests', 'hold'), ('bash -c "$CMD"', 'hold'),
                 ('sudo ls', 'hold'), ('invented.action', 'deny'),
                 ('printf hello', 'allow'), ('ls', 'allow'), ('git status', 'allow')]
        for level in ('standard', 'full'):
            for command, legacy_floor in cases:
                with self.subTest(level=level,command=command), tempfile.TemporaryDirectory() as directory:
                    st=_state(directory);sent=[];h=_owner_host(st,sent);ph=Phone(st);session=_ready(h,ph);h.ask_ttl=2
                    tool='invented.action' if legacy_floor=='deny' else 'Bash'
                    inp={'command':command}
                    self.assertEqual(p.main_requirement(tool,inp),'allow')
                    with patch.object(approval_jev,'available',return_value=True), \
                         patch.object(approval_jev,'judge',return_value=approval_jev.Result('allow',.99,'fixture')) as judge, \
                         patch.object(approval_level,'level',return_value=level), \
                         patch.object(p,'main_requirement',wraps=p.main_requirement) as lookup:
                        # 0.30.7 ADR-0307-offlist: none of these is on the card list → answered at once (owner turn:
                        # allow), no card; Jev and the legacy category floor are still never consulted.
                        result=await asyncio.wait_for(h.ask(tool,inp,_harness_permission=True),3)
                    self.assertEqual(result['behavior'],'allow')
                    self.assertFalse(h.asks)
                    self.assertEqual(len([obj for _,obj in sent if obj['t']=='ask']),0)
                    judge.assert_not_called()
                    lookup.assert_not_called()  # uv_list, not the legacy category floor

    async def test_real_uv_proof_rejects_missing_uv_and_replay(self):
        import tempfile
        from test_l1 import _state,_owner_host,Phone,_ready
        from _p541_device import Phone as V2Phone
        from test_p57_passkey import Authenticator,pk_record
        from agentj import passkey
        with tempfile.TemporaryDirectory() as directory:
            st=_state(directory);h=_owner_host(st,[]);ph=Phone(st);session=_ready(h,ph)
            h.agent=None
            auth=Authenticator();st.set_passkey(ph.pub,pk_record(auth));ph.auth=auth;h.ask_ttl=2
            # Preserve the old canonical company proposal input as a plain native
            # permission vector, without inventing a company-to-main UV exemption.
            # 0.30.7 ADR-0307-offlist: as a NATIVE request it is off the card list → answered at once, no card; with no
            # agent (h.agent=None) nothing host-held names the turn → unknown trust → an off-list write is refused.
            result=await asyncio.wait_for(h.ask('company.write',{'digest':'a'*64,'summary':'sample'},_harness_permission=True),3)
            self.assertEqual((result['behavior'],result.get('reason')),('deny','off_list_write_unknown'));self.assertFalse(h.asks)
            task=asyncio.create_task(h.ask('agentj fn run',{'name':'synthetic-test-only'}))
            try:
                for _ in range(200):
                    if h.asks:break
                    await asyncio.sleep(.001)
                self.assertTrue(h.asks);a=next(iter(h.asks.values()))
                card=h._ask_msg(a,session);obj=V2Phone.answer(ph,card,True);obj.pop('fa')
                await h._answer(session,obj);self.assertFalse(a.fut.done())
                challenge=passkey.elevate_challenge(h.channel,ph.did,a.rid,a.pk_kind,a.rid,a.envelope_digest)
                await h._answer(session,{**obj,'fa':auth.assert_(challenge,b'0',nonce=b'0',flags=1)})
                self.assertFalse(a.fut.done())
                good={**obj,'fa':auth.assert_(challenge,b'0',nonce=b'0')}
                await h._answer(session,good);self.assertEqual((await task)['behavior'],'allow')
                await h._answer(session,good);self.assertFalse(h.asks)
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
        dispatch = {'agent.dispatch', 'agent.session'}     # chairman #321 (P551): sending a sub-agent off is ①; P553: its own housekeeping
        covered = free | native | reversible | uv | dispatch | {'file.read','company.read'} | set(P0300_REVIEWED_ACTIONS)
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
                    if action in P0300_REVIEWED_ACTIONS:
                        self.assertEqual(p.decision(layer, action), P0300_REVIEWED_ACTIONS[action][layer])
                    elif layer == 'main' and action == 'file.write':
                        self.assertEqual(p.decision(layer, action), 'allow')  # chairman #274 / F14
                    elif layer == 'main' and action in p551:
                        self.assertEqual(p.decision(layer, action), 'hold')   # chairman #318 (P551)
                    elif layer == 'main' and action in P573_BUTTON:
                        self.assertEqual(p.decision(layer, action), 'hold')   # P573: Face ID → one button, never silent
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
                if action in P0300_REVIEWED_ACTIONS:
                    self.assertEqual(actual, P0300_REVIEWED_ACTIONS[action][layer])
                elif layer == 'main' and action == 'file.write':
                    self.assertEqual(actual, 'allow')  # #274/F14 intentional owner-only change
                elif layer == 'main' and action in P573_BUTTON:
                    self.assertEqual(actual, 'hold')   # P573 (#386 ⑤): Face ID → one button, never silent
                elif layer == 'main' and action in P539_REVIEWED:
                    self.assertEqual(actual, 'uv')     # P539 #300: a reviewed new action asks with a passkey
                elif layer == 'main' and action in ('mail.send', 'calendar.write', 'publish'):
                    self.assertEqual(actual, 'hold')   # #318 (P551): reversible — long-press, never silent
                elif layer == 'main' and action in ('agent.dispatch', 'agent.session'):
                    self.assertEqual(actual, 'allow')  # #321 (P551): a sub-agent dispatch is ①; its own calls are checked
                elif layer == 'main' and tool in P553_UNKNOWN_BEFORE:
                    self.assertIn(actual, ('allow', 'hold', 'uv'))   # P553 (#326): reviewed, was unknown-deny
                elif rank[actual] < rank[old]:
                    self.assertEqual(layer, 'channel')
                    self.assertIn(tool, differences)
                    self.assertEqual((old, actual, action), ('deny', 'allow', 'web.search'))
                    seen.add(tool)
        self.assertEqual(seen, differences)

    def test_exact_native_mapping_and_no_tool_masquerading(self):
        for tool,action in {**p.NATIVE_TOOLS,**p.HOST_TOOLS}.items():
            self.assertEqual(p.main_requirement(tool), 'uv' if tool in P0300_UV_HOST else 'allow', tool)
        for tool,action in p.MCP_TOOLS.items():
            self.assertEqual(p.main_requirement('mcp__fixture__'+tool), 'allow')
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
                              ('mcp__fs__delete_file','ask'),('mcp__unknown','deny')):
                with self.subTest(tool=tool):
                    result=await f.event('PreToolUse',tool,{'file_path':'fixture.txt'})
                    self.assertEqual(result.get('hookSpecificOutput',{}).get('permissionDecision'),None)
            for lang,fragment in (('zh','需要主人确认'),('en-US','owner confirmation required')):
                f.host.lang=lang
                result=await f.event('PreToolUse','Write',{'file_path':'.env'})
                self.assertIsNone(result.get('hookSpecificOutput',{}).get('permissionDecision'))
        finally:f.tmp.cleanup();f.doCleanups()

    async def test_codex_opencode_hook_events_never_reuse_registry_cards(self):
        from agentj.shared_risk import RiskGuard
        for kind in ('codex','opencode'):
            host=NS(ask=AsyncMock(return_value={'behavior':'allow'}),stopped=lambda:False,emit=lambda *a,**k:None)
            guard=RiskGuard(NS(host=host,kind=kind,cfg={'shared_session_id':'fixture'}))
            for tool in ('Read','Write','Delete','exec_command','mcp__unknown'):
                host.ask.reset_mock()
                result=await guard.hook_event({'hook_event_name':'PreToolUse','session_id':'fixture',
                                             'tool_name':tool,'tool_input':{'path':'fixture.txt'}})
                self.assertIsNone(result.get('hookSpecificOutput',{}).get('permissionDecision'))
                host.ask.assert_not_awaited()  # ordinary PreToolUse never adds a second gate
                # Preserve the original Delete-with-empty-input vector as well.
                empty = await guard.hook_event({'hook_event_name':'PreToolUse', 'session_id':'fixture',
                                               'tool_name':tool, 'tool_input':{}})
                self.assertIsNone(empty.get('hookSpecificOutput',{}).get('permissionDecision'))
                host.ask.assert_not_awaited()
    async def test_independent_claude_hook_native_events(self):
        from test_danger import Hook
        hook = Hook()
        for tool, want in (('Read', None), ('Write', None), ('Bash', None),
                           ('Delete', 'ask'), ('mcp__unknown', 'deny')):
            with self.subTest(tool=tool):
                result = hook.run_hook({'tool_name': tool, 'tool_input': {'command': 'ls', 'file_path': 'fixture.txt'}})
                self.assertEqual(result.returncode, 0)
                output = json.loads(result.stdout)['hookSpecificOutput'] if result.stdout else {}
                self.assertEqual(output.get('permissionDecision'), None)

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
                     ('settings.write', {}, 'ask'), ('mcp__unknown', {}, 'deny')]
            for tool, inp, want in cases:
                # Preserve the actual protected ancestor write vector as UV.
                # #537② (Jarvis 2026-11-11): a listed real charge is routed to Host.ask (fingerprint card there) too.
                want = 'ask' if tool == 'Bash' and inp.get('command') in ('rm -rf ~', 'stripe payment_intents create') else None
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
                ('Write', {'file_path': '.env'}, 'uv'),                    # P573: a credential write is one button
                ('Write', {'file_path': '~/.aws/credentials'}, 'uv'),
                ('Bash', {'command': 'agentj secret get fixture'}, 'uv'),
                ('Bash', {'command': 'sudo ls'}, 'hold'),                    # P573: sudo is one button
                ('Bash', {'command': 'stripe payment_intents create'}, 'uv'),
                ('settings.write', {}, 'hold'),
            ):
                protected = tool == 'Bash' and inp.get('command') == 'rm -rf ~'
                self.assertEqual(p.main_requirement(tool, inp), 'uv' if protected else 'allow')
                out = io.StringIO()
                with patch('sys.stdin', io.StringIO(json.dumps({'tool_name': tool, 'tool_input': inp}))), redirect_stdout(out):
                    self.assertEqual(danger.hook_main(args), 0)
                charge = tool == 'Bash' and inp.get('command') == 'stripe payment_intents create'   # #537②
                if protected or charge:
                    self.assertEqual(json.loads(out.getvalue())['hookSpecificOutput']['permissionDecision'], 'ask')
                else:
                    self.assertEqual(out.getvalue(), '')
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
                                     ('mcp__unknown', 'unknown')):
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
                    self.assertFalse(accepted)  # this fixture's actual Host decision is deny
                    self.assertEqual(len(host.asked), 1)
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
            for tool in (None,'Read','Write','Delete','Bash','mcp__unknown','answer'):
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
