"""#563: real stdio fake server, isolated accounts; no native login or consume."""
import _hermetic  # noqa: F401,I001
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from agentj import accounts, codex_reset_credits as native, quota, serve

NOW = 1_800_000_000
DAY = 86400

FAKE = r'''
import json, os, sys, time
from pathlib import Path
home = Path(os.environ["CODEX_HOME"])
d = json.loads((home/"quota-fixture.json").read_text())
for line in sys.stdin:
    req=json.loads(line)
    method=req.get("method")
    with (home/"requests.jsonl").open("a") as f: f.write(json.dumps(req)+"\n")
    if "id" not in req: continue
    mode=d.get("mode")
    if mode=="timeout": time.sleep(10)
    if mode=="malformed": print("not-json",flush=True); continue
    if mode=="huge": print("x"*300000,flush=True); continue
    if method=="initialize": result={}
    elif method=="account/read":
        n=globals().get("n",0)+1
        result={"account":{"type":"apiKey" if mode=="api" else "chatgpt", "email": "second" if mode=="changed" and n>1 else "first"}}
    elif method=="account/rateLimits/read": result=d["limits"]
    else: raise RuntimeError("unexpected method")
    print(json.dumps({"method":"irrelevant","params":{}}),flush=True)
    print(json.dumps({"id":req["id"],"result":result}),flush=True)
'''


def limits(identity="account-a", used=30, credits=None, h5=4):
    return {"accountId": identity,
            "rateLimits": {"primary": {"windowDurationMins": 300, "usedPercent": h5, "resetsAt": NOW+3600},
                           "secondary": {"windowDurationMins": 10080, "usedPercent": used, "resetsAt": NOW+2*DAY}},
            "rateLimitResetCredits": {"credits": credits if credits is not None else [credit()]}}


def credit(iid="credit-a", exp=NOW+DAY, **kw):
    return {"id": iid, "status": "available", "resetType": "codexRateLimits", "expiresAt": exp, **kw}


class ResetCredits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root/"bin"
        self.bin.mkdir()
        script = self.bin/"codex"
        script.write_text("#!"+sys.executable+"\n"+FAKE)
        script.chmod(0o700)
        self.env = {"HOME": str(self.root), "PATH": str(self.bin), "CODEX_HOME": str(self.root/"current"), "AGENTJ_CODEX_BIN": str(script)}
        self.registry = {"codex": {"active": "aaaaaaaa", "items": [
            {"id": "aaaaaaaa", "name": "主号", "fp": "a"*16, "method": "device"},
            {"id": "bbbbbbbb", "name": "备用", "fp": "b"*16, "method": "device"}]}}
        self.registry["claude"] = {"active": None, "items": []}
        self.home = self.root/"current"
        self.slot = self.root/"accounts"/"codex"/"bbbbbbbb"
        self.put(self.home, limits())
        self.put(self.slot, limits("account-b", 55, [credit("credit-b", NOW+7*DAY)]))
        accounts._save(self.root,{"v":1,"harness":self.registry})
        native._CACHE.clear()

    def put(self, home, value, mode=None):
        home.mkdir(parents=True, exist_ok=True)
        (home/"quota-fixture.json").write_text(json.dumps({"limits": value, "mode": mode}))

    def test_real_stdio_reads_all_homes_no_consume_no_login_files(self):
        result=native.read_accounts(self.root, self.registry, NOW, self.env)
        self.assertEqual(result["aaaaaaaa"]["reading"]["used_pct"],30)
        self.assertEqual(result["bbbbbbbb"]["reading"]["used_pct"],55)
        self.assertEqual(result["bbbbbbbb"]["reset_credits"]["nearest_expires_at"],NOW+7*DAY)
        for home in (self.home,self.slot):
            methods=[json.loads(x)["method"] for x in (home/"requests.jsonl").read_text().splitlines()]
            self.assertEqual(methods,["initialize","initialized","account/read","account/rateLimits/read","account/read"])
            self.assertFalse((home/"auth.json").exists())
        self.assertNotIn("email",json.dumps(result))
        self.assertNotIn("account-a",json.dumps(result))

    def test_timestamp_range_is_unknown_not_formatter_crash(self):
        box=native.normalize(limits(credits=[credit(exp=1e300)]),NOW)
        self.assertIsNone(box["count"])
        self.assertIn("暂时读不到",native.summary(box))

    def test_ledger_maximum_fits_reload_cap_and_never_evicts(self):
        d=quota.load_state(self.root)
        d["credit_sent"]={"codex:credit:"+str(i).zfill(64):NOW+DAY for i in range(1024)}
        quota.save_state(self.root,d)
        self.assertLess(quota.state_path(self.root).stat().st_size,256*1024)
        self.assertEqual(len(quota.load_state(self.root)["credit_sent"]),1024)
        rep={"at":NOW,"harnesses":[],"codex_accounts":[{"id":"aaaaaaaa","name":"主号","reset_credits":native.normalize(limits(),NOW)}]}
        self.assertEqual(quota.due(rep,self.root),[])

    def test_registry_switch_back_race_invalidates_binding(self):
        orig=native.probe
        def switched(*args,**kw):
            result=orig(*args,**kw)
            changed=copy.deepcopy(self.registry)
            changed["codex"]["switched"]=NOW+.5
            accounts._save(self.root,{"v":1,"harness":changed})
            return result
        with patch.object(native,"probe",side_effect=switched):
            result=native.read_accounts(self.root,self.registry,NOW,self.env)
        self.assertTrue(all(r["reset_credits"]["why"]=="registry_changed" for r in result.values()))
        self.assertFalse((self.root/"codex-quota-identities.json").exists())

    def test_unknown_vs_explicit_zero_and_filter(self):
        self.assertIsNone(native.normalize({},NOW)["count"])
        self.assertEqual(native.normalize({"rateLimitResetCredits":{"credits":[]}},NOW)["count"],0)
        cs=[credit(),credit(),credit("expired",NOW),credit("redeemed",status="redeemed"),credit("other",resetType="other"),credit("forever",None)]
        box=native.normalize(limits(credits=cs),NOW)
        self.assertEqual(box["count"],2)
        self.assertEqual([c["id"] for c in box["credits"]],["credit-a","forever"])

    def test_timeout_malformed_oversize_api_changed_and_missing_binary(self):
        for mode in ("timeout","malformed","huge","api","changed"):
            with self.subTest(mode=mode):
                self.put(self.home, limits(),mode)
                start=time.monotonic()
                res=native.probe(self.home,NOW,self.env,timeout=.15)
                self.assertIsNone(res["reset_credits"]["count"])
                self.assertLess(time.monotonic()-start,1)
        res=native.probe(self.home,NOW,{"HOME":str(self.root),"PATH":""})
        self.assertEqual(res["reset_credits"]["why"],"missing_app_server")

    def test_cache_filters_expiry_without_claiming_fresh_read(self):
        self.put(self.home,limits(credits=[credit(exp=NOW+1)]))
        first=native.read_accounts(self.root,self.registry,NOW,self.env)
        second=native.read_accounts(self.root,self.registry,NOW+2,self.env)
        self.assertEqual(first["aaaaaaaa"]["reset_credits"]["count"],1)
        self.assertEqual(second["aaaaaaaa"]["reset_credits"]["count"],0)
        self.assertEqual(second["aaaaaaaa"]["reading"]["source_at"],NOW)

    def test_identity_pin_mismatch_duplicate_and_symlink(self):
        native.read_accounts(self.root,self.registry,NOW,self.env)
        self.put(self.slot,limits("swapped-account"))
        native._CACHE.clear()
        res=native.read_accounts(self.root,self.registry,NOW+1,self.env)
        self.assertEqual(res["bbbbbbbb"]["reset_credits"]["why"],"account_mismatch")
        self.put(self.slot,limits("account-a")); native._CACHE.clear()
        res=native.read_accounts(self.root,self.registry,NOW+2,self.env)
        self.assertTrue(all(r["reset_credits"]["why"]=="account_mismatch" for r in res.values()))
        for p in self.slot.iterdir(): p.unlink()
        self.slot.rmdir(); self.slot.symlink_to(self.home,target_is_directory=True)
        native._CACHE.clear()
        res=native.read_accounts(self.root,self.registry,NOW+3,self.env)
        self.assertEqual(res["bbbbbbbb"]["reset_credits"]["why"],"unsafe_home")

    def test_report_and_pick_use_fresh_native_accounts_and_h5_guard(self):
        with patch.object(quota,"_accounts",return_value=self.registry):
            rep=quota.report(self.root,NOW,self.env)
        cx=next(a for a in rep["harnesses"] if a["harness"]=="codex")
        self.assertEqual(cx["source"],"app_server")
        self.assertEqual(len(cx["accounts"]),2)
        choice=quota.pick(rep,NOW)
        self.assertEqual((choice["account_id"],choice["why"]),("aaaaaaaa","reset_credit_expiring"))
        cx["accounts"][0]["quota"]["h5"]["used_pct"]=100
        self.assertEqual(quota.pick(rep,NOW)["account_id"],"bbbbbbbb")
        cx["accounts"][1]["quota"]["pct"]=100
        self.assertIsNone(quota.pick(rep,NOW)["harness"])

    def test_once_per_credit_across_days_and_legacy_ledger_rollover(self):
        box=native.normalize(limits(credits=[credit(exp=NOW+3*DAY)]),NOW)
        acc={"id":"aaaaaaaa","name":"主号","reset_credits":box}
        rep={"at":NOW,"harnesses":[],"codex_accounts":[acc]}
        peek=quota.due(rep,self.root,mark=False)
        self.assertEqual(len(peek),1)
        self.assertIn("主号 账号有一张重置券",quota.due_text(peek[0],{},rep,"zh"))
        self.assertEqual(len(quota.due(rep,self.root)),1)
        d=quota.load_state(self.root);d["sent"]={str(i):str(i) for i in range(90)};quota.save_state(self.root,d)
        self.assertEqual(quota.due({**rep,"at":NOW+DAY},self.root),[])
        acc["reset_credits"]["credits"].append({"id":"new","expires_at":NOW+3*DAY})
        self.assertEqual(len(quota.due(rep,self.root)),1)

    def test_atomic_concurrent_reminder_once(self):
        rep={"at":NOW,"harnesses":[],"codex_accounts":[{"id":"aaaaaaaa","name":"主号","reset_credits":native.normalize(limits(),NOW)}]}
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:quota.due(rep,self.root),range(8)))
        self.assertEqual(sum(map(len,results)),1)

    def test_reminder_boundaries_all_accounts_even_active_claude(self):
        cs=[credit("expired",NOW),credit("edge",NOW+7*DAY),credit("outside",NOW+7*DAY+1),credit("forever",None)]
        rep={"at":NOW,"harnesses":[],"codex_accounts":[{"id":"bbbbbbbb","name":"备用","reset_credits":native.normalize(limits(credits=cs),NOW)}]}
        self.assertEqual([i["credit"]["id"] for i in quota.due(rep,self.root)],["edge"])

    def test_public_cli_json_pick_remind_and_plain_text(self):
        now=int(time.time())
        for home, identity in ((self.home,"account-a"),(self.slot,"account-b")):
            raw=limits(identity,credits=[credit("soon",now+DAY),credit("later",now+10*DAY)])
            raw["rateLimits"]["primary"]["resetsAt"]=now+3600
            raw["rateLimits"]["secondary"]["resetsAt"]=now+2*DAY
            self.put(home,raw)
        env={**self.env,"PYTHONPATH":str(Path(__file__).resolve().parents[1]),"AGENTJ_STATE_DIR":str(self.root),
             "AGENTJ_TEST":"1","AGENTJ_SKILL_LINK":"off","AGENTJ_HARNESS_PREPARE":"off",
             "XDG_CONFIG_HOME":str(self.root/"config")}
        def cli(*args):
            proc=subprocess.run([sys.executable,"-m","agentj.cli","quota",*args],env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(proc.returncode,0,proc.stderr)
            return proc.stdout
        rep=json.loads(cli("--json"))
        self.assertEqual(len(rep["codex_accounts"]),2)
        pick=json.loads(cli("--pick","--json"))
        self.assertEqual(pick["why"],"reset_credit_expiring")
        self.assertEqual(pick["codex_accounts"][0]["reset_credits"]["count"],2)
        reminded=json.loads(cli("--remind","--json"))
        self.assertEqual(len([i for i in reminded["due"] if i["kind"]=="native_reset_expiring"]),2)
        self.assertEqual(json.loads(cli("--remind","--json"))["due"],[])
        text=cli()
        self.assertIn("重置券 2 张",text)
        self.assertEqual(text.count("重置券到期："),4)

    def test_account_view_and_actual_host_meter_wire(self):
        from types import SimpleNamespace
        with patch.object(accounts,"load",return_value={"harness":self.registry}), patch.dict(os.environ,self.env,clear=True):
            view=accounts.view(self.root)
            self.assertEqual(view["codex"]["items"][1]["reset_credits"]["count"],1)
            host=object.__new__(serve.Host)
            host.st=SimpleNamespace(root=self.root)
            host.agent=SimpleNamespace(kind="codex",cfg={})
            host.meter_state={k:None for k in serve.METER_KEYS}
            host.meter_task=None
            host.meter_at=0
            host._post=lambda *a:None
            async def run():
                await native.refresh_meter(host)
                msg=host._meter_msg()
                self.assertEqual(msg["reset_credits"]["count"],1)
                if host.meter_task:
                    host.meter_task.cancel()
            asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
