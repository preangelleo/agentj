"""P580 #406: replacing inputs after Scheduler validation cannot replace its instructions."""
import _hermetic  # noqa: F401
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agentj import capability, tasks, wfrun
from test_controls import task_dir
from test_p426_wfresume import Host, make_wf
from test_p113_long_tasks import write_brief


class Snapshot(unittest.TestCase):
    def test_post_validation_replacement_single_and_multistep(self):
        for multi in (False, True):
            with self.subTest(multi=multi), tempfile.TemporaryDirectory(prefix='aj-p580-') as raw:
                root = Path(raw)
                wf = make_wf(root) if multi else task_dir(root / 'work', 'daily', 'VALIDATED BODY')
                write_brief(wf)
                h = Host(root)
                sch = h.scheduler
                sch.request('daily', 'manual')
                seen = []

                async def replace_after_validation(host, kind, task, title):
                    write_brief(wf, revision=2)
                    (wf / task['prompt_file']).write_text('UNVALIDATED REPLACEMENT')
                    for step in task.get('steps', []):
                        (wf / step['prompt_file']).write_text('UNVALIDATED REPLACEMENT')
                    changed = dict(task, title={'zh': 'UNVALIDATED TITLE', 'en': 'UNVALIDATED TITLE'})
                    (wf / 'task.json').write_text(json.dumps(changed))
                    return kind, None

                async def execute(kind, e, research, prompt=None):
                    body = prompt or tasks.prompt_for(e, research)
                    seen.append(body)
                    self.assertNotIn('UNVALIDATED', body)
                    self.assertIn('revision 1', body)
                    self.assertNotIn('revision 2', body)
                    with mock.patch.object(capability, 'receipt_for', return_value={}):
                        self.assertIsNotNone(capability.brief_scope_for(h.st, e))
                    with self.assertRaises(TypeError):
                        e['prompt_bytes'][e['task']['prompt_file']] = b'changed'
                    if multi:
                        i = len(seen) - 1
                        step = e['task']['steps'][i]
                        self.assertIn('do ' + step['id'], body)
                        self.assertEqual(wfrun.prompt_sha(e, step), hashlib.sha256(('do ' + step['id'] + '\n').encode()).hexdigest())
                    else:
                        self.assertIn('VALIDATED BODY', body)
                    return 'VERDICT: ok — completed', 0

                sch._exec = execute
                with mock.patch('agentj.quota.route', replace_after_validation):
                    res = asyncio.run(sch.run('daily', 'manual'))
                self.assertEqual(res['verdict'], 'attention', res)
                self.assertEqual(res['report_check'], 'missing')
                self.assertEqual(len(seen), 3 if multi else 1)

    def test_missing_snapshot_never_falls_back_to_disk(self):
        with self.assertRaises(tasks.TaskError):
            tasks.prompt_bytes({'dir': '/unused'}, 'RUN.md')
