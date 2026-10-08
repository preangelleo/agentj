"""P76 owner upgrade authorization, durable completion and no-model three-state flow."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import AsyncMock, Mock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import controls, phone_update, update, wire
from agentj.state import State
from agentj.serve import Host, Session

class Upgrade(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        td = self.tmp.name
        env = patch.dict(os.environ, {"HOME":td,"AGENTJ_STATE_DIR":td+"/st","XDG_CONFIG_HOME":td+"/cfg"})
        env.start();self.addCleanup(env.stop)
        self.st=State();self.st.init();self.h=Host(self.st);self.h.cmd_card=Mock();self.h._post=Mock();self.h.cmd_turn=Mock(return_value=1)
        self.s=Session(1,state="ready",pub=b"a"*32,device="fixture",name="Owner")
        self.key=Ed25519PrivateKey.generate()
        self.obj={"n":"ab"*16,"ts":int(time.time()*1000)}
        self.obj["sig"]=wire.b64u(self.key.sign(controls.signed_message(self.h.channel,self.s.device,"update",self.obj["n"],self.obj["ts"],controls.object_digest("update",{}))))
    async def command(self,obj):
        with patch.object(self.st,"is_allowed",return_value=True),patch.object(self.st,"sign_key",return_value=self.key.public_key().public_bytes_raw()):
            return await self.h.on_slash(self.s,"update","",False,False,authorization=obj)
    async def test_only_paired_signed_owner_and_nonce_once(self):
        await self.command({})
        self.assertEqual(self.h.cmd_card.call_args.args[1].kind,"refused");self.assertFalse(any(c.args[0] == self.h.phone_upgrade for c in self.h._post.call_args_list))
        with patch("agentj.serve.asyncio.create_task",return_value=Mock()) as task:
            await self.command(self.obj);task.assert_called_once();task.call_args.args[0].close()
        await self.command(self.obj);self.assertEqual(self.h.cmd_card.call_args.args[1].kind,"refused")
        self.s.state="new";await self.command(self.obj);self.assertEqual(self.h.cmd_card.call_args.args[1].kind,"refused")
    async def test_latest_no_install_and_unknown_actionable(self):
        for status in ("current","unknown"):
            with patch.object(update,"check",return_value={"status":status,"latest":"0.16.2a1"}),patch.object(phone_update,"launch") as launch:
                await self.h.phone_upgrade(1,"Owner")
                launch.assert_not_called()
                self.assertIn("最新" if status=="current" else "agentj update check",self.h.cmd_card.call_args.args[1].text)
    async def test_new_version_launches_and_reports_apply_failure(self):
        with patch.object(update,"check",return_value={"status":"newer","latest":"0.16.3a1"}),patch.object(phone_update,"launch") as launch,patch.object(phone_update,"take",return_value={"result":"failed","reason":"install_failed"}),patch("agentj.serve.asyncio.sleep",new=AsyncMock()):
            await self.h.phone_upgrade(1,"Owner");launch.assert_called_once_with(self.st)
        self.assertIn("install_failed",self.h.cmd_card.call_args.args[1].text)
    async def test_restarted_host_replays_result_into_history_once(self):
        rec={"from":"0.16.1a1","to":"0.16.2a1","result":"ok","reason":"upgraded","counts":[10,2,0]}
        self.st.write_private(self.st.root/phone_update.RESULT,json.dumps(rec).encode())
        with patch("agentj.doctor.run",return_value=[{"status":"ok"}]) as doctor:
            await self.h.upgraded_notice();doctor.assert_called_once_with(self.st)
        self.assertIn("1✓/0!/0✗",self.h.cmd_card.call_args.args[1].text)
        self.h.cmd_card.reset_mock();await self.h.upgraded_notice();self.h.cmd_card.assert_not_called()
        self.st.write_private(self.st.root/phone_update.RESULT,json.dumps(rec).encode())
        with patch("agentj.doctor.run",return_value=[{"status":"fail"}]):
            await self.h.upgraded_notice()
        self.assertIn("0✓/0!/1✗",self.h.cmd_card.call_args.args[1].text)
        self.assertIn("agentj doctor",self.h.cmd_card.call_args.args[1].text)
    def test_worker_durable_before_restart_and_apply_failure_no_restart(self):
        restart=Mock(side_effect=lambda:self.assertTrue((self.st.root/phone_update.RESULT).exists()))
        with patch("agentj.doctor.run",return_value=[{"status":"ok"}]):
            phone_update.run(self.st,apply_fn=Mock(return_value={"from":"0.16.1a1","to":"0.16.2a1","reason":"upgraded","result":"ok"}),restart_fn=restart)
        restart.assert_called_once();self.assertEqual(phone_update.take(self.st)["counts"],[1,0,0])
        restart.reset_mock();phone_update.run(self.st,apply_fn=Mock(return_value={"reason":"install_failed","result":"failed"}),restart_fn=restart)
        restart.assert_not_called();self.assertIn("Upgrade failed",phone_update.text(phone_update.take(self.st),"en"))
    def test_linux_updater_is_separate_unit_mac_separate_process(self):
        with patch("agentj.service_recovery.launch_job") as launch:
            phone_update.launch(self.st)
            self.assertEqual(launch.call_args.args[2], "update")
            self.assertEqual(launch.call_args.args[1][-2:], ["-m", "agentj.phone_update"])

class InstalledDoctorFailure(unittest.TestCase):
    def test_failed_doctor_still_restarts_installed_package_and_reports_counts(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{"AGENTJ_STATE_DIR":td}):
            st=State();restart=Mock()
            with patch("agentj.doctor.run",return_value=[{"status":"fail"}]):
                phone_update.run(st,apply_fn=Mock(return_value={"from":"0.16.1a1","to":"0.16.2a1","reason":"doctor_failed","result":"failed"}),restart_fn=restart)
            restart.assert_called_once();rec=phone_update.take(st)
            self.assertIn("0✓/0!/1✗",phone_update.text(rec));self.assertIn("agentj doctor",phone_update.text(rec,"en"))
