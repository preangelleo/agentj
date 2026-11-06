"""Bounded terminal protocol replies never become shell input or held typeahead."""
import _hermetic  # noqa: F401
import asyncio
import types
import unittest
from unittest import mock
from agentj import term_pty

COLOR = '\x1b]11;rgb:2c2c/2b2b/2626\x1b\\'

class Replies(unittest.TestCase):
    def rig(self, **fields):
        desk = term_pty.PtyDesk()
        desk.clock = lambda: 1
        desk.owner_acted = mock.Mock()
        desk.refused = mock.Mock()
        desk.shell_front = mock.Mock(return_value=False)
        desk._pty_write = mock.Mock(return_value=True)
        desk.program_front = mock.Mock(return_value=True)
        x = types.SimpleNamespace(kind='pty', in_win=1, in_bytes=0, in_note=False, last=0,
            io=None, at_prompt=False, hold='after', closing=False, disconnected_at=None,
            typed='', pending='', enter_seq=20)
        for k, v in fields.items(): setattr(x,k,v)
        return desk, x

    def send(self, desk, x, data=COLOR, **extra):
        asyncio.run(desk.on_term_in(x, types.SimpleNamespace(device='fixture'), {'d':data,'reply':True,**extra}))

    def test_reply_during_launch_is_immediate_and_does_not_consume_pending(self):
        desk,x=self.rig(pending='owner typeahead')
        self.send(desk,x)
        desk._pty_write.assert_called_once_with(x,COLOR)
        self.assertEqual((x.pending,x.typed,x.enter_seq,x.hold),('owner typeahead','',20,'after'))
        desk.owner_acted.assert_not_called()
        self.assertEqual(x.last,0)

    def test_grammar_and_index_are_strict(self):
        for data in [COLOR, '\x1b]4;255;rgb:ffff/0/123\x07', '\x1b]10;rgb:ffff/ffff/ffff\x1b\\',
                     '\x1b]12;rgb:1/2/3\x07', '\x1b[1;80R','\x1b[?24;80R', '\x1b[0n']:
            desk,x=self.rig();self.send(desk,x,data);desk._pty_write.assert_called_once_with(x,data)
        for data in [COLOR+'touch bad\r','x'+COLOR,COLOR+COLOR,'\x1b]4;256;rgb:1/2/3\x07',
                     '\x1b]52;c;abc\x07','\x1b]11;rgb:1/2/3\x07\r','\x1b[0;1R',
                     '\x1b]11;rgb:11111/1/1\x07','\x1b[99999;1R','\x1b[1;1R\r','\x1b[1n','\x1b[0nBAD','\x1b[0N','\x1b[1;1r']:
            desk,x=self.rig();self.send(desk,x,data);desk._pty_write.assert_not_called();self.assertEqual(x.pending,'')

    def test_no_reply_at_prompt_or_confirmation_or_unknown_front(self):
        for f in [{'at_prompt':True},{'hold':'confirm'},{'hold':'query'},{'closing':True},{'disconnected_at':1}]:
            desk,x=self.rig(**f);self.send(desk,x);desk._pty_write.assert_not_called();self.assertEqual(x.pending,'')
        desk,x=self.rig();desk.program_front.return_value=False;self.send(desk,x);desk._pty_write.assert_not_called()

    def test_reply_marker_must_be_boolean_true(self):
        desk,x=self.rig();self.send(desk,x,reply=1);desk._pty_write.assert_not_called();self.assertEqual(x.pending,'')

    def test_program_front_unknown_fails_closed(self):
        desk=term_pty.PtyDesk()
        x=types.SimpleNamespace(io=types.SimpleNamespace(fd=9),proc=types.SimpleNamespace(pid=44),shell='bash')
        with mock.patch.object(term_pty.os,'tcgetpgrp',side_effect=OSError): self.assertFalse(desk.program_front(x))
        with mock.patch.object(term_pty.os,'tcgetpgrp',return_value=45): self.assertTrue(desk.program_front(x))
        with mock.patch.object(term_pty.os,'tcgetpgrp',return_value=44),mock.patch.object(term_pty,'proc_name',return_value=None): self.assertFalse(desk.program_front(x))
        with mock.patch.object(term_pty.os,'tcgetpgrp',return_value=44),mock.patch.object(term_pty,'proc_name',return_value='bash'): self.assertFalse(desk.program_front(x))
        with mock.patch.object(term_pty.os,'tcgetpgrp',return_value=44),mock.patch.object(term_pty,'proc_name',return_value='nvim'): self.assertTrue(desk.program_front(x))

from test_p408_terminal import Rig
import test_p0269_pty as pty_tests
PY = pty_tests.PY
import os
import shutil

@unittest.skipUnless(term_pty.pty_ok() and shutil.which('bash'), 'PTY bash required')
class LiveReply(Rig):
    open_pty = pty_tests.Pty.open_pty
    typed = pty_tests.Pty.typed
    out = pty_tests.Pty.out
    script = pty_tests.Pty.script

    def setUp(self):
        super().setUp()
        self.desk.pty_ok = True
        self.desk.pty_shell = 'bash'
        self.k = 0

    def test_color_query_receives_reply_while_typeahead_hold_remains(self):
        self.passkey();self.enable()
        self.script('query.py', "import os,tty,select\ntty.setraw(0)\nos.write(1,b'COLOR_QUERY \\x1b]11;?\\x07')\n"
                    "ready,_,_=select.select([0],[],[],0.25)\n"
                    "b=os.read(0,128) if ready else b''\n"
                    "os.write(1,b'COLOR_OK ' if b==b'\\x1b]11;rgb:2c2c/2b2b/2626\\x1b\\\\' else b'COLOR_TIMEOUT ')\n")
        async def go():
            await self.open_pty()
            x=self.desk.term
            # Keep the ordinary typeahead wait deterministic and longer than the child's query deadline.
            with mock.patch.object(term_pty,'AFTER_ENTER',1):
                n=len(self.sent)
                await self.typed('./query.py\r')
                await self.until(lambda:'COLOR_QUERY' in self.out(n))
                self.assertEqual(x.hold,'after')
                self.assertFalse(x.at_prompt)
                self.assertTrue(self.desk.program_front(x))
                await self.desk.on_phone(self.s,{'t':'term_in','id':x.id,'seq':123,'d':COLOR,'reply':True})
                await self.until(lambda:'COLOR_OK' in self.out(n) or 'COLOR_TIMEOUT' in self.out(n))
                self.assertIn('COLOR_OK',self.out(n))
                self.assertNotIn('COLOR_TIMEOUT',self.out(n))
                self.assertEqual(x.pending,'')
        self.run_async(go)

    @unittest.skipUnless(shutil.which('nvim'), 'requires real nvim')
    def test_real_nvim_detects_light_and_dark_via_native_background_queries(self):
        import re
        async def go():
            await self.open_pty()
            x=self.desk.term
            for theme, rgb in [('light','fafa/f9f9/f5f5'),('dark','2c2c/2b2b/2626')]:
                proof=self.work / ('background-'+theme+'.txt')
                n=len(self.sent)
                await self.typed('nvim --clean -i NONE -n\r')
                colors = statuses = 0
                stop = False
                async def respond():
                    nonlocal colors, statuses
                    seen = 0
                    pattern = re.compile(r'\x1b\]11;\?(?:\x07|\x1b\\)|\x1b\[5n')
                    while not stop:
                        for query in pattern.finditer(self.out(n), seen):
                            if query.group() == '\x1b[5n':
                                data = '\x1b[0n'
                                statuses += 1
                            else:
                                data = '\x1b]11;rgb:'+rgb+'\x1b\\'
                                colors += 1
                            await self.desk.on_phone(self.s,{'t':'term_in','id':x.id,'seq':800,'reply':True,'d':data})
                            seen = query.end()
                        await asyncio.sleep(.003)
                responder = asyncio.create_task(respond())
                await self.until(lambda:colors and statuses and x.alt)
                await asyncio.sleep(.1)
                await self.typed(':call writefile([&background], "'+str(proof)+'")\r')
                await self.until(lambda:proof.exists(),5)
                for _ in range(20):
                    if proof.read_text().strip() == theme:
                        break
                    await asyncio.sleep(.05)
                    proof.unlink()
                    await self.typed(':call writefile([&background], "'+str(proof)+'")\r')
                    await self.until(lambda:proof.exists(),1)
                self.assertEqual(proof.read_text().strip(),theme)
                self.assertNotIn('E1568',self.out(n))
                await self.typed(':qa!\r')
                await self.until(lambda:x.at_prompt and not x.alt)
                stop = True
                await responder
        self.passkey();self.enable();self.run_async(go)
