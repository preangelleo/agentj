// P396 L1 only: real phone signing + slow/refusing host, local attention vs acknowledgement. Never production/CDP :9222.
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep } from './browser.mjs';
const out = fileURLToPath(new URL('../../../reports/qa/p396/phone/', import.meta.url));
mkdirSync(out, {recursive:true});
const web = await startWebServer({port:0, relayCsp:'ws://127.0.0.1:*'});
const fake = await startFakeHost();
const br = await launch();
const failures = [];
const check = (ok, msg) => {if (!ok) failures.push(msg);};
try {
  for (const lang of ['zh','en']) {
    fake.reset();
    const p = await newPage(br,390,844,'light',{allow:[web.url,fake.relay+'/']});
    const base = web.url + (lang === 'en' ? '?lang=en' : '');
    await navigate(p,base); await waitState(p,'idle');
    await navigate(p,'about:blank'); await navigate(p,fake.newPairing(base));
    await waitState(p,'awaiting-approval'); await fake.approve(); await waitState(p,'ready');
    await evaluate(p,`document.getElementById('a2hs-ok')?.click(); document.getElementById('confirm-no')?.click();`);
    fake.st.holdAnswers = true;
    await fake.send({t:'status',s:'waiting',agent:'claude'});
    for (const [n,button,ok,group] of [[1,'frQAuto',true,'colleague'],[2,'frQSkip',false,null],[3,'frQSend',true,null]]) {
      const id = String(n).repeat(32);
      const card = {t:'ask',id,tool:'peer_question',summary:'朋友说：您好😀\n草稿：好的',ttl:120,cat:['send'],why:'需确认',pq:{friend:'AJ-PH8E-AJT4-26GJ-ACQ9',name:'Kai',text:'您好😀',draft:'好的'}};
      await fake.ask(card);
      await waitFor(p,`document.body.dataset.status==='waiting' && !!document.getElementById('${button}')`);
      const before = fake.st.answers.length;
      await evaluate(p,`document.getElementById('${button}').click()`);
      await waitFor(p,`document.body.dataset.status!=='waiting'`);
      for (let i=0; i<60 && fake.st.answers.length===before; i++) await sleep(50);
      const answer = fake.st.answers.at(-1);
      check(answer?.id===id && answer.ok===ok && (answer.group||null)===group,`${lang} ${button}: signed decision`);
      check(fake.st.sigOk.at(-1)===true,`${lang} ${button}: real device signature verifies`);
      // Refusal carries the current signed text, opens the same card for retry while staying out of waiting colour.
      const current = {...card,summary:card.summary+'\n主机最新原文'};
      fake.st.asksOpen.set(id,current);
      await fake.send({t:'answer_refused',id,why:'bad_signature',ask:current});
      await waitFor(p,`!!document.getElementById('${button}') && document.body.dataset.status!=='waiting'`);
      const text = await evaluate(p,`document.body.textContent`);
      check(text.includes(lang==='zh'?'确认没生效':'did not take effect'),`${lang}: explicit failure`);
      if (n===1) {
        const {data} = await p.send('Page.captureScreenshot',{format:'png'});
        writeFileSync(out+`retry-${lang}.png`,Buffer.from(data,'base64'));
      }
      const count = fake.st.answers.length;
      await evaluate(p,`document.getElementById('${button}').click()`);
      for (let i=0;i<60 && fake.st.answers.length===count;i++) await sleep(50);
      check(fake.st.sigOk.at(-1)===true,`${lang}: retry signs refreshed summary`);
      await fake.send({t:'ask_done',id,result:ok?'allow':'deny'});
      await waitFor(p,`document.body.dataset.status!=='waiting'`);
    }
    await p.dispose();
  }
} finally { await br.close(); await fake.stop(); await web.stop(); }
writeFileSync(out+'result.json',JSON.stringify({failures},null,2)+'\n');
if (failures.length) { console.error(failures.join('\n')); process.exitCode=1; }
else console.log('P396 phone: allow/deny/group, immediate nonwaiting, refusal, fresh-card retry PASS');
