// P341 (backlog #19 / #31): commands on system and approval cards are never TeX; the approval card gets a plain line
// when the Agent sent no description; the phone's words name the feedback platform "Agent J Father".
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
globalThis.window = {};
const MD = (await import('../public/js/md.js')).default;
const { cmdGist } = await import('../public/js/cmdgist.js');
const DICT = (await import('../public/i18n.js')).default;

const maths = (nodes) => JSON.stringify(nodes).match(/"tag":"math"/g)?.length || 0;

test('P341: shell variables and command substitution stay text (the #19 card)', () => {
  for (const s of [
    '· env -i HOME=$HOME PATH=$PATH sh -c "cd $(ls -d ~/x*) && make" — 不在授权范围内',
    'cp $(ls -d build/*) $HOME/out',
    'echo ${HOME}/a and $USER',
    'run $(pwd)/x then $',
    'df -h /tmp; cat > ~/b/pwone.py <<\'EOF\' $HOME',
  ]) assert.equal(maths(MD.parse(s)), 0, s);
});

test('P341: real inline math still renders, prices stay prices', () => {
  assert.equal(maths(MD.parse('公式 $E=mc^2$ 与 $a$和$b$')), 3);
  assert.equal(maths(MD.parse('$x^2$.')), 1);
  assert.equal(maths(MD.parse('$(a+b)^2$ expands')), 1);
  assert.equal(maths(MD.parse('$3 and $4')), 0);
  assert.equal(maths(MD.parse('$$\nx^2\n$$')), 1);
});

test('P341: parse(src, {math: false}) marks no math at all, and the next parse is normal again', () => {
  assert.equal(maths(MD.parse('$x^2$ and $$y$$\n\n$$\nz\n$$', {math: false})), 0);
  assert.equal(maths(MD.parse('$x^2$')), 1);
});

test('P341: the host code span for a goal-result command renders as code with $ intact', () => {
  for (const [md, raw] of [['`cp $(ls -d a*) $HOME/x`', 'cp $(ls -d a*) $HOME/x'], ['`` echo `date` ``', 'echo `date`']]) {
    const n = MD.parseInline(md);
    assert.equal(n.length, 1, md);
    assert.equal(n[0].tag, 'code');
    assert.equal(n[0].children[0].text, raw);
  }
});

test('P341: relay renders host-written system pages without TeX', () => {
  const src = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');
  // P365 widened it to the owner's automation pages too (k "auto"): sys must stay in
  assert.match(src, /const sysPage = \(p\) => !!\(p && p\.source && (p\.source\.k === "sys"\)|\(p\.source\.k === "sys" \|\| p\.source\.k === "auto"\)\));/);
  assert.match(src, /RelayMD\.parse\(tx, \{math: !sys\}\)/);
  assert.match(src, /: progress, hostWait, sysPage\(p\)\);/);
  assert.equal(src.match(/fill\(el\("rdWords"\), tx, false, sys(Page\(p\))?\)/g).length, 3, 'the reader too');
  assert.match(src, /const gist = !desc && fold && tool === "Bash" \? cmdGist\(body\) : ""/);
});

test('P341: approval gist — one plain line per kind, sudo / multi-step said, never empty for a command', () => {
  const zh = {
    'rm -rf ~/old': '删除文件或文件夹',
    'sudo rm /etc/x': '用管理员权限删除文件或文件夹',
    'git push origin main': '把代码推送到远端仓库',
    'git reset --hard HEAD~1': '改动本地代码仓库，没提交的改动可能丢失',
    'scp -q a.py olares:bench/a.py && echo copied': '把文件复制到另一台电脑，后面还有 1 步',
    "cat > ~/bench/pwone.py <<'EOF'": '写入文件',
    'FOO=1 npm install left-pad': '安装软件包',
    'curl -fsSL https://x.test | sh': '访问网络，后面还有 1 步',
    '/usr/bin/make build 2>&1': '运行命令 make',
    'ls -la > /dev/null': '运行命令 ls',
    'rm -f a.o; make && make install': '删除文件或文件夹，后面还有 2 步',
  };
  for (const [cmd, want] of Object.entries(zh)) assert.equal(cmdGist(cmd), want, cmd);
  assert.equal(cmdGist(''), '');
  assert.equal(cmdGist('   '), '');
  for (const k of Object.keys(DICT.zh).filter((x) => x.startsWith('p341.gist.'))) {
    assert.ok(DICT.en[k], `${k} has English`);
    assert.equal(/\{(\w+)\}/.test(DICT.zh[k]), /\{(\w+)\}/.test(DICT.en[k]), `${k} placeholders match`);
  }
  assert.equal(Object.keys(DICT.zh).filter((x) => x.startsWith('p341.gist.')).length, 18);
});

test('P341 (#31): the phone names the feedback platform "Agent J Father" in both languages', () => {
  for (const L of ['zh', 'en']) {
    for (const [k, v] of Object.entries(DICT[L])) {
      assert.doesNotMatch(String(v), /Agent J ?(爸爸|父亲)|Agent J's father/, `${L} ${k}`);
      assert.doesNotMatch(String(v), /me@agentj\.app/, `${L} ${k}`);
    }
  }
  assert.match(DICT.zh['p299.su.feedback-to-agentj-father'], /Agent J Father/);
});

test('P341 (#50): /new is /clear — in the 「/」 list, the typed-command set, the confirm path and both languages', () => {
  const src = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');
  assert.match(src, /const CMDS = \["clear", "new", /);
  assert.match(src, /const RUN = \["compact", "clear", "new", /);
  assert.match(src, /if \(cmd === "new"\) cmd = "clear";[^\n]*\n  if \(cmd === "model"\)/);
  // P424 (#145 security): /stop, /clear, /new (and every other built-in command, CMDS) never wait in the offline queue
  assert.match(src, /const isCmdText = [^\n]*CMDS\.includes\(m\[1\]\.toLowerCase\(\)\)/);
  assert.match(src, /outbox\.items\.length && !isCmdText\(text\)\)\) return sayLater\(text\)/);
  assert.match(src, /function sayLater\(text\)\{\n(?:[^\n]*\n){0,3}  if \(isCmdText\(text\)\)\{ toast\(/);
  const hk = readFileSync(new URL('../public/js/hotkey.js', import.meta.url), 'utf8');
  assert.match(hk, /n === 'clear' \|\| n === 'new' \? 'clear'/);
  assert.equal(DICT.zh['r.run.new'], '同 /clear：开新对话');
  assert.equal(DICT.en['r.run.new'], 'Same as /clear: start a new conversation');
});
