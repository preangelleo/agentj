"""P495: actual serve status gives only fixed variable-presence booleans."""
import _hermetic
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from agentj import doctor,service_runtime as rt


class Runtime(unittest.TestCase):
    def test_snapshot_contains_only_fixed_presence(self):
        private={'PATH':'/terminal-private','HOME':'/owner-private','HTTPS_PROXY':'http://private:password@proxy','SECRET':'hidden'}
        result=rt.snapshot(private)
        self.assertTrue(rt.valid(result));self.assertNotIn('SECRET',result)
        for v in private.values():self.assertNotIn(v,str(result))

    def test_runtime_diff_is_presence_only_not_values(self):
        shell={'PATH':'/terminal-private','HOME':'/owner-private','HTTPS_PROXY':'http://private:password@proxy'}
        live=rt.snapshot({'PATH':'/service-private','HOME':shell['HOME']})
        row=doctor.check_service_environment({'kind':'systemd'},shell,{'environment':live})
        self.assertEqual(row['status'],'warn')
        self.assertIn('HTTPS_PROXY',row['summary'])
        for v in shell.values():self.assertNotIn(v,str(row))

    def test_missing_old_or_malformed_runtime_is_unknown(self):
        for result in (None,{}, {'environment':{}},{'environment':{'PATH':'private'}}):
            row=doctor.check_service_environment({'kind':'launchd'}, {},result)
            self.assertEqual(row['status'],'warn');self.assertIn('AJ_ENV_UNAVAILABLE',row['summary'])
            self.assertNotIn('private',str(row))

    def test_matching_presence_is_explicitly_not_value_match(self):
        live=rt.snapshot({'PATH':'/service','HOME':'/owner'})
        row=doctor.check_service_environment({'kind':'launchd'},{'PATH':'/terminal','HOME':'/different'},{'environment':live})
        self.assertEqual(row['status'],'ok');self.assertIn('presence only',row['summary'])
        self.assertIsNone(doctor.check_service_environment({'kind':'none'}))


class LocalStatus(unittest.IsolatedAsyncioTestCase):
    async def test_real_local_status_reports_in_process_projection(self):
        from test_l1 import _state,_host
        with tempfile.TemporaryDirectory() as d:
            host=_host(_state(d),[])
            sock=d+'/ctl';server=await asyncio.start_unix_server(host.on_ctl,path=sock)
            try:
                with patch.dict(os.environ,{'HTTPS_PROXY':'http://private:fixture@proxy'}):
                    r,w=await asyncio.open_unix_connection(sock)
                    w.write(b'{"cmd":"status"}\n');await w.drain()
                    obj=json.loads(await r.readline());w.close();await w.wait_closed()
                self.assertTrue(rt.valid(obj['environment']));self.assertTrue(obj['environment']['HTTPS_PROXY'])
                self.assertNotIn('private:fixture',json.dumps(obj))
            finally:server.close();await server.wait_closed()
