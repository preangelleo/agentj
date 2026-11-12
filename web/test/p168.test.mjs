// 2026-11-07 owner decision (1450 G): remove the disconnected/recovered overlay entirely.
// These assertions replace the retired P168 modal's duration/mailto/popup behavior; quiet retry and support remain.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
const APP=read('../public/app.js'),HTML=read('../public/index.html'),CSS=read('../public/app.css'),REG=read('../public/js/cardplace.js');
test('1450 G: disconnected overlay has no shipped renderer, DOM surface, styles, or card placement',()=>{
 assert.equal(existsSync(new URL('../public/js/offline-card.js',import.meta.url)),false);
 assert.doesNotMatch(HTML,/id="off(?:Dlg|Title|Since|Prog|Long|Later|Retry|Copy|Rescue)"/);
 assert.doesNotMatch(CSS,/\.offdlg|\.off-long|\.off-tips|aj-recovery-off-pulse/);
 assert.doesNotMatch(REG,/#offDlg/);
});
test('1450 G: status, boot, reload, foreground and reconnect cannot invoke a disconnected/recovered overlay',()=>{
 assert.doesNotMatch(APP,/offline-card\.js|recovery574\.js|offline\.(?:configure|init|restore|update|dropped|hush|later)|offMode\(|offRescue/);
 assert.doesNotMatch(APP,/p574\.(?:recovered|unknown|frequent|stall)/);
 const settings=read('../public/js/settings.js');const changed=settings.slice(settings.indexOf('export function connectionChanged'),settings.indexOf('onLang(() => { if (connectionState'));assert.doesNotMatch(changed,/toast\(|notify\(/,'Settings connection transitions cannot open a separate notice');
});
test('1450 G: quiet connection indicator, retry on wake, outbox and uploads retain their existing wiring',()=>{
 assert.match(APP,/document\.body\.toggleAttribute\('data-relink', v === 'quiet'\)/);
 assert.match(APP,/relay\.connLost\(\)/);
 assert.match(APP,/if \(down && PAIRED_VIEWS\.includes\(view\)\) link\.drop\(\)/);
 assert.match(APP,/addEventListener\('online', \(\) => \{ wakeLink\(\); session\.reconnectNow\(\)/);
 assert.match(APP,/settings\.connectionChanged\(on, !on && link\.state\(\) === 'quiet'\)/);
 assert.match(HTML,/id="outbox"/);
 const relay=read('../public/js/relay.js');assert.match(relay,/export function connLost\(\)\{\s*quietLost = true; outbox\.lost\(\); historyAwaitingMeta = false;/);
 assert.match(relay,/historyGeneration\+\+; historyHead = null; historySync = true;/);
 assert.match(relay,/if \(on && \(!was \|\| quietLost\)\) uploadsResume\(\);/);
});
test('1450 G: the removed timer no longer persists an outage timestamp or installs a modal observer',()=>{
 assert.doesNotMatch(APP,/host\.downAt|persist: \(at\) => ticketQueue/);
 assert.doesNotMatch(APP,/off(?:Dlg|Later|Retry|Copy|Rescue)/);
});
test('support on owner-opened Settings and error/removed pages remains available',()=>{
 const mails=[...HTML.matchAll(/href="(mailto:[^"]*)"/g)].map(m=>m[1]);assert.ok(mails.length);
 for(const mail of mails)assert.equal(mail,'mailto:feedback@agentj.app');
 assert.match(HTML,/id="error-help-mail" data-i18n="help\.mail"/);
 assert.match(HTML,/id="set-help-mail" data-i18n="help\.mail"/);
 const pub=['index.html','app.js','js/settings.js','i18n.js'].map(f=>read('../public/'+f)).join('\n');
 assert.doesNotMatch(pub,/founder@agentj\.app|\bme@agentj\.app/);
});
