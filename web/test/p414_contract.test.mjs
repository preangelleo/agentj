// P414 (PROTOCOL §37.9–§37.13) — the phone and the REAL host agree. Runs p414_contract_host.py (serve.Host + terminal.Desk +
// elevate + term_vault + term_bang + term_vshare, a real bash, a stand-in sudo) with the host's test interpreter; that script
// drives the page's own code through p414_contract_phone.mjs (wire.js seals / signatures / digests, vault.js PRF concat,
// faceid.js challenge, and elevate.js add() / done(), bang.js bangLine() / termOf(), vaultrow.js parse() / state(),
// terminal.js onBang() evaluated from source). It covers what p414.mjs (real page, fake host) cannot prove: that the real
// host emits and accepts exactly these names, types and bytes — host block `vault`, sudo-card `v` / `vault:1` / `pct` +
// `save:` tail / `saved:1`, the re-armed card without `stale`, term_open `vn` + 64-byte PRF, `bang` sid + owner page
// `src.term`, term_bang `failed` / `running`, term_go {cmd, h} / {}, term_confirm `agent:1`, vault_share two steps, the
// `vshare` card (tries 1, digest order, an emoji label) ending `saved`.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const AJ = resolve(HERE, '../..');
const local = join(AJ, 'host/.venv/bin/python');
const PY = process.env.AJ_PYTHON || (existsSync(local) ? local : null);

test('P414 contract: the page and the real host agree on every §37.9–§37.13 message', { skip: PY ? false : 'no host interpreter (AJ_PYTHON / host/.venv)', timeout: 180000 }, () => {
  assert.ok(existsSync(PY), 'AJ_PYTHON points at an interpreter: ' + PY);
  const home = mkdtempSync(join(tmpdir(), 'p414-contract-home-'));
  let r;
  try {
    // The phone coprocess runs the actual elevate.js add()/done() in a VM. Their special-approval
    // helpers moved into approval-special541.js after this contract was written. Inject those REAL
    // helper functions into the VM (including envelope verification), not a permissive fake; the
    // copied source exists only in the test loader, never in shipped code or the checked-in fixture.
    const loader = join(home, 'fixture-loader.mjs');
    writeFileSync(loader, `import { registerHooks } from 'node:module';
import { readFileSync } from 'node:fs';
const phone = ${JSON.stringify(join(HERE, 'p414_contract_phone.mjs'))};
const special = ${JSON.stringify(join(AJ, 'web/public/js/approval-special541.js'))};
registerHooks({ load(url, context, next) {
  const result = next(url, context);
  if (url !== new URL('file://' + phone).href) return result;
  const src = String(result.source);
  const s = readFileSync(special, 'utf8');
  const part = (a, b) => { const i = s.indexOf(a), j = s.indexOf(b, i + 1); if (i < 0 || j < 0) throw Error('special helper moved: ' + a); return s.slice(i, j).replace(/^export /gm, ''); };
  const helpers = 'const expired=new Map(),records=new Map(); function refreshSpecialVisibility() {}\\n'
    + part('export function attachSpecial(', 'export function canSpecialApprove(')
    + part('export function forgetSpecial(', 'export const specialApprovalById');
  const marker = "+ 'function notify(x) { notified.push(x); }\\\\nfunction clearTimeout() {}\\\\n'";
  if (!src.includes(marker)) throw Error('phone VM fixture moved');
  const patched = src.replace(marker, marker + '\\n    + ' + JSON.stringify(helpers) + ' + \\x27\\\\n\\x27');
  const bound = patched.replace('unb64u: wire.unb64u, salt32,', 'unb64u: wire.unb64u, salt32, approvalEnvelopeDigest: wire.approvalEnvelopeDigest,');
  if (bound === patched) throw Error('phone VM bindings moved');
  return { ...result, source: bound };
} });
`);
    r = spawnSync(PY, [join(HERE, 'p414_contract_host.py')], { cwd: resolve(AJ, '..'), encoding: 'utf8', timeout: 170000,
      env: { ...process.env, HOME: home, XDG_CONFIG_HOME: join(home, '.config'), XDG_DATA_HOME: join(home, '.local/share'),
        XDG_CACHE_HOME: join(home, '.cache'), NODE_OPTIONS: [process.env.NODE_OPTIONS, '--import=' + loader].filter(Boolean).join(' '),
        AJ_NODE: process.execPath, PYTHONDONTWRITEBYTECODE: '1' } });
  } finally { rmSync(home, { recursive: true, force: true }); }
  const out = (r.stdout || '') + (r.stderr || '');
  const line = out.split('\n').find((l) => l.startsWith('P414 contract: ')) || '';
  console.log(line || out.slice(-3000));
  assert.equal(r.status, 0, out.slice(-4000));
  const m = line.match(/^P414 contract: (\d+) checks PASS$/);
  assert.ok(m && Number(m[1]) >= 42, 'every check ran: ' + line);
});
