"""P299: the 技能 page's owner-language line (desc_zh) and usage (use_zh) — bundled table, front matter, Chinese sentence."""
import json
import pathlib
import unittest
from agentj import workflows as wf


class ZhLines(unittest.TestCase):
    def test_front_matter_wins_then_chinese_sentence(self):
        meta = {'description': 'Do the thing well. 把这件事好好地做完。', 'description_zh': '自己写的中文说明', 'usage_zh': '说「做吧」'}
        self.assertEqual(wf.zh_lines(meta, 'Do the thing well.'), {'desc_zh': '自己写的中文说明', 'use_zh': '说「做吧」'})
        self.assertEqual(wf.zh_lines({'description': 'Do the thing well. 把这件事好好地做完。'}, 'Do the thing well.'), {'desc_zh': '把这件事好好地做完。'})

    def test_chinese_or_english_only_sends_nothing_extra(self):
        self.assertEqual(wf.zh_lines({'description': '管理域名与解析记录。Use for DNS.'}, '管理域名与解析记录。'), {})
        self.assertEqual(wf.zh_lines({'description': 'English only. Still English.'}, 'English only.'), {})
        self.assertEqual(wf.zh_lines({'description': 'Use for 「换个主题」「换个颜色」, "change the theme".'}, 'Use for …'), {})

    def test_bundled_skills_have_a_shipped_line_in_the_web_dictionary(self):
        root = pathlib.Path(__file__).resolve().parents[2] / 'web/i18n'
        zh, en = (json.loads((root / f).read_text(encoding='utf-8')) for f in ('web.zh.json', 'web.en.json'))
        for s in wf.skill_dir(str(wf.SKILLS), 'bundled', None, None)[0]:
            for k in ('sk', 'su'):
                self.assertTrue(wf._CJK.search(zh.get(f'p299.{k}.{s["name"]}', '')), s['name'])
                self.assertTrue(en.get(f'p299.{k}.{s["name"]}'), s['name'])


if __name__ == '__main__':
    unittest.main()
