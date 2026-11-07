#!/usr/bin/env node
import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync, realpathSync, mkdtempSync, rmSync } from 'node:fs';
import { resolve, join, basename } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { deflateSync } from 'node:zlib';
import { parseArgs } from 'node:util';
import { decodePng, crc32 } from '../../tools/png-evidence.mjs';
export function encodeRGBA(width, height, rgba) {
  const chunk = (type, data) => {
    const t = Buffer.from(type),
      n = Buffer.alloc(4),
      c = Buffer.alloc(4);
    n.writeUInt32BE(data.length);
    c.writeUInt32BE(crc32(Buffer.concat([t, data])));
    return Buffer.concat([n, t, data, c]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 6;
  const raw = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y++) rgba.copy(raw, y * (width * 4 + 1) + 1, y * width * 4, (y + 1) * width * 4);
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
export function compareDirectories(before, after, out, {
  requireManifests = false
} = {}) {
  mkdirSync(out, {
    recursive: true
  });
  const errors = [];
  if (requireManifests) {
    try {
      const a = JSON.parse(readFileSync(join(before, 'result.json'))),
        b = JSON.parse(readFileSync(join(after, 'result.json')));
      for (const [side, r] of [['before', a], ['after', b]]) {
        if (!r.environment || !r.fixtureBinding || !r.browser || !r.captureVersion) errors.push(`${side}: incomplete environment/fixture manifest`);
        if(r.environment?.versionInput?.mode==='same-version-combined-input'&&(!r.versionInputStable||!r.versionInput?.interceptions))errors.push(`${side}: VERSION input drift or missing binding`);
        if (!r.fixtureStable) errors.push(`${side}: fixture drift or missing fixture qualification`);
        if (!r.coverage?.ok) errors.push(`${side}: incomplete scene manifest coverage`);
        if (!r.sourceStable) errors.push(`${side}: source drift or missing source qualification`);
        if (!r.rows?.length || r.rows.some(x => !x.ok)) errors.push(`${side}: failed or absent scenes`);
      }
      for (const k of ['browser', 'captureVersion', 'configs', 'environment', 'fixtureBinding']) if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) errors.push(`Capture mismatch: ${k}`);
      const keys = r => r.rows.map(x => [x.name, x.width, x.height, x.theme, x.lang].join('|')).sort();
      if (JSON.stringify(keys(a)) !== JSON.stringify(keys(b))) errors.push('Scene coverage differs');
    } catch (e) {
      errors.push('Missing/invalid capture manifest: ' + e.message);
    }
  }
  const names = [...new Set([...readdirSync(before), ...readdirSync(after)].filter(x => x.endsWith('.png')))].sort(),
    rows = [];
  for (const name of names) {
    try {
      const a = decodePng(readFileSync(join(before, name))),
        b = decodePng(readFileSync(join(after, name)));
      if (a.width !== b.width || a.height !== b.height) throw Error('Image dimensions differ');
      const diff = Buffer.alloc(a.rgba.length);
      let changedPixels = 0,
        minX = a.width,
        minY = a.height,
        maxX = -1,
        maxY = -1;
      for (let i = 0; i < a.rgba.length; i += 4) {
        const changed = !a.rgba.subarray(i, i + 4).equals(b.rgba.subarray(i, i + 4));
        if (changed) {
          changedPixels++;
          const p = i / 4,
            x = p % a.width,
            y = Math.floor(p / a.width);
          minX = Math.min(minX, x);
          maxX = Math.max(maxX, x);
          minY = Math.min(minY, y);
          maxY = Math.max(maxY, y);
          diff.set([255, 0, 64, 255], i);
        } else diff.set([a.rgba[i], a.rgba[i + 1], a.rgba[i + 2], 60], i);
      }
      if (changedPixels) writeFileSync(join(out, name), encodeRGBA(a.width, a.height, diff));
      rows.push({
        name,
        ok: changedPixels === 0,
        changedPixels,
        bbox: changedPixels ? [minX, minY, maxX, maxY] : null
      });
    } catch (e) {
      rows.push({
        name,
        ok: false,
        error: e.message
      });
    }
  }
  const result = {
    schemaVersion: 1,
    ok: names.length > 0 && errors.length === 0 && rows.every(x => x.ok),
    images: names.length,
    errors,
    rows
  };
  writeFileSync(join(out, 'result.json'), JSON.stringify(result, null, 2));
  return result;
}
// #1012: derive noise only from repeated baseline captures, before inspecting candidate pixels.
// No candidate-derived enlargement or authored masks are accepted by this interface.
export function compareNoiseEnvelope(baselineDirectories, candidateDirectory, out, {requireManifests = false, referenceDirectory} = {}) {
  mkdirSync(join(out, 'envelope'), {recursive: true});
  mkdirSync(join(out, 'diff'), {recursive: true});
  const sha = bytes => createHash('sha256').update(bytes).digest('hex');
  const errors = [], rows = [], samples = [];
  const dirs = Array.isArray(baselineDirectories) ? baselineDirectories : [];
  const reference = referenceDirectory || dirs[0] || candidateDirectory;
  if (dirs.length < 3) errors.push('At least three actual baseline sample directories are required');
  const paths = [];
  for (const directory of [...dirs, candidateDirectory]) {
    try { paths.push(realpathSync(directory)); } catch (e) { errors.push('Missing capture directory: ' + e.message); }
  }
  if (new Set(paths).size !== paths.length) errors.push('Baseline samples and candidate must have distinct actual directories');
  const list = directory => readdirSync(directory).filter(name => name.endsWith('.png')).sort();
  let names = [];
  try {
    names = list(dirs[0]);
    for (const directory of [...dirs, candidateDirectory, reference]) {
      if (JSON.stringify(list(directory)) !== JSON.stringify(names)) errors.push('Missing or extra scene PNG: ' + directory);
    }
  } catch (e) { errors.push('Cannot enumerate sample images: ' + e.message); }
  if (!names.length) errors.push('No baseline scene PNGs');
  let referenceManifest;
  for (const [index, directory] of [...dirs, candidateDirectory, reference].entries()) {
    const sample = {index, role: index < dirs.length ? 'baseline' : index === dirs.length ? 'candidate' : 'reference', directory: basename(resolve(directory)), images: {}};
    samples.push(sample);
    if (requireManifests) {
      try {
        const bytes = readFileSync(join(directory, 'result.json')), r = JSON.parse(bytes);
        sample.manifestSha256 = sha(bytes);
        if (!r.environment || !r.fixtureBinding || !r.browser || !r.captureVersion || !r.fixtureStable || !r.sourceStable || !r.coverage?.ok || !r.rows?.length || r.rows.some(x => !x.ok)) throw Error('Incomplete capture manifest');
        if (r.environment?.versionInput?.mode === 'same-version-combined-input' && (!r.versionInputStable || !r.versionInput?.interceptions)) throw Error('VERSION input drift or missing binding');
        if (!referenceManifest) referenceManifest = r;
        for (const key of ['browser', 'captureVersion', 'configs', 'environment', 'fixtureBinding']) if (JSON.stringify(referenceManifest[key]) !== JSON.stringify(r[key])) throw Error('Capture mismatch: ' + key);
        const keys = manifest => manifest.rows.map(x => [x.name,x.width,x.height,x.theme,x.lang].join('|')).sort();
        if (JSON.stringify(keys(referenceManifest)) !== JSON.stringify(keys(r))) throw Error('Scene coverage differs');
        if (index < dirs.length && r.sourceCombined !== referenceManifest.sourceCombined) throw Error('Repeated baseline source identity differs');
      } catch (e) { errors.push('Invalid sample manifest ' + index + ': ' + e.message); }
    }
  }
  for (const name of names) {
    try {
      const load = (directory, index) => {
        const bytes = readFileSync(join(directory, name)), image = decodePng(bytes);
        samples[index].images[name] = {pngSha256: sha(bytes), rgbaSha256: sha(image.rgba), width: image.width, height: image.height};
        return image;
      };
      const baselines = dirs.map(load), a = baselines[0];
      if (!a) throw Error('Missing reference baseline');
      for (const image of baselines) if (image.width !== a.width || image.height !== a.height) throw Error('Baseline image dimensions differ');
      const area = a.width * a.height, mask = Buffer.alloc(area), envelope = Buffer.alloc(a.rgba.length);
      let envelopePixels = 0;
      for (let pixel = 0; pixel < area; pixel++) {
        const i = pixel * 4;
        if (baselines.slice(1).some(image => !a.rgba.subarray(i,i+4).equals(image.rgba.subarray(i,i+4)))) {
          mask[pixel] = 1; envelopePixels++; envelope.set([255,255,255,255],i);
        }
      }
      const envelopeBytes = encodeRGBA(a.width,a.height,envelope);
      writeFileSync(join(out,'envelope',name),envelopeBytes);
      writeFileSync(join(out,'envelope',name+'.pixels.bin'),mask);
      const comparisonReference = load(reference,dirs.length+1);
      if (comparisonReference.width !== a.width || comparisonReference.height !== a.height) throw Error('Reference image dimensions differ');
      const b = load(candidateDirectory, dirs.length);
      if (b.width !== a.width || b.height !== a.height) throw Error('Candidate image dimensions differ');
      const diff = Buffer.alloc(a.rgba.length);
      let changedPixels = 0, outsidePixels = 0;
      for (let pixel = 0; pixel < area; pixel++) {
        const i = pixel * 4;
        if (!comparisonReference.rgba.subarray(i,i+4).equals(b.rgba.subarray(i,i+4))) {
          changedPixels++;
          if (!mask[pixel]) outsidePixels++;
          diff.set(mask[pixel] ? [255,192,0,255] : [255,0,64,255],i);
        } else diff.set([comparisonReference.rgba[i],comparisonReference.rgba[i+1],comparisonReference.rgba[i+2],60],i);
      }
      const diffBytes = encodeRGBA(a.width,a.height,diff);
      writeFileSync(join(out,'diff',name),diffBytes);
      let sideBySideSha256 = null;
      if (envelopePixels / area > 0.005) {
        const pair = Buffer.alloc(a.rgba.length * 2);
        for (let y = 0; y < a.height; y++) {
          a.rgba.copy(pair,y*a.width*8,y*a.width*4,(y+1)*a.width*4);
          b.rgba.copy(pair,y*a.width*8+a.width*4,y*a.width*4,(y+1)*a.width*4);
        }
        const pairBytes = encodeRGBA(a.width*2,a.height,pair);
        mkdirSync(join(out,'side-by-side'),{recursive:true});
        writeFileSync(join(out,'side-by-side',name),pairBytes);
        sideBySideSha256 = sha(pairBytes);
      }
      const verdict = outsidePixels ? 'FAIL' : envelopePixels / area > 0.005 ? 'UNCERTAIN' : 'PASS';
      rows.push({name,width:a.width,height:a.height,sampleCount:dirs.length,envelopePixels,envelopeFraction:envelopePixels/area,changedPixels,outsidePixels,verdict,ok:verdict==='PASS',envelopeSha256:sha(envelopeBytes),envelopePixelsSha256:sha(mask),diffSha256:sha(diffBytes),sideBySideSha256});
    } catch (e) { rows.push({name,ok:false,verdict:'FAIL',error:e.message}); }
  }
  const verdict = errors.length || !rows.length || rows.some(r => r.verdict === 'FAIL') ? 'FAIL' : rows.some(r => r.verdict === 'UNCERTAIN') ? 'UNCERTAIN' : 'PASS';
  const result = {schemaVersion:2,mode:'baseline-noise-envelope',verdict,ok:verdict==='PASS',images:names.length,sampleCount:dirs.length,samples,errors,rows,requireManifests,referenceDirectory:basename(resolve(reference))};
  writeFileSync(join(out,'result.json'),JSON.stringify(result,null,2));
  return result;
}
// Recompute actual pixels independently; no receipt verdict/count/hash is accepted on trust.
export function verifyNoiseEnvelopeArtifacts(baselineDirectories, candidateDirectory, resultDirectory, options) {
  const temp = mkdtempSync(join(tmpdir(),'p577-noise-verify-'));
  try {
    const recorded = JSON.parse(readFileSync(join(resultDirectory,'result.json'),'utf8'));
    const effective = options || {requireManifests:recorded.requireManifests,referenceDirectory:baselineDirectories[0]};
    const actual = compareNoiseEnvelope(baselineDirectories,candidateDirectory,temp,effective);
    if (JSON.stringify(recorded) !== JSON.stringify(actual)) throw Error('Noise receipt differs from independent pixel recomputation');
    const files = directory => readdirSync(directory,{withFileTypes:true}).flatMap(entry => entry.isDirectory() ? files(join(directory,entry.name)).map(name=>entry.name+'/'+name) : [entry.name]).sort();
    const expected = files(temp), present = files(resultDirectory);
    if (JSON.stringify(expected) !== JSON.stringify(present)) throw Error('Noise artifact inventory differs');
    for (const name of expected) if (!readFileSync(join(temp,name)).equals(readFileSync(join(resultDirectory,name)))) throw Error('Noise artifact differs from recomputation: '+name);
    return actual;
  } finally {rmSync(temp,{recursive:true,force:true});}
}
export function verifyNoiseEnvelope(baselineDirectories, candidateDirectory, resultDirectory, options) {
  const actual = verifyNoiseEnvelopeArtifacts(baselineDirectories,candidateDirectory,resultDirectory,options);
  if (!actual.ok) throw Error('Noise comparison is not PASS: '+actual.verdict);
  return actual;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const {
    values
  } = parseArgs({
    options: {
      before: {
        type: 'string'
      },
      after: {
        type: 'string'
      },
      out: {
        type: 'string'
      }
    }
  });
  if (!values.before || !values.after || !values.out) throw Error('Usage: compare.mjs --before <shots> --after <shots> --out <diff>');
  const r = compareDirectories(resolve(values.before), resolve(values.after), resolve(values.out), {
    requireManifests: true
  });
  console.log(JSON.stringify({
    ok: r.ok,
    images: r.images,
    failed: r.rows.filter(x => !x.ok).length,
    errors: r.errors
  }));
  if (!r.ok) process.exitCode = 1;
}
