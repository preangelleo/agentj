import asyncio
import json
import os
import pathlib
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock
from agentj import jcards, jpalettes


def item():
    return {"id": str(uuid.uuid4()), "colors": ["#123456", "#827dbd", "#faf9f5", "#141414", "#ffe292"]}


class Palettes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def save(self, row):
        return jpalettes.request(self.root, {"op": "save", "palette": row})

    def test_default_off_card_and_switch(self):
        rows = jcards.phone_list(self.root)["cards"]
        card = next(c for c in rows if c["id"] == "official-palette")
        self.assertFalse(card["on"])
        self.assertFalse(jcards.phone_get(self.root, "official-palette")["ok"])
        self.assertTrue(jcards.set_on(self.root, "official-palette", True))
        self.assertEqual(jcards.phone_get(self.root, "official-palette")["template"], "palette")
        self.assertTrue(jcards.set_on(self.root, "official-palette", False))
        self.assertFalse(jcards.find(self.root, "official-palette")["on"])
        self.assertEqual({c["id"] for c in rows if not c["on"]}, {"official-palette", "files", "obsidian"})
        self.assertTrue(all(c["on"] for c in rows if c["id"] in ("notes", "todo", "automation", "calendar")))

    def test_existing_owner_palette_card_is_not_shadowed(self):
        doc=json.loads((jcards.official_dir('notes')/'card.json').read_text())
        doc['id']='palette'
        folder=self.root/'owner-card';folder.mkdir()
        (folder/'card.json').write_text(json.dumps(doc))
        self.assertTrue(jcards.apply(self.root,folder)['ok'])
        owner=jcards.find(self.root,'palette')
        self.assertTrue(owner['on']);self.assertFalse(owner['official'])
        self.assertFalse(jcards.find(self.root,'official-palette')['on'])
        self.assertEqual(len([c for c in jcards.cards(self.root) if c['id']=='palette']),1)

    def test_save_reload_delete_idempotent_and_private(self):
        row = item()
        self.assertTrue(self.save(row)["ok"])
        self.assertTrue(self.save(row)["ok"])
        self.assertEqual(jpalettes.read(self.root), [row])
        path = self.root / "agentperm/cards/palettes.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(jpalettes.request(self.root, {"op":"delete", "id":row["id"]})["ok"])
        self.assertEqual(jpalettes.read(self.root), [])

    def test_invalid_requests_change_nothing(self):
        row = item(); self.save(row)
        for bad in [None, [], {}, {**row,"colors":["#123456"]}, {**row,"id":"../../bad"}, {**row,"colors":["url(x)"]*5}, {**row,"colors":[1]*5}, {**row,"colors":["#abcdef"]*11}, {**row,"name":"unbounded field"}]:
            self.assertFalse(self.save(bad)["ok"])
        self.assertFalse(jpalettes.request(self.root, {"op":"shell"})["ok"])
        self.assertEqual(jpalettes.read(self.root), [row])

    def test_limit_and_concurrent_updates(self):
        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(self.save, [item() for _ in range(32)]))
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual(len(jpalettes.read(self.root)), 32)
        self.assertEqual(self.save(item())["why"], "full")

    def test_corrupt_and_symlink_libraries_never_overwritten(self):
        row=item();self.save(row)
        path=self.root/'agentperm/cards/palettes.json'
        for raw in [b'not json',b'{}',b'[]'*10000,b'[{"id":"broken"}]']:
            path.write_bytes(raw)
            self.assertFalse(self.save(row)["ok"])
            self.assertEqual(path.read_bytes(),raw)
        path.unlink(); outside=self.root/'outside';outside.write_text('untouched');path.symlink_to(outside)
        self.assertFalse(self.save(row)["ok"])
        self.assertEqual(outside.read_text(),'untouched');self.assertTrue(path.is_symlink())

    def test_paired_wire_handler(self):
        from agentj.serve import Host
        from types import SimpleNamespace
        state=SimpleNamespace(root=self.root,log=lambda *a,**k:None)
        host=SimpleNamespace(st=state,lang='en',send_app=AsyncMock())
        session=SimpleNamespace(device='test')
        row=item()
        asyncio.run(Host.on_jcard(host,session,'jpalette',{'r':'p420-test','op':'save','palette':row}))
        reply=host.send_app.call_args.args[1]
        self.assertTrue(reply['ok']);self.assertEqual(reply['palettes'],[row])
        self.assertIn('jpalette',jcards.PHONE_TYPES)
