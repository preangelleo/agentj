import assert from 'node:assert/strict';
import { generateKeyPairSync, randomBytes } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { parityRecorder } from '../../parity/lib.mjs';
import { evaluate, launch, navigate, newPage, shoot, sleep, waitFor, waitState } from './browser.mjs';
import { startFakeHost } from './fakehost.mjs';
import { startWebServer } from './serve.mjs';

const base = process.env.P0374_RELOGIN_PUBLIC;
const out = process.env.P0374_RELOGIN_OUT;
if (out) mkdirSync(out, { recursive: true });
const web = await startWebServer({ publicDir: base });
const pageUrl = web.url.replace('127.0.0.1', 'localhost');
const fake = await startFakeHost();
const browser = await launch();
const receipt = parityRecorder('web/test/p0374-health-relogin.mjs');
const peachy = 'rgb(217, 119, 87)';
const confirmId = 'a'.repeat(32);
const health = 'b'.repeat(16);
const time = Math.floor(Date.now() / 1000);
const row = { kind: 'claude', label: 'Claude Code', installed: true, state: 'login', expiry: 'known',
  health: { at: time, ok: false }, last: { result: 'failed', why: 'probe' } };
const token = { days_left: 365, expires: '2027-11-24' };
const bad = [];

try {
  for (const [width, height, theme] of [[390, 844, 'light'], [390, 844, 'dark'], [1440, 900, 'light'], [1440, 900, 'dark']]) {
    fake.reset();
    let sent = null, legacy = false, notice, answerStart;
    let view = { harnesses: [row], flow: null, dot: true };
    const p = await newPage(browser, width, height, theme, { touch: width < 500, allow: [pageUrl, fake.relay + '/'] });
    const save = async name => out && writeFileSync(join(out, `relogin-${base ? 'before' : 'after'}-${width}-${theme}-${name}.png`), await shoot(p, ''));
    const css = async selector => evaluate(p, `getComputedStyle(document.querySelector(${JSON.stringify(selector)})).color`);
    const showView = async next => {
      view = next;
      await fake.send({ t: 'auth_rescue', view });
    };
    const warning = async () => {
      await waitFor(p, '!!document.querySelector("#ar-list [data-kind=claude] .archip--login")');
      await evaluate(p, 'document.querySelector("#ar-list").scrollIntoView({block:"start"})');
      await sleep(200);
      await save('warning');
      if (await css('#ar-list .archip--login') !== peachy || await css('#ar-list .ardanger') !== peachy) {
        bad.push(`${width} ${theme}: login warning color is not Peachy`);
      }
    };
    const credentialId = randomBytes(16);
    const challenge = randomBytes(32).toString('base64url');
    try {
      await navigate(p, fake.newPairing(pageUrl + '?lang=zh'));
      await waitState(p, 'awaiting-approval');
      await fake.approve();
      await waitState(p, 'ready');
      await p.send('WebAuthn.enable', { enableUI: false });
      const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: {
        protocol: 'ctap2', transport: 'internal', hasResidentKey: true, hasUserVerification: true,
        isUserVerified: true, automaticPresenceSimulation: true,
      } });
      await p.send('WebAuthn.addCredential', { authenticatorId, credential: {
        credentialId: credentialId.toString('base64'), isResidentCredential: true, rpId: 'localhost',
        privateKey: generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64'),
        userHandle: Buffer.from('synthetic-owner').toString('base64'), signCount: 0,
      } });
      fake.st.onApp = async (conn, message, send) => {
        const { t, r } = message;
        if (t === 'keys_get') {
          const rows = ['AGENTSRELAY_CLAUDE_KEY', 'AGENTSRELAY_OPENAI_KEY'].map(name => ({ type: 'key', name, set: true }));
          const vendor = { id: 'agentsrelay', name: { zh: 'Agents Relay', en: 'Agents Relay' }, rows };
          await send({ t: 'keys', r, id: '1'.repeat(32), n: '2'.repeat(32), base: [vendor] });
          return true;
        }
        if (t === 'auth_get') { await send({ t: 'auth_res', r, ok: true, view }); return true; }
        if (t === 'auth_rescue' && message.action === 'login') {
          assert.ok(await fake.verifyControl(conn, t, message, { action: 'login', agent: message.agent }));
          legacy = true;
          await send({ t, r, view: { step: 'done', agent: 'claude', ok: false, why: 'not_main', text: '只能给主对话正在用的 AI 重新登录。' } });
          return true;
        }
        if (t !== 'auth_start') return false;
        const { kind, notice: id, health: tag, route, account } = message;
        sent = { kind, id, tag, route, account };
        if (kind !== 'claude' || id !== notice.id || tag !== health || route !== undefined || account !== undefined) {
          await send({ t: 'auth_res', r, ok: false, why: 'unsupported', view });
          return true;
        }
        answerStart = () => {
          view = { ...view, flow: { kind: 'claude', stage: 'starting', route: 'setup_token' } };
          return send({ t: 'auth_res', r, ok: true, view });
        };
        await fake.ask({ host_tool: true, id: confirmId, tool: 'AuthRescue', summary: '重新登录 Claude Code', ttl: 600,
          passkey_required: true, fa: credentialId.toString('base64url'), fa_ch: challenge });
        return true;
      };
      await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();import('./js/settings.js').then(s=>s.applyThemeLocal('${theme}'))`);
      await fake.send({ t: 'status', s: 'idle', agent: 'codex', name: 'Agent J' });
      if (!base) {
        await evaluate(p, `import('./js/settings.js').then(m=>m.openAuth())`);
        await warning();
        await evaluate(p, `import('./js/settings.js').then(m=>m.close())`);
      }
      notice = await fake.addTurn({ k: 'sys' }, base
        ? '每日体检：Claude Code 的登录已失效（可能在网页上退出了全部设备，或被官方撤销）。打开 设置 → 紧急修复 → AI 登录救援，在手机上重新登录。'
        : '每日体检：Claude Code 的登录已失效。点「重新登录」，也可打开 设置 → 紧急修复 → AI 登录救援 处理。', 'done');
      await fake.updateTurn(notice.id, { login: { agent: 'claude', why: 'expired', health } });
      await waitFor(p, '!!document.querySelector("#cmdx [data-login-btn=claude]")');
      await sleep(350);
      await save('notice');
      await evaluate(p, 'document.querySelector("#cmdx [data-login-btn=claude]").click()');
      await waitFor(p, '!document.getElementById("set-auth").hidden || document.getElementById("lgs")?.dataset.state === "fail"');
      const usedLegacy = legacy;
      if (usedLegacy) {
        await sleep(350);
        await save('result');
        bad.push(`${width} ${theme}: notification sent main-session login and hit not_main`);
        await evaluate(p, `document.getElementById('lgs-x').click();import('./js/settings.js').then(m=>m.openAuth())`);
        await warning();
      } else {
        await waitFor(p, '!document.getElementById("sheet").hidden && document.getElementById("apprAllowLbl").textContent === "开始重新登录" && !document.getElementById("apprAllow").disabled');
        assert.deepEqual(sent, { kind: 'claude', id: notice.id, tag: health, route: undefined, account: undefined });
        await sleep(350);
        assert.equal(await css('#askTags .arconfirm'), peachy);
        await save('confirm');
        const tap = await evaluate(p, `(() => {
          const rect = document.getElementById('apprAllow').getBoundingClientRect();
          return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
        })()`);
        await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', ...tap, button: 'left', clickCount: 1 });
        await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...tap, button: 'left', clickCount: 1 });
        await waitFor(p, 'document.getElementById("sheet").hidden');
        assert.equal(fake.st.sigOk.at(-1), true, 'paired-phone signature verified');
        assert.ok(fake.st.answers.at(-1)?.fa, 'fresh virtual passkey assertion sent');
        await answerStart();
      }
      await showView({ harnesses: [{ ...row, token, health: { at: time, ok: true } }],
        flow: { kind: 'claude', stage: 'verifying', saved: true }, dot: true });
      await waitFor(p, 'document.getElementById("ar-flow").textContent.includes("实测")');
      await evaluate(p, 'document.getElementById("ar-flow").scrollIntoView({block:"start"})');
      await sleep(400);
      await save('verifying');
      if (await evaluate(p, '!!document.querySelector("#ar-list .archip--login, #ar-list .artoken, #ar-list .arhealth")')) {
        bad.push(`${width} ${theme}: verification shows stale/contradictory status`);
      }
      if (!usedLegacy) {
        await showView({ harnesses: [{ ...row, state: 'ok', token, health: { at: time, ok: true }, last: { result: 'recovered' } }],
          flow: { kind: 'claude', stage: 'done' }, dot: false });
        await waitFor(p, 'document.getElementById("ar-flow").hidden && !!document.querySelector("#ar-list .archip--ok")');
        await evaluate(p, 'document.querySelector("#ar-list").scrollIntoView({block:"start"})');
        await sleep(500);
        assert.match(await evaluate(p, 'document.querySelector("#ar-list .artoken").textContent'), /365/);
        assert.equal(await evaluate(p, 'document.getElementById("ar-msg").textContent'), '');
        await save('result');
        // A live-session response still uses the original, main-session-bound sheet.
        await evaluate(p, 'import("./js/settings.js").then(m=>m.close())');
        const live = await fake.addTurn({ k: 'phone', text: '示例请求' }, '请先重新登录。', 'done');
        await fake.updateTurn(live.id, { login: { agent: 'claude', why: 'not_logged_in' } });
        await waitFor(p, `(() => {
          if (!document.querySelector('#words')?.textContent.includes('请先重新登录。')) return false;
          const button = document.querySelector('#cmdx [data-login-btn=claude]');
          if (!button) return false;
          button.click();
          return true;
        })()`);
        await waitFor(p, 'document.getElementById("lgs")?.dataset.state === "fail"');
        assert.equal(await css('#lgs-msg'), peachy);
      }
      console.log(`${width}×${height} ${theme}: ${usedLegacy ? 'legacy not_main' : 'notice → fresh UV → verification → recovered'}`);
    } finally {
      await p.dispose();
    }
  }
  assert.deepEqual(bad, [], bad.join('\n'));
  receipt.record('Daily health notice opens the shared, verified AI login rescue', true);
  console.log(receipt.write());
} finally {
  await browser.close();
  await fake.stop();
  await web.stop();
}
