"""P536 provider transport is substituted; real API/host receipt routing is exercised."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace as NS
import test_p501_wechat as fixture
Transport = fixture.Transport
from agentj.wechat_protocol import API, Failure
from agentj.wechat_forward import on_forward, plain_text


class Forward(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.Channel.asyncSetUp
    asyncTearDown = fixture.Channel.asyncTearDown

    def ready(self):
        self.h.hist = {1: {'end':'done', 'reply':{'text':'hello'}, 'ts':1}}
        self.c.store.data.update(context='fixture-context', context_at=self.c.clock())
        self.transport = Transport([{}] * 20)
        self.c.api_factory = lambda account: API(account, self.transport)

    async def test_eligibility_and_capability_changes(self):
        self.ready(); self.assertTrue(self.c.can_forward())
        for key,value in [('context',''),('context_stale',True),('account',{})]:   # P554: age alone never blocks
            old=self.c.store.data.get(key);self.c.store.data[key]=value
            self.assertFalse(self.c.can_forward());self.c.store.data[key]=old
        self.c.error='poll_failed';self.assertFalse(self.c.can_forward());self.c.error=None
        self.stopped=True;self.assertFalse(self.c.can_forward());self.stopped=False
        await self.c.unbind();self.assertFalse(self.c.can_forward())

    async def test_real_api_receipt_and_dedupe(self):
        self.ready()
        self.assertTrue((await self.c.forward(1,'phone'))['ok'])
        body=json.loads(self.transport.calls[0][0][3])
        self.assertEqual(body['msg']['to_user_id'],'owner')
        self.assertEqual(body['msg']['item_list'][0]['text_item']['text'],'hello')
        self.assertEqual((await self.c.forward(1,'phone'))['why'],'recently_sent')
        self.assertEqual(len(self.transport.calls),1)

    async def test_failure_no_retry_until_explicit_request(self):
        self.ready();self.transport.replies=[Failure('timeout'),{}]
        self.assertFalse((await self.c.forward(1,'phone'))['ok'])
        await asyncio.sleep(0)
        self.assertEqual(len(self.transport.calls),1)
        self.assertTrue((await self.c.forward(1,'phone'))['ok'])
        self.assertEqual(len(self.transport.calls),2)

    async def test_platform_error_is_not_success(self):
        self.ready();self.transport.replies=[{'ret':-14}]
        self.assertFalse((await self.c.forward(1,'phone'))['ok'])
        self.assertNotIn(1,self.c.fwd_sent)

    async def test_refuse_missing_open_and_silent_turns(self):
        self.ready()
        for value in [None,'1',True,999]:self.assertFalse((await self.c.forward(value,'phone'))['ok'])
        self.h.hist[1]['end']='open';self.assertFalse((await self.c.forward(1,'phone'))['ok'])
        self.assertFalse(self.transport.calls)

    async def test_inflight_rate_and_expired_context(self):
        self.ready();self.c.fwd_inflight.add(1)
        self.assertEqual((await self.c.forward(1,'phone'))['why'],'in_flight')
        self.c.fwd_inflight.clear()
        self.c.store.data['context']=''
        self.assertEqual((await self.c.forward(1,'phone'))['why'],'needs_message')
        self.assertFalse(self.transport.calls)

    async def test_phone_receipt_and_revoked_device(self):
        self.ready();s=NS(cid='c',state='ready',pub=b'p',device='phone')
        self.h.sessions={'c':s};self.st.is_allowed=lambda _:True
        await on_forward(self.h,s,'r',1)
        self.h.send_app.assert_awaited_once_with(s,{'t':'wx_fwd_res','r':'r','ok':True})
        self.h.send_app.reset_mock();self.st.is_allowed=lambda _:False
        await on_forward(self.h,s,'r',1);self.h.send_app.assert_not_awaited()
        self.assertEqual(len(self.transport.calls),1)

    async def test_greeting_first_message_once(self):
        # P562: the greeting rides on the first answer the main conversation sends back
        self.c.store.data['greeting']='needs_message'
        await self.c.receive(self.api,fixture.Channel.msg(self),self.gen)
        await fixture.Channel.complete(self,201,'译文')
        self.assertEqual(self.c.store.data['greeting'],'sent')
        sent=[json.loads(args[0][3])['msg'] for args in self.tr.calls if '/sendmessage' in args[0][1]]
        self.assertTrue(sent[0]['item_list'][0]['text_item']['text'].startswith('你好'))
        await self.c.receive(self.api,fixture.Channel.msg(self,message_id=2),self.gen)
        await fixture.Channel.complete(self,202,'译文')
        sent=[json.loads(args[0][3])['msg'] for args in self.tr.calls if '/sendmessage' in args[0][1]]
        self.assertFalse(sent[-1]['item_list'][0]['text_item']['text'].startswith('你好'))

    async def test_greeting_fallback_is_explicit(self):
        self.c.store.data.update(context='',greeting='needs_message')
        view=self.c.view()
        self.assertEqual(view['greeting'],'needs_message');self.assertFalse(view['can_forward'])
        self.assertFalse(self.tr.calls)

    async def test_long_reply_upload_revalidates_binding_and_device(self):
        from unittest.mock import patch
        self.ready(); self.h.hist[1]['reply']['text']='long reply ' * 1000
        item={'type':4,'file_item':{'file_name':'Agent-J-reply.txt'}}
        with patch('agentj.wechat_forward.Media.upload', return_value=item) as upload:
            self.assertTrue((await self.c.forward(1,'phone'))['ok'])
            self.assertEqual(upload.call_args.args[2], ('long reply '*1000).strip().encode())
        self.c.fwd_sent.clear();self.transport.calls.clear()
        def revoked(*_): self.stopped=True;return item
        with patch('agentj.wechat_forward.Media.upload', side_effect=revoked):
            self.assertFalse((await self.c.forward(1,'phone'))['ok'])
        self.assertFalse(self.transport.calls)

    async def test_rate_limit_and_secret_redaction(self):
        import time
        self.ready();self.c.fwd_rate['phone']=[time.monotonic()]*6
        self.assertEqual((await self.c.forward(1,'phone'))['why'],'rate_limited')
        self.c.fwd_rate.clear();self.h.hist[1]['reply']['text']='Authorization: Bearer '+('fixture-value-'*4)
        self.assertTrue((await self.c.forward(1,'phone'))['ok'])
        self.assertNotIn('fixture-value-',self.transport.calls[0][0][3].decode())


SAMPLE = """# 今日进展

**结论**：P536 已*完成*，详见 [报告](https://example.com/r) 和 `REPORT-536.md`。

> 注意：__不要__ 直接发布
> ~~旧方案~~ 作废

- 第一项 **重点**
* 第二项
  + 子项 snake_case_name 保留
1) 先看图
2. 再拍板
- [ ] 待办
- [x] 已办

| 项目 | 状态 |
|:---|---:|
| 微信 | **通过** |

---

```python
def f(**kwargs):
    # 注释保留
    return a*b*c
```

图片 ![截图](https://example.com/a.png) <https://agentj.app>
派不派？[派](reply:派) [先不派](reply:先不派) 2*3*4 \\*字面星号\\*
"""
EXPECTED = """今日进展

结论：P536 已完成，详见 报告 https://example.com/r 和 REPORT-536.md。

注意：不要 直接发布
旧方案 作废

• 第一项 重点
• 第二项
  • 子项 snake_case_name 保留
1. 先看图
2. 再拍板
• ☐ 待办
• ☑ 已办

项目  状态
微信  通过

def f(**kwargs):
    # 注释保留
    return a*b*c

图片 截图 https://example.com/a.png https://agentj.app
派不派？派 先不派 2*3*4 *字面星号*"""


class PlainText(unittest.TestCase):
    """#304: everything sent to WeChat is Markdown-free plain text; Telegram is untouched."""
    def test_typical_reply_sample(self):
        self.assertEqual(plain_text(SAMPLE), EXPECTED)

    def test_edges(self):
        for src, want in [('***两者***', '两者'), ('[https://a.b](https://a.b)', 'https://a.b'), ('a\r\n\n\n\nb', 'a\n\nb'),
                          ('~~~\n# x\n~~~', '# x'), ('###### 标题 ###', '标题'), ('> > 嵌套', '嵌套'), ('第一行<br>第二行', '第一行\n第二行'),
                          ('普通文字，没有格式。', '普通文字，没有格式。'), ('', '')]:
            self.assertEqual(plain_text(src), want, src)

    def test_telegram_path_keeps_markdown(self):
        import inspect
        from agentj import telegram
        self.assertNotIn('plain_text', inspect.getsource(telegram))


class PlainSend(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.Channel.asyncSetUp
    asyncTearDown = fixture.Channel.asyncTearDown
    ready = Forward.ready

    async def test_forward_and_twin_reply_send_plain_text(self):
        from unittest.mock import patch
        self.ready();self.h.hist[1]['reply']['text']='**粗体** [链接](https://x.y) [好](reply:好)'
        self.assertTrue((await self.c.forward(1,'phone'))['ok'])
        body=json.loads(self.transport.calls[0][0][3])
        self.assertEqual(body['msg']['item_list'][0]['text_item']['text'],'粗体 链接 https://x.y 好')
        self.c.fwd_sent.clear();self.h.hist[1]['reply']['text']='## 长文\n' + '**块** ' * 1000
        item={'type':4,'file_item':{'file_name':'Agent-J-reply.txt'}}
        with patch('agentj.wechat_forward.Media.upload', return_value=item) as upload:
            self.assertTrue((await self.c.forward(1,'phone'))['ok'])
        sent=upload.call_args.args[2].decode()
        self.assertTrue(sent.startswith('长文\n块 块'));self.assertNotIn('*',sent);self.assertNotIn('#',sent)
        await self.c.receive(self.api,fixture.Channel.msg(self),self.gen)     # P562: the main answer, sent back as plain text
        await fixture.Channel.complete(self,201,'# 译文\n- **一**\n`代码`')
        sent=[json.loads(args[0][3])['msg'] for args in self.transport.calls if '/sendmessage' in args[0][1]]
        self.assertEqual(sent[-1]['item_list'][0]['text_item']['text'],'译文\n• 一\n代码')


class HostWire(unittest.IsolatedAsyncioTestCase):
    async def test_caps_dispatch_and_phone_receipt(self):
        import tempfile
        from test_l1 import _host, _state, Phone, _ready
        with tempfile.TemporaryDirectory(dir=fixture.TEST_TMP) as path:
            st=_state(path);sent=[];host=_host(st,sent);phone=Phone(st);s=_ready(host,phone);s.p33=True
            wc=NS(can_forward=lambda:True,store=NS(data={'account':{'owner':'fixture'}}),
                  forward=AsyncMock(return_value={'ok':True}))
            host.wechat=wc
            self.assertIn('wxfwd',host._caps(s)['caps']);self.assertIn('wxbound',host._caps(s)['caps'])
            await host.on_p33(s,'wx_fwd',{'t':'wx_fwd','r':'p536','id':7})
            for _ in range(50):
                if sent:break
                await asyncio.sleep(.01)
            self.assertEqual(sent[-1][1],{'t':'wx_fwd_res','r':'p536','ok':True})
            wc.can_forward=lambda:False
            self.assertNotIn('wxfwd',host._caps(s)['caps']);self.assertIn('wxbound',host._caps(s)['caps'])
            wc.store.data['account']={};self.assertNotIn('wxbound',host._caps(s)['caps'])
