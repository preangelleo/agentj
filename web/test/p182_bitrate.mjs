// P182 report tool (not a gate): what MediaRecorder actually produces for speech, per engine and setting, measured on a
// 60-second take of real speech (the ASR test clip, looped) fed through Web Audio → MediaStreamDestination, three recorders
// at once (browser default · 48 kbps = what relay.js asks for · 32 kbps). Sizes are extrapolated linearly to a 20-minute part.
// usage: node p182_bitrate.mjs <speech.wav> [secs] → reports/qa/p182/bitrate.json
// Engines: system Chromium (Playwright's chromium if CHROMIUM unset fails) and Playwright WebKit (Linux GTK port; iOS Safari
// uses the same MediaRecorder front end but Apple's AVFoundation AAC encoder — see REPORT-182 for that caveat).
import {createServer} from 'node:http';
import {readFileSync, writeFileSync, mkdirSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';

const wav = process.argv[2], SECS = +(process.argv[3] || 60);
const require = createRequire(import.meta.url);
const pw = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const PAGE = `<!doctype html><meta charset=utf-8><script>
window.run = async (secs) => {
  const ctx = new AudioContext(); try { await ctx.resume(); } catch {}
  const ab = await (await fetch('/speech.wav')).arrayBuffer(), v = new DataView(ab); let o = 12, rate = 48000, data = 0, len = 0;
  while (o + 8 <= ab.byteLength) { const id = String.fromCharCode(v.getUint8(o), v.getUint8(o+1), v.getUint8(o+2), v.getUint8(o+3)), n = v.getUint32(o + 4, true);
    if (id === 'fmt ') rate = v.getUint32(o + 12, true); if (id === 'data') { data = o + 8; len = Math.min(n, ab.byteLength - data); break; } o += 8 + n + (n & 1); }
  const pcm = new Int16Array(ab.slice(data, data + len - (len & 1))); const buf = ctx.createBuffer(1, pcm.length, rate); const ch = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const src = ctx.createBufferSource(); src.buffer = buf; src.loop = true; const dst = ctx.createMediaStreamDestination(); src.connect(dst); src.start();
  const kinds = ['audio/webm;codecs=opus', 'audio/mp4;codecs=mp4a.40.2', 'audio/mp4', 'audio/ogg;codecs=opus'];
  const pick = kinds.filter(k => { try { return MediaRecorder.isTypeSupported(k); } catch { return false; } });
  const configs = [];
  for (const mime of pick.length ? [pick[0], ...pick.filter(m => m.startsWith('audio/mp4')).slice(0, 1)].filter((v, i, a) => a.indexOf(v) === i) : [null])
    for (const bps of [null, 48000, 32000]) configs.push({mime, bps});
  const runs = configs.map(c => new Promise(res => {
    const o = {}; if (c.mime) o.mimeType = c.mime; if (c.bps) o.audioBitsPerSecond = c.bps;
    let r; try { r = new MediaRecorder(dst.stream, o); } catch (e) { return res({...c, error: String(e)}); }
    const parts = []; r.ondataavailable = e => e.data.size && parts.push(e.data);
    r.onstop = async () => { const b = new Blob(parts, {type: r.mimeType}); const u8 = new Uint8Array(await b.arrayBuffer());
      let s = ''; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode(...u8.subarray(i, i + 0x8000));
      res({...c, actualMime: r.mimeType, bytes: b.size, b64: btoa(s)}); };
    r.start(4000); setTimeout(() => r.stop(), secs * 1000);
  }));
  return {supported: pick, results: await Promise.all(runs), ua: navigator.userAgent};
};</script>`;
const srv = createServer((q, s) => { if (q.url === '/speech.wav') { s.setHeader('content-type', 'audio/wav'); s.end(readFileSync(wav)); } else { s.setHeader('content-type', 'text/html'); s.end(PAGE); } });
await new Promise(r => srv.listen(0, '127.0.0.1', r));
const url = `http://127.0.0.1:${srv.address().port}/`;
const out = {secs: SECS, source: 'ASR test clip (Mandarin speech) looped, 48 kHz mono, Web Audio → MediaRecorder', engines: {}};
const dir = join(tmpdir(), 'p182-bitrate'); mkdirSync(dir, {recursive: true});
async function engine(name, launcher, opts) {
  const b = await launcher.launch(opts); const p = await (await b.newContext()).newPage();
  await p.goto(url);
  const r = await p.evaluate((s) => window.run(s), SECS);
  r.version = b.version(); await b.close();
  for (const x of r.results) {
    if (x.error) continue;
    const ext = /mp4/.test(x.actualMime) ? 'm4a' : /ogg/.test(x.actualMime) ? 'ogg' : 'webm', f = join(dir, `${name}-${(x.mime || 'default').replace(/\W+/g, '_')}-${x.bps || 'default'}.${ext}`);
    writeFileSync(f, Buffer.from(x.b64, 'base64')); delete x.b64;
    const pr = spawnSync('ffprobe', ['-v', 'error', '-show_entries', 'stream=codec_name,sample_rate,channels,bit_rate:format=duration,bit_rate', '-of', 'json', f], {encoding: 'utf8'});
    try { const j = JSON.parse(pr.stdout); x.codec = j.streams?.[0]?.codec_name; x.sampleRate = +j.streams?.[0]?.sample_rate; x.channels = j.streams?.[0]?.channels; } catch { /* unreadable */ }
    const dec = spawnSync('ffmpeg', ['-nostdin', '-v', 'error', '-i', f, '-ac', '1', '-ar', '16000', '-f', 'null', '-'], {encoding: 'utf8'});
    x.ffmpegDecodes = dec.status === 0;
    x.kbps = Math.round(x.bytes * 8 / SECS / 100) / 10;
    x.mbPer20min = Math.round(x.bytes / SECS * 1200 / 1048576 * 10) / 10;
    x.mbPer200min = Math.round(x.bytes / SECS * 12000 / 1048576);
  }
  out.engines[name] = r;
}
try {
  const only = process.env.ENGINE, list = [['chromium', pw.chromium, {executablePath: process.env.CHROMIUM || '/usr/bin/chromium', args: ['--autoplay-policy=no-user-gesture-required']}], ['webkit', pw.webkit, {}]];
  await Promise.all(list.filter(([n]) => !only || only === n).map(([n, l, o]) => engine(n, l, o).catch(e => { out.engines[n] = {error: String(e).slice(0, 300)}; })));
} finally { srv.close(); }
out.wav16k = {kbps: 256, mbPer20min: Math.round(44 + 32000 * 1200) / 1048576 | 0, note: '16 kHz mono PCM16 (what toWav makes for takes ≤ 10 min)'};
const dest = new URL('../../../reports/qa/p182/bitrate.json', import.meta.url);
mkdirSync(new URL('.', dest), {recursive: true});
writeFileSync(dest, JSON.stringify(out, null, 2) + '\n');
for (const [n, e] of Object.entries(out.engines)) for (const x of e.results || []) console.log(n, x.mime || 'default', x.bps || 'default', x.actualMime, x.codec, x.sampleRate, x.channels, x.kbps + ' kbps', x.mbPer20min + ' MB/20min', 'ffmpeg:' + x.ffmpegDecodes, x.error || '');
