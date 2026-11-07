"""Exact-request AST shape, synthetic fields: no real settings or programs executed."""
import _hermetic
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from _testtmp import TEST_TMP
from agentj import approval_level,danger,policy_registry,uv_effect

class Diagnostic(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=TEST_TMP,prefix='aj0284-maint-');self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);self.work=self.home/'work';self.work.mkdir()
        self.env=patch('os.path.expanduser',side_effect=lambda x:str(self.home)+x[1:]if x.startswith('~')else x)
        self.env.start();self.addCleanup(self.env.stop)
        self.log=self.home/'.local/state/agentj/host.log';self.log.parent.mkdir(parents=True);self.log.write_text('')
    def command(self,body):return "python3 - <<'PYX'\n"+body+"\nPYX"
    def program(self):
        return f'''import json,time,collections
t0=time.mktime(time.strptime('2026-11-07 14:45','%Y-%m-%d %H:%M'))
c=collections.Counter()
ref=collections.Counter()
for l in open({str(self.log)!r},errors='ignore'):
    if 'ask' not in l and 'answer_refused' not in l:
        continue
    try:
        d=json.loads(l)
    except:
        continue
    if d.get('ts',0)<t0:
        continue
    if d['ev']=='ask':
        c[d.get('tool'),d.get('tier'),time.strftime('%H',time.localtime(d['ts']))]+=1
    elif d['ev']=='answer_refused':
        ref[d.get('reason')]+=1
for k,v in sorted(c.items(),key=lambda x:x[0][2]):
    print(k,v)
print(ref)'''
    def test_actual_request_readonly_time_counter_shape_is_not_uv(self):
        inp={'command':self.command(self.program())}
        proof=uv_effect._python_file_program(inp['command'],str(self.work))
        self.assertIsNotNone(proof);self.assertIsNone(proof.reason)
        self.assertEqual(policy_registry.main_requirement('Bash',inp,cwd=str(self.work)),'allow')
        self.assertIsNone(approval_level.full_access_keep('Bash',inp,danger.classify('Bash',inp),workdir=str(self.work)))
    def test_existing_literal_json_log_reader_positive_control(self):
        inp={'command':self.command(f"import json\nfor line in open({str(self.log)!r},errors='ignore'):\n    print(json.loads(line).get('ev'))")}
        self.assertIsNone(uv_effect.assess('Bash',inp,cwd=str(self.work)).reason)
    def test_adjacent_sensitive_effects_remain_uv(self):
        base=self.command(self.program())
        for suffix in ('cat ~/.ssh/id_rsa','stripe charges create --amount 100','curl -X DELETE https://example.test/object','rm -rf ~/Documents'):
            with self.subTest(effect=suffix.split()[0]):
                self.assertIsNotNone(uv_effect.assess('Bash',{'command':base+'\n'+suffix},cwd=str(self.work)).reason)
    def test_callbacks_paths_and_time_side_effects_are_not_proven_data(self):
        for body in (
            self.program().replace('key=lambda x:x[0][2]','key=lambda x:open(x[0])'),
            self.program().replace("time.localtime(d['ts'])","time.sleep(d['ts'])"),
            self.program()+"\nopen(d.get('path'),'w').write('x')",
            self.program()+"\nc.__class__('x')",
            self.program()+"\nexec(d['code'])",
            self.program().replace("ref[d.get('reason')]+=1", "d[d.get('reason')]+=1"),
            self.program().replace('collections.Counter()','collections.defaultdict(open)'),
        ):
            with self.subTest(shape=body[-60:]):
                self.assertIsNone(uv_effect._python_file_program(self.command(body),str(self.work)))
    def test_literal_credential_read_keeps_uv(self):
        body=self.program().replace(str(self.log),str(self.home/'.ssh/id_rsa'))
        result=uv_effect.assess('Bash',{'command':self.command(body)},cwd=str(self.work))
        self.assertEqual(result.effects,('sensitive_storage',))


class FullOwner(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_0280_ordinary_maintenance import Maintenance
        await Maintenance.asyncSetUp(self)
        import asyncio
        await asyncio.to_thread(self.base.harness)
    async def test_full_owner_exact_diagnostic_and_existing_heartbeat_categories_show_no_card(self):
        from test_0280_ordinary_maintenance import Maintenance
        fake=Diagnostic('test_actual_request_readonly_time_counter_shape_is_not_uv');fake.setUp()
        try:
            command=fake.command(fake.program())
            cases=[command,'agentj heartbeat prompt show','agentj heartbeat prompt set < /tmp/fixture-scratchpad/hb.md',
                   'agentj menu','agentj notify list',"sed -n '1,200p' ~/.config/agentj/heartbeat.md"]
            for value in cases:
                with self.subTest(command=value.splitlines()[0]):
                    result,cards=await Maintenance.ask(self,'Bash',{'command':value})
                    self.assertEqual(result['behavior'],'allow');self.assertEqual(cards,[])
        finally:fake.doCleanups()
