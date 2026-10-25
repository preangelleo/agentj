// P352 fixture: the host's `keys` reply (names / states only) and a fake host handler for keys_get / keys_op / keys_set.
// The copy value is a made-up string (not a key of anything); the tests check it reaches the clipboard and never the DOM.
import { generateKeyPairSync, randomBytes } from 'node:crypto';
export const FAKE_VALUE = 'p352-fixture-not-a-key-' + 'Zq7Wm2';
const hex = () => randomBytes(16).toString('hex');
export function keysMsg(fa) {
  return {
    t: 'keys', id: hex(), n: hex(), ...(fa ? { fa } : {}),
    base: [
      { id: 'anthropic', name: { zh: 'Anthropic · Claude', en: 'Anthropic · Claude' }, rows: [
        { type: 'oauth', name: 'claude', set: false, state: 'in' },
        { type: 'sub', name: 'CLAUDE_CODE_OAUTH_TOKEN', set: false },
        { type: 'key', name: 'ANTHROPIC_API_KEY', set: true, tail: 'x9Qa', in_use: true }] },
      { id: 'openai', name: { zh: 'OpenAI · Codex', en: 'OpenAI · Codex' }, rows: [
        { type: 'oauth', name: 'codex', set: false, state: 'out' },
        { type: 'key', name: 'OPENAI_API_KEY', set: true, tail: '7f3b' }] },
      { id: 'bailian', name: { zh: '阿里云百炼 · 千问', en: 'Alibaba Bailian · Qwen' }, rows: [
        { type: 'sub', name: 'BAILIAN_TOKEN_PLAN_API_KEY', set: true },
        { type: 'key', name: 'DASHSCOPE_API_KEY', set: false }] },
      { id: 'deepseek', name: { zh: 'DeepSeek', en: 'DeepSeek' }, rows: [{ type: 'key', name: 'DEEPSEEK_API_KEY', set: false }] },
    ],
    third: [{ name: 'ELEVENLABS_API_KEY', set: true, tail: 'k2Lm' }, { name: 'WOLFRAM_APP_TOKEN', set: true, in_use: false }],
  };
}
/** st.onApp for fakehost: answers keys_*; `calls` records what the page sent (names / ops only). */
export function keysHandler(st, { fa = null, calls = [], fail = null } = {}) {
  let cur = null;
  const prev = st.onApp;
  st.onApp = async (c, m, send) => {
    if (m.t === 'keys_get') { cur = keysMsg(fa); await send({ ...cur, r: m.r }); return true; }
    if (m.t === 'keys_op') {
      calls.push({ op: m.op, name: m.name, id: m.id, n: m.n, fa: !!(m.fa && m.fa.sig) });
      const n = hex();
      if (fail) { await send({ t: 'keys_res', r: m.r, ok: false, op: m.op, name: m.name, why: fail, n }); return true; }
      if (!cur || m.id !== cur.id || m.n !== cur.n) { await send({ t: 'keys_res', r: m.r, ok: false, op: m.op, name: m.name, why: 'gone', n }); return true; }
      cur.n = n;
      await send(m.op === 'copy' ? { t: 'keys_res', r: m.r, ok: true, op: 'copy', name: m.name, value: FAKE_VALUE, n } : { t: 'keys_res', r: m.r, ok: true, op: m.op, name: m.name, n });
      return true;
    }
    if (m.t === 'keys_set') { calls.push({ op: 'set', name: m.name }); await send({ t: 'keys_res', r: m.r, ok: true, op: 'set', name: m.name, card: {t:'elev',kind:'secret',name:m.name,purpose:'API Key',dest:'Host key file',verify:'',id:hex(),n:hex(),epk:generateKeyPairSync('x25519').publicKey.export({format:'jwk'}).x,ttl:120,passkey_required:true} }); return true; }
    if (m.t === 'models_get') { await send({ t: 'models', r: m.r, models: [{ id: 'p352/model-a', name: 'p352/model-a', efforts: [] }], default: { model: 'p352/model-a' } }); return true; }
    return prev ? prev(c, m, send) : false;
  };
  return calls;
}
