// P302 追加 20: the App's new logo is the one source — no image under agentjarvis/ may still be a retired logo (by SHA-256),
// and the shipped copies of each icon (brand/, web, site, account page, admin, Android) are byte-identical to brand/img.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const AJ = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const HAVE_BRAND = existsSync(join(AJ, 'brand/retired-logo-sha256.json'));   // brand/ is private (not in the public export)
const RETIRED = HAVE_BRAND ? JSON.parse(readFileSync(join(AJ, 'brand/retired-logo-sha256.json'), 'utf8')).retired : [];
const sha = (f) => createHash('sha256').update(readFileSync(f)).digest('hex');
function* images(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (['node_modules', '.git', 'dist', '.wrangler', 'build'].includes(e.name)) continue;
    const p = join(dir, e.name);
    if (e.isDirectory()) yield* images(p);
    else if (/\.(png|webp|ico|jpe?g|icns)$/i.test(e.name)) yield p;
  }
}

test('the retired list is not empty', { skip: !HAVE_BRAND && 'no agentjarvis/brand (public export)' }, () => assert.ok(Object.keys(RETIRED).length >= 15));

test('no image under agentjarvis/ is a retired logo', { skip: !HAVE_BRAND && 'no agentjarvis/brand (public export)' }, () => {
  const hits = [];
  for (const f of images(AJ)) if (RETIRED[sha(f)]) hits.push(relative(AJ, f) + ' = old ' + RETIRED[sha(f)]);
  assert.deepEqual(hits, []);
});

test('every shipped copy of an icon is the brand/img one', { skip: !HAVE_BRAND && 'no agentjarvis/brand (public export)' }, () => {
  const COPIES = { 'brand/img/favicon-32.png': ['web/public/brand/img/favicon-32.png', 'site/public/brand/img/favicon-32.png', 'dashboard/public/brand/img/favicon-32.png', 'host/agentj/admin/brand/img/favicon-32.png'],
    'brand/img/icon-192.png': ['web/public/brand/img/icon-192.png', 'site/public/brand/img/icon-192.png', 'dashboard/public/brand/img/icon-192.png'],
    'brand/img/icon-512.png': ['web/public/brand/img/icon-512.png', 'site/public/brand/img/icon-512.png', 'dashboard/public/brand/img/icon-512.png'],
    'brand/img/icon-512-maskable.png': ['web/public/brand/img/icon-512-maskable.png'],
    'brand/img/apple-touch-icon.png': ['web/public/apple-touch-icon.png', 'site/public/apple-touch-icon.png', 'dashboard/public/apple-touch-icon.png', 'host/agentj/admin/apple-touch-icon.png'],
    'brand/img/favicon.ico': ['web/public/favicon.ico', 'site/public/favicon.ico', 'dashboard/public/favicon.ico', 'host/agentj/admin/favicon.ico'],
    'brand/img/shield-128.png': ['web/public/brand/img/shield-128.png', 'site/public/brand/img/shield-128.png', 'dashboard/public/brand/img/shield-128.png', 'host/agentj/admin/brand/img/shield-128.png'],
    'brand/img/og-1200x630.png': ['site/public/brand/img/og-1200x630.png', 'dashboard/public/brand/img/og-1200x630.png'] };
  for (const [src, copies] of Object.entries(COPIES)) for (const c of copies) {
    if (!existsSync(join(AJ, c))) continue;
    assert.equal(sha(join(AJ, c)), sha(join(AJ, src)), `${c} = ${src}`);
  }
});
