// P291: background work in a shared Claude session shows as robots even when the main Agent is idle (Leo, Mac, 10-14 23:17:
// 「后台在干活，但看不到跳舞的机器人」). The list on the page is produced by the real host code (agentj/busy.py Board, run
// through python) from the hook sequence Claude Code sends: a background Agent + a run_in_background Bash, main turn over.
//   a) before (the Mac): the served session's hooks refused → the host sends an empty list → no robot while work runs
//   b) after: the sub-agent and the background command are two robots on an idle reply card; the dialog lists both
//   c) the SubagentStart lost on the way: Stop.background_tasks alone still brings the robot
// Real page + local fake host, independent headless Chromium (never :9222). Shots → reports/qa/p291/screens/.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const HOST = join(ROOT, 'agentjarvis/host');
const OUT = join(ROOT, 'reports/qa/p291/screens');
const $ = (id) => `document.getElementById('${id}')`;
const NAME = 'Jarvis Mac';

// The host's own wire list for a hook sequence (busy.Board.msg), so the page shows exactly what serve would send.
const BOARD = String.raw`
import json, sys
sys.path.insert(0, sys.argv[1])
from agentj import busy
SID, AID, SH = "eb106b5c", "afaae013b19cf33f3", "b8my5htwq"
desc = sys.argv[3]
sub = {"id": AID, "type": "subagent", "status": "running", "description": desc, "agent_type": "general-purpose"}
sh = {"id": SH, "type": "shell", "status": "running", "description": sys.argv[4], "command": "opencode run …"}
seq = [{"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": {"description": desc, "subagent_type": "general-purpose", "run_in_background": True}},
       {"hook_event_name": "SubagentStart", "agent_id": AID, "agent_type": "general-purpose"},
       {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "opencode run …", "description": sys.argv[4], "run_in_background": True},
        "tool_response": {"backgroundTaskId": SH}},
       {"hook_event_name": "Stop", "background_tasks": [sub, sh]}]
mode = sys.argv[2]
if mode == "refused":
    seq = []                                   # the Mac: every one of them refused ("process") → the board never heard
elif mode == "lost-start":
    seq = [e for e in seq if e["hook_event_name"] not in ("SubagentStart", "PostToolUse")]
b = busy.Board(None)
for e in seq:
    b.hook_event(dict(e, session_id=SID), scope="shared-claude")
m = b.msg("${NAME}")
print(json.dumps(m, ensure_ascii=False))
`;
const LABELS = { zh: ['OpenCode 免费模型能否自举安装 Agent J', '测第 5 个免费模型 step-5-preview'],
  en: ['Can OpenCode free models bootstrap Agent J', 'Testing free model 5: step-5-preview'] };
const board = (mode, lang) => JSON.parse(execFileSync('python3', ['-c', BOARD, HOST, mode, ...LABELS[lang]], { encoding: 'utf8' }));

export async function runP291() {
  mkdirSync(OUT, { recursive: true });
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  try {
    for (const [lang, theme] of [['zh', 'light'], ['zh', 'dark'], ['en', 'light']]) {
      fake.reset();
      const p = await newPage(B, 390, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
      const save = async (name) => writeFileSync(join(OUT, `${lang}-${theme}-${name}.png`), await shoot(p, ''));
      const freeze = () => evaluate(p, `document.getAnimations().forEach(a=>{a.pause();a.currentTime=600})`);
      const robots = () => evaluate(p, `${$('busyDots')}.querySelectorAll('i.rb:not(.bye)').length`);
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });     // the main Agent is NOT replying
        await waitFor(p, `${$('agent')}.textContent===${JSON.stringify(NAME)}`);

        const before = board('refused', lang);                                       // a) the Mac, 0.21.0a1
        check(before.tasks.length === 0, 'refused hooks → empty list');
        await fake.send(before);
        await waitFor(p, `${$('busyDots')}.hidden`);
        await freeze(); await save('a-before-mac-refused');

        const after = board('fixed', lang);                                          // b) P291
        check(after.tasks.length === 2 && after.tasks.every((t) => t.kind === 'temp' && t.who === null), 'sub-agent + background command: ' + JSON.stringify(after.tasks));
        check(after.tasks.map((t) => t.label).sort().join('|') === [...LABELS[lang]].sort().join('|'), 'labels are the descriptions');
        await fake.send(after);
        await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb:not(.bye)').length===2`);
        check(await robots() === 2, 'two robots on an idle reply card');
        await freeze(); await save('b-after-two-robots');
        await evaluate(p, `${$('busyDots')}.click()`);
        await waitFor(p, `!${$('busyDlg')}.hidden && document.querySelectorAll('#busyList li').length===2`);
        const rows = await evaluate(p, `[...document.querySelectorAll('#busyList li')].map(li=>li.textContent)`);
        check(LABELS[lang].every((l) => rows.some((r) => r.includes(l))), 'the dialog lists both: ' + JSON.stringify(rows));
        await freeze(); await save('b-after-dialog');
        await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);

        const lost = board('lost-start', lang);                                      // c) SubagentStart never arrived
        check(lost.tasks.length === 2, 'Stop.background_tasks alone brings both robots');
        await fake.send({ t: 'busy', tasks: [], by: NAME }); await waitFor(p, `${$('busyDots')}.hidden`, 6000);
        await fake.send(lost);
        await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb:not(.bye)').length===2`);
        await freeze(); await save('c-lost-start-still-shown');
      } finally {
        await p.close();
      }
    }
  } finally {
    await B.close(); await fake.stop(); await web.stop();
  }
  writeFileSync(join(OUT, 'results.json'), JSON.stringify({ scope: 'real Chromium + local fake host + the real host Board; not a physical phone', checks, complete: true }, null, 2) + '\n');
  return checks;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  runP291().then((n) => { console.log(`P291 phone: ${n} checks passed → ${OUT}`); }, (e) => { console.error(e); process.exit(1); });
}
