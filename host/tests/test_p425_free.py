"""P425 #6: signed in-card free answers, exact cross-language bytes and native/CLI delivery."""
import _hermetic  # noqa: F401
import asyncio
import contextlib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import types
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from agentj import approvals, ask, wire
from agentj.serve import Host

CARD = approvals.norm_questions([{"question": "Your answer?", "options": [{"label": "A"}, {"label": "B"}]}])
TEXT = "我自己填写\nemoji 🐈 and ;1,2"

class FreeAnswers(unittest.TestCase):
    def test_validation_and_legacy_numbers(self):
        for value in [TEXT, "x" * 2000, "🐈" * 1000]:
            self.assertTrue(approvals.check_picks(CARD, [value]))
        for value in ["", " \n", "x" * 2001, "🐈" * 1001, "a\r\nb", "x\x00", "x\ud800", {}, True]:
            self.assertFalse(approvals.check_picks(CARD, [value]), repr(value))
        self.assertTrue(approvals.check_picks(CARD, [[2]]))
        self.assertEqual(approvals.picks_text([[2]]), "2")
        self.assertEqual(approvals.picks_text([TEXT]), "t:" + hashlib.sha256(TEXT.encode()).hexdigest())
        self.assertNotIn(TEXT, approvals.picks_text([TEXT]))

    def test_javascript_python_signature_bytes_and_limits(self):
        node = shutil.which("node")
        self.assertIsNotNone(node)
        root = Path(__file__).resolve().parents[2]
        code = '''import {questionMessage,checkPicks} from './protocol/wire.js';
let s='';for await(const x of process.stdin)s+=x;const v=JSON.parse(s);
const m=await questionMessage('ch','dev','qid','answer',v.qs,[v.text]);
console.log(JSON.stringify({hex:Buffer.from(m).toString('hex'),bad:['','x'.repeat(2001),'🐈'.repeat(1001),'x\\r','x\\ud800'].map(x=>checkPicks(v.qs,[x]))}));'''
        r = subprocess.run([node, '--input-type=module', '-e', code], cwd=root,
                           input=json.dumps({"qs": CARD, "text": TEXT}), text=True, capture_output=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got["hex"], approvals.question_message("ch", "dev", "qid", "answer", CARD, [TEXT]).hex())
        self.assertEqual(got["bad"], [False] * 5)

    def test_real_host_rejects_tampering_and_late_then_delivers_original(self):
        async def go():
            sk = Ed25519PrivateKey.generate()
            pub = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            import time
            q = types.SimpleNamespace(qs=CARD, deadline=time.monotonic()+60, fut=asyncio.get_running_loop().create_future())
            logs=[]
            h = types.SimpleNamespace(questions={"id":q},channel="ch", st=types.SimpleNamespace(sign_key=lambda _:pub, log=lambda *a,**k:logs.append((a,k))))
            s=types.SimpleNamespace(device="dev")
            sig=wire.b64u(sk.sign(approvals.question_message("ch","dev","id","answer",CARD,[TEXT])))
            Host._q_answer(h,s,{"id":"id","pick":[TEXT+'changed'],"sig":sig})
            self.assertFalse(q.fut.done())
            q.deadline=0;Host._q_answer(h,s,{"id":"id","pick":[TEXT],"sig":sig});self.assertFalse(q.fut.done())
            q.deadline=time.monotonic()+60
            Host._q_answer(h,s,{"id":"id","pick":[TEXT],"sig":sig})
            self.assertEqual(q.fut.result()[1],[TEXT])
            self.assertNotIn(TEXT,repr(logs))
        asyncio.run(go())

    def test_v2_free_text_matching_a_label_is_not_mapped_to_its_backend_value(self):
        from agentj.agent_opencode2 import OpenCodeV2Agent
        async def go():
            for kind,pick,want in [('string','Label','Label'),('string',[1],'backend'),
                                   ('multiselect','Label',['Label']),('multiselect',[1],['backend'])]:
                sent=[]
                async def question(*a,**kw):return 'answer',[pick]
                async def request(*a):sent.append(a)
                agent=types.SimpleNamespace(questions={},q_sent={},sid='s',research=False,children={},   # P426: sub-agent form hooks
                    _label=lambda raw,sid:raw,_src_ctx=lambda sid:contextlib.nullcontext(),_req_src=lambda sid:None,_refused=lambda:False,
                    host=types.SimpleNamespace(question=question),client=types.SimpleNamespace(request=request))
                form={'id':'f','fields':[{'key':'x','type':kind,'title':'Q','options':[{'label':'Label','value':'backend'}]}]}
                await OpenCodeV2Agent._form(agent,form)
                self.assertEqual(sent[-1][-1],{'answer':{'x':want}})
        asyncio.run(go())

    def test_cli_and_native_claude_receive_answer_without_waiting_for_chat(self):
        card,meta=ask.build([{"question":"Your answer?"}])
        a=ask.answers(card,meta,[TEXT])[0]
        self.assertEqual(a['picked'],[TEXT]);self.assertFalse(a['typed_in_chat'])
        async def answer(*a,**kw):return 'answer',[TEXT]
        h=types.SimpleNamespace(question=answer,task_label=None)
        raw={"questions":[{"question":"Your answer?","options":[{"label":"A"}]}]}
        result=asyncio.run(Host._ask_user_question(h,raw,None))
        self.assertEqual(result['updatedInput']['answers'],{'Your answer?':TEXT})


from test_p33_chain import _Chain

class _NativeFree:
    def test_free_answer_reaches_native_tool(self):
        text="my custom answer"
        async def script(c):
            raw={"question":"Pick one", "header":"H", "options":[{"label":"A"},{"label":"B"}]}
            if self.KIND=="codex":raw.update(id="q1",isOther=False,isSecret=False)
            sid=await c["say"]("ASK: "+json.dumps([raw]))
            await c["wait"](lambda:c["of"]("question"))
            q=c["of"]("question")[-1]
            self.assertEqual(q['text_max'],2000)
            await c["answer_q"](q,[text])
            await c["wait"](lambda:c["of"]("question_done"))
            rr=c["res"](sid)
            await c["wait"](lambda:c["turns"]().get(rr["turn"],{}).get("end")=="done")
            self.assertIn(text,c["turns"]()[rr["turn"]]["reply"]["text"])
            audit=list(self.st.root.glob("approvals.log"))
            self.assertTrue(audit)
            self.assertNotIn(text,audit[0].read_text())
        self.run_chain(script)

class ClaudeFree(_NativeFree,_Chain):KIND="claude"
class CodexFree(_NativeFree,_Chain):KIND="codex"
class OpenCodeFree(_NativeFree,_Chain):KIND="opencode"

if __name__=='__main__':unittest.main()
