"""Protocol stand-ins run installed native guards; a denial cannot execute a tool."""
import _hermetic
from _testtmp import TEST_TMP
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock
from agentj import main_opencode_hook, shared_hook, shared_risk


class StandinGuard(unittest.IsolatedAsyncioTestCase):
    async def test_generated_loaders_keep_real_protected_path_denial(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as td:
            root=Path(td);cwd=root/'project';cwd.mkdir()
            perm=root/'state'/'agentperm';perm.mkdir(parents=True)
            host=NS(st=NS(root=root/'state',perm_dir=perm),ask=AsyncMock(return_value={'behavior':'deny'}),stopped=lambda:False)
            for family in ('codex','opencode'):
                with self.subTest(family=family):
                    adapter=NS(kind=family,cfg={'dir':str(cwd)},host=host,tid='native',sid='native',proc=NS(pid=os.getpid()))
                    channel=shared_hook.Channel(shared_risk.NativePathGuard(adapter),shared_hook.channel_path(perm,family))
                    await channel.start()
                    try:
                        args=[]
                        if family=='codex':
                            hooks=shared_hook.settings(channel.path,('PreToolUse',))['hooks']['PreToolUse']
                            # Same real -c hook syntax as the adapter's generated argv.
                            command=hooks[0]['hooks'][0]['command']
                            args=['-c','hooks.PreToolUse=[{hooks=[{type="command", command='+json.dumps(command)+'}]}]']
                            call="f.codex_pretool('Write',inp,'native',cwd)"
                        else:
                            main_opencode_hook.install(cwd,channel.path)
                            call="f.opencode_event(cwd,'PreToolUse','native','Write',inp)"
                        for protected in (True,False):
                            host.ask.reset_mock()
                            target=root/'state'/'settings.json' if protected else cwd/'ordinary.py'
                            code="import _native_hook_fixture as f; inp="+repr({'file_path':str(target),'content':'changed'})+"; cwd="+repr(str(cwd))+"; print("+call+")"
                            proc=await asyncio.create_subprocess_exec(sys.executable,'-c',code,*args,
                                cwd=Path(__file__).resolve().parent,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                            out,err=await asyncio.wait_for(proc.communicate(),10)
                            self.assertEqual(proc.returncode,0,err.decode())
                            self.assertEqual(out.decode().strip(),str(not protected))
                            if protected:host.ask.assert_awaited_once()
                            else:host.ask.assert_not_awaited()
                    finally:await channel.stop()
