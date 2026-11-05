// #440: ordinary settings contain endpoint metadata and key names only.
export const ASR_KEYS = ['mode', 'base_url', 'model', 'key_env', 'hotwords'].map(k => 'voice.asr.' + k);
export const DEFAULT_HOTWORDS = ['Agent J', 'Olares', 'Claude Code', 'Codex', 'OpenCode'];
const node = id => document.getElementById('set-asr-' + id);
export function readAsr() {
  return { mode: node('mode').value, base_url: node('url').value.trim(), model: node('model').value.trim(),
    key_env: node('key-env').value.trim(), hotwords: [...new Set(node('hotwords').value.split(/\r?\n/).map(s => s.trim()).filter(Boolean))] };
}
export function validAsr(v) {
  if (!['local', 'cloud', 'custom'].includes(v.mode)) return false;
  if (v.key_env && !/^[A-Z][A-Z0-9_]{0,127}$/.test(v.key_env)) return false;
  if (v.hotwords.length > 100 || v.hotwords.some(s => s.length > 80)) return false;
  if (v.mode === 'custom') {
    try { const u = new URL(v.base_url); if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || u.search || u.hash) return false; } catch { return false; }
    if (!v.model) return false;
  }
  return v.mode !== 'cloud' || !!v.model;
}
function fields() {
  const mode = node('mode').value;
  document.querySelectorAll('[data-asr-custom]').forEach(e => e.hidden = mode !== 'custom');
  document.querySelectorAll('[data-asr-remote]').forEach(e => e.hidden = mode === 'local');
}
export function renderAsr({ get, paired, settable }) {
  const box = document.getElementById('set-asr');
  box.hidden = !paired || !settable('voice.asr.mode');
  if (box.hidden || box.dataset.saving) return;
  for (const [id, key, fallback] of [['mode', 'mode', 'local'], ['url', 'base_url', ''], ['model', 'model', ''], ['key-env', 'key_env', ''], ['hotwords', 'hotwords', DEFAULT_HOTWORDS]]) {
    const input = node(id), value = get('voice.asr.' + key, fallback);
    if (document.activeElement !== input) input.value = Array.isArray(value) ? value.join('\n') : value;
  }
  fields();
}
export function bindAsr({ set, failed, keysSet, t, notify }) {
  const box = document.getElementById('set-asr');
  node('mode').addEventListener('change', fields);
  node('save').addEventListener('click', async () => {
    const v = readAsr();
    if (!validAsr(v)) { notify({kind:'warn', text:t('asr440.invalid')}); return; }
    if (v.mode === 'cloud' || (v.mode !== 'local' && v.key_env)) {
      notify({kind:'info', ms:10000, text:t('asr440.agentSetup', {url:v.base_url, model:v.model, name:v.key_env})});
      return;
    }
    box.dataset.saving = 'true'; node('save').disabled = true;
    try {
      // Park on the built-in path so cross-field validation never activates a partial remote config.
      if (!await set('voice.asr.mode', 'local')) { failed(); return; }
      const keys = v.mode === 'local' ? ['hotwords'] : ['key_env', 'base_url', 'model', 'hotwords', 'mode'];
      for (const key of keys) if (!await set('voice.asr.' + key, v[key])) { failed(); return; }
    } finally { delete box.dataset.saving; node('save').disabled = false; }
  });
  node('key').addEventListener('click', async () => {
    const name = node('key-env').value.trim() || 'ASR_API_KEY';
    if (!/^[A-Z][A-Z0-9_]{0,127}$/.test(name)) { notify({kind:'warn', text:t('asr440.invalid')}); return; }
    node('key-env').value = name;
    const res = await keysSet(name);
    if (!res.ok) notify({kind:'warn', text:t('models.failed')});
  });
}
