"""Real CLI→UNIX socket→Host dormant form path; production flag remains off."""
import _hermetic  # noqa: F401
import asyncio
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import forms as F

class RealSocket(unittest.IsolatedAsyncioTestCase):
    """The real path: `agentj ask` (forms.cmd, as the Agent runs it) → elevate.sock → serve → the page; list / show / cancel."""

    async def test_cli_over_the_elevate_socket(self):
        from test_l1 import _host, _state
        self.assertFalse(F.FORMS_RELEASED)
        patch = mock.patch.object(F, "FORMS_RELEASED", True)
        patch.start(); self.addCleanup(patch.stop)
        from agentj import elevate
        with tempfile.TemporaryDirectory(prefix="p212r-", dir=os.environ.get("TEST_TMP", "/tmp")) as d:
            st = _state(d)
            host = _host(st, [])
            host.agent = SimpleNamespace(kind="claude", native_source={"kind":"terminal", "role":"owner"}, cur_send=None)
            await host.elevate.start()
            self.addAsyncCleanup(host.elevate.stop)
            env = {elevate.ENV: str(st.perm_dir / elevate.SOCK_NAME)}

            def run(**kw):
                a = SimpleNamespace(**{"action": "send", "target": None, "file": None, "template": None, "fill": None, "set": None,
                                       "mode": "auto", "ttl": None, "en": True, "json": True, **kw})
                out = io.StringIO()
                with mock.patch.dict("os.environ", env), contextlib.redirect_stdout(out):
                    code = F.cmd(a)
                return code, json.loads(out.getvalue())
            code, res = await asyncio.to_thread(run, template="decide-3", set=["question=Ship which?", "a=A", "b=B", "c=C"])
            self.assertEqual((code, res.get("ok")), (0, True), res)
            self.assertEqual((code, res["ok"], res["mode"], res["tap"]), (0, True, "light", True))
            page = host.hist.get(res["turn"])
            self.assertEqual((page["src"]["k"], page["form"]["id"], page["form"]["title"]), ("agent", res["id"], "Ship which?"))
            code, ls = await asyncio.to_thread(run, action="list")
            self.assertEqual(ls["forms"][0]["id"], res["id"])
            code, sh = await asyncio.to_thread(run, action="show", target=res["id"])
            self.assertEqual((sh["state"], sh["answer"]), ("open", None))
            code, cc = await asyncio.to_thread(run, action="cancel", target=res["id"])
            self.assertEqual((code, cc["result"]), (0, "cancelled"))
            self.assertEqual(host.hist.get(res["turn"])["form"]["state"], "cancelled")
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir=d) as f:
                json.dump({"title": "x", "questions": [{"id": "pw", "type": "password", "label": "Password"}]}, f)
            code, bad = await asyncio.to_thread(run, file=f.name)
            self.assertEqual((code, bad["why"]), (2, "type"), "refused before anything is sent")
    async def test_production_default_off_refuses_over_real_socket(self):
        from test_l1 import _host, _state
        from agentj import elevate
        self.assertFalse(F.FORMS_RELEASED)
        with tempfile.TemporaryDirectory(dir=os.environ.get('TEST_TMP', '/tmp')) as d:
            st = _state(d)
            host = _host(st, [])
            await host.elevate.start()
            self.addAsyncCleanup(host.elevate.stop)
            req = {'t':'form', 'op':'send', 'form':{'title':'x','questions':[{'id':'q','type':'text','label':'x'}]}}
            F.normalize(req['form'])  # valid dormant request; refusal must come from the release flag
            with mock.patch.dict(os.environ, {elevate.ENV: str(st.perm_dir / elevate.SOCK_NAME)}):
                result = await asyncio.to_thread(elevate.client_request, st, req, timeout=3)
            self.assertEqual((result['ok'], result['why']), (False, 'pending_owner_decision'))
            self.assertFalse(host.forms.store.all())

