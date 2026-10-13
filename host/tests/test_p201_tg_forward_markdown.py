"""P201: the phone's 「转发到 Telegram」 sends the reply rendered (Telegram HTML), like a Telegram reply — never plain text.
Golden reply styles (report table, code block with language, links, lists, quotes, a long report); local fake Bot API only."""
import _hermetic  # noqa: F401,I001
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from agentj import tg_format as fmt, telegram as tg
from test_p118_telegram import _Base, OWNER

REPORT = '''## P201 结论

**是否完成**：是。转发路径从纯文本改成 *HTML*，失败自动退回纯文本。

| 项 | 状态 | 证据 |
|---|:-:|---|
| 回复卡转发 | ✅ | `telegram.forward` |
| 文件卡片 | 不变 | 只有文件名 |

1. 找到路径
2. 改成 `parse_mode=HTML`
   - 子项 snake_case_name 保持原样
- [x] 已测
- [ ] 待 Leo 看

> 注意：**不要**轮换 key
> 第二行引用

```python
def f(x):
    return x < 3 and x > 1  # <&>
```

详见 [报告](https://example.org/r?a=1&b=2)，路径 ~/work/notes/REPORT_201.md 和 https://relay.example/a_b_c。

---'''

REPORT_HTML = '''<b>P201 结论</b>

<b>是否完成</b>：是。转发路径从纯文本改成 <i>HTML</i>，失败自动退回纯文本。

<pre>项         │ 状态 │ 证据
───────────┼──────┼─────────────────
回复卡转发 │ ✅   │ telegram.forward
文件卡片   │ 不变 │ 只有文件名</pre>

1. 找到路径
2. 改成 <code>parse_mode=HTML</code>
   • 子项 snake_case_name 保持原样
☑ 已测
☐ 待 Leo 看

<blockquote>注意：<b>不要</b>轮换 key
第二行引用</blockquote>

<pre><code class="language-python">def f(x):
    return x &lt; 3 and x &gt; 1  # &lt;&amp;&gt;</code></pre>

详见 <a href="https://example.org/r?a=1&amp;b=2">报告</a>，路径 ~/work/notes/REPORT_201.md 和 https://relay.example/a_b_c。

──────────'''

WIDE = '''| 时间 | 项目 | 结论 | 下一步 |
|---|---|---|---|
| 08:20 | vibe-remote 转发 | Markdown 没渲染成富文本 | P201 修 **tg_format** |
| 09:10 | 官网 | 版本号对齐 | |'''

WIDE_HTML = '''▪ <b>08:20</b>
   项目：vibe-remote 转发
   结论：Markdown 没渲染成富文本
   下一步：P201 修 <b>tg_format</b>

▪ <b>09:10</b>
   项目：官网
   结论：版本号对齐'''


class Checked(HTMLParser):
    ALLOWED = ('b', 'i', 'u', 's', 'code', 'pre', 'a', 'blockquote')

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.text = [], ''

    def handle_starttag(self, tag, attrs):
        assert tag in self.ALLOWED, tag
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag

    def handle_data(self, data):
        self.text += data


def long_report(n=60):
    rows = '\n'.join(f'| {i} | 任务 {i} | **完成** |' for i in range(n))
    code = '\n'.join(f'    step_{i} = run("<cmd {i}>") && echo ok' for i in range(150))
    paras = '\n\n'.join(f'第 {i} 段：这是一段较长的说明文字，含 `code_{i}` 与 [链接 {i}](https://example.org/{i})。' * 3 for i in range(30))
    return f'# 长报告\n\n| # | 名 | 状态 |\n|---|---|---|\n{rows}\n\n```bash\n{code}\n```\n\n{paras}'


class Golden(unittest.TestCase):
    def test_report_golden(self):
        self.assertEqual(fmt.markdown(REPORT), REPORT_HTML)

    def test_wide_table_becomes_row_cards(self):
        self.assertEqual(fmt.markdown(WIDE), WIDE_HTML)

    def test_escapes_autolinks_images_rules_and_nested_quotes(self):
        h = fmt.markdown(r'\*not italic\* <https://a.example/x_y_z> ![图](https://a.example/i.png) a<b>&c' + '\n>> deep\n***')
        self.assertIn('*not italic*', h); self.assertNotIn('<i>', h)
        self.assertIn('<a href="https://a.example/x_y_z">https://a.example/x_y_z</a>', h)
        self.assertIn('<a href="https://a.example/i.png">图</a>', h)
        self.assertIn('a&lt;b&gt;&amp;c', h)
        self.assertIn('<blockquote>deep</blockquote>\n──────────', h)
        self.assertNotIn('<b>', fmt.markdown('# **already bold** heading').replace('<b>', '', 1))

    def test_long_report_parts_are_valid_bounded_and_cut_at_line_ends(self):
        text = long_report()
        parts = fmt.parts(text)
        self.assertGreater(len(parts), 3)
        whole = Checked(); whole.feed(fmt.markdown(text)); whole.close()
        shown = ''
        for i, (rendered, plain) in enumerate(parts):
            self.assertLessEqual(len(rendered.encode('utf-16-le')) // 2, 3900)
            c = Checked(); c.feed(rendered); c.close()
            self.assertEqual(c.stack, [])
            if i < len(parts) - 1:
                self.assertTrue(c.text.endswith('\n'), 'cut at a line end: %r' % c.text[-40:])
            if '<code class="language-bash">' in rendered:
                self.assertTrue(rendered.startswith('<pre><code class="language-bash">') or 'step_0 ' in rendered)
            shown += c.text
        self.assertEqual(shown.strip(), whole.text.strip())

    def test_plain_fallback_keeps_link_targets(self):
        (_, plain), = fmt.parts('看 [报告](https://example.org/r) 和 https://b.example')
        self.assertEqual(plain, '看 报告 (https://example.org/r) 和 https://b.example')


class ForwardRendered(_Base):
    def setUp(self):
        super().setUp()
        self.turns = {1: {'id': 1, 'end': 'done', 'ts': 1_791_600_000_000, 'reply': {'text': REPORT}},
                      2: {'id': 2, 'end': 'done', 'reply': {'text': long_report()}}}
        self.host.hist = type('H', (), {'get': staticmethod(lambda i: self.turns.get(i))})()

    async def test_forward_sends_html_not_plain(self):
        r = await self.t.forward(1, 'dev1')
        self.assertEqual(r, {'ok': True, 'parts': 1})
        (sent,) = [x['fields'] for x in self.bot.calls if x['method'] == 'sendMessage']
        self.assertEqual(sent['chat_id'], OWNER)
        self.assertEqual(sent['parse_mode'], 'HTML')
        self.assertTrue(sent['text'].startswith('📨 '))
        self.assertIn(REPORT_HTML, sent['text'])

    async def test_long_forward_is_split_valid(self):
        r = await self.t.forward(2, 'dev1')
        sent = [x['fields'] for x in self.bot.calls if x['method'] == 'sendMessage']
        self.assertEqual(r['parts'], len(sent)); self.assertGreater(len(sent), 3)
        for x in sent:
            self.assertEqual(x['parse_mode'], 'HTML')
            c = Checked(); c.feed(x['text']); c.close(); self.assertEqual(c.stack, [])

    async def test_parse_error_resends_only_that_part_as_plain(self):
        calls = []

        def fake(cfg, method, values):
            calls.append(values)
            if values.get('parse_mode') and len(calls) == 2:
                raise tg.TelegramError('parse_entities')
        with patch.object(tg, 'api', side_effect=fake):
            r = await self.t.forward(2, 'dev1')
        self.assertTrue(r['ok'])
        self.assertEqual([('parse_mode' in c) for c in calls[:4]], [True, True, False, True])
        c = Checked(); c.feed(calls[1]['text']); c.close()
        self.assertEqual(calls[2]['text'], c.text)      # the same piece, as the text it shows, once
        for tag in ('<b>', '<pre', '<code', '<a '):
            self.assertNotIn(tag, calls[2]['text'])
        self.assertEqual(len([c for c in calls if 'parse_mode' in c]), r['parts'])

    async def test_transport_failure_is_not_retried_plain(self):
        with patch.object(tg, 'api', side_effect=tg.TelegramError('timeout')) as send:
            r = await self.t.forward(1, 'dev1')
        self.assertEqual(r['why'], 'send_failed'); self.assertEqual(send.call_count, 1)


if __name__ == '__main__':
    unittest.main()
