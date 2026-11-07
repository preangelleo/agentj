import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, renameSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { encodeRGBA, compareDirectories, compareNoiseEnvelope, verifyNoiseEnvelope, verifyNoiseEnvelopeArtifacts } from './compare.mjs';
import { decodePng, crc32 } from '../../tools/png-evidence.mjs';
test('P577 zero RGBA comparator: one pixel, missing image and dimensions are failures', () => {
  const d = mkdtempSync(join(tmpdir(), 'p577-pixels-'));
  try {
    const a = join(d, 'a'),
      b = join(d, 'b'),
      o = join(d, 'out');
    mkdirSync(a);
    mkdirSync(b);
    const rgba = Buffer.from([10, 20, 30, 255, 1, 2, 3, 255]);
    const png = encodeRGBA(2, 1, rgba);
    writeFileSync(join(a, 'x.png'), png);
    writeFileSync(join(b, 'x.png'), png);
    assert.equal(compareDirectories(a, b, o).ok, true);
    const other = Buffer.from(rgba);
    other[0]++;
    writeFileSync(join(b, 'x.png'), encodeRGBA(2, 1, other));
    assert.equal(compareDirectories(a, b, o).rows[0].changedPixels, 1);
    writeFileSync(join(b, 'x.png'), encodeRGBA(1, 2, rgba));
    assert.equal(compareDirectories(a, b, o).ok, false);
    rmSync(join(b, 'x.png'));
    assert.equal(compareDirectories(a, b, o).ok, false);
    assert.equal(compareDirectories(a, b, o, {
      requireManifests: true
    }).ok, false);
  } finally {
    rmSync(d, {
      recursive: true,
      force: true
    });
  }
});
test('P577 compares pixels rather than ancillary PNG compression/container bytes', () => {
  const d = mkdtempSync(join(tmpdir(), 'p577-container-'));
  try {
    const a = join(d, 'a'),
      b = join(d, 'b'),
      o = join(d, 'out');
    mkdirSync(a);
    mkdirSync(b);
    const png = encodeRGBA(1, 1, Buffer.from([1, 2, 3, 255]));
    const type = Buffer.from('tEXt'),
      data = Buffer.from('fixture\0different container'),
      n = Buffer.alloc(4),
      crc = Buffer.alloc(4);
    n.writeUInt32BE(data.length);
    crc.writeUInt32BE(crc32(Buffer.concat([type, data])));
    const changed = Buffer.concat([png.subarray(0, png.length - 12), n, type, data, crc, png.subarray(png.length - 12)]);
    assert.notDeepEqual(png, changed);
    writeFileSync(join(a, 'x.png'), png);
    writeFileSync(join(b, 'x.png'), changed);
    assert.equal(compareDirectories(a, b, o).ok, true);
  } finally {
    rmSync(d, {
      recursive: true,
      force: true
    });
  }
});

function noiseFixture(run) {
  const root = mkdtempSync(join(tmpdir(), 'p577-noise-'));
  const dirs = ['baseline0','baseline1','baseline2','candidate'].map(name => {const dir=join(root,name);mkdirSync(dir);return dir;});
  const rgba = Buffer.alloc(20*20*4,255);
  const put = (index, pixels=[], width=20, height=20) => {
    const image=Buffer.from(rgba);
    for(const pixel of pixels) image[pixel*4]=0;
    writeFileSync(join(dirs[index],'chat-idle.png'),encodeRGBA(width,height,image));
  };
  dirs.forEach((_,index)=>put(index));
  const compare = (options={}) => compareNoiseEnvelope(dirs.slice(0,3),dirs[3],join(root,'out'),options);
  try {run({root,dirs,put,compare});} finally {rmSync(root,{recursive:true,force:true});}
}
test('#1012 three actual baseline samples allow only their decoded pixel union and bind artifacts',()=>noiseFixture(({root,put,compare})=>{
  put(1,[0]);put(2,[1]);put(3,[0,1]);
  const r=compare(),row=r.rows[0];
  assert.equal(r.verdict,'PASS');assert.equal(r.ok,true);assert.equal(r.sampleCount,3);
  assert.equal(row.envelopePixels,2);assert.equal(row.changedPixels,2);assert.equal(row.outsidePixels,0);
  assert.equal(row.envelopeFraction,0.005,'exactly0.5% is not excessive');
  assert.equal(r.samples.length,5);
  for(const sample of r.samples) {const image=sample.images['chat-idle.png'];assert.match(image.pngSha256,/^[a-f0-9]{64}$/);assert.match(image.rgbaSha256,/^[a-f0-9]{64}$/);assert.equal(image.width,20);assert.equal(image.height,20);}
  assert.deepEqual([...readFileSync(join(root,'out/envelope/chat-idle.png.pixels.bin'))].slice(0,4),[1,1,0,0]);
  assert.equal(readFileSync(join(root,'out/diff/chat-idle.png')).length>0,true);
  assert.equal(JSON.parse(readFileSync(join(root,'out/result.json'))).verdict,'PASS');
}));
test('#1012 one changed pixel outside baseline noise fails without enlarging the envelope',()=>noiseFixture(({root,put,compare})=>{
  put(1,[0]);put(2,[0]);put(3,[0,1]);
  const r=compare();assert.equal(r.verdict,'FAIL');assert.equal(r.rows[0].outsidePixels,1);assert.equal(r.rows[0].envelopePixels,1);
  assert.equal(readFileSync(join(root,'out/envelope/chat-idle.png.pixels.bin'))[1],0);
}));
test('#1012 baseline noise above0.5% is UNCERTAIN even for an identical candidate',()=>noiseFixture(({root,dirs,put,compare})=>{
  put(1,[0,1]);put(2,[2]);
  const r=compare();assert.equal(r.verdict,'UNCERTAIN');assert.equal(r.ok,false);assert.equal(r.rows[0].changedPixels,0);assert.equal(r.rows[0].envelopePixels,3);
  const pair=decodePng(readFileSync(join(root,'out/side-by-side/chat-idle.png')));assert.equal(pair.width,40);assert.equal(pair.height,20);
  assert.throws(()=>verifyNoiseEnvelope(dirs.slice(0,3),dirs[3],join(root,'out')),/not PASS: UNCERTAIN/);
  put(3,[3]);assert.equal(compare().verdict,'FAIL','outside pixel still fails for an excessive envelope');
}));
test('#1012 fewer than three samples or reusing an actual directory cannot qualify',()=>noiseFixture(({root,dirs})=>{
  for(const baselines of [dirs.slice(0,2),[dirs[0],dirs[1],dirs[1]]]) {
    const r=compareNoiseEnvelope(baselines,dirs[3],join(root,'out'));
    assert.equal(r.verdict,'FAIL');assert.ok(r.errors.length);
  }
}));
test('#1012 missing and extra scene images fail across baseline samples and candidate',()=>noiseFixture(({root,dirs,put,compare})=>{
  rmSync(join(dirs[2],'chat-idle.png'));assert.equal(compare().verdict,'FAIL');put(2);
  rmSync(join(dirs[3],'chat-idle.png'));assert.equal(compare().verdict,'FAIL');put(3);
  writeFileSync(join(dirs[3],'extra.png'),readFileSync(join(dirs[3],'chat-idle.png')));
  assert.equal(compare().verdict,'FAIL');
}));
test('#1012 baseline or candidate image dimension changes fail',()=>noiseFixture(({put,compare})=>{
  put(1,[],10,40);assert.equal(compare().verdict,'FAIL');put(1);
  put(3,[],10,40);assert.equal(compare().verdict,'FAIL');
}));
test('#1012 optional manifests reject missing bindings and repeated baseline source drift',()=>noiseFixture(({dirs,compare})=>{
  assert.equal(compare({requireManifests:true}).verdict,'FAIL');
  const manifest={environment:{fixture:'same'},fixtureBinding:{sha:'same'},browser:'fixture',captureVersion:1,fixtureStable:true,sourceStable:true,sourceCombined:'baseline',coverage:{ok:true},rows:[{name:'chat-idle',width:20,height:20,theme:'light',lang:'en',ok:true}]};
  dirs.forEach(dir=>writeFileSync(join(dir,'result.json'),JSON.stringify(manifest)));
  assert.equal(compare({requireManifests:true}).verdict,'PASS');
  writeFileSync(join(dirs[1],'result.json'),JSON.stringify({...manifest,sourceCombined:'different'}));
  assert.equal(compare({requireManifests:true}).verdict,'FAIL');
}));
test('#1012 envelope mode does not alter the default strict comparison',()=>noiseFixture(({root,dirs,put,compare})=>{
  put(1,[0]);put(3,[0]);assert.equal(compare().verdict,'PASS');
  const strict=compareDirectories(dirs[0],dirs[3],join(root,'strict'));
  assert.equal(strict.ok,false);assert.equal(strict.rows[0].changedPixels,1);assert.equal(strict.schemaVersion,1);
}));

test('#1012 independent verification rejects edited result, envelope, diff and actual source pixels',()=>noiseFixture(({root,dirs,put,compare})=>{
  put(1,[0]);put(3,[0]);
  const verify=()=>verifyNoiseEnvelope(dirs.slice(0,3),dirs[3],join(root,'out'));
  assert.equal(compare().verdict,'PASS');assert.equal(verify().ok,true);
  const resultPath=join(root,'out/result.json'),r=JSON.parse(readFileSync(resultPath));r.rows[0].outsidePixels=1;
  writeFileSync(resultPath,JSON.stringify(r));assert.throws(verify,/recomputation/);compare();
  for(const file of ['envelope/chat-idle.png','envelope/chat-idle.png.pixels.bin','diff/chat-idle.png']) {
    const path=join(root,'out',file),bytes=readFileSync(path);bytes[0]^=1;writeFileSync(path,bytes);
    assert.throws(verify,/artifact differs/);compare();
  }
  put(3,[0,1]);assert.throws(verify,/recomputation/);
}));
test('#1012 explicit reference stays separate from baseline-only envelope and requires authoritative verifier option',()=>noiseFixture(({root,dirs,put})=>{
  put(1,[0]);put(2,[0]);put(3,[4]);
  const reference=join(root,'reference');mkdirSync(reference);
  writeFileSync(join(reference,'chat-idle.png'),readFileSync(join(dirs[3],'chat-idle.png')));
  const options={referenceDirectory:reference};
  const r=compareNoiseEnvelope(dirs.slice(0,3),dirs[3],join(root,'out'),options);
  assert.equal(r.verdict,'PASS');assert.equal(r.rows[0].envelopePixels,1);assert.equal(r.rows[0].changedPixels,0);
  assert.equal(readFileSync(join(root,'out/envelope/chat-idle.png.pixels.bin'))[4],0,'reference and candidate never enlarge baseline noise');
  assert.throws(()=>verifyNoiseEnvelope(dirs.slice(0,3),dirs[3],join(root,'out')),/recomputation/);
  assert.equal(verifyNoiseEnvelope(dirs.slice(0,3),dirs[3],join(root,'out'),options).ok,true);
}));

test('#1012 relocated complete evidence independently verifies without remote absolute paths',()=>noiseFixture(({root,dirs,put,compare})=>{
  put(1,[0]);put(3,[0]);
  const result=compare();assert.equal(result.verdict,'PASS');
  assert.equal(JSON.stringify(result).includes(root),false,'portable receipt has no absolute sample or reference paths');
  const moved=root+'-relocated';renameSync(root,moved);
  try {
    const relocated=dirs.map(dir=>join(moved,dir.slice(root.length+1)));
    assert.equal(verifyNoiseEnvelope(relocated.slice(0,3),relocated[3],join(moved,'out')).verdict,'PASS');
  } finally {renameSync(moved,root);}
}));

test('#1025 artifact verification preserves actual FAIL while default verification still rejects it',()=>noiseFixture(({root,dirs,put,compare})=>{
 put(3,[4]);assert.equal(compare().verdict,'FAIL');
 const actual=verifyNoiseEnvelopeArtifacts(dirs.slice(0,3),dirs[3],join(root,'out'));
 assert.equal(actual.verdict,'FAIL');assert.equal(actual.ok,false);assert.equal(actual.rows[0].outsidePixels,1);
 assert.throws(()=>verifyNoiseEnvelope(dirs.slice(0,3),dirs[3],join(root,'out')),/not PASS: FAIL/);
}));
