"""P574 phone summaries come from the actual host renderer. Default checks; --write refreshes the test fixture."""
import json
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'host'))
from agentj import owner_grant as og
path = Path(__file__).with_name('fixtures') / 'p574-grants.json'
out = {}
for lang in ('zh', 'en'):
    zh = lang == 'zh'
    words = ['测试文件', '测试资源', '测试报告', '示例商品'] if zh else ['test file', 'test resource', 'test report', 'sample item']
    steps = [og.secret_step('DEMO_KEY', 'spare', 'work')]
    for cat, word, limit in zip(('delete','spend','send','price'), words,
            ({'paths':['/tmp/agentj-p574-demo.txt']}, {'exact':'demo-pay --order fixture-order'},
             {'match':['demo-send --report fixture']}, {'exact':'demo-price --sku fixture --value 12'})):
        steps.append(og.grant_step('spare', {'cat':cat,'what':word,'max':1, **limit}))
    with patch.object(og.secrets, 'token_hex', return_value='0' * 32):
        otg = og.build('work', {'work':'工作主机' if zh else 'Work host','spare':'备用主机' if zh else 'Spare host'},
                '部署示例' if zh else 'Deploy sample', '完成这次测试任务' if zh else 'Complete this test task', steps, lang, now=1800000000)
    out[lang] = {'tool':og.TOOL, 'otg':otg, 'summary':og.card_text(otg)}
text = json.dumps(out, ensure_ascii=False, indent=2) + '\n'
if '--write' in sys.argv:
    path.write_text(text)
else:
    assert path.read_text() == text, 'host card renderer changed: review and refresh p574-grants.json'
print('P574 host-rendered phone fixture current')
