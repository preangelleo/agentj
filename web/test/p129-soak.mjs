// P129 soak: one paired page against the encrypted fake host for --minutes, with the same fault schedule for every run, so
// an old and a new page can be compared (resumes, visible disconnects, seconds shown grey). Owned headless Chromium only.
//   node web/test/p129-soak.mjs --minutes 60 --public <dir of web/public> --out <file.json>
// Faults (seconds from start, repeating): a busy computer whose pong is 12.5 s late while other frames keep flowing
// (every 300 s), a relay / network blink that closes the socket (every 420 s), a computer that is silent for 11 s (every
// 660 s), the computer restarting for 1.5 s (every 1200 s). These are the classes the host.log evidence points at.
import {writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,sleep} from './browser.mjs';

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const minutes = Number(arg('--minutes', '60')), out = arg('--out', '/tmp/p129-soak.json'), publicDir = arg('--public');
const web = await startWebServer(publicDir ? { publicDir } : {}), fake = await startFakeHost(), B = await launch();
const res = { publicDir: publicDir || 'worktree', minutes, faults: { busy: 0, blink: 0, silent: 0, restart: 0 }, resumes: 0, shownOffline: 0, greyMs: 0, notReadyMs: 0, done: false };
const save = () => writeFileSync(out, JSON.stringify(res, null, 2) + '\n');
let mode = 'normal';
try {
  const p = await newPage(B, 390, 844, 'light', { allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  fake.st.heartbeat = true;
  await evaluate(p, `import('./js/session.js').then(m=>{m.closeSession();m.scheduleReconnect()})`); await waitState(p, 'ready');
  fake.st.onApp = async (_c, m, send) => {
    if (m.t !== 'ping') return false;
    if (mode === 'silent') return true;
    if (mode === 'busy') { setTimeout(() => send({ t: 'pong', r: m.r }).catch(() => {}), 12500); return true; }
    return false;
  };
  // the fake host answers ping only through onApp: a normal pong
  const normal = fake.st.onApp;
  fake.st.onApp = async (c, m, send) => { if (await normal(c, m, send)) return true; if (m.t === 'ping') { await send({ t: 'pong', r: m.r }); return true; } return false; };
  await evaluate(p, `window.soak={conn:[],t0:performance.now()};let last=null,lastS=null,at=performance.now(),atS=at;window.soak.grey=0;window.soak.off=0;window.soak.nr=0;
    setInterval(()=>{const now=performance.now(),c=document.body.dataset.conn,s=window.__ajState==='ready';
      if(last==='off')window.soak.grey+=now-at; if(lastS===false)window.soak.nr+=now-atS;
      if(c==='off'&&last!=='off')window.soak.off++; last=c;at=now;lastS=s;atS=now;},50)`);
  const hellos0 = fake.log.filter((m) => m.t === 'hello').length;
  const t0 = Date.now(); let lastSave = 0, busyBeat = null;
  for (let sec = 1; sec <= minutes * 60; sec++) {
    while (Date.now() - t0 < sec * 1000) await sleep(50);
    if (sec % 300 === 60) { mode = 'busy'; res.faults.busy++; busyBeat = setInterval(() => fake.send({ t: 'status', s: 'working', agent: 'claude', name: null }).catch(() => {}), 2000);
      setTimeout(() => { mode = 'normal'; clearInterval(busyBeat); }, 13000); }
    if (sec % 420 === 150) { res.faults.blink++; fake.kick(1001); }
    if (sec % 660 === 240) { mode = 'silent'; res.faults.silent++; setTimeout(() => { mode = 'normal'; }, 11000); }
    if (sec % 1200 === 600) { res.faults.restart++; fake.setUp(false); setTimeout(() => fake.setUp(true), 1500); }
    if (Date.now() - lastSave > 30000) {
      lastSave = Date.now();
      const w = await evaluate(p, `({off:window.soak.off,grey:Math.round(window.soak.grey),nr:Math.round(window.soak.nr)})`);
      Object.assign(res, { resumes: fake.log.filter((m) => m.t === 'hello').length - hellos0, shownOffline: w.off, greyMs: w.grey, notReadyMs: w.nr, elapsedS: sec });
      save();
    }
  }
  await sleep(20000);
  const w = await evaluate(p, `({off:window.soak.off,grey:Math.round(window.soak.grey),nr:Math.round(window.soak.nr)})`);
  Object.assign(res, { resumes: fake.log.filter((m) => m.t === 'hello').length - hellos0, shownOffline: w.off, greyMs: w.grey, notReadyMs: w.nr,
    rc: fake.log.filter((m) => m.t === 'hello').slice(hellos0).map((m) => m.rc ? m.rc.why + (m.rc.code ? ':' + m.rc.code : '') : '-'),
    problems: p.problems.length, done: true });
  save();
  console.log(JSON.stringify(res));
} finally { await B.close(); await fake.stop(); await web.stop(); }
