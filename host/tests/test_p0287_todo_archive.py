"""Bounded active To-do storage archives closed rows without loss; isolated roots only."""
import _hermetic  # noqa: F401
import concurrent.futures
import json
import pathlib
import tempfile
import unittest
from unittest import mock
from agentj import jcards as cards, jcards_todo as todo


class Archive(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='todo0287-')
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name) / 'state'
        self.root.mkdir()
        cards._save_switches(self.root, {'todo': True})
        cards.find(self.root, 'todo')
        self.file = cards.data_dir(self.root, 'todo') / 'todo.json'
        self.file.parent.mkdir(parents=True, exist_ok=True)

    def seed(self, count=141):
        rows = [{'id': f't{i}', 'text': '已完成工作' * 12, 'done': i > 1,
                 'status': 'done' if i > 1 else 'pending', 'note': '备注' * 20,
                 'unknown': {'kept': i}} for i in range(1, count + 1)]
        self.file.write_text(json.dumps({'schema': 2, 'extra': 'keep', 'items': rows}, ensure_ascii=False))
        return rows

    def test_full_141_rows_add_and_archived_note_and_reopen(self):
        old = self.seed()
        result = todo.change(self.root, 'add', text='新待办')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['item']['id'], 't142')
        stored = json.loads(self.file.read_bytes())
        self.assertEqual(stored['extra'], 'keep')
        self.assertLess(len(self.file.read_bytes()), 64 * 1024)
        self.assertTrue(result['capacity']['archived'])
        archive = todo.listing(self.root, 'archive')
        self.assertTrue(archive['ok'], archive)
        self.assertEqual(len(stored['items']) + archive['total'], 142)
        selected = archive['items'][0]['id']
        self.assertTrue(todo.change(self.root, 'edit', selected, note='后来补的备注')['ok'])
        self.assertEqual(next(r for r in todo.listing(self.root, 'archive')['items'] if r['id'] == selected)['note'], '后来补的备注')
        self.assertTrue(todo.change(self.root, 'reopen', selected)['ok'])
        self.assertNotIn(selected, [r['id'] for r in todo.listing(self.root, 'archive')['items']])
        current = next(r for r in json.loads(self.file.read_bytes())['items'] if r['id'] == selected)
        self.assertEqual(current['unknown'], next(r['unknown'] for r in old if r['id'] == selected))

    def test_archive_failure_never_removes_active_rows(self):
        self.seed()
        before = self.file.read_bytes()
        with mock.patch('agentj.jcards_todo_archive.store', side_effect=OSError('disk full')):
            result = todo.change(self.root, 'add', text='新待办')
        self.assertFalse(result['ok'])
        self.assertEqual(self.file.read_bytes(), before)

    def test_capacity_warning_before_hard_limit_and_archive_symlink_refused(self):
        self.seed()
        snap = cards.phone_get(self.root, 'todo')
        self.assertTrue(snap['todo']['capacity']['near_limit'])
        self.assertEqual(snap['todo']['capacity']['maxBytes'], 64 * 1024)
        folder = self.file.parent / 'archive'
        outside = pathlib.Path(self.temp.name) / 'outside'
        outside.mkdir()
        folder.symlink_to(outside, target_is_directory=True)
        before = self.file.read_bytes()
        self.assertFalse(todo.change(self.root, 'add', text='新待办')['ok'])
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(list(outside.iterdir()), [])

    def test_ids_never_reused_and_archive_delete_stays_deleted(self):
        self.seed()
        result = todo.change(self.root, 'add', text='新待办')
        self.assertTrue(result['ok'], result)
        ids = {r['id'] for r in todo.listing(self.root, 'archive')['items']}
        target = sorted(ids)[0]
        self.assertTrue(todo.change(self.root, 'delete', target)['ok'])
        self.assertNotIn(target, {r['id'] for r in todo.listing(self.root, 'archive')['items']})
        self.assertEqual(todo.change(self.root, 'add', text='另一个')['item']['id'], 't143')

    def test_archive_query_is_redacted_paginated_and_cli_accessible(self):
        self.seed()
        self.assertTrue(todo.change(self.root, 'add', text='新待办')['ok'])
        first = todo.listing(self.root, 'archive', page=0)
        self.assertIn('total', first)
        self.assertEqual(todo.listing(self.root, 'archive', page=1)['items'], [])
        args = cards.parser().parse_args(['todo', 'archive', '--page', '0', '--json'])
        self.assertEqual(args.op, 'archive')

    def test_main_commit_failure_keeps_all_original_rows_and_no_ghost_add(self):
        from agentj import jcards_todo_archive as archive
        self.seed()
        before = self.file.read_bytes()
        atomic = archive.atomic
        def fail_main(d, name, body):
            if name == 'todo.json':
                raise OSError('injected main commit failure')
            return atomic(d, name, body)
        with mock.patch.object(archive, 'atomic', side_effect=fail_main):
            result = todo.change(self.root, 'add', text='不应出现的待办')
        self.assertFalse(result['ok'])
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(todo.listing(self.root, 'archive')['items'], [])
        self.assertTrue(todo.change(self.root, 'add', text='重试成功')['ok'])
        live = todo.listing(self.root, 'list')['items']
        history = todo.listing(self.root, 'archive')['items']
        self.assertEqual(len(live) + len(history), 142)
        self.assertNotIn('不应出现的待办', [x['text'] for x in [*live, *history]])

    def test_archived_delete_does_not_require_a_main_file_commit(self):
        from agentj import jcards_todo_archive as archive
        self.seed()
        self.assertTrue(todo.change(self.root, 'add', text='新待办')['ok'])
        target = todo.listing(self.root, 'archive')['items'][0]['id']
        before = self.file.read_bytes()
        with mock.patch.object(archive, 'atomic', side_effect=OSError('no main commit allowed')):
            result = todo.change(self.root, 'delete', target)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertNotIn(target, {x['id'] for x in todo.listing(self.root, 'archive')['items']})

    def test_archived_dependencies_keep_due_flags_and_phone_condition(self):
        self.seed()
        # Earliest closed row t2 will be archived; both conditions depend on it.
        data = json.loads(self.file.read_bytes())
        data['items'][0].update(owner='agent', start={'after': 't2'}, end={'after': 't2'})
        self.file.write_text(json.dumps(data, ensure_ascii=False))
        self.assertTrue(todo.change(self.root, 'add', text='新待办')['ok'])
        self.assertIn('t2', {x['id'] for x in todo.listing(self.root, 'archive')['items']})
        row = next(x for x in todo.listing(self.root, 'due')['items'] if x['id'] == 't1')
        self.assertIn('ready', row['flags'])
        self.assertIn('end_met', row['flags'])
        phone = next(x for x in cards.phone_get(self.root, 'todo')['items'] if x['id'] == 't1')
        self.assertTrue(phone['start']['met'])
        self.assertTrue(phone['end']['met'])
        self.assertEqual(phone['start']['text'], data['items'][1]['text'][:59] + '…' if len(data['items'][1]['text']) > 60 else data['items'][1]['text'])
        self.assertTrue(todo.change(self.root, 'add', text='后续', start='after:t2')['ok'])

    def test_multiple_writers_keep_every_add_and_unique_ids(self):
        self.seed()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda i: todo.change(self.root, 'add', text='并发待办' + str(i)), range(12)))
        self.assertTrue(all(x['ok'] for x in results), results)
        self.assertEqual(len({x['item']['id'] for x in results}), 12)
        self.assertEqual(len(todo.listing(self.root, 'list')['items']) + todo.listing(self.root, 'archive')['total'], 153)

    def test_archive_preserves_original_extra_fields_and_redacts_queries(self):
        from agentj import jcards_todo_archive as archive
        rows = self.seed()
        rows[1]['note'] = '旧长备注' * 130
        rows[1]['source'] = 'source' * 40
        self.file.write_text(json.dumps({'items': rows}, ensure_ascii=False))
        self.assertTrue(todo.change(self.root, 'add', text='新待办')['ok'])
        self.assertEqual(archive.get(self.root, 't2'), rows[1])
        self.assertTrue(todo.change(self.root, 'edit', 't2', note='新备注')['ok'])
        self.assertEqual(archive.get(self.root, 't2')['source'], rows[1]['source'])
        key = 'AKIA' + 'Q3EGRKX7' + 'PLM2VN4B'
        archive.store(self.root, {'id': 'oldkey', 'text': key, 'status': 'done'})
        self.assertNotIn(key, json.dumps(todo.listing(self.root, 'archive')))

    def test_existing_truncated_file_is_never_overwritten(self):
        self.file.write_bytes(b' ' * (64 * 1024 + 1))
        before = self.file.read_bytes()
        self.assertFalse(todo.change(self.root, 'add', text='新待办')['ok'])
        self.assertEqual(self.file.read_bytes(), before)
