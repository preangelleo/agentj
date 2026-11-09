#!/usr/bin/env node
// P572 (#385): an approval card's reason (host `reason`, the Agent's description) sits in plain words outside the code block,
// right above the buttons; a Touch ID card's code block folds to half its old height (展开全部 opens it; the copy icon stays).
// No reason given ("" from the host) → 「Agent 没说明理由」 in a lighter weight. No `reason` key (an older host) → as before.
// The real web client against fakehost.mjs, own headless Chromium (never :9222). Cards: Touch ID (with reason), 长按 (with
// reason), Touch ID without a reason, and zh with an English reason — 390 × 844 light / dark × zh / en, + 1440 × 900 light zh.
// Step 2: the summaries are the host's own card text (fixtures/p572/summaries.json, kept equal to action_policy.summary() by
// host tests/test_p572_reason.py); a `reason` that is not in the signed summary shows the fallback (card `unsigned`).
// Visual regression against the approved draft (Leo 10-29 「确认卡理由版式可以」): every screen's geometry that CSS alone
// decides (code block height, the gaps around the reason line) must equal fixtures/p572/approved-metrics.json (measured on the
// draft prototype 66ba84f3d) within 2 px; with P572_APPROVED=<dir of the draft's PNGs from the same machine> every shot is
// also compared pixel by pixel (≤ P572_MAXDIFF of pixels differ, default 0.001) and the diff counts land in metrics-after.json.
//   node web/test/p572.mjs   → 「p572 after: … checks passed」, exit 1 on any failure
//   P572_FIXTURE=<json> P572_OUT=<dir> P572_TAG=before|after|approved node p572.mjs
// `before` = the base tree's shots (a client that ignores `reason`); `approved` = shots + metrics on the draft prototype tree.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync, existsSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIX = JSON.parse(readFileSync(process.env.P572_FIXTURE || join(HERE, 'fixtures/p572/summaries.json'), 'utf8'));
const OUT = process.env.P572_OUT || mkdtempSync(join(tmpdir(), 'p572-')), TAG = process.env.P572_TAG || 'after';
const APPROVED_METRICS = join(HERE, 'fixtures/p572/approved-metrics.json');
const APPROVED = process.env.P572_APPROVED || '', MAXDIFF = Number(process.env.P572_MAXDIFF || 0.001);
mkdirSync(OUT, { recursive: true });
const UA = 'Mozilla/5.0 (Linux; Android 14; PJZ110) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36';
const REASON = { zh: '释放空间并列出 Qwen3-1.7B 的量化文件', en: 'Free space and list Qwen3-1.7B quantized files' };
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
let checks = 0;
const check = (v, label) => { if (TAG !== 'after') return; assert.ok(v, label); checks++; };
const hex = (n) => n.toString(16).padStart(32, '0');
const b64 = (u8) => Buffer.from(u8).toString('base64url');
const metrics = {};
const visualDifferences = [];

async function paired(w, h, scheme, lang) {
  fake.reset();
  const p = await newPage(B, w, h, scheme, { ua: w < 500 ? UA : undefined, touch: w < 500, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await ev(p, `document.getElementById('phone-env-dismiss')?.click()`);
  await sleep(300);
  return p;
}
const box = (p, id) => ev(p, `(()=>{const e=document.getElementById(${JSON.stringify(id)});if(!e||e.hidden||!e.offsetParent)return null;const r=e.getBoundingClientRect();return {top:Math.round(r.top),h:Math.round(r.height),bottom:Math.round(r.bottom),left:r.left,right:r.right,w:r.width}})()`);

const CARDS = [
  // name, fixture key, passkey, reason
  ['touchid', 'with', true, (l) => REASON[l]],
  ['hold', 'with', false, (l) => REASON[l]],
  ['noreason', 'none', true, () => ''],
  ['mixed', 'mixed', true, () => REASON.en],
  ['unsigned', 'with', true, () => 'Delete the whole home folder'],   // not in the signed summary → the fallback
];
const POLISHED = (card, lang) => card === 'noreason' && lang === 'zh';   // step 2 polished the zh fallback words (same line, same box)
const SHOWN = (card, lang, reason) => card === 'unsigned' ? '' : card === 'mixed' && lang === 'zh' ? '运行一条命令；Agent 原始说明见「展开全部」' : reason(lang);   // what the reason line must say ('' = fallback)
const approvedMetrics = TAG === 'after' && existsSync(APPROVED_METRICS) ? JSON.parse(readFileSync(APPROVED_METRICS, 'utf8')) : null;
let cmpPage = null;
// The share of pixels that differ (any channel by more than 16) between two PNGs of the same size; 1 when sizes differ.
// `skip` = CSS-pixel rects left out: the ticking countdown, and a reason line whose words step 2 polished on purpose.
async function pixelDiff(a, b, skip = []) {
  cmpPage ||= await newPage(B, 64, 64, 'light');
  return ev(cmpPage, `(async()=>{const load=async(s)=>{const i=await createImageBitmap(await (await fetch('data:image/png;base64,'+s)).blob());
    const c=new OffscreenCanvas(i.width,i.height),x=c.getContext('2d');x.drawImage(i,0,0);return x.getImageData(0,0,i.width,i.height)};
    const A=await load(${JSON.stringify(a.toString('base64'))}),B=await load(${JSON.stringify(b.toString('base64'))});
    if(A.width!==B.width||A.height!==B.height)return 1;let d=0;const S=${JSON.stringify(skip)};
    for(let k=0;k<A.data.length;k+=4){const px=(k/4)%A.width,py=Math.floor(k/4/A.width);
      if(S.some((r)=>px>=r.x&&px<r.x+r.w&&py>=r.y&&py<r.y+r.h))continue;if(Math.abs(A.data[k]-B.data[k])>16||Math.abs(A.data[k+1]-B.data[k+1])>16||Math.abs(A.data[k+2]-B.data[k+2])>16)d++}
    return d/(A.width*A.height)})()`);
}

// Real input events for copy/approval. No direct clipboard calls or source/verification injection.
async function trustedTap(p, selector) {
  const point=await ev(p,`(()=>{const n=document.querySelector(${JSON.stringify(selector)});n.scrollIntoView({block:'center'});const r=n.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  await p.send('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});
  await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1});
}
async function currentV2Contract() {
  const py=spawn(process.env.P0300_PYTHON||'host/.venv/bin/python',[fileURLToPath(new URL('./p0300-account-host.py',import.meta.url))],{stdio:['pipe','pipe','pipe'],env:process.env});
  let stderr='',pending=[],spawnError=null;py.on('error',e=>spawnError=e);py.stderr.on('data',b=>stderr+=b);
  createInterface({input:py.stdout}).on('line',line=>{if(line.startsWith('0300RPC:'))pending.shift()?.(JSON.parse(line.slice(8)));});
  async function rpc(req){let timer;try{return await Promise.race([new Promise((resolve,reject)=>{if(spawnError)return reject(spawnError);pending.push(resolve);py.stdin.write(JSON.stringify(req)+'\n');}),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('p572 Host RPC: '+stderr.slice(-1000))),15000);})]).then(r=>{assert.ok(r.ok,(r.error||'')+' '+(r.trace||''));return r;});}finally{clearTimeout(timer);}}
  const traces=[];let serial=0;
  try{for(const [width,height,theme,lang,scenario] of [[390,844,'light','zh','allow'],[1440,900,'dark','en','allow'],[390,844,'dark','zh','missing-uv'],[390,844,'dark','en','forged']]){
    fake.reset();const local=web.url.replace('127.0.0.1','localhost'),p=await newPage(B,width,height,theme,{allow:[local,fake.relay+'/']});
    const trace={width,height,theme,lang,scenario,frames:[]};traces.push(trace);let latest,ask,poll,polling;
    const forward=async r=>{latest=r;trace.executions=r.executions;trace.audits=r.audits;for(const frame of r.frames){if(frame.t==='ask')ask=frame;trace.frames.push({t:frame.t,tool:frame.tool,why:frame.why,ok:frame.ok,approval_v:frame.approval_v});await fake.send(frame);}};
    try{
      await navigate(p,fake.newPairing(local+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
      const keys=await ev(p,`(async()=>{const s=await import('./js/store.js'),w=await import('./proto/wire.js');return{pub:w.b64u((await s.deviceKey()).pub),sk:w.b64u((await s.signKey()).pub)}})()`);
      const setup=await rpc({op:'setup',serial:++serial,...keys,device:fake.devs.at(-1).id,channel:fake.channel,has_key:true});
      await p.send('WebAuthn.enable',{enableUI:false});const {authenticatorId}=await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});await p.send('WebAuthn.addCredential',{authenticatorId,credential:setup.credential});await fake.send(setup.pk_state);
      fake.st.onApp=async(c,m)=>{if(!['acct_get','auth_get','auth_start','answer'].includes(m.t))return false;if(m.t==='answer')trace.frames.push({t:'answer',has_uv:!!m.fa,signature_shape:typeof m.sig==='string'});await forward(await rpc({op:'frame',frame:m}));return true;};
      poll=setInterval(()=>{if(!polling)polling=rpc({op:'drain'}).then(forward).catch(e=>trace.pollError=String(e)).finally(()=>polling=null);},100);
      await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.getElementById('setBtn').click();document.getElementById('set-accts-nav').click();`);
      await waitFor(p,`!!document.querySelector('#acct-list [data-kind=codex] .acctadd')`);await trustedTap(p,'#acct-list [data-kind=codex] .acctadd');await trustedTap(p,'#acct-list [data-kind=codex] .acctform button');
      await waitFor(p,`document.body.dataset.sheet==='1'&&document.getElementById('sheet').dataset.approvalV==='2'`);
      check(ask?.approval_v===2&&ask.tool==='AuthRescue'&&ask.envelope?.details,'current v2 comes from real Host listed operation with canonical details');
      const id=ask.id;
      check(await ev(p,`import('./js/snap.js').then(m=>m.approvalById(${JSON.stringify(id)})?.envelope_verified===true)`),'real client verified Host envelope, never fixture-supplied verified flag');
      check(await ev(p,`(async()=>{const t=await import('./js/t.js');return document.getElementById('action541').textContent===t.t('p0287.reason.missingFields')})()`),'real AuthRescue honestly states unsupported independent reason fields');
      check(await ev(p,`document.getElementById('cmdReason').hidden&&document.getElementById('details541').contains(document.getElementById('cmdBox'))&&!document.getElementById('details541').open`),'current v2 places command in folded details and hides the old reason line');
      check(await ev(p,`(()=>{const ids=['source541','action541','details541','apprBtns'],r=ids.map(id=>document.getElementById(id).getBoundingClientRect());return r.every(x=>x.width>0&&x.left>=0&&x.right<=innerWidth)&&r.every((x,i)=>!i||x.top>=r[i-1].bottom)&&['apprAllow','doubt541c','apprDeny'].every(id=>{const n=document.getElementById(id),x=n.getBoundingClientRect();return !n.hidden&&x.width>=44&&x.height>=44&&x.top>=0&&x.bottom<=innerHeight})})()`),'current v2 source/reason/details/actions do not overlap and all three actions remain reachable');
      await trustedTap(p,'#details541 summary');await trustedTap(p,'#detailsFull541');await waitFor(p,`document.getElementById('full541').open`);
      check(await ev(p,`document.querySelector('#full541 pre').textContent.includes(${JSON.stringify(ask.envelope.details)})`),'fullscreen retains actual Host canonical details in full');
      await trustedTap(p,'#full541 pre');
      await waitFor(p,`navigator.clipboard.readText().then(x=>x.includes(${JSON.stringify(ask.envelope.details)}))`);
      check((await ev(p,`navigator.clipboard.readText()`)).includes(ask.envelope.details),'real fullscreen copy retains canonical details, not a fixture-created summary');
      writeFileSync(`${OUT}/v2-${width}-${theme}-${lang}-${scenario}.png`,await shoot(p,''));
      await trustedTap(p,'#full541 button');await waitFor(p,`!document.getElementById('full541').open&&document.body.dataset.sheet==='1'`);
      if(scenario==='allow'){
        await trustedTap(p,'#apprAllow');await waitFor(p,`!!document.querySelector('#acct-flow .arcode')`);
        check(latest.executions===1&&latest.audits.includes('ok')&&trace.frames.filter(x=>x.t==='answer'&&x.has_uv).length===1,'real fresh native UV executes once with Host-verified audit');
      }else{
        // Sign the actual client-verified card with this paired device, omitting UV on purpose.
        // No source, envelope field or verified bit is constructed or changed for this negative control.
        const answer=await ev(p,`(async()=>{const snap=await import('./js/snap.js'),wire=await import('./proto/wire.js'),store=await import('./js/store.js'),session=await import('./js/session.js'),api=await import('./js/api.js'),card=snap.approvalById(${JSON.stringify(id)}),msg=await wire.approvalV2Message(session.channel(),await api.myDeviceId(),card,'allow',[]),sig=wire.b64u(new Uint8Array(await crypto.subtle.sign({name:'Ed25519'},(await store.signKey()).priv,msg)));return{t:'answer',id:card.id,ok:true,sig,approval_v:2,revision:card.revision,envelope_sha256:card.envelope_sha256}})()`);
        if(scenario==='forged')answer.sig='A'.repeat(86);
        await ev(p,`import('./js/session.js').then(s=>s.sendApp(${JSON.stringify(answer)}))`);
        await sleep(150);await forward(await rpc({op:'drain'}));
        check(latest.executions===0&&latest.pending===1,`${scenario}: real Host refuses the answer and executes nothing`);
        check(trace.frames.some(x=>x.t==='answer'&&!x.has_uv),`${scenario}: negative went through real Noise without UV`);
        check(trace.frames.some(x=>x.t==='answer_refused'&&x.why===(scenario==='missing-uv'?'passkey_required':'bad_signature')),`${scenario}: Host rejects the precise tested boundary, not an unrelated invalid fixture`);
        await trustedTap(p,'#apprDeny');await sleep(150);await forward(await rpc({op:'drain'}));check(latest.executions===0,`${scenario}: cleanup refusal cannot execute`);
      }
      check(!p.problems.some(x=>x.startsWith('exception:')),'current v2 has no uncaught client exception');
    }finally{clearInterval(poll);if(polling)await polling;fake.st.onApp=null;await p.dispose();}
  }}finally{try{await rpc({op:'stop'});}catch{}py.kill();writeFileSync(`${OUT}/v2-host-contract.json`,JSON.stringify({traces,stderr},null,2)+'\n');}
}

let n = 0;
try {
  for (const [w, h, scheme, lang] of [[390, 844, 'light', 'zh'], [390, 844, 'dark', 'zh'], [390, 844, 'light', 'en'], [390, 844, 'dark', 'en'], [1440, 900, 'light', 'zh'], [1440, 900, 'dark', 'zh']]) {
    for (const [card, fk, passkey, reason] of CARDS) {
      if (card === 'mixed' && lang !== 'zh') continue;
      if (w > 500 && card !== 'touchid') continue;
      if (card === 'unsigned' && (TAG !== 'after' || scheme !== 'light')) continue;
      const name = `${card}-${lang}-${scheme}${w > 500 ? '-1440' : ''}`;
      const p = await paired(w, h, scheme, lang);
      if (passkey) await fake.send({ t: 'pk_state', s: 'local' });
      const id = hex(57200 + ++n);
      await fake.ask({ t: 'ask', id, tool: 'Bash', summary: FIX[`${lang}-${fk}`], ttl: 600, cat: [], why: '', reason: reason(lang),
        ...(passkey ? { passkey_required: true, fa: 'Y3JlZC0x', fa_ch: b64(Buffer.alloc(32, 7)), fa_s: 'local' } : {}) });
      await waitFor(p, `!document.getElementById('sheet').hidden && !document.getElementById('apprBtns').hidden`);
      await sleep(400);
      await ev(p, `{const a=document.getElementById('a2hs');if(a&&!a.hidden)document.getElementById('a2hs-ok').click()}`);   // the install hint covers the card top
      await sleep(300);
      const m = { cmdBox: await box(p, 'cmdBox'), pre: await box(p, 'cmd'), reason: await box(p, 'cmdReason'), allow: await box(p, 'apprAllow'), vh: h };
      metrics[name] = m;
      const png = await shoot(p, '');
      writeFileSync(`${OUT}/${name}.png`, png);
      if (TAG === 'after' && approvedMetrics && approvedMetrics[name]) {
        // the approved draft, screen by screen: the CSS-decided geometry is font-independent, so it holds on any machine
        const A = approvedMetrics[name], gap = (x) => ({ pre: x.pre.h, above: x.reason.top - x.cmdBox.bottom, below: x.allow.top - x.reason.bottom });
        const got = gap(m), want = gap(A);
        // 7aa5df0687 (formal 0286): a visible legacy-source box now separates code/reason.
        // Keep the historic code height and still-adjacent lower gap; never rewrite 10 as 95.
        for (const k of ['pre','below']) check(Math.abs(got[k] - want[k]) <= 2, `${name}: ${k} ${got[k]} px = approved ${want[k]} px (±2)`);
      }
      if (TAG === 'after' && APPROVED && existsSync(`${APPROVED}/${name}.png`)) {
        const rectOf = (id) => ev(p, `(()=>{const e=document.getElementById(${JSON.stringify(id)});if(!e||e.hidden||!e.offsetParent)return null;const r=e.getBoundingClientRect();return {x:Math.floor(r.left)-2,y:Math.floor(r.top)-2,w:Math.ceil(r.width)+4,h:Math.ceil(r.height)+4}})()`);
        const skip = [await rectOf('askLeft'), POLISHED(card, lang) ? await rectOf('cmdReason') : null].filter(Boolean);
        const d = await pixelDiff(png, readFileSync(`${APPROVED}/${name}.png`), skip);
        m.diff = d;
        // Original draft retained verbatim. The approved new label intentionally changes pixels; record every difference.
        if (d > MAXDIFF) visualDifferences.push({name, diff:d, threshold:MAXDIFF});
      }
      if (TAG === 'after') {
        const txt = await ev(p, `document.getElementById('cmdReason').textContent`);
        const want = (lang === 'en' ? "Agent's note: " : 'Agent 说明： ') + (SHOWN(card, lang, reason) || (lang === 'en' ? 'The agent gave no reason' : 'Agent 没说明理由'));
        check(txt === want, `${name}: the reason line reads ${JSON.stringify(want)}`);
        check(m.reason && m.allow && m.reason.bottom <= m.allow.top && m.reason.top >= m.cmdBox.bottom, `${name}: reason sits between the code block and the buttons`);
        const source = await box(p, 'approvalSource572');
        check(source && source.h > 0 && source.w > 0 && source.left >= 0 && source.right <= w && m.cmdBox.bottom <= source.top && source.bottom <= m.reason.top, `${name}: honest legacy source is between command and reason without overlap`);
        check(await ev(p, `document.getElementById('approvalSource572').textContent`) === (lang === 'en' ? 'This host has not supplied a source record bound to the approval signature.' : '当前 Agent J 主机未提供已绑定签名的来源记录。'), `${name}: legacy source is not presented as verified provenance`);
        check(await ev(p, `(()=>{const c=getComputedStyle(document.getElementById('cmdBox')),r=getComputedStyle(document.getElementById('cmdReason')),s=getComputedStyle(document.getElementById('approvalSource572'));return c.marginBottom==='10px'&&r.marginTop==='0px'&&s.marginTop==='12px'&&s.marginBottom==='12px'&&s.paddingTop==='12px'&&s.paddingBottom==='12px'&&s.borderTopWidth==='1px'})()`), `${name}: component spacing keeps the original command gap and formal legacy-source styles`);
        m.legacySource = source; m.historicalAbove = {got:m.reason.top-m.cmdBox.bottom,prototype:approvedMetrics?.[name] ? approvedMetrics[name].reason.top-approvedMetrics[name].cmdBox.bottom : null,contract:'non-adjacent since formal 0286 7aa5df0687'};

        check(await ev(p, `document.getElementById('cmdReason').classList.contains('none')`) === !SHOWN(card, lang, reason), `${name}: fallback is marked (.none, lighter weight)`);
        check(await ev(p, `getComputedStyle(document.getElementById('cmdReason')).fontFamily`) !== await ev(p, `getComputedStyle(document.querySelector('#cmdBox.aj-approval-command .tool')).fontFamily`), `${name}: reason is not monospace`);
        check(await ev(p, `document.getElementById('cmd').textContent.includes('Request SHA-256')`), `${name}: SHA stays inside the code block`);
        check(await ev(p, `!document.getElementById('cmdMore').hidden && !document.getElementById('cmdCopy').hidden`), `${name}: 展开全部 + copy icon present`);
        await trustedTap(p, '#cmdCopy');
        await waitFor(p, `navigator.clipboard.readText().then(x=>x===${JSON.stringify(FIX[`${lang}-${fk}`])})`);
        check(await ev(p, `navigator.clipboard.readText()`) === FIX[`${lang}-${fk}`], `${name}: real copy action preserves the entire summary`);
        check(await ev(p, `document.getElementById('cmdDesc').hidden`), `${name}: no gist line over the code when the host sent reason`);
        if (passkey && w < 500 && card !== 'unsigned') {
          // 展开全部 → the whole summary is reachable (up to the old 30vh, scrolling inside)
          await ev(p, `document.getElementById('cmdMore').click()`);
          await sleep(200);
          const open = await box(p, 'cmd');
          check(open.h > m.pre.h, `${name}: 展开全部 opens the half-height block (${m.pre.h} → ${open.h})`);
          writeFileSync(`${OUT}/${name}-open.png`, await shoot(p, ''));
          metrics[name].preOpen = open;
        }
      }
      await p.dispose();
    }
  }
  if (TAG === 'after') for (const w of [390, 360]) for (const passkey of [false, true]) for (const short of [false, true]) {
    const p = await paired(w, 844, 'light', 'zh');
    if (passkey) await fake.send({t:'pk_state',s:'local'});
    const reason = '这是为了检查临时目录，'.repeat(short ? 30 : 100) + '只能删除临时文件，不能删除主目录。';
    const summary = 'ls /tmp\nAgent 附注：\n' + JSON.stringify({description:reason}) + '\nRequest SHA-256: test';
    await fake.ask({t:'ask',id:hex(57500 + ++n),tool:'Bash',summary,reason,ttl:600,cat:[],why:'',
      ...(passkey ? {passkey_required:true,fa:'Y3JlZC0x',fa_ch:b64(Buffer.alloc(32,7)),fa_s:'local'} : {})});
    await waitFor(p, `!document.getElementById('sheet').hidden && !document.getElementById('apprBtns').hidden`);
    await sleep(400);
    await ev(p, `{const a=document.getElementById('a2hs');if(a&&!a.hidden)document.getElementById('a2hs-ok').click()}`);
    const allow = await box(p,'apprAllow');
    metrics[`long-${w}-${passkey ? 'touchid' : 'hold'}-${short ? 'under400' : 'over400'}`] = {allow, reason:await box(p,'cmdReason'), vh:844, length:reason.length};
    check(allow && allow.top >= 0 && allow.bottom <= 844, `long ${w}/${passkey}: approval button stays on screen`);
    check(await ev(p, `document.getElementById('cmdReason').textContent`) === 'Agent 说明： 理由太长，展开看全文', 'long: no truncated qualifier');
    check(await ev(p, `!document.getElementById('cmdMore').hidden`), 'long: full summary can expand');
    await ev(p, `document.getElementById('cmdMore').click()`);
    check(await ev(p, `document.getElementById('cmd').textContent.includes('只能删除临时文件，不能删除主目录。')`), 'long: final qualifier retained in full');
    writeFileSync(`${OUT}/long-${w}-${passkey ? 'touchid' : 'hold'}-${short ? 'under400' : 'over400'}.png`, await shoot(p,''));
    await p.dispose();
  }
  if (TAG === 'after') {
    for (const kind of ['sudo', 'secret']) for (const hasReason of [false, true]) {
      if (kind === 'vshare' && hasReason) continue;
      const p = await paired(390,844,'light','zh');
      const text = '检查临时文件';
      await fake.send({t:'elev',id:hex(57600 + ++n),kind,n:hex(57600+n),epk:b64(Buffer.alloc(32,9)),ttl:120,tries:3,
        ...(kind === 'sudo' ? {cmd:'ls /tmp',why:text,effect:''} : kind === 'secret' ? {name:'EXAMPLE_API_KEY',purpose:text,dest:'local config',verify:''} : {dev:'Qx7cK2mN9pLw',name:'Tablet',cred:'cr3dABCDEFGHIJKLMNOPqrstuv',paired:1760400000,flags:['web'],v:{has:1}}),
        reason:hasReason ? text : ''});
      await waitFor(p, `!document.getElementById('elev').hidden`);
      const rows = await ev(p, `[...document.querySelectorAll('#elev-rows dd')].map(e=>e.textContent)`);
      check(rows.includes(hasReason ? text : 'Agent 没说明理由'), `${kind}: signed explanation or no-reason fallback shown`);
      writeFileSync(`${OUT}/${kind}-${hasReason ? 'reason' : 'fallback'}.png`, await shoot(p,''));
      await p.dispose();
    }
    for (const tool of ['friend_request','peer_question']) {
      const p = await paired(390,844,'light','zh');
      await fake.ask({t:'ask',id:hex(57700 + ++n),tool,summary:'peer card',reason:'',ttl:600,cat:['friend'],why:'host gate explanation',
        ...(tool === 'friend_request' ? {fr:{id:'AJ-RQCZ-QGZK-REZD-0S3C',name:'Peer',note:'Peer note',groups:[{id:'friend',name:'Friends'}]}} : {pq:{name:'Peer',text:'Question',draft:'Reply',reason:'host gate explanation'}})});
      await waitFor(p, `!document.getElementById('frAsk').hidden && !!document.querySelector('#frAsk .reason')`);
      check(await ev(p, `document.querySelector('#frAsk .reason').textContent`) === 'Agent 说明： Agent 没说明理由', `${tool}: peer/host wording is not this Agent's reason`);
      writeFileSync(`${OUT}/${tool}-fallback.png`, await shoot(p,''));
      await p.dispose();
    }
  }
  if (TAG === 'after') await currentV2Contract();
  // an older host (no `reason`): the Touch ID card renders exactly as before — no reason line, never folded
  if (TAG === 'after') {
    const p = await paired(390, 844, 'light', 'zh');
    await fake.send({ t: 'pk_state', s: 'local' });
    await fake.ask({ t: 'ask', id: hex(57299), tool: 'Bash', summary: FIX['zh-with'], ttl: 600, cat: [], why: '',
      passkey_required: true, fa: 'Y3JlZC0x', fa_ch: b64(Buffer.alloc(32, 7)), fa_s: 'local' });
    await waitFor(p, `!document.getElementById('sheet').hidden && !document.getElementById('apprBtns').hidden`);
    check(await ev(p, `document.getElementById('cmdReason').hidden && !document.getElementById('cmd').classList.contains('fold')`), 'older host: no reason line, the Touch ID text never folds');
    await p.dispose();
    if (approvedMetrics) check(Object.keys(approvedMetrics).every((k) => metrics[k]), 'every approved screen was shot again');
  }
} finally {
  await cmpPage?.dispose();
  await B.close();
  await web.stop();
  await fake.stop?.();
}
writeFileSync(`${OUT}/metrics-${TAG}.json`, JSON.stringify(metrics, null, 1));
if (TAG === 'approved' && process.env.P572_WRITE_APPROVED) writeFileSync(APPROVED_METRICS, JSON.stringify(metrics, null, 1) + '\n');
writeFileSync(`${OUT}/visual-differences.json`, JSON.stringify({baseline: APPROVED, differences:visualDifferences},null,2));
console.log(`p572 ${TAG}: ${Object.keys(metrics).length} shots, ${checks} behavior/geometry checks passed`);
if (TAG === 'after' && APPROVED) {
  console.log(`p572 original visual verdict: ${visualDifferences.length ? 'FAIL' : 'PASS'} (${visualDifferences.length} differences above unchanged ${MAXDIFF} threshold); see visual-differences.json`);
  assert.equal(visualDifferences.length, 0, 'original approved pixel threshold (label change approved separately; full differences retained)');
}
process.exit(0);
