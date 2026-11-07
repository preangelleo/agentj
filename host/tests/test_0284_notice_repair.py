"""Host-authored fixed repair metadata, readable fallback, no command inferred from prose."""
import _hermetic
import ast,json,re,unittest
from pathlib import Path
from agentj import update,phone_update,auto_update,service_recovery,slash,cmdrun
from test_p551_confirm_levels import Base

BLOCK_PUNCT=re.compile(r'(?:```[^\n]*\n.*?\n```|</(?:button|card|pre)>)[\s]*[，。、；：,.;:]',re.S)
def strings(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for item in value.values():yield from strings(item)
    elif isinstance(value,list):
        for item in value:yield from strings(item)

class Templates(unittest.TestCase):
    def test_web_upgrade_messages_are_actionable_without_terminal_commands(self):
        for lang in ('zh','en'):
            for web in ('older','same',None):
                text=update.web_refresh_text(lang,web)
                self.assertNotIn('agentj doctor',text);self.assertNotIn('Terminal',text);self.assertNotIn('打开终端',text)
                self.assertIn('修复连接'if lang=='zh'else'Repair connection',text)
    def test_failed_update_templates_never_emit_an_executable_recovery_string(self):
        for lang in ('zh','en'):
            text=phone_update.text({'reason':'install_failed','result':'failed'},lang)
            self.assertNotIn('agentj update apply',text)
            text=auto_update.notice({'result':'failed','reason':'rollback_failed','recover':'SYNTHETIC_COMMAND_SENTINEL'},lang)
            self.assertNotIn('SYNTHETIC_COMMAND_SENTINEL',text);self.assertNotIn('agentj doctor',text)
    def test_block_boundary_lint_covers_messages_and_host_i18n(self):
        self.assertIsNotNone(BLOCK_PUNCT.search('```sh\nstatus\n```\n，bad'))
        self.assertIsNotNone(BLOCK_PUNCT.search('<button>Repair</button>\n, bad'))
        root=Path(update.__file__).parent
        for path in sorted(root.rglob('*.py')):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node,ast.Constant)and isinstance(node.value,str):
                    self.assertIsNone(BLOCK_PUNCT.search(node.value),(str(path.relative_to(root)),node.lineno))
        for path in [root/'humane.json',*sorted((root/'admin/i18n').glob('*.json'))]:
            for value in strings(json.loads(path.read_text())):self.assertIsNone(BLOCK_PUNCT.search(value),path.name)
    def test_reconnect_is_an_existing_fixed_command_and_real_parser_flag(self):
        self.assertEqual(cmdrun.RUN_CMDS['reconnect'][0],('doctor','--reconnect'))
        from agentj import cli
        self.assertIn('--reconnect',Path(cli.__file__).read_text())

class HostMetadata(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.base=Base();self.base.setUp();self.addCleanup(self.base.doCleanups);self.host=self.base.host
    async def test_only_explicit_system_repair_metadata_can_mark_a_notice(self):
        before=self.host.st.config_path.read_bytes()
        tid=self.host.agent_notice('Synthetic system update.',repair='reconnect')
        src=self.host.hist.get(tid)['src'];self.assertEqual(src['k'],'sys');self.assertEqual(src['name'],'AgentJ')
        self.assertEqual(src['repair'],'reconnect')
        tid=self.host.agent_notice('Agent prose says agentj doctor --reconnect.')
        self.assertNotIn('repair',self.host.hist.get(tid)['src'])
        self.assertEqual(self.host.st.config_path.read_bytes(),before)
    async def test_only_final_update_card_has_fixed_repair_and_preserves_owner_name(self):
        self.host.cmd_card('update',slash.Result('Synthetic completion.','ok'),by='Owner custom')
        page=list(self.host.hist.turns.values())[-1]
        self.assertEqual(page['src']['k'],'cmd');self.assertEqual(page['src']['name'],'Owner custom')
        self.assertEqual(page['card']['repair'],'reconnect');self.assertEqual(page['card']['system_name'],'AgentJ')
        self.host.cmd_card('help',slash.Result('agentj doctor --reconnect','ok'))
        self.assertNotIn('repair',list(self.host.hist.turns.values())[-1]['card'])

    async def test_actual_upgrade_marker_notice_writes_fixed_metadata(self):
        import time
        from unittest.mock import patch,AsyncMock
        from agentj import __version__
        rec={'from':'0.28.2','to':__version__,'at':int(time.time())}
        self.host.st.write_private(self.host.st.root/update.UPGRADED,json.dumps(rec).encode())
        with patch('agentj.service.sweep_jobs',return_value=[]),patch('agentj.doctor.run',return_value=[{'id':'version','status':'ok'}]),patch.object(self.host,'_receipt_context',new=AsyncMock(return_value=('same','synthetic-host'))):
            await self.host.upgraded_notice()
        page=list(self.host.hist.turns.values())[-1]
        self.assertEqual(page['src']['k'],'sys');self.assertEqual(page['src']['repair'],'reconnect')
        self.assertEqual(page['src']['name'],'AgentJ')

    async def test_real_history_restore_to_host_hist_get_wire_keeps_repair_metadata(self):
        import asyncio
        from types import SimpleNamespace as NS
        from agentj import history,serve,wire
        tid=self.host.agent_notice('Synthetic upgrade result.',repair='reconnect')
        self.host.cmd_card('update',slash.Result('Synthetic failure.','error'),by='Owner custom')
        cmd_id=list(self.host.hist.turns)[-1]
        self.host.hist=history.History(self.host.st)
        self.assertEqual(self.host.hist.get(tid)['src']['repair'],'reconnect')
        self.assertEqual(self.host.hist.get(cmd_id)['card']['repair'],'reconnect')
        messages=[]
        def encrypt(ad,plain):messages.append(wire.unpad_json(plain));return b'synthetic-ciphertext'
        session=self.base.s;session.p33=True;session.send=NS(encrypt=encrypt)
        async def op(*args):pass
        self.host._op=op
        self.host.send_app=serve.Host.send_app.__get__(self.host,serve.Host)
        await self.host._app(session,{'t':'hist_get','r':'0123456789abcdef'})
        page=next(m for m in messages if m.get('t')=='hist_page')
        turns={turn['id']:turn for turn in page['turns']}
        self.assertEqual(turns[tid]['src']['repair'],'reconnect')
        self.assertEqual(turns[tid]['src']['name'],'AgentJ')
        self.assertEqual(turns[cmd_id]['card']['repair'],'reconnect')
        self.assertEqual(turns[cmd_id]['src']['name'],'Owner custom')
