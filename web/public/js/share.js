// Browser-local share handoff. No HTTP, persistent content, or automatic send.
import {el} from './ui.js';
import {t, onLang} from './t.js';
import {importShare, pasteClipImages} from './relay.js';
let pending = null, paired = false, ready = false, enabled = false, key = null;
function render(){
  const box = el('share-notice'); box.hidden = !key;
  if (document.body.dataset.view === 'chat') document.querySelector('.composer').prepend(box);
  else document.body.append(box);
  el('share-status').textContent = key ? t(key) : '';
  const button = el('share-paste'); button.hidden = !enabled || !paired;
  button.disabled = !ready; button.textContent = t('share.paste');
}
function note(k){ key = k; render(); }
function consume(id){
  return new Promise((resolve, reject) => {
    const sw = navigator.serviceWorker?.controller;
    if (!sw) return reject(new Error('missing'));
    const ch = new MessageChannel();
    const timer = setTimeout(() => { ch.port1.close(); reject(new Error('timeout')); }, 5000);
    ch.port1.onmessage = e => { clearTimeout(timer); ch.port1.close(); resolve(e.data); };
    sw.postMessage({t:'share-take', id}, [ch.port2]);
  });
}
export async function initShare(approved){
  paired = approved;
  const u = new URL(location.href), id = u.searchParams.get('share');
  enabled = u.searchParams.get('from') === 'share';
  el('share-paste').addEventListener('click', () => {
    if (!paired || !ready) return;
    // Call clipboard.read directly inside this click, preserving Safari user activation.
    pasteClipImages();
  });
  onLang(render);
  if (id) {
    try {
      const value = id === 'failed' ? {error:'failed'} : await consume(id);
      if (!paired) note('share.pair');
      else if (value.error) note('share.failed');
      else { pending = value; note('share.wait'); onShareReady(ready); }
    } catch { note(paired ? 'share.failed' : 'share.pair'); }
    finally {
      u.searchParams.delete('share'); history.replaceState(history.state, '', u.pathname + u.search + u.hash);
    }
  } else if (enabled) note(paired ? 'share.clip' : 'share.pair');
}
export function onShareReady(on = true){
  ready = on;
  if (ready && paired && pending) { importShare(pending); pending = null; note('share.added'); }
  render();
}
export function forgetShare(){ pending = null; paired = false; ready = false; if (key) note('share.pair'); }
