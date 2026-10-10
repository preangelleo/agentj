import _hermetic
import unittest
from unittest.mock import patch
import test_p109_bots
from agentj import update,phone_update
from agentj.bots.provider import Provider,invoke
from agentj.bots.visitor import VisitorSession,issue_claim,public,client_finish
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from agentj.wire import b64u
import time

class WebHistory(unittest.IsolatedAsyncioTestCase):
    setUp=test_p109_bots.Bots.setUp
    tearDown=test_p109_bots.Bots.tearDown
    bot=test_p109_bots.Bots.bot
    engine=test_p109_bots.Bots.engine
    async def test_web_visitor_original_and_second_turn_http_content(self):
        bid=self.bot(enabled=True,terms_accepted=True);bodies=[]
        signing=Ed25519PrivateKey.generate();issuer=Ed25519PrivateKey.generate();client_key=X25519PrivateKey.generate()
        session=VisitorSession(bid,self.s.lifecycle(bid)['epoch'],signing,public(issuer))
        ticket=issue_claim(issuer,bid,b64u(public(signing)),b64u(public(client_key)),session.epoch,int(time.time())+299)
        client=client_finish(client_key,session.accept(ticket),public(signing))
        v,cap=self.s.admit_visitor(bid,session.visitor)
        session.visitor=v  # Runtime admits the web public key under the Store-owned visitor ID.
        def transport(url,method,body,headers,**kw):
            bodies.append(body);return {'choices':[{'message':{'content':'列表可修改，元组不可修改。'}}],'usage':{'total_tokens':5}}
        def model(p,messages,*args):return invoke(Provider('https://example.com/v1','fixture','chat','dummy'),messages,transport=transport)
        e=self.engine(model=model)
        for rid,text in [('a'*32,'网页回归：Python list 和 tuple 有什么区别？'),('b'*32,'请给个例子。')]:
            frame=client.seal({'t':'say','r':rid,'text':text});self.assertNotIn(text.encode(),frame)
            obj=session.decode(frame);self.s.authenticate(bid,session.visitor,cap)
            result=await e.reply(bid,session.visitor,cap,obj['r'],obj['text'])
            reply=client.open(session.cipher.seal({'t':'reply','r':obj['r'],**result}))
            self.assertEqual(reply['text'],'列表可修改，元组不可修改。')
        self.assertEqual(bodies[0]['messages'][-1],{'role':'user','content':'网页回归：Python list 和 tuple 有什么区别？'})
        self.assertIn({'role':'assistant','content':'列表可修改，元组不可修改。'},bodies[1]['messages'])
        self.assertEqual([m['content'] for m in bodies[1]['messages'] if m['role']=='user'],['网页回归：Python list 和 tuple 有什么区别？','请给个例子。'])

class Receipts(unittest.TestCase):
    def test_bilingual_pass_warn_fail_missing_and_no_private_detail(self):
        rec={'reason':'upgraded','from':'0.17.2a1','to':'0.17.3a1','counts':[30,4,0]}
        for lang in ('zh','en'):
            text=phone_update.text(rec,lang)
            self.assertIn('0.17.3a1',text);self.assertIn('0.17.2a1',text)
            self.assertIn('right' if lang=='en' else '右上角',text)
            self.assertIn('4',text);self.assertNotIn('doctor 30',text)
            if lang == 'en':
                self.assertIn('Update and reload', text);self.assertIn('4 reminders', text);self.assertNotIn('(s)', text)
                self.assertIn('1 reminder,', phone_update.text({**rec,'counts':[30,1,0]},lang))
            failed={**rec,'reason':'doctor_failed','counts':[30,0,3],'checks':[{'id':'service','status':'fail','detail':'PRIVATE_CANARY'},{'id':'platform','status':'fail'},{'id':'config','status':'fail'}]}
            text=phone_update.text(failed,lang)
            self.assertIn('background service' if lang=='en' else '后台服务',text);self.assertIn('operating system' if lang=='en' else '操作系统',text);self.assertIn('configuration' if lang=='en' else '程序配置',text);self.assertIn('agentj doctor',text);self.assertNotIn('PRIVATE_CANARY',text)
            self.assertIn('could not finish' if lang=='en' else '未能完成',phone_update.text({**rec,'counts':None},lang))
        with patch.object(update,'__version__','0.17.3a1'):
            self.assertEqual(update.upgraded_text(rec,[30,4,0]),phone_update.text(rec))
        self.assertNotIn('已升级到',phone_update.text({'reason':'install_failed'}))

class DictionaryPackaging(unittest.TestCase):
    def test_recompressed_wake_dictionary_preserves_all_original_bytes(self):
        import gzip,hashlib
        from pathlib import Path
        data=Path(__file__).resolve().parents[1]/'agentj/config/en.phone.gz'
        self.assertEqual(hashlib.sha256(gzip.decompress(data.read_bytes())).hexdigest(),
                         'f7000ec3a90544c0c7c16090d8951779c2b322e14dad5006290f498567d439ea')
