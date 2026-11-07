"""P429 C4: patch edits are fenced, scoped and approved as a single tool call."""
import _hermetic  # noqa: F401
import asyncio
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from agentj.core import pipeline, prompt, tools


def patch(body):
    return '*** Begin Patch\n' + body + '\n*** End Patch\n'


class Patch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def runpatch(self, body):
        return tools.run(str(self.root), 'apply_patch', {'patch': patch(body)})

    def test_add_update_move_delete_and_unicode(self):
        self.runpatch('*** Add File: sub/a.txt\n+标题\n+one\n+two')
        self.runpatch('*** Update File: sub/a.txt\n*** Move to: sub/b.txt\n@@ 标题\n-one\n+three\n two\n*** End of File')
        self.assertFalse((self.root / 'sub/a.txt').exists())
        self.assertEqual((self.root / 'sub/b.txt').read_text(), '标题\nthree\ntwo\n')
        self.runpatch('*** Update File: sub/b.txt\n*** Move to: renamed.txt')
        self.assertEqual((self.root / 'renamed.txt').read_text(), '标题\nthree\ntwo\n')
        self.runpatch('*** Delete File: renamed.txt')
        self.assertFalse((self.root / 'renamed.txt').exists())

    def test_all_changes_validated_before_any_mutation(self):
        (self.root / 'a').write_text('original\n')
        for bad in ('*** Update File: a\n@@\n-not there\n+bad',
                    '*** Add File: a\n+overwrite', '*** Delete File: missing'):
            with self.subTest(bad=bad), self.assertRaises(tools.ToolError):
                self.runpatch('*** Add File: new\n+new\n' + bad)
            self.assertFalse((self.root / 'new').exists())
            self.assertEqual((self.root / 'a').read_text(), 'original\n')

    def test_strict_grammar_and_no_partial_parse(self):
        bodies = ['', '*** Update File: a', '*** Delete File: a\n+bad',
                  '*** Add File: a\nplain text', '*** Add File: a\n+x\n*** Add File: a\n+y',
                  '*** Update File: a\n@@\n-x\n+y\n*** Move to: b']
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(tools.ToolError):
                self.runpatch(body)
        with self.assertRaises(tools.ToolError):
            tools.run(str(self.root), 'apply_patch', {'patch': patch('*** Add File: a\n+x') + 'trailing'})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_paths_symlinks_and_destinations(self):
        (self.root / 'a').write_text('x\n')
        (self.root / 'link').symlink_to(self.root / 'a')
        (self.root / 'dirlink').symlink_to(self.root, target_is_directory=True)
        for path in ('../escape', '/tmp/p429-escape', 'link', 'dirlink/b', 'a/../b', '.', 'x\\y'):
            with self.subTest(path=path), self.assertRaises(tools.ToolError):
                self.runpatch('*** Add File: ' + path + '\n+y')
        (self.root / 'b').write_text('keep\n')
        with self.assertRaises(tools.ToolError):
            self.runpatch('*** Update File: a\n*** Move to: b\n@@\n-x\n+y')
        self.assertEqual((self.root / 'b').read_text(), 'keep\n')

    def test_ambiguous_context_is_actionable_and_no_eof_newline(self):
        (self.root / 'a').write_text('x\nx\n')
        with self.assertRaisesRegex(tools.ToolError, 'unique'):
            self.runpatch('*** Update File: a\n@@\n-x\n+y')
        (self.root / 'a').write_text('x')
        self.runpatch('*** Update File: a\n@@\n-x\n+y\n*** End of File')
        self.assertEqual((self.root / 'a').read_text(), 'y')

    def test_gate_scope_research_and_unknown_args(self):
        for args in ({}, {'patch': []}, {'patch': 'bad'}):
            ok, _ = asyncio.run(pipeline.Gate(str(self.root)).check('apply_patch', args))
            self.assertFalse(ok)
        text = patch('*** Update File: src/a\n*** Move to: other/b\n@@\n-x\n+y')
        gate = pipeline.Gate(str(self.root), scope_write=['src/**'])
        ok, why = asyncio.run(gate.check('apply_patch', {'patch': text}))
        self.assertFalse(ok)
        self.assertIn('scope', why)
        ok, _ = asyncio.run(pipeline.Gate(str(self.root), research=True).check('apply_patch', {'patch': text}))
        self.assertFalse(ok)

    def test_gate_delete_credentials_deny_shared_and_exact_diff(self):
        text = patch('*** Delete File: a\n*** Add File: b\n+x')
        denied, seen = set(), []

        async def ask(tool, inp, who):
            seen.append((tool, inp, who))
            return {'behavior': 'deny'}

        gate = pipeline.Gate(str(self.root), ask, denied=denied)
        self.assertFalse(asyncio.run(gate.check('apply_patch', {'patch': text}, 'host-origin'))[0])
        self.assertEqual(seen[0][0], 'Delete')
        self.assertEqual(seen[0][1]['patch'], text)
        self.assertEqual(seen[0][2], 'host-origin')
        self.assertFalse(asyncio.run(gate.check('apply_patch', {'patch': text}))[0])
        self.assertEqual(len(seen), 1)
        cred = patch('*** Add File: .env\n+SYNTHETIC=value')
        self.assertFalse(asyncio.run(pipeline.Gate(str(self.root)).check('apply_patch', {'patch': cred}))[0])

        async def allow(tool, inp, who):
            seen.append((tool, inp, who))
            return {'behavior': 'allow'}

        gate = pipeline.Gate(str(self.root), allow)
        self.assertTrue(asyncio.run(gate.check('apply_patch', {'patch': cred}))[0])
        self.assertEqual(seen[-1][0], 'Write')
        self.assertEqual(seen[-1][1]['file_path'], str(self.root / '.env'))

    def test_toolbox_process_executes_patch_and_multiple_sections(self):
        from agentj.core.toolbox import Toolbox
        tb = Toolbox(str(self.root), fenced=False)
        try:
            ok, out = tb.call('apply_patch', {'patch': patch('*** Add File: a\n+head\n+x\n+middle\n+y\n+tail')})
            self.assertTrue(ok, out)
            ok, out = tb.call('apply_patch', {'patch': patch('*** Update File: a\n@@ head\n-x\n+X\n@@ middle\n-y\n+Y')})
            self.assertTrue(ok, out)
        finally:
            tb.close()
        self.assertEqual((self.root / 'a').read_text(), 'head\nX\nmiddle\nY\ntail\n')

    def test_mixed_delete_add_update_and_preserved_mode(self):
        (self.root / 'a').write_text('x\n')
        (self.root / 'a').chmod(0o755)
        (self.root / 'b').write_text('delete\n')
        self.runpatch('*** Delete File: b\n*** Add File: c\n+new\n*** Update File: a\n@@\n-x\n+y')
        self.assertFalse((self.root / 'b').exists())
        self.assertEqual((self.root / 'c').read_text(), 'new\n')
        self.assertEqual((self.root / 'a').read_text(), 'y\n')
        self.assertEqual((self.root / 'a').stat().st_mode & 0o777, 0o755)
        self.assertFalse(list(self.root.glob('.agentj-patch-*')))

    def test_staging_failure_keeps_original_and_cleans_temps(self):
        (self.root / 'a').write_text('original\n')
        with mock.patch('os.fsync', side_effect=OSError('synthetic disk error')):
            with self.assertRaises(tools.ToolError):
                self.runpatch('*** Update File: a\n@@\n-original\n+changed')
        self.assertEqual((self.root / 'a').read_text(), 'original\n')
        self.assertFalse(list(self.root.glob('.agentj-patch-*')))

    def test_specs_and_prompt_budget(self):
        self.assertIn('apply_patch', tools.SPECS)
        for role in ('main', 'ceo', 'sub'):
            self.assertNotIn('apply_patch', prompt.tool_list(role))
            self.assertIn('apply_patch', prompt.tool_list(role, edit_mode='apply_patch'))
            for tier in ('small', 'large'):
                self.assertLessEqual(prompt.harness_tokens(role, tier, "apply_patch"), prompt.HARNESS_MAX)
