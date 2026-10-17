// P362 fixture: a `keys` reply with keys the computer already has (`found`, `src`) and the sign-in rows' real login (`use`),
// plus a fake host handler that also answers keys_op import. Every label and tail is made up; no value is ever sent.
import { randomBytes } from 'node:crypto';
const hex = () => randomBytes(16).toString('hex');
export const ENVD = 's0123456789abcdef', FILE = 'sfedcba9876543210', FP = '0123456789abcdef';
export function keysMsg(fa, imported = new Set()) {
  const has = (n) => imported.has(n);
  return {
    t: 'keys', id: hex(), n: hex(), ...(fa ? { fa } : {}),
    base: [
      { id: 'anthropic', name: { zh: 'Anthropic · Claude', en: 'Anthropic · Claude' }, rows: [
        { type: 'oauth', name: 'claude', set: true, state: 'in', use: 'sub', use_name: 'CLAUDE_CODE_OAUTH_TOKEN', use_src: has('CLAUDE_CODE_OAUTH_TOKEN') ? 'agentj' : ENVD },
        { type: 'sub', name: 'CLAUDE_CODE_OAUTH_TOKEN', set: has('CLAUDE_CODE_OAUTH_TOKEN'), ...(has('CLAUDE_CODE_OAUTH_TOKEN') ? { tail: 'k7Qz' } : {}), in_use: true,
          found: [{ src: ENVD, tail: 'k7Qz', ...(has('CLAUDE_CODE_OAUTH_TOKEN') ? { same: true } : { live: true, fp: FP }) }] },
        { type: 'key', name: 'ANTHROPIC_API_KEY', set: false }] },
      { id: 'openai', name: { zh: 'OpenAI · Codex', en: 'OpenAI · Codex' }, rows: [
        { type: 'oauth', name: 'codex', set: true, state: 'in', use: 'login' },
        { type: 'key', name: 'OPENAI_API_KEY', set: true, tail: '7f3b', found: [{ src: FILE, tail: '9a9a', same: false }] }] },
    ],
    third: [
      { name: 'ELEVENLABS_API_KEY', set: has('ELEVENLABS_API_KEY'), ...(has('ELEVENLABS_API_KEY') ? { tail: 'e1E2' } : {}), found: [{ src: FILE, tail: 'e1E2', ...(has('ELEVENLABS_API_KEY') ? { same: true } : { fp: FP }) }] },
      { name: 'GITHUB_TOKEN', set: false, found: [{ src: 'systemd', tail: 'ghgh', fp: FP }] },
      { name: 'WOLFRAM_APP_TOKEN', set: true }],
    src: [
      { id: ENVD, label: '~/.config/environment.d/50-claude-oauth.conf', kind: 'envd', count: 1 },
      { id: 'systemd', label: 'systemd', kind: 'systemd', count: 1 },
      { id: FILE, label: '~/projects/.env', kind: 'file', count: 3 },
      { id: 's1111111111111111', label: '~/shared/team.env', kind: 'file', count: 0, why: 'symlink' }],
  };
}
/** st.onApp for fakehost: keys_get / keys_op (copy | delete | import) / keys_set; `calls` = what the page sent (no values). */
export function keysHandler(st, { fa = null, calls = [] } = {}) {
  let cur = null;
  const imported = new Set();
  const prev = st.onApp;
  st.onApp = async (c, m, send) => {
    if (m.t === 'keys_get') { cur = keysMsg(fa, imported); await send({ ...cur, r: m.r }); return true; }
    if (m.t === 'keys_op') {
      calls.push({ op: m.op, name: m.name, src: m.src, keep: m.keep, fp: m.fp, id: m.id, n: m.n, fa: !!(m.fa && m.fa.sig) });
      const n = hex();
      if (!cur || m.id !== cur.id || m.n !== cur.n) { await send({ t: 'keys_res', r: m.r, ok: false, op: m.op, name: m.name, why: 'gone', n }); return true; }
      cur.n = n;
      if (m.op === 'import') imported.add(m.name);
      await send({ t: 'keys_res', r: m.r, ok: true, op: m.op, name: m.name, n, ...(m.op === 'import' ? { old: m.keep ? 'kept' : 'commented' } : {}) });
      return true;
    }
    if (m.t === 'keys_set') { calls.push({ op: 'set', name: m.name }); await send({ t: 'keys_res', r: m.r, ok: true, op: 'set', name: m.name }); return true; }
    if (m.t === 'models_get') { await send({ t: 'models', r: m.r, models: [{ id: 'p362/model-a', name: 'p362/model-a', efforts: [] }], default: { model: 'p362/model-a' } }); return true; }
    return prev ? prev(c, m, send) : false;
  };
  return calls;
}
