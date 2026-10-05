// Own headless Chromium for the web client's browser tests (never the shared CDP on :9222): random debugging port, temp
// profile, one isolated browser context per page. Fake camera + microphone (Chromium's test devices), so hold-to-talk,
// MediaRecorder, the in-page camera and the WAV encoder run for real; no permission prompts.
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

export const CHROMIUM = process.env.CHROMIUM || '/usr/bin/chromium';

export function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0; const pending = new Map(); const listeners = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
    else if (m.method) listeners.forEach((l) => l(m));
  };
  const ready = new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  return {
    ready,
    send: (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); }),
    on: (fn) => listeners.push(fn),
    waitFor: (method, ms = 15000) => new Promise((res, rej) => {
      const t = setTimeout(() => rej(new Error(`timeout waiting for ${method}`)), ms);
      listeners.push((m) => { if (m.method === method) { clearTimeout(t); res(m.params); } });
    }),
    close: () => ws.close(),
  };
}

export async function launch({ allowOrigins = [], args = [] } = {}) {
  // args: extra Chromium switches, e.g. --use-file-for-fake-audio-capture=<wav> (the real-host parity e2e feeds a speech WAV)
  const profile = mkdtempSync(join(tmpdir(), 'aj-web-chrome-'));
  const chrome = spawn(CHROMIUM, ['--headless=new', `--user-data-dir=${profile}`, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', '--disable-background-networking',
    '--disable-component-update', '--disable-sync', '--disable-extensions', '--mute-audio', '--autoplay-policy=no-user-gesture-required',
    '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', ...(process.env.AJ_MEDIA_DIAGNOSTICS === '1' ? ['--enable-logging=stderr', '--vmodule=audio*=2,media_stream*=2,user_media*=2'] : []), ...args, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  let err = '';
  chrome.stderr.on('data', (d) => { err += d; });
  const f = join(profile, 'DevToolsActivePort');
  let port = 0;
  for (let i = 0; i < 300 && !port; i++) { if (existsSync(f)) { const [p] = readFileSync(f, 'utf8').split('\n'); if (p) port = Number(p); } if (!port) await sleep(100); }
  if (!port) throw new Error('chromium did not start: ' + err.slice(-500));
  const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const browser = cdp(version.webSocketDebuggerUrl); await browser.ready;
  return {
    port, browser, allowOrigins,
    async mediaDiagnostics() {
      const processes=await browser.send('SystemInfo.getProcessInfo').catch(()=>({processInfo:[]}));
      const histograms=await browser.send('Browser.getHistograms',{query:'Media.Audio',delta:false}).catch(()=>({histograms:[]}));
      return {processes:processes.processInfo, histograms:histograms.histograms, errors:{outputHostDown:(err.match(/PcmOpen:.*Host is down/g)||[]).length, outputAuthorizationTimeout:(err.match(/Output device authorization timed out/g)||[]).length},
        native:err.split('\n').filter(s=>/audio|media_stream|user_media|service_process_host|Out of memory|crash/i.test(s)).slice(-40)};
    },
    async close() { if (process.env.AJ_MEDIA_DIAGNOSTICS === '1') console.error('native-media-final', JSON.stringify(await this.mediaDiagnostics().catch(e => ({error:String(e)})))); try { browser.close(); } catch { /* gone */ } chrome.kill('SIGTERM'); await sleep(300); try { rmSync(profile, { recursive: true, force: true }); } catch { /* ignore */ } },
  };
}

/** A page in its own browser context. opts: {ua, touch, allow: [origins that are not "off-site"]} */
export async function newPage(B, w, h, scheme, { ua, touch, allow = [] } = {}) {
  const { browserContextId } = await B.browser.send('Target.createBrowserContext', { disposeOnDetach: true });
  const { targetId } = await B.browser.send('Target.createTarget', { url: 'about:blank', browserContextId });
  const p = cdp(`ws://127.0.0.1:${B.port}/devtools/page/${targetId}`); await p.ready;
  p.problems = []; p.offsite = []; p.mediaDiagnostics = B.mediaDiagnostics;
  p.on((m) => {
    if (m.method === 'Log.entryAdded' && (m.params.entry.level === 'error' || /Content Security Policy/i.test(m.params.entry.text))) p.problems.push(`log: ${m.params.entry.text} ${m.params.entry.url || ''}`);
    if (m.method === 'Runtime.exceptionThrown') p.problems.push(`exception: ${m.params.exceptionDetails.text} ${m.params.exceptionDetails.exception?.description || ''}`);
    if (m.method === 'Runtime.consoleAPICalled' && (m.params.type === 'error' || m.params.type === 'warning')) p.problems.push(`console.${m.params.type}: ${m.params.args.map((a) => a.value ?? a.description).join(' ')}`);
    if (m.method === 'Network.requestWillBeSent') {
      const u = m.params.request.url;
      if (!(allow.some((o) => u.startsWith(o)) || /^(data|blob|about):/.test(u))) p.offsite.push(u);
    }
    if (m.method === 'Network.webSocketCreated' && !allow.some((o) => m.params.url.startsWith(o))) p.offsite.push(m.params.url);
  });
  await p.send('Page.enable'); await p.send('Runtime.enable'); await p.send('Log.enable'); await p.send('Network.enable');
  // Metadata-only media diagnostics: recording failures used to lose the native
  // decoder error inside toWav's safe null fallback. No audio/body is captured.
  await p.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {
    const trace = window.__ajMediaDiagnostics = [];
    window.addEventListener('agentj-audio-error', e => note('toWav.failed', e.detail));
    const note = (kind, detail = {}) => { trace.push({kind, ...detail}); if(trace.length > 32) trace.shift(); };
    const gum = navigator.mediaDevices?.getUserMedia;
    if(gum) navigator.mediaDevices.getUserMedia = function(...args) {
      note('getUserMedia.start');
      const result = gum.apply(this,args);
      result.then(() => note('getUserMedia.ok'), error => note('getUserMedia.failed', {error: error.name}));
      return result;
    };
    const Recorder = window.MediaRecorder;
    if(Recorder) window.MediaRecorder = new Proxy(Recorder, {construct(target,args,newTarget) {
      const recorder = Reflect.construct(target,args,newTarget);
      note('MediaRecorder.created', {mime: recorder.mimeType});
      for(const event of ['start','stop','dataavailable','error']) recorder.addEventListener(event, e =>
        note('MediaRecorder.' + event, {bytes: e.data?.size, error: e.error?.name}));
      return recorder;
    }});
    for (const name of ['AudioContext', 'webkitAudioContext', 'OfflineAudioContext', 'webkitOfflineAudioContext']) {
      const C = window[name]; if(!C) continue;
      const proto = C.prototype;
      for (const method of ['decodeAudioData', 'startRendering']) {
        const fn = proto[method]; if(!fn || fn.__ajDiagnostic) continue;
        const wrapped = function(...args) {
          note(name + '.' + method, {bytes: method === 'decodeAudioData' ? args[0]?.byteLength : undefined});
          try {
            const result = fn.apply(this,args);
            if(result?.then) result.then(() => note(method + '.ok'), error => note(method + '.failed', {error: error.name}));
            return result;
          } catch(error) { note(method + '.threw', {error: error.name}); throw error; }
        };
        wrapped.__ajDiagnostic = true; proto[method] = wrapped;
      }
      window[name] = new Proxy(C, { construct(target, args, newTarget) {
        try { const instance = Reflect.construct(target,args,newTarget); note(name + '.created', {state: instance.state, rate: instance.sampleRate}); return instance; }
        catch(error) { note(name + '.failed', {error: error.name}); throw error; }
      }});
    }
  })()` });
  await p.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
  if (touch) await p.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });
  await p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
  if (ua) await p.send('Emulation.setUserAgentOverride', { userAgent: ua, platform: ua.includes('iPhone') ? 'iPhone' : 'Linux armv8l' });
  await B.browser.send('Browser.grantPermissions', { permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite', 'audioCapture', 'videoCapture'], browserContextId }).catch(() => {});
  p.dispose = async () => { p.close(); await B.browser.send('Target.closeTarget', { targetId }).catch(() => {}); await B.browser.send('Target.disposeBrowserContext', { browserContextId }).catch(() => {}); };
  p.ctx = browserContextId;
  return p;
}

export async function evaluate(p, expr) {
  const r = await p.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true, userGesture: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}
export async function navigate(p, url) { const load = p.waitFor('Page.loadEventFired'); await p.send('Page.navigate', { url }); await load; await sleep(250); }
export async function waitFor(p, expr, ms = 8000) {
  for (let t = 0; t < ms; t += 50) { if (await evaluate(p, expr)) return true; await sleep(50); }
  if (process.env.AJ_MEDIA_DIAGNOSTICS === '1' && p.mediaDiagnostics) console.error('native-media-diagnostic', JSON.stringify({page:await evaluate(p, `Promise.all(['microphone','camera'].map(name=>navigator.permissions.query({name}).then(x=>({name,state:x.state})).catch(()=>({name,state:'unknown'})))).then(permissions=>({focus:document.hasFocus(),visibility:document.visibilityState,permissions}))`).catch(()=>null), native:await p.mediaDiagnostics().catch(e => ({ error: String(e) }))}));
  throw new Error('timeout: ' + expr);
}
export async function waitState(p, want, ms = 10000) {
  let s;
  for (let t = 0; t < ms; t += 100) { s = await evaluate(p, 'window.__ajState'); if (s === want) return true; await sleep(100); }
  throw new Error(`state ${s} != ${want} (status: ${await evaluate(p, `document.getElementById('status').textContent + ' / ' + document.getElementById('error-text').textContent`)}; problems: ${p.problems.join(' | ')})`);
}
export async function key(p, k, { code, shift, ctrl, meta, text, type = 'both', keyCode } = {}) {
  const mod = (shift ? 8 : 0) | (ctrl ? 2 : 0) | (meta ? 4 : 0);
  const base = { key: k, code: code || (k.length === 1 ? 'Key' + k.toUpperCase() : k), modifiers: mod, windowsVirtualKeyCode: keyCode ?? (k.length === 1 ? k.toUpperCase().charCodeAt(0) : { Escape: 27, Enter: 13, ArrowUp: 38, ArrowDown: 40, ArrowLeft: 37, ArrowRight: 39, Tab: 9, ' ': 32 }[k] || 0) };
  if (type !== 'up') await p.send('Input.dispatchKeyEvent', { type: text === undefined && (ctrl || meta || k.length > 1) ? 'rawKeyDown' : 'keyDown', ...base, ...(text !== undefined ? { text } : k.length === 1 && !ctrl && !meta ? { text: k } : {}) });
  if (type !== 'down') await p.send('Input.dispatchKeyEvent', { type: 'keyUp', ...base });
}
export async function tap(p, x, y) {
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}
export async function rect(p, sel) {
  return evaluate(p, `(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height, top: r.top, left: r.left }; })()`);
}
export async function swipe(p, x0, y0, x1, y1, steps = 8, ms = 120) {
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: x0, y: y0 }] });
  for (let i = 1; i <= steps; i++) {
    await p.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x0 + (x1 - x0) * i / steps, y: y0 + (y1 - y0) * i / steps }] });
    await sleep(ms / steps);
  }
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}
export async function shoot(p, file, { full = false } = {}) {
  await sleep(150);
  const { cssContentSize: s, cssLayoutViewport: v } = await p.send('Page.getLayoutMetrics');
  const clip = full ? { x: 0, y: 0, width: s.width, height: s.height, scale: 1 } : { x: 0, y: v.pageY, width: v.clientWidth, height: v.clientHeight, scale: 1 };
  const { data } = await p.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: full, clip });
  return Buffer.from(data, 'base64');
}
export { sleep };
