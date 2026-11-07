import test from 'node:test';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
import {readFile, mkdtemp, mkdir, rm} from 'node:fs/promises';
import {resolve} from 'node:path';

// Existing isolated browser dependency only. No install, network or shared CDP.
test('B10 real DOM: 1000 chunks, boundaries, identity, selection, finish and inert input', async () => {
  const {chromium} = process.env.P193_PLAYWRIGHT ? await import(process.env.P193_PLAYWRIGHT) : createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
  await mkdir(resolve('reports/qa/p193-stream'),{recursive:true});
  const profile = await mkdtemp(resolve('reports/qa/p193-stream/browser-'));
  let browser;
  try {
    browser = await chromium.launchPersistentContext(profile, {headless:true, executablePath:process.env.CHROMIUM || '/usr/bin/chromium', args:['--no-sandbox','--disable-background-networking']});
    const page = await browser.newPage();
    let external = 0;
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin === 'http://offline.invalid' && ['/md.js','/icons.js'].includes(url.pathname)) {
        return route.fulfill({contentType:'text/javascript', body:await readFile(resolve('web/public/js' + url.pathname),'utf8')});
      }
      if (url.href === 'http://offline.invalid/') return route.fulfill({contentType:'text/html',body:'<!doctype html><body></body>'});
      external++; await route.abort();
    });
    await page.goto('http://offline.invalid/');
    const result = await page.evaluate(async () => {
      const {RelayMD: MD} = await import('/md.js');
      const check = (ok, label) => {if (!ok) throw Error(label);};
      const box = document.createElement('div'); document.body.append(box);
      const stream = MD.stream(box, document);
      let src = '# Stable\n\nfirst\n\n';
      stream.update(src, 'epoch1');
      const heading = box.firstChild;
      const range = document.createRange(); range.selectNodeContents(heading);
      getSelection().removeAllRanges(); getSelection().addRange(range);
      for (let i=0;i<1000;i++) {
        src += `paragraph ${i} **bold** and [safe](https://example.invalid/)\n\n`;
        stream.update(src,'epoch1');
        check(box.firstChild === heading, 'stable heading identity');
      }
      check(getSelection().toString() === 'Stable', 'selected prefix retained');
      const stableNode = box.firstChild;
      stream.update(src, 'epoch1', true);
      check(box.firstChild === stableNode, 'finish preserves stable prefix');
      stream.update(src, 'epoch1', true);
      const expected = document.createElement('div'); expected.append(MD.toDOM(MD.parse(src),document));
      check(box.innerHTML === expected.innerHTML, '1000 finish DOM equivalence');
      check(stream.metrics.parsedUnits < stream.metrics.fullUnits / 20, 'parse work reduced');
      let cases = 0;
      const samples = [
        'a\n\n- one\n\n  continued\n- two\n\nend',
        '> quoted\n> **bold**\nlazy\n\nend',
        '```js\n<script>window.pwn=1</script>\n\n```\n\nend',
        '$$\nx\n# heading\n$$\n\nend',
        'a\n\n```mermaid\ngraph TD; A-->B\n```\n\n$x$ [bad](javascript:alert(1))',
        'a\n\n|a|b|\n|-|-|\n|x|y|\n\nend',
        '<img src="https://leak.invalid/SECRET_PLACEHOLDER" onerror="window.pwn=1">\n\n`SECRET_PLACEHOLDER` [x](javascript:alert(1))',
        'a\r\n\r\n\t- indent\n\n**unfinished** and ``ticks`x``',
        'short', '', '````\nx\n```\n\ntext\n````'
      ];
      for (const sample of samples) {
        const target = document.createElement('div'); document.body.append(target);
        const renderer = MD.stream(target,document);
        for(let i=1;i<=sample.length;i++) {
          renderer.update(sample.slice(0,i),'case');
          const oracle = document.createElement('div'); oracle.append(MD.toDOM(MD.parse(sample.slice(0,i)),document));
          check(target.innerHTML===oracle.innerHTML, `chunk semantics case ${cases} offset ${i}`);
        }
        renderer.update(sample,'case',true);
        const oracle = document.createElement('div'); oracle.append(MD.toDOM(MD.parse(sample),document));
        check(target.innerHTML===oracle.innerHTML,`finish case ${cases}`);
        renderer.update('same size edit','case'); renderer.update('DIFF size edit','case',true);
        const edit = document.createElement('div'); edit.append(MD.toDOM(MD.parse('DIFF size edit'),document));
        check(target.innerHTML===edit.innerHTML,'same-length edit resets');
        cases++;
      }
      // Stateful code/details controls in a settled prefix survive later chunks.
      const controls = document.createElement('div');
      const controlStream = MD.stream(controls,document);
      const start = '```details\ncopy me\n```\n\nend';
      controlStream.update(start,'controls');
      const details = controls.querySelector('details'), button = controls.querySelector('button');
      details.open = true; button.dataset.copied = 'yes';
      controlStream.update(start+' more\n\nnext','controls');
      check(controls.querySelector('details')===details && details.open, 'source details stays open');
      check(controls.querySelector('button')===button && button.dataset.copied==='yes','copy state retained');
      controlStream.update(start+' more\n\nnext','new epoch');
      check(controls.querySelector('details')!==details,'identity resets even equal content');
      const decorated = controls.querySelector('details');
      const replacement = document.createElement('section'); replacement.textContent = 'trusted upgrade';
      decorated.replaceWith(replacement); controlStream.adopt();
      controlStream.update('changed source','new epoch');
      check(!controls.contains(replacement),'replaced tail decorator removed');
      let seed = 193;
      const pieces = ['a\n', '\n', '- x\n', '  child\n', '> q\n', '```\n', '$$\n', '# h\n', '|a|b|\n', '|-|-|\n', '**x', '`', '[x](https://example.invalid)'];
      for(let trial=0; trial<40; trial++) {
        const target = document.createElement('div'), renderer = MD.stream(target,document);
        let text = '';
        for(let chunk=0; chunk<35; chunk++) {
          seed = (Math.imul(seed,1664525)+1013904223)>>>0;
          text += pieces[seed%pieces.length]; renderer.update(text,'fuzz');
          const oracle = document.createElement('div'); oracle.append(MD.toDOM(MD.parse(text),document));
          check(target.innerHTML===oracle.innerHTML,`mixed boundaries ${trial}/${chunk}`);
        }
      }
      // Global budgets, including table cells and DOM fallback, use full-parse semantics.
      for (const sample of [
        ('|a|b|\n|-|-|\n|x|y|\n\n').repeat(1500),
        ('# h\n\n').repeat(21000),
        'x'.repeat(200010)
      ]) {
        const target = document.createElement('div'), renderer = MD.stream(target,document);
        for (let i=1;i<=4;i++) renderer.update(sample.slice(0,Math.ceil(sample.length*i/4)),'budget');
        renderer.update(sample,'budget',true);
        const oracle = document.createElement('div'); oracle.append(MD.toDOM(MD.parse(sample),document));
        check(target.innerHTML===oracle.innerHTML,'global budget equivalence');
      }
      check(!window.pwn && !document.querySelector('script,img,iframe'), 'untrusted input stays inert');
      return {chunks:1000,cases,mixedChunks:1400,metrics:stream.metrics};
    });
    assert.equal(external,0,'no new outbound request, including placeholder content');
    console.log(JSON.stringify({...result,external}));
  } finally {if(browser) await browser.close(); await rm(profile,{recursive:true,force:true});}
});
