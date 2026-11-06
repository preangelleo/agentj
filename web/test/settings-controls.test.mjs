import test from 'node:test';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
// Included automatically in the existing web-unit glob. A missing class, dynamic
// renderer regression, squared shared field or collapsed section makes the gate red.
test('settings shared components and section rhythm lint (rendered DOM)', {timeout:120000}, () => {
  const script=fileURLToPath(new URL('./settings-controls-lint.mjs',import.meta.url));
  process.stdout.write(execFileSync(process.execPath,[script],{encoding:'utf8',timeout:110000,maxBuffer:8*1024*1024}));
});
