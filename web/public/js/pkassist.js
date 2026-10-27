// P539 (#300 / #299): one place for "this remote has no passkey of its own", the reason after a failed passkey tap, the
// 「本机通行密钥」 state, pre-authorization by action (「以后这台遥控器批准的 Computer Use 不再问」) and the draft a flagged
// message comes back as. Pure helpers first (node tests drive them), then the small painters every card shares:
// elevate.js (sudo / secret), relay.js (approval sheet) and secretout.js (pickup card) call paintPk() under their confirm
// button, so the words and the two buttons are the same everywhere. Host text is textContent only.
import { t, tRaw, lang, textOs } from './t.js';
import { bioLabel } from './phone-env.js';
import { mk, confirmSheet, closeConfirm, toastAction } from './ui.js';
import { faState, pkState, hintKey, failKey, NAME, actionOf } from './pkrules.js';
export * from './pkrules.js';

let H = { setupFaceId() {} };
/** app.js: {setupFaceId()} — the F20 offer (pk_offer_req), the same one the Settings row uses. */
export function configure(h) { H = { ...H, ...h }; }

/** The name of a pre-authorized action in a list (unknown → the raw action, it is short and [a-z.] only). */
export const preauthName = (action) => (Object.hasOwn(NAME, actionOf(action)) ? t(NAME[actionOf(action)]) : actionOf(action));

/** The variant key of a per-platform text (pk.notSaved / pk.none): linux shares 'other'. */
export function osKey(base, os = textOs()) {
  return base + '.' + (['ios', 'android', 'mac', 'windows'].includes(os) ? os : 'other');
}
export const bio = (os = textOs()) => bioLabel(os, lang());
/** A text with {bio}: a Chinese label (指纹或人脸 · 屏幕锁) takes the spaces around the slot with it. */
export function withBio(key, os = textOs()) {
  const label = bio(os), s = t(key, { bio: '\u0001' });
  return /[\u3400-\u9fff]/.test(label) ? s.replace(/ ?\u0001 ?/g, label) : s.replace(/\u0001/g, label);
}

// ---------------------------------------------------------------- painters
function button(cls, key, fn) {
  const b = mk('button', 'pkassist__btn' + (cls ? ' ' + cls : ''), t(key));
  b.type = 'button';
  b.dataset.k = key;
  b.addEventListener('click', (e) => { e.stopPropagation(); fn(); });
  return b;
}
/** The block under a confirm button. opts = {fa (fa_s), err (an error name after a failed tap, or null), retry (fn, runs
 *  straight from the tap so the system sheet follows it), setup (false: no 「在本机设置」 — a sheet that cannot step aside)}.
 *  Nothing to say → the box hides. */
export function paintPk(box, { fa = null, err = null, retry = null, setup = true } = {}) {
  if (!box) return;
  const f = faState(fa);
  const key = err ? failKey(err, f) : hintKey(f);
  box.dataset.state = err ? 'fail' : f || '';
  if (!key) { box.hidden = true; box.replaceChildren(); return; }
  const line = mk('p', 'pkassist__text', withBio(key));   // P550: p539.hint.* name this device's own {bio}
  line.dataset.k = key;
  const acts = mk('div', 'pkassist__acts');
  if (err && retry) acts.append(button('pkassist__retry', 'p539.fail.retry', retry));
  if ((f !== 'local' || err) && setup) acts.append(   // P555: a failed tap on a key thought local (lost, #317) may set a new one
button('pkassist__setup', err ? 'p539.fail.setup' : 'p539.pk.setup', () => H.setupFaceId()));
  box.replaceChildren(line, ...(acts.childElementCount ? [acts] : []));
  box.hidden = false;
}

/** A block whose 「在本机设置」 must first put its own sheet away (the shared confirm sheet, the terminal's sheet): `away` runs
 *  in the capture phase, before the button's own click opens the F20 offer. Wired once per box. */
export function setupStepsAside(box, away) {
  if (!box || box.dataset.aside) return;
  box.dataset.aside = '1';
  box.addEventListener('click', (e) => { if (e.target.closest && e.target.closest('.pkassist__setup')) away(); }, true);
}
/** The shared confirm sheet (ui.js confirmSheet) with the quiet line under its buttons (#confirm-pk) — the keys page, 「Agent J
 *  主机上的命令」, the approval switches, 恢复, 「让这台也能用」 all ask for the passkey from this sheet. confirmSheet clears it. */
export function pkSheet(fa, ...args) {
  const shown = confirmSheet(...args);
  const box = document.getElementById('confirm-pk');
  setupStepsAside(box, closeConfirm);
  paintPk(box, { fa });
  return shown;
}
/** A passkey tap that failed where no card stays open to say it (恢复, the approval switches): the reason as a notice with one
 *  action — 「在本机设置通行密钥」 when the passkey lives on another device, else 「再试一次」 (it opens the same sheet again). */
/** P550 (#323 补): pk_reg_res {ok:false, why} → the words for it (PROTOCOL §12: proof | expired | unknown | bad); app.js pkSaved
 *  puts them beside 「在本机设置…（需另一台批准）」 — never the old 「没记住，不影响现在的配对」. */
export const regWhy = (why) => (why === 'proof' ? 'p550.pkReg.proof' : why === 'expired' ? 'p550.pkReg.expired' : 'p550.pkReg.fail');
export function failNotice(err, fa, retry) {
  const f = faState(fa);
  const setup = f === 'borrowed' || !retry;
  toastAction(t(failKey(err, f)), t(setup ? 'p539.fail.setup' : 'p539.fail.retry'), setup ? () => H.setupFaceId() : retry, 8000);
}

/** Settings → 「本机通行密钥」 row. state null (an older host never said) → the row hides and the old 「设置 Face ID」
 *  entry stays as it was. */
export function paintPkRow(state, { row, label, note, setupBtn, oldBtn, failed = false } = {}) {
  const s = pkState(state);
  if (!row) return;
  row.hidden = !s;
  if (!s) { if (oldBtn) oldBtn.dataset.pkRow = '0'; return; }
  // P559: 「已设好」 only while this device's own key passes here — failed in both request forms since its last pass → say so
  const bad = s === 'local' && failed;
  label.textContent = withBio(bad ? 'p559.pk.st.failed' : 'p539.pk.st.' + s);   // P550: 「本机 Face ID / 指纹」 as this system calls it (PASSKEY-SEAT-MODEL §1, §2)
  row.dataset.pk = bad ? 'failed' : s;
  setupBtn.hidden = s === 'local' && !bad;
  // P555: a key the Agent J host has on record but cannot place → 「用 Face ID 确认」 (one tap, no other remote)
  setupBtn.textContent = s === 'unknown' ? withBio('p555.pk.check') : t(bad ? 'p539.fail.setup' : 'p539.pk.setup');
  const n = bad ? withBio('p559.pk.failedNote') : s === 'borrowed' ? withBio('p539.pk.borrowedNote') : s === 'unknown' ? withBio('p555.pk.unknownNote') : '';   // P559: 「未设」 needs no note — the explanation line under the title says it
  note.textContent = n; note.hidden = !n;
  // P559 (chairman 10-27): the row looks like 遥控器管理 / 转移席位 — face icon on iPhone / iPad, fingerprint elsewhere, and
  // the small line saying what it is for (that line names Face ID, Touch ID and the screen lock side by side: tRaw)
  for (const ic of row.querySelectorAll?.('svg.mi') || []) ic.toggleAttribute('hidden', ic.dataset.icon !== (textOs() === 'ios' ? 'face' : 'finger'));   // SVG has no .hidden
  const sub = row.querySelector?.('#set-pk-sub');
  if (sub) sub.textContent = tRaw('p559.pk.sub');
  if (oldBtn) oldBtn.dataset.pkRow = '1';
}

/** Settings → 安全 → 「免问的动作」. Empty → the block hides. revoke(item) sends preauth_revoke. */
export function paintPreauth(row, list, items, revoke) {
  if (!row || !list) return;
  row.hidden = !items.length;
  list.replaceChildren(...items.map((x) => {
    const li = mk('li', 'preauth__item');
    li.dataset.action = x.action;
    const txt = mk('div', 'preauth__txt');
    txt.append(mk('b', '', preauthName(x.action)));
    const when = x.at ? new Date(x.at * 1000).toLocaleDateString(lang() === 'en' ? 'en-GB' : 'zh-CN') : '';
    txt.append(mk('small', '', [x.name || x.device || t('p539.pre.thisRemote'), when && t('p539.pre.since', { d: when })].filter(Boolean).join(' · ')));
    const b = mk('button', 'aj-btn aj-btn--sm', t('p539.pre.revoke'));
    b.type = 'button';
    b.addEventListener('click', () => revoke(x));   // {action, device}: only this remote's grant
    li.append(txt, b);
    return li;
  }));
}
