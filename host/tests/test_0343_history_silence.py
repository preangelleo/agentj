"""Display-only backfill for an attested, terminal shared Claude report."""
import _hermetic
import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from agentj import hooks, provenance, shared_turn_origin as origin
from agentj import shared_report_history as repair


class HistoryRepair(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sid, self.socket = str(uuid.uuid4()), "/tmp/host-fixture"
        path = Path(tmp.name) / (self.sid + ".jsonl")
        src = {"k": "agent", "via": "hook", "name": "fixture", "text": '{"event_id":"fixture","message":"two  spaces"}',
               "source_kind":"system", "source_role":"owner"}
        self.row = {"id":17,"ts":10000,"end":"done","src":src,"reply":{"text":"private reason"}}
        self.history = SimpleNamespace(epoch=1, turns={17:self.row})
        self.agent = SimpleNamespace(session={"sessionId":self.sid,"messagingSocketPath":self.socket},
                                     transcript=path,host=SimpleNamespace(hist=self.history))
        hook = {"id":"fixture","name":"fixture","preset":"generic","life":"always"}
        entry = {"hook":"fixture","id":"d123456abcdef","enc":"utf8","body":src["text"]}
        body = hooks.envelope("zh",hook,entry,"fixture")
        root = self.line("user", "", origin={"kind":"peer","verifiedLocal":True,"verifiedPeerPid":123,
                           "verifiedPeerProcStart":"300","from":"uds:"+self.socket,"msg_id":str(uuid.uuid4())},
                         turnOrigin="peer",promptSource="system",isMeta=True,
                         message={"content":origin.NATIVE_PEER_PREFIX+provenance.envelope(self.socket,body,provenance.system())+origin.NATIVE_PEER_SUFFIX})
        att = self.line("attachment",root["uuid"])
        ans = self.line("assistant",att["uuid"],message={"stop_reason":"end_turn","content":[{"type":"text","text":"[silent] private reason"}]})
        self.records = [root,att,ans]

    def line(self, kind, parent, **extras):
        return {"type":kind,"uuid":str(uuid.uuid4()),"parentUuid":parent,"sessionId":self.sid,
                "timestamp":"1970-01-01T00:00:10.400Z","isSidechain":False,**extras}

    async def project(self):
        self.agent.transcript.write_text("".join(json.dumps(r)+"\n" for r in self.records))
        repair.attach(self.agent)
        return await repair.for_display(self.agent, self.history.epoch)

    async def test_exact_unique_native_chain_changes_only_display(self):
        record = copy.deepcopy(self.row)
        projection = await self.project()
        self.assertTrue(repair.turn(projection,self.row)["silent"])
        self.assertEqual(self.row,record)
        self.assertEqual(repair.turn(projection,{**self.row, "reply":{"text":"real report"}}).get("silent"),None)
        self.agent.session["sessionId"]=str(uuid.uuid4())
        self.assertFalse(await repair.for_display(self.agent, self.history.epoch))

    async def test_negative_controls(self):
        original = copy.deepcopy(self.records)
        for mutation in ("duplicate", "system-origin", "branch", "ordinary-reply", "extra-tool", "literal-spaces"):
            with self.subTest(mutation=mutation):
                self.records = copy.deepcopy(original)
                if mutation=="duplicate": self.records += copy.deepcopy(original)
                if mutation=="system-origin": self.records[0]["origin"]["verifiedLocal"] = False
                if mutation=="branch": self.records[-1]["parentUuid"] = str(uuid.uuid4())
                if mutation=="ordinary-reply": self.records[-1]["message"]["content"][0]["text"]="private reason"
                if mutation=="extra-tool": self.records[-1]["message"]["content"].append({"type":"tool_use"})
                if mutation=="literal-spaces": self.records[0]["message"]["content"]=self.records[0]["message"]["content"].replace("two  spaces","two spaces")
                self.assertFalse(await self.project())

    async def test_parented_tool_result_and_split_final(self):
        first, att, final = self.records
        call = self.line("assistant",att["uuid"],message={"stop_reason":"tool_use","content":[{"type":"tool_use","name":"Bash","id":"one"}]})
        result = self.line("user",call["uuid"],message={"content":[{"type":"tool_result","tool_use_id":"one"}]})
        thought = self.line("assistant",result["uuid"],message={"stop_reason":"end_turn","id":"message","content":[{"type":"thinking"}]})
        final["parentUuid"] = thought["uuid"]
        final["message"]["id"] = "message"
        self.records=[first,att,call,result,thought,final]
        self.assertIn(17,await self.project())
        for failure in ("mismatched result","wakeup","intermediate public text","unrelated final","malformed blocks"):
            with self.subTest(failure=failure):
                saved = copy.deepcopy(self.records)
                if failure=="mismatched result":
                    self.records[3]["message"]["content"][0]["tool_use_id"]="two"
                if failure=="wakeup":
                    self.records[2]["message"]["content"][0]["name"]="ScheduleWakeup"
                if failure=="intermediate public text":
                    self.records[2]["message"]["content"].append({"type":"text","text":"report"})
                if failure=="unrelated final":
                    self.records[5]["message"]["id"]="other"
                if failure=="malformed blocks":
                    self.records[4]["message"]["content"]=[True]
                self.assertFalse(await self.project())
                self.records=saved

    async def test_wire_projection_and_removed_source(self):
        from agentj.silent import public_message
        await self.project()
        msg={"t":"hist_page","epoch":1,"turns":[self.row]}
        visible=public_message(await repair.message(self.agent,msg))
        self.assertTrue(visible["turns"][0]["silent"])
        self.assertEqual(visible["turns"][0]["reply"]["pre"],"private reason")
        self.assertFalse(msg["turns"][0].get("silent"))
        self.agent.transcript.unlink()
        self.assertIs(await repair.message(self.agent,msg),msg)

    async def test_attaching_refreshes_the_existing_page_for_online_devices_only_once(self):
        posted = []
        self.agent.host.sessions={1:SimpleNamespace(state="ready")}
        self.agent.host.hist_emit=lambda rows: posted.extend(r["id"] for r in rows)
        self.assertIn(17,await self.project())
        self.assertEqual(posted,[17])
        self.assertIn(17,await self.project())
        self.assertEqual(posted,[17])
        self.assertFalse(self.row.get("silent"))
