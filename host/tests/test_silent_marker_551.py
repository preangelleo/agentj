import _hermetic
import unittest
from agentj import silent
from agentj.readmark import ReadMark
from agentj import hsearch

MARKERS = ('〔静默消息〕', '[silent]', '〔不回群〕')

class Contract(unittest.TestCase):
    def test_boundaries(self):
        for marker in MARKERS:
            for text in (marker, marker+'正文', '正文'+marker, '\n '+marker+'正文 \t'):
                with self.subTest(text=text): self.assertTrue(silent.is_silent(text))
            self.assertFalse(silent.is_silent('前'+marker+'后'))
        self.assertFalse(silent.is_silent(None))
        self.assertFalse(silent.is_silent(''))
    def test_markers_never_visible(self):
        for marker in MARKERS:
            self.assertEqual('前后',silent.strip_markers('前'+marker+'后'))
    def test_unread_and_search_share_boundary_rule(self):
        for marker in MARKERS:
            for text in (marker,marker+'正文','正文'+marker):
                turn={'reply':{'text':text}}
                self.assertFalse(ReadMark.visible(turn))
                self.assertFalse(hsearch.visible(turn))
            turn={'reply':{'text':'前'+marker+'后'}}
            self.assertTrue(ReadMark.visible(turn));self.assertTrue(hsearch.visible(turn))
    def test_history_projection_keeps_raw_and_hides_marker(self):
        for marker in MARKERS:
            turn={'id':1,'src':{'text':'source'},'reply':{'text':marker+'正文','pre':'先说'+marker}}
            projected=silent.public_turn(turn)
            self.assertTrue(projected['silent'])
            self.assertEqual('',projected['reply']['text'])
            self.assertEqual('先说\n\n正文',projected['reply']['pre'])
            self.assertEqual(marker+'正文',turn['reply']['text'])
            self.assertEqual('前后',silent.public_turn({'reply':{'text':'前'+marker+'后'}})['reply']['text'])

from types import SimpleNamespace as NS
from unittest.mock import Mock, AsyncMock, patch
import asyncio
from agentj import telegram, mail, wechat
from agentj.serve import Host

class Channels(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_silent_and_middle_reply(self):
        h=NS(lang='zh',st=NS(log=Mock()))
        tg=NS(host=h,out=asyncio.Queue(),group=Mock(return_value={'profile':'proxy'}),private_domains=Mock(return_value=[]))
        for marker in MARKERS:
            for text in (marker,marker+'正文','正文'+marker):
                self.assertFalse(telegram.Telegram.queue_reply(tg,{'owner_id':1},9,2,2,text))
            with patch.object(telegram,'group_filter',side_effect=lambda text,*args:text):
                self.assertTrue(telegram.Telegram.queue_reply(tg,{'owner_id':1},9,2,2,'前'+marker+'后'))
            self.assertEqual('前后',tg.out.get_nowait()[1]['text'])
        self.assertTrue(tg.out.empty())

    async def test_mail_silent_skips_send_and_normal_middle_is_clean(self):
        for marker in MARKERS:
            for text in (marker,marker+'正文','正文'+marker,'前'+marker+'后'):
                state={'routes':{'9':{'gen':1,'role':'guest','to':'guest@example.test'}},'hour':{}}
                cfg={'generation':1,'allow':[],'enabled':True,'reply_per_hour':10}
                m=NS(st=NS(),lang=lambda:'zh',count=Mock(),log=Mock(),password=lambda cfg:'test',host=NS(ask=AsyncMock(return_value={'behavior':'deny'})))
                with patch.object(mail,'read_state',return_value=state),patch.object(mail,'read_config',return_value=cfg),patch.object(mail,'save_state'),patch.object(mail,'allow_row',return_value={'role':'guest'}),patch.object(mail,'build_reply',side_effect=lambda cfg,route,body,*args:{'To':'guest@example.test','body':body}) as build,patch.object(mail,'send_target',return_value={}):
                    result=await mail.Mail.reply(m,9,text)
                if text.startswith('前'):
                    self.assertEqual('denied',result)
                    self.assertEqual('前后',build.call_args.args[2])
                else:
                    self.assertEqual('silent',result);m.host.ask.assert_not_called()

    async def test_wechat_silence_has_priority_over_failed_status(self):
        for marker in MARKERS:
            for text in (marker,marker+'正文','正文'+marker):
                r={'generation':1}
                wc=NS(store=NS(data={'generation':1,'account':{'id':1}},save=Mock()),h=NS(stopped=lambda:False,lang='zh'))
                with patch.object(wechat.owner_channels if hasattr(wechat,'owner_channels') else __import__('agentj.owner_channels',fromlist=['x']),'lang_of',return_value='zh'):
                    for failed in (False,True):
                        await wechat.WeChat.answer_main(wc,r,text,failed)
                        self.assertEqual('silent',r['outcome'])

class Delivery(unittest.TestCase):
    def test_final_folds_prior_text_without_live_event(self):
        for marker in MARKERS:
            hist=NS(get=lambda tid:{'reply':{'text':'先前正文'}},parts={})
            h=NS(cur_turn=7,hist=hist,turn_text=True,turn_shown=False,jev_plan='',hist_update=Mock(return_value=[]),emit=Mock(),st=NS(log=Mock()))
            Host.agent_text(h,marker+'静默正文')
            h.hist_update.assert_called_once_with(7,text=marker+'静默正文',pre='先前正文')
            self.assertFalse(h.turn_text);h.emit.assert_not_called()
    def test_last_mile_cleans_nested_history_search_and_notice(self):
        for marker in MARKERS:
            obj={'t':'hist_turn','turn':{'src':{'text':'前'+marker+'后'},'reply':{'text':marker+'正文'}}}
            result=silent.public_message(obj)
            self.assertTrue(result['turn']['silent'])
            self.assertEqual('〔不回群〕',result['turn']['reply']['text'])
            self.assertNotIn(marker,result['turn']['src']['text'])
            self.assertEqual('前后',silent.public_message({'t':'hist_found','snip':'前'+marker+'后'})['snip'])

class AttachmentSafety(unittest.TestCase):
    def test_channel_reply_attachments_survive_marker_cleanup(self):
        from agentj.channel_avatar import Reply
        for marker in MARKERS:
            reply=Reply('前'+marker+'后',images=[('image/png',b'img')],files=[('file.txt','text/plain',b'data')])
            clean=silent.strip_markers(reply)
            self.assertEqual('前后',clean)
            self.assertEqual(reply.images,clean.images)
            self.assertEqual(reply.files,clean.files)

class Desktop(unittest.TestCase):
    def test_final_silences_desktop_prior_notes(self):
        h=NS(desktop_turn=7,st=NS(log=Mock()),hist=NS(parts={},get=lambda tid:{'reply':{'text':'先前正文'}}),hist_update=Mock(),media_later=Mock())
        Host.desktop_text(h,'正文[silent]')
        h.hist_update.assert_called_once_with(7,text='正文[silent]',pre='先前正文')
        h.media_later.assert_not_called()

class MalformedProjection(unittest.TestCase):
    def test_missing_source_still_silent_and_all_display_strings_clean(self):
        for marker in MARKERS:
            for src in ({}, {'src':None}):
                obj={'t':'hist_turn','turn':{'reply':{'text':marker+'正文'},**src}}
                result=silent.public_message(obj)
                self.assertTrue(result['turn']['silent']);self.assertEqual('〔不回群〕',result['turn']['reply']['text'])
            card={'t':'hist_turn','card':{'title':marker+'标题','summary':'前'+marker+'后','body':'正文'+marker,'rows':[marker, {'nested':marker+'值'}]}}
            self.assertNotIn(marker,str(silent.public_message(card)))

class WeChatFailureFallback(unittest.IsolatedAsyncioTestCase):
    async def test_failed_empty_keeps_notice_and_middle_marker_is_removed(self):
        from agentj import owner_channels
        for text in ('', '前[silent]后'):
            r={'generation':1}
            wc=NS(store=NS(data={'generation':1,'account':{'id':1}},save=Mock()),h=NS(stopped=lambda:False,lang='zh'))
            # Stop at the first attachment operation: no provider/network call.
            with patch.object(owner_channels,'lang_of',return_value='zh'),patch.object(wechat,'attachments',side_effect=RuntimeError('captured')) as attach:
                with self.assertRaisesRegex(RuntimeError,'captured'):
                    await wechat.WeChat.answer_main(wc,r,text,True)
            self.assertEqual('前后' if text else '这一轮没有完成，请在 Agent J 网页查看。',attach.call_args.args[1])

class MachineContractSafety(unittest.TestCase):
    def test_secret_and_signed_approval_payloads_are_byte_preserved(self):
        for marker in MARKERS:
            for payload in ({'t':'secret_out_val','value':'credential'+marker},
                            {'t':'ask','summary':marker,'goal_add':{'text':'limit'+marker},'signature':marker},
                            {'t':'preferences','value':{'agent':{'instructions':marker}}}):
                self.assertIs(payload,silent.public_message(payload))
                self.assertIn(marker,str(payload))

class Fallback(unittest.TestCase):
    def test_failure_cleanup_precedes_truncation(self):
        h=NS()
        Host.fallback_failure(h,'x'*3998+'[silent]'+'正文')
        self.assertEqual('x'*3998+'正文',h._fallback_error)

class LongHistory(unittest.TestCase):
    def test_real_history_keeps_every_long_reply_part_silent_across_restart(self):
        import tempfile
        from pathlib import Path
        from agentj.history import History, PART_MAX
        from agentj.state import State
        for marker in MARKERS:
            for text in (marker+'字'*(PART_MAX+10), '字'*(PART_MAX+10)+marker):
                for prior in ('', '先前正文'):
                    with tempfile.TemporaryDirectory() as tmp:
                        st=State(Path(tmp)/'state');st.init(relay='ws://127.0.0.1:1')
                        hist=History(st);turn=hist.add({'k':'phone','text':'输入'}, prior,'open')
                        if prior:
                            hist.update(turn['id'],text=text,pre=prior)
                        else:
                            hist.update(turn['id'],append=text)
                        again=History(st)
                        pages=list(again.turns.values())
                        self.assertTrue(all(t.get('silent') is True for t in pages))
                        self.assertTrue(all(not ReadMark.visible(t) and not hsearch.visible(t) for t in pages))
                        self.assertTrue(all(silent.public_turn(t)['reply']['text']=='' for t in pages))
                        raw=''.join(t['reply'].get('silent_raw',t['reply']['text']) for t in pages)
                        self.assertIn(text,raw)

class SilentThenResult(unittest.TestCase):
    def test_normal_final_clears_persisted_silence(self):
        import tempfile
        from pathlib import Path
        from agentj.history import History
        from agentj.state import State
        for prior in ('', '先前过程'):
            with tempfile.TemporaryDirectory() as tmp:
                st=State(Path(tmp)/'state');st.init(relay='ws://127.0.0.1:1')
                hist=History(st);page=hist.add({'k':'phone','text':'输入'},prior,'open')
                h=NS(cur_turn=page['id'],hist=hist,turn_text=bool(prior),turn_shown=False,jev_plan='',hist_update=hist.update,emit=Mock(),st=st,_remember=Mock(),_post=Mock(),_send_legacy=Mock(),media_later=Mock())
                Host.agent_text(h,'[silent]')
                self.assertFalse(ReadMark.visible(hist.get(page['id'])))
                with patch('agentj.serve.login_flow.on_text'):
                    Host.agent_text(h,'现在有结果')
                final=hist.get(page['id'])
                self.assertTrue(ReadMark.visible(final));self.assertTrue(hsearch.visible(final))
                self.assertNotIn('[silent]',final['reply']['text'])
                self.assertIn('现在有结果',final['reply']['text'])
