import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {launchBrowser} from '../../tests/lib/browser.mjs';

const root = new URL('../public/', import.meta.url);
const html = readFileSync(new URL('index.html', root), 'utf8');
const body = /<body[^>]*>([\s\S]*)<\/body>/.exec(html)[1];
const css = [...html.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)]
  .map(match => readFileSync(new URL(match[1], root), 'utf8')).join('\n');

test('settings subpage keeps the remote-count badge anchored to its actual settings button at phone, tablet and desktop sizes', async () => {
  const browser = await launchBrowser();
  try {
    for (const width of [390, 834, 1440]) {
      const page = await browser.newPage(width, 900);
      try {
        await page.eval(`document.head.innerHTML='<meta name="viewport" content="width=device-width,initial-scale=1">';
          document.head.append(Object.assign(document.createElement('style'),{textContent:${JSON.stringify(css)}}));
          document.body.innerHTML=${JSON.stringify(body)};
          document.documentElement.dataset.theme='dark';document.body.dataset.view='settings';
          document.getElementById('settings').hidden=false;
          for(const id of ['scanBtn','srchBtn','setBtn'])document.getElementById('set-actions').append(document.getElementById(id));
          const badge=document.querySelector('#setBtn>[data-remote-count]');badge.hidden=false;badge.textContent='1';`);
        const measured = await page.eval(`(()=>{const button=document.getElementById('setBtn'),badge=button.querySelector('[data-remote-count]');
          const b=button.getBoundingClientRect(),n=badge.getBoundingClientRect();return{position:getComputedStyle(button).position,button:{top:b.top,right:b.right,width:b.width,height:b.height},badge:{top:n.top,right:n.right,width:n.width,height:n.height}}})()`);
        assert.equal(measured.position, 'relative', `actual compiled styles at ${width}: ${JSON.stringify(measured)}`);
        assert.ok(measured.button.width > 0 && measured.badge.width > 0, 'actual visible controls, not hidden zero-size boxes');
        assert.equal(measured.badge.right, measured.button.right, `badge right follows its button at ${width}`);
        assert.equal(measured.badge.top, measured.button.top, `badge top follows its button at ${width}`);
      } finally { await page.close(); }
    }
  } finally { await browser.close(); }
});
