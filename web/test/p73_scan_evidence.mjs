// F29 / ADR-A177 evidence (not a gate; p73_scan.test.mjs asserts the key cells): the old (0.15.8) vs new (scan.js) pipeline
// over simulated screen photos — both link formats × 640×480 / 1920×1080 × camera pixels per module, 8 frames each — plus
// one PNG per stream size and format of the report's scene. Writes reports/qa/p73/f29/{grid.json,grid.md,*.png} (the committed
// 1080p frames were converted to JPEG by hand: 1.6 MB PNGs each).
//   node web/test/p73_scan_evidence.mjs [outdir]
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import * as L from './p73_scan_lib.mjs';

const out = new URL(process.argv[2] ? `file://${process.argv[2].replace(/\/?$/, '/')}` : '../../../reports/qa/p73/f29/', import.meta.url);
mkdirSync(out, { recursive: true });
const FX = JSON.parse(readFileSync(new URL('./fixtures/p73/pair_qr.json', import.meta.url), 'utf8'));
const rows = [];
for (const [w, h, blur, ppms] of [[640, 480, 1, [2.2, 2.6, 3, 3.5, 4, 4.5, 6]], [1920, 1080, 2, [3, 4.5, 6, 6.6, 7.8, 9]]]) {
  for (const k of ['v1', 'v2']) for (const ppm of ppms) {
    if ((FX[k].modules + 8) * ppm > h * 0.98) continue;
    const sc = L.screenScene(FX[k].rows, { w, h, ppm, blur });
    const o = L.scanFrames(sc, 'old'), n = L.scanFrames(sc, 'new');
    if (n && n.data !== FX[k].link) throw new Error('decoded something else');
    rows.push({ stream: `${w}x${h}`, code: `${k} (version ${FX[k].version}, ${FX[k].modules} modules)`, px_per_module: ppm,
      old: o ? `frame ${o.frame}` : 'no', new: n ? `frame ${n.frame}` : 'no' });
    if ((w === 640 && ppm === 2.6) || (w === 1920 && ppm === 7.8)) writeFileSync(new URL(`sim-${w}x${h}-${k}-ppm${ppm}.png`, out), L.writePngGray(L.shoot(sc, 1).gray, w, h));
  }
}
const photo = L.readPngGray(readFileSync(new URL('./fixtures/p73/iphone-scan-terminal.crop50.png', import.meta.url)));
const report = { links: { v1: { chars: FX.v1.chars, version: FX.v1.version, modules: FX.v1.modules }, v2: { chars: FX.v2.chars, version: FX.v2.version, modules: FX.v2.modules } },
  photo: { old: L.oldPipeline(photo), new: [0, 1, 2, 3].map((k) => L.newPipeline(photo, k)) }, grid: rows };
writeFileSync(new URL('grid.json', out), JSON.stringify(report, null, 1) + '\n');
const md = ['| stream | code | px/module | old pipeline (8 frames) | new pipeline (8 frames) |', '|---|---|---|---|---|',
  ...rows.map((r) => `| ${r.stream} | ${r.code} | ${r.px_per_module} | ${r.old} | ${r.new} |`)].join('\n');
writeFileSync(new URL('grid.md', out), md + '\n');
console.log(md);
console.log('photo:', JSON.stringify(report.photo), '→', out.pathname);
