"""0.28.2 owner maintenance regressions: classify only, never execute commands."""
import _hermetic
import asyncio
import json
import unittest
from unittest.mock import patch
from pathlib import Path
from test_0280_ordinary_maintenance import Maintenance


class Replay(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Maintenance.asyncSetUp(self)
        await asyncio.to_thread(self.base.harness)
        (self.home/'bin/agentj-report-closeout').write_text('#!/bin/sh\nprintf done\n')
        (self.home/'.config/systemd/user/daily-video-publish.service').write_text('[Service]\nExecStart=%h/bin/agentj-report-closeout\n')
        (self.home/'.config/systemd/user/daily-publish-closeout.service').write_text('[Service]\nExecStart=%h/bin/agentj-report-closeout\n')
        (self.home/'.config/systemd/user/daily-publish-closeout.timer').write_text('[Timer]\nOnCalendar=daily\n')
    ask = Maintenance.ask

    async def test_today_categories_do_not_open_uv(self):
        h = str(self.home)
        fresh=Path(self.base.tmp.name)/'fresh-owner-test'
        fresh.mkdir();(fresh/'result.txt').write_text('synthetic test output')
        cases = [
            ('Bash', {'command': 'rm -rf -- '+str(self.old)}),
            ('Write', {'file_path': h+'/bin/agentj-report-closeout', 'content': '#!/bin/sh\nprintf done\n'}),
            ('Write', {'file_path': h+'/.config/systemd/user/daily-publish-closeout.service', 'content': '[Service]\nExecStart=%h/bin/agentj-report-closeout\n'}),
            ('Write', {'file_path': h+'/.config/systemd/user/daily-publish-closeout.timer', 'content': '[Timer]\nOnCalendar=daily\n'}),
            ('Bash', {'command': 'systemctl --user daemon-reload && systemctl --user enable --now daily-publish-closeout.timer'}),
            ('Edit', {'file_path': str(self.handoff/'compact.md'), 'old_string': 'before', 'new_string': 'after'}),
            ('Bash', {'command': 'systemd-run --user --collect --unit=agentj-ceo codex exec --dangerously-bypass-approvals-and-sandbox -C /tmp "write the report"'}),
            ('Bash', {'command': 'systemctl --user start daily-video-publish'}),
            ('exec_command', {'cmd': 'systemctl --user start daily-video-publish.service'}),
            ('Bash', {'command': 'systemctl --user set-environment DVP_MAKEUP_DATE=2026-11-08 DVP_CLOSEOUT=1'}),
            ('Bash', {'command': 'rm -rf -- '+str(fresh)}),
        ]
        for index, (tool, inp) in enumerate(cases):
            with self.subTest(category=index):
                result, cards = await self.ask(tool, inp)
                self.assertEqual(result['behavior'], 'allow')
                self.assertEqual(cards, [])

    async def test_environment_secret_and_adjacent_effects_keep_uv(self):
        for command in (
            'systemctl --user set-environment API_TOKEN=synthetic',
            'systemctl --user set-environment DVP_CLOSEOUT=$API_TOKEN',
            'systemctl --user set-environment DVP_CLOSEOUT=$(cat ~/.ssh/id_rsa)',
            'systemctl --user start daily-video-publish; cat ~/.ssh/id_rsa',
            'systemctl --user start daily-video-publish; rm -rf ~/Documents',
            'systemctl --user start daily-video-publish; stripe charges create --amount 100',
            'systemctl --user set-environment DVP_CLOSEOUT=1; curl -X DELETE https://example.test/objects/test',
        ):
            with self.subTest(shape=command.split(';')[0].split('=')[0]):
                result, cards = await self.ask('Bash', {'command': command})
                self.assertNotEqual(result['behavior'], 'allow')
                self.assertTrue(cards and cards[-1]['passkey_required'])

    async def test_host_unit_and_unsafe_environment_never_borrow_routine(self):
        from agentj import ordinary_maintenance
        for command in ('systemctl --user start agentj', 'systemctl --user start agentj.service',
                        'systemctl --user set-environment PATH=/tmp',
                        'systemctl --user import-environment',
                        'systemctl --user set-environment DVP_CLOSEOUT',
                        'systemctl --user start daily-video-publish --system'):
            with self.subTest(shape=command):
                self.assertFalse(ordinary_maintenance.proven('Bash', {'command': command}, cwd=str(self.home)))

    async def test_new_ordinary_operations_still_require_verified_source(self):
        self.host.agent.cur_send = None
        result, cards = await self.ask('Bash', {'command': 'systemctl --user start daily-video-publish'})
        # 0.30.7 ADR-0307-offlist: an off-list request never opens a card; with no queued Send the host-held source is
        # the local terminal (owner trust), so it is answered at once by trust — allow, audited as off_list.
        from agentj import approval_offlist, approvals
        self.assertEqual(approval_offlist.source(self.host)['trust'], 'owner')
        self.assertEqual(result['behavior'], 'allow')
        self.assertEqual(cards, [])
        self.assertEqual(approvals.read_log(self.host.st)[-1]['reason'], 'off_list')

    async def test_new_test_directory_does_not_hide_real_or_sensitive_data(self):
        from agentj import ordinary_maintenance
        root=Path(self.base.tmp.name)
        real=root/'personal-notes';real.mkdir();(real/'notes.txt').write_text('real owner data')
        sensitive=root/'sensitive-test';sensitive.mkdir();(sensitive/'auth.json').write_text('{}')
        linked=root/'linked-test';linked.mkdir();(linked/'alias').symlink_to(real,target_is_directory=True)
        for path in (real,sensitive,linked):
            with self.subTest(category=path.name):
                self.assertFalse(ordinary_maintenance.proven('Bash',{'command':'rm -rf -- '+str(path)},cwd=str(root)))

    async def test_sensitive_units_do_not_borrow_transport(self):
        root=self.home/'.config/systemd/user'
        for body in ('[Service]\nExecStart=/usr/bin/cat ~/.ssh/id_rsa\n',
                     '[Service]\nEnvironment=API_TOKEN=synthetic\nExecStart=/usr/bin/true\n'):
            result,cards=await self.ask('Write',{'file_path':str(root/'new-reader.service'),'content':body})
            self.assertNotEqual(result['behavior'],'allow')
            self.assertTrue(cards and cards[-1]['passkey_required'])
            (root/'fixture-reader.service').write_text(body)
            result,cards=await self.ask('Bash',{'command':'systemctl --user start fixture-reader'})
            self.assertNotEqual(result['behavior'],'allow')
            self.assertTrue(cards and cards[-1]['passkey_required'])
        result,cards=await self.ask('Bash',{'command':'systemctl --user start keyring'})
        self.assertNotEqual(result['behavior'],'allow')
        self.assertTrue(cards and cards[-1]['passkey_required'])

    async def test_complete_unit_load_roots_and_script_effects(self):
        roots=[self.home/'.local/share/systemd/user',Path(self.base.tmp.name)/'runtime/systemd/generator']
        from agentj import ordinary_maintenance
        for root in roots:
            root.mkdir(parents=True,exist_ok=True)
            (root/'ordinary-report.service').write_text('[Service]\nExecStart=/usr/bin/cat %h/.ssh/id_rsa\n')
            with patch.object(ordinary_maintenance,'_unit_roots',return_value=((str(root),),False)):
                result,_=await self.ask('Bash',{'command':'systemctl --user start ordinary-report'})
                # 0.30.7 ADR-0307-offlist: the maintenance proof still refuses this unit (its subject), but an off-list
                # owner request no longer cards — what a launched program reads is outside any command-line gate
                # (documented residual: owner / host off-list = the owner's harness with full access).
                self.assertFalse(ordinary_maintenance.proven('Bash',{'command':'systemctl --user start ordinary-report'},cwd=str(self.home)))
                self.assertEqual(result['behavior'],'allow')
        script=self.home/'bin/ordinary-report'
        script.write_text('#!/bin/sh\ncat "$HOME/.ssh/id_rsa"\n')
        (self.home/'.config/systemd/user/ordinary-report.service').write_text('[Service]\nExecStart=%h/bin/ordinary-report\n')
        result,_=await self.ask('Bash',{'command':'systemctl --user start ordinary-report'})
        self.assertFalse(ordinary_maintenance.proven('Bash',{'command':'systemctl --user start ordinary-report'},cwd=str(self.home)))
        self.assertEqual(result['behavior'],'allow')     # 0.30.7 ADR-0307-offlist residual (see above)

    async def test_sealed_ordinary_dispatch_script_and_replacement(self):
        from agentj import ordinary_maintenance
        script=self.home/'bin/ordinary-ceo'
        script.write_text('#!/bin/sh\nsystemd-run --user --collect --unit=fixture-ceo codex exec --dangerously-bypass-approvals-and-sandbox fixture-task\n')
        (self.home/'.config/systemd/user/ordinary-ceo.service').write_text('[Service]\nExecStart=%h/bin/ordinary-ceo\n')
        inp={'command':'systemctl --user start ordinary-ceo'}
        proof=ordinary_maintenance.qualify('Bash',inp,cwd=str(self.home))
        self.assertIsNotNone(proof)
        self.assertTrue(ordinary_maintenance.still_valid(proof))
        script.write_text('#!/bin/sh\ncat "$HOME/.ssh/id_rsa"\n')
        self.assertFalse(ordinary_maintenance.still_valid(proof))

    async def test_unknown_linked_and_custom_timer_targets_abstain(self):
        from agentj import ordinary_maintenance
        unit=self.home/'.config/systemd/user/alias-fixture.service'
        target=self.home/'.config/systemd/user/daily-video-publish.service'
        unit.symlink_to(target)
        (unit.parent/'custom-fixture.timer').write_text('[Timer]\nUnit=keyring.service\n')
        for name in ('alias-fixture','custom-fixture.timer','unresolvable-fixture'):
            self.assertFalse(ordinary_maintenance.proven('Bash',{'command':'systemctl --user start '+name},cwd=str(self.home)))

    async def test_reviewed_dispatch_profile_binds_source_prompt_registry(self):
        from agentj import ordinary_maintenance as o
        work=self.home/'project';work.mkdir()
        prompt=self.home/'prompts/input.md';prompt.parent.mkdir();prompt.write_text('Dispatch the registered workflow.')
        helper=self.home/'bin/codex-run';helper.write_text('#!/bin/sh\nprintf fixture\n')
        script=self.home/'bin/reviewed-autopilot'
        body='#!/usr/bin/env bash\nWORK="$HOME/project"\nPROMPT="$HOME/prompts/input.md"\nexport PATH="$HOME/bin:/usr/bin:/bin"\n"$HOME/bin/codex-run" -- -C "$WORK" - < "$PROMPT"\n'
        script.write_text(body)
        unit=self.home/'.config/systemd/user/reviewed-fixture.service'
        unit.write_text('[Service]\nWorkingDirectory=%h/project\nExecStart=%h/bin/reviewed-autopilot\n')
        registry=self.home/'.workflow-registry/registry.json';registry.parent.mkdir()
        row={'name':'reviewed-fixture','home':'~/project','unit':'reviewed-fixture.timer','kind':'systemd-timer','runtime':'codex exec'}
        registry.write_text(json.dumps({'automations':[row]}))
        profile={'name':row['name'],'launcher':o._digest(body),'unit':o._digest(unit.read_bytes()),'work':o._digest('project'),
                 'files':{o._digest(str(p.relative_to(self.home))):o._digest(p.read_bytes()) for p in (script,prompt,helper)}}
        inp={'command':'systemctl --user start reviewed-fixture'}
        with patch.object(o,'REVIEWED_DISPATCH',profile),patch.object(o,'_dispatch_runtime',return_value=True):
            proof=o.qualify('Bash',inp,cwd=str(work))
            self.assertIsNotNone(proof);self.assertTrue(o.still_valid(proof))
            self.assertFalse(o._unit_exec(str(script)+' --unreviewed-argument',str(work)))
            result,cards=await self.ask('Bash',inp)
            self.assertEqual(result['behavior'],'allow');self.assertEqual(cards,[])
            for file,changed in ((script,body+'cat ~/.ssh/id_rsa\n'),(prompt,'Injected changed prompt'),(helper,'#!/bin/sh\ncat ~/.ssh/id_rsa\n'),
                                 (registry,json.dumps({'automations':[{**row,'home':'~/elsewhere'}]}))):
                original=file.read_text();file.write_text(changed)
                self.assertIsNone(o.qualify('Bash',inp,cwd=str(work)))
                self.assertFalse(o.still_valid(proof))
                file.write_text(original)
            with patch.object(o,'_dispatch_runtime',return_value=False):
                self.assertIsNone(o.qualify('Bash',inp,cwd=str(work)))

    async def test_dispatch_runtime_rejects_startup_or_path_shadow(self):
        from agentj import ordinary_maintenance as o
        from types import SimpleNamespace
        body='export PATH="$HOME/bin:/usr/bin:/bin"\n'
        response=SimpleNamespace(returncode=0,stdout='')
        for assignment in ('BASH_ENV=/synthetic-fixture','SHELLOPTS=xtrace','BASHOPTS=extdebug','PS4=fixture','LD_PRELOAD=/synthetic-fixture','DYLD_INSERT_LIBRARIES=/synthetic-fixture'):
            response.stdout='PATH=/usr/bin:/bin\n'+assignment+'\n'
            with patch.object(o.subprocess,'run',return_value=response):
                self.assertFalse(o._dispatch_runtime(body,str(self.home)))
        (self.home/'bin/bash').write_text('#!/bin/sh\nprintf fixture\n');(self.home/'bin/bash').chmod(0o755)
        response.stdout='PATH='+str(self.home/'bin')+':/usr/bin:/bin\n'
        with patch.object(o.subprocess,'run',return_value=response):
            self.assertFalse(o._dispatch_runtime(body,str(self.home)))
