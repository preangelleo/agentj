"""P144 paid-customer Telegram reply formatting; only local fake Bot API fixtures."""
import _hermetic  # noqa: F401,I001
import io
import json
import unittest
import urllib.error
from html.parser import HTMLParser
from unittest.mock import patch

from agentj import tg_format as fmt, telegram as tg
from test_p118_telegram import _Base, FAMILY


class Checked(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.text = [], ''

    def handle_starttag(self, tag, attrs):
        assert tag in ('b', 'i', 'u', 's', 'code', 'pre', 'a', 'blockquote')
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag

    def handle_data(self, data):
        self.text += data


class Formatting(unittest.TestCase):
    def test_bold_heading_lists_and_styles(self):
        rendered = fmt.markdown('# Title\n**bold** *italic* ++underline++ ~~strike~~\n- one\n1. two\n> quote\n')
        for token in ('<b>Title</b>', '<b>bold</b>', '<i>italic</i>', '<u>underline</u>', '<s>strike</s>', '• one', '• two', '<blockquote>quote</blockquote>'):
            self.assertIn(token, rendered)

    def test_intraword_underscores_in_paths_and_identifiers_stay_literal(self):
        path='/var/tmp/tmpa6i1yb_w/work/.agentj/handover/opencode-ses_000001fake39a89408.md'
        text=path+' foo_bar_baz __bold__ _italic_ _hidden_file **粗体**文字'
        rendered=fmt.markdown(text)
        self.assertIn(path,rendered); self.assertIn('foo_bar_baz',rendered)
        self.assertIn('<b>bold</b>',rendered); self.assertIn('<i>italic</i>',rendered)
        self.assertEqual(''.join(p for _,p in fmt.parts(text)),path+' foo_bar_baz bold italic _hidden_file 粗体文字')

    def test_code_links_and_untrusted_html_all_escaped(self):
        rendered = fmt.markdown('`**literal** <x>` [safe](https://example.org/?a=1&b=2) [bad](javascript:evil)\n```python\n<x>& **code**\n```\n<b>raw</b>')
        self.assertIn('<code>**literal** &lt;x&gt;</code>', rendered)
        self.assertIn('href="https://example.org/?a=1&amp;b=2"', rendered)
        self.assertNotIn('javascript:', rendered)
        self.assertIn('<pre>&lt;x&gt;&amp; **code**\n</pre>', rendered)
        self.assertIn('&lt;b&gt;raw&lt;/b&gt;', rendered)
        self.assertNotIn('<b>raw</b>', rendered)

    def test_long_chunks_tags_entities_emoji_and_same_visible_fallback(self):
        text = '# title\n**' + ('a<&😀 ' * 3000) + '**\n```\n' + ('<&😀' * 3000) + '\n```'
        parts = fmt.parts(text)
        self.assertGreater(len(parts), 10)
        visible = ''
        for rendered, plain in parts:
            self.assertLessEqual(len(rendered.encode('utf-16-le')) // 2, 3900)
            parser = Checked(); parser.feed(rendered); parser.close()
            self.assertEqual(parser.stack, [])
            self.assertEqual(parser.text, plain)
            visible += plain
        parser = Checked(); parser.feed(fmt.markdown(text))
        self.assertEqual(visible, parser.text)

    def test_malformed_markdown_and_nested_link_never_emit_raw_html(self):
        for text in ('**unclosed <script>', '[**bold**](https://example.org)', '> **quote**', '```\n<&', '**a ~~b~~ c**'):
            parser = Checked(); parser.feed(fmt.markdown(text))
            self.assertEqual(parser.stack, [])


class ReplyFormatting(_Base):
    async def test_private_and_group_drain_use_html_after_privacy_filter(self):
        for dest in (self.cfg['owner_id'], int(FAMILY['id'])):
            text = '**bold**\n- item\n'
            if dest == self.cfg['owner_id']:
                self.t.out.put_nowait((self.cfg, text))
            else:
                self.t.out.put_nowait((self.cfg, {'chat_id': dest, 'sender_id': 456, 'text': text}))
            await self.t.drain(self.cfg)
        sent = [x['fields'] for x in self.bot.calls if x['method'] == 'sendMessage']
        self.assertEqual(len(sent), 2)
        self.assertEqual({x['chat_id'] for x in sent}, {123, -100})
        for x in sent:
            self.assertEqual(x['parse_mode'], 'HTML')
            self.assertIn('<b>bold</b>', x['text']); self.assertIn('• item', x['text'])

    async def test_illegal_nesting_refusal_retries_same_piece_as_plain_then_continues(self):
        pieces = fmt.parts('**' + 'reply <& ' * 1000 + '**')
        self.t.out.put_nowait((self.cfg, '**' + 'reply <& ' * 1000 + '**'))
        calls = []
        def fake(cfg, method, values):
            calls.append(values)
            if values.get('parse_mode') and len(calls) == 1:
                raise tg.TelegramError('parse_entities')
        with patch.object(tg, 'api', side_effect=fake):
            await self.t.drain(self.cfg)
        self.assertEqual(calls[0]['text'], pieces[0][0])
        self.assertEqual(calls[1]['text'], pieces[0][1]); self.assertNotIn('parse_mode', calls[1])
        self.assertEqual([x['text'] for x in calls[2:]], [x[0] for x in pieces[1:]])

    async def test_transport_failure_does_not_duplicate_send(self):
        with patch.object(tg, 'api', side_effect=tg.TelegramError('timeout')) as send:
            with self.assertRaises(tg.TelegramError):
                await tg.send_formatted(self.cfg, 123, '<b>hi</b>', 'hi')
        self.assertEqual(send.call_count, 1)

    async def test_real_http_parse_error_is_sanitized_and_classified(self):
        # Exercise HTTPError body classification without printing URL/body or retrying network failures.
        err = urllib.error.HTTPError('https://invalid.example/bot-secret', 400, 'bad', {},
            io.BytesIO(json.dumps({'description': "Bad Request: can't parse entities: illegal nesting secret-text"}).encode()))
        with patch('urllib.request.OpenerDirector.open', side_effect=err):
            with self.assertRaises(tg.TelegramError) as caught:
                tg.api(self.cfg, 'sendMessage', {'text': '<b>hi</b>', 'parse_mode': 'HTML'})
        self.assertEqual(caught.exception.kind, 'parse_entities')
        self.assertNotIn('secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
