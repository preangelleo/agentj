// 0.26.9 lane w4-web · pure logic: md.js marks [label](reply:/cmd) as an rcmd (never an rlink / <a>), slashlink.js knows which
// commands are phone commands and which tap only fills the composer, an AuthRescue card shows the host's sentence (no JSON,
// no request hash), and the shipped sources keep the contracts the browser test (feedback573-lost-batch.mjs) exercises.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import MD from '../public/js/md.js';
import { parseCmd, tapKind, KNOWN, SEND, rescueCard } from '../public/js/slashlink.js';

const src = (f) => readFileSync(new URL('../public/' + f, import.meta.url), 'utf8');

test('#447: [label](reply:/cmd) is an rcmd with the command, the label kept; never an rlink or a link', () => {
  const out = MD.parseInline('[查看待核对的后台结果](reply:/recover-background) 和 [读报告](reply:/recover-background ' + 'a'.repeat(64) + ')');
  const rc = out.filter((n) => n.tag === 'rcmd');
  assert.deepEqual(rc.map((n) => n.cmd), ['/recover-background', '/recover-background ' + 'a'.repeat(64)]);
  assert.equal(rc[0].children[0].text, '查看待核对的后台结果');
  assert.equal(out.some((n) => n.tag === 'rlink' || n.tag === 'a'), false);
  assert.equal(MD.parseInline('[](reply:/compact)').find((n) => n.tag === 'rcmd').children[0].text, '/compact', 'empty label → the command');
  for (const bad of ['[x](reply:／stop)', '[x](reply:/Stop)', '[x](reply:/a b c)', '[x](reply:/x%0Ay)', '`[x](reply:/clear)`']) {
    assert.equal(MD.parseInline(bad).some((n) => n.tag === 'rcmd'), false, bad);
  }
  assert.deepEqual(MD.parseInline('[派](reply:派)').filter((n) => n.tag === 'rlink').map((n) => n.say), ['派'], 'P368 words unchanged');
});

test('#433: phone commands, look-only ones go at once, the rest only fill the composer', () => {
  assert.deepEqual(parseCmd('/recover-background'), { cmd: 'recover-background', arg: '' });
  assert.deepEqual(parseCmd('/recover-background ' + 'f'.repeat(64)), { cmd: 'recover-background', arg: 'f'.repeat(64) });
  for (const bad of ['/recover-background 123', '/clear now', '/home', '/nosuch', 'compact', '/login']) assert.equal(parseCmd(bad), null, bad);
  for (const c of ['recover-background', 'status', 'usage']) assert.equal(tapKind(c), 'send', c);
  // security C-1: /compact changes the session — one tap only fills the composer, like /clear
  for (const c of ['compact', 'clear', 'new', 'reset', 'model', 'switch-harness', 'goal', 'update', 'stop']) assert.equal(tapKind(c), 'fill', c);
  assert.ok([...SEND].every((c) => KNOWN.has(c)));
  // the host's phone command list (slash.PHONE_COMMANDS) is what the page knows
  let host = '';
  try { host = readFileSync(new URL('../../host/agentj/slash.py', import.meta.url), 'utf8'); } catch { return; }   // web-only export
  const names = [...host.slice(host.indexOf('PHONE_COMMANDS')).split('\n\n')[0].matchAll(/\('([a-z-]+)',/g)].map((m) => m[1]);
  assert.ok(names.length > 10);
  for (const n of names) assert.ok(KNOWN.has(n), 'page knows /' + n);
});

test('#452: an AuthRescue card shows the host sentence, titled by what it does', () => {
  const raw = 'AuthRescue\n{}\nAgent 附注:\n{\n  "summary": "添加另一个 Codex 账号「备用 1」：请用另一个账号登录，现在这个不受影响。"\n}\nRequest SHA-256: a06626f19f2e570b33e';
  assert.deepEqual(rescueCard(raw), { text: '添加另一个 Codex 账号「备用 1」：请用另一个账号登录，现在这个不受影响。', kind: 'add', h: 'Codex' });
  assert.equal(rescueCard('从这台 Agent J 主机删除 Claude Code 账号「备用」？它在本机存的登录凭据会一起删掉；账号本身不受影响。').kind, 'del');
  assert.equal(rescueCard('Remove the Codex account 「x」 from this Agent J host?').h, 'Codex');
  const old = rescueCard('AI 登录救援：重新登录 Claude Code。\nAgent J 会…\nRequest SHA-256: abc');
  assert.equal(old.kind, null); assert.ok(!old.text.includes('SHA'));
});

test('#451 #453 #454 #456: the shipped sources keep the contracts', () => {
  const vn = src('js/version-notice.js');
  assert.match(vn, /LATER_MS=10000/); assert.match(vn, /line\.hidden=true/);
  assert.match(vn, /later&&box\.hidden&&hostVersion&&webChecked/, 'never before version.json was read (B may already be out)');
  assert.doesNotMatch(src('js/media.js'), /mfullscreen-label/);
  // security C-2: only Agent J's own card (host_tool, set by the host for its own AuthRescue) gets the plain layout / Settings float
  assert.match(src('app.js'), /m\.tool === 'AuthRescue' && m\.host_tool === true && settings\.isOpen\(\)\) \{ floatAuthAsk\(\); return; \}/);
  assert.match(src('js/relay.js'), /const rescueAsk = !!\(p && p\.tool === "AuthRescue" && p\.host_tool === true\);/);
  assert.match(src('js/relay.js'), /item\.tool === "AuthRescue" && item\.host_tool === true/);
  // security C-1: a friend's report or an owner-channel page (a stranger's words) never gets command links
  const relay = src('js/relay.js');
  assert.match(relay, /const replyDataOnly=p=>externalEcho\(p\)\|\|p\?\.source\?\.k==='friend_report'\|\|/);
  assert.match(relay, /if\(!replyDataOnly\(p\)\)linkSlash\(root,\{run:phoneCmd,fill:text=>appendText\(text\)\},t\)/);
  assert.match(relay, /enhanceReplySurface\(el\("words"\),p\)/, 'reply card uses the guarded enhancer');
  assert.match(relay, /enhanceReplySurface\([^\n]*p,true\)/, 'reader uses the guarded enhancer');
  const ar = src('js/authrescue.js');
  assert.match(ar, /END_MS = 10000/); assert.match(ar, /if \(!f \|\| ended\(f\)\) \{ box\.hidden = true; return; \}/);
  for (const lang of ['zh', 'en']) {
    const d = JSON.parse(readFileSync(new URL(`../i18n/web.${lang}.json`, import.meta.url), 'utf8'));
    for (const k of ['cmdSend', 'cmdFill', 'close', 'addTitle', 'addStart', 'delTitle', 'delStart', 'addAnother', 'nameLab', 'spare', 'inUseName', 'inUseNote', 'where', 'whereLocal', 'remote']) {
      assert.ok(d['p0269w4.' + k], lang + ' p0269w4.' + k);
    }
  }
});

import { validRemoteNames } from '../public/js/remote-names-rules.js';
test('#444 remote display aliases accept safe names and reject duplicate or executable addresses',()=>{
  assert.equal(validRemoteNames([{host:'olares',name:'家里的机器'},{host:'192.0.2.10',name:'测试机'}]),true);
  for(const rows of [[null],[{host:':bad',name:'x'}],[{host:'user@host',name:'x'}],[{host:'$(bad)',name:'x'}],[{host:'ok',name:'x'},{host:'ok',name:'y'}],[{host:'ok',name:'\n'}],Array.from({length:33},(_,i)=>({host:'h'+i,name:'x'}))]) assert.equal(validRemoteNames(rows),false);
});
