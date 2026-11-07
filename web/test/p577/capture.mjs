#!/usr/bin/env node
import{stopOwnedEndpoints}from'./endpoint-stop.mjs';
import {captureSession,launchOwnedBrowser,captureLaunchFlags,recordCaptureContext,acquireCaptureContext,phaseInitScript,captureMainPage,registerPhaseCDP,releaseCaptureContext,observePhaseDocument,registerPhaseOrigins,recordPhaseResponse,phasePageOn,phaseAuxiliaryPage,phaseModuleBarrier,navigateOwnedBlank,navigatePhaseSource,phaseTap} from './owned-browser-session.mjs';
import {collectExecutionResources,chooseExecutionJobs} from './execution-resources.mjs';
import {collectFontEnvironment,collectRuntimeEnvironment,environmentFingerprint,stableEnvironment} from './environment-attestation.mjs';
import {criticalConfigs,comparisonFixture,criticalReply,criticalFixtureFingerprint,runCriticalFlow,criticalSceneSelection,criticalOwnershipScope} from './publish-critical-profile.mjs';
import {loadVersionInput,versionModuleResponse,versionInputStable}from './version-input.mjs';
import {bindNativeRuntime}from './native-runtime.mjs';
import {aggregateNativeCoverage} from './native-coverage.mjs';
import { pngEvidence } from '../../tools/png-evidence.mjs';
import{browserEnvironment}from'./browser-environment.mjs';
import{auditArchiveJson}from'./archive-transport.mjs';
import{fixtureFingerprint as fingerprintFixtures,fixtureSceneContracts}from'./fixture-binding.mjs';
import { gzipSync } from 'node:zlib';
import { parseArgs } from 'node:util';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { publicVisualCrypto } from './visual-crypto.mjs';
import { requireTool,resolveTool } from './dependencies.mjs';
const {
  values: options
} = parseArgs({
  ...(captureSession()?{args:captureSession().args}:{}),
  options: {
    'environment-only':{type:'boolean',default:false},
    profile:{type:'string'},
    'critical-scenes':{type:'string'},
    'dashboard-source':{type:'string'},
    'comparison-inputs':{type:'string'},
    'version-input':{type:'string'},
    source: {
      type: 'string'
    },
    out: {
      type: 'string'
    },
    fixtures: {
      type: 'string'
    },
    modules: {
      type: 'string'
    },
    chromium: {
      type: 'string'
    },
    mode: {
      type: 'string',
      default: 'full'
    },
    width: {
      type: 'string'
    },
    config: {type:'string'},
    theme: {type:'string'},
    lang: {type:'string'},
    side: {
      type: 'string'
    },
    audit: {
      type: 'boolean',
      default: false
    }
  }
});
const critical=options.profile==='publish-critical';
if(options.profile&&!critical)throw Error('Unknown profile');
if(options['critical-scenes']&&!critical)throw Error('--critical-scenes requires publish-critical');
const selectedCritical=critical?criticalSceneSelection(options['critical-scenes']):null;
if(critical&&(options.mode!=='full'||options.width||options.theme||options.lang))throw Error('Critical profile uses its exact configuration list; select only by --config');
const comparisonInput=critical?comparisonFixture(options['comparison-inputs']):null;
if(critical&&!options['dashboard-source'])throw Error('publish-critical needs frozen dashboard-source');
const rawAbout=[],versionTransforms=[];
for (const key of ['QA_TOUCH','QA_SMOKE','QA_EXTRA','QA_MINIMAL','QA_WIDTH','QA_SIDE','QA_AUDIT']) delete process.env[key];
if (options.theme && !['light','dark'].includes(options.theme)) throw Error('theme must be light or dark');
if (options.lang && !['zh','en'].includes(options.lang)) throw Error('lang must be zh or en');
if((!options['environment-only']||options.side!==undefined)&&!['before','after'].includes(options.side))throw Error('--side must explicitly be before or after');
if (!options.source || !options.out) throw Error('Usage: capture.mjs --source <public> --out <evidence-dir> [--fixtures <repo>] [--modules <node_modules>] [--mode full|smoke|touch] [--side before|after] [--audit]');
if(options.mode==='version-check'&&!options['version-input'])throw Error('version-check requires explicit --version-input');
const versionInput=loadVersionInput(options['version-input'],JSON.parse(readFileSync(resolve(options.source,'version.json'))));let versionInputInterceptions=0;
const fixtureInput = resolve(options.fixtures || fileURLToPath(new URL('../../../', import.meta.url)));
const FIXTURE_ROOT = existsSync(resolve(fixtureInput, 'web/test/fakehost.mjs')) ? fixtureInput : resolve(fixtureInput, 'agentjarvis');
if (!existsSync(resolve(FIXTURE_ROOT, 'web/test/fakehost.mjs')) || !existsSync(resolve(FIXTURE_ROOT, 'protocol/noise.js'))) throw Error('Fixtures require a product root (web+protocol) or private repository root (agentjarvis/web+protocol)');
if (options.modules) process.env.P577_NODE_MODULES = resolve(options.modules);
process.env.QA_FIXED_ORIGIN = '1';
if (options.mode === 'smoke' || options.mode === 'minimal' || options.mode === 'version-check') {
  process.env.QA_SMOKE = '1';
  if (options.mode !== 'smoke') process.env.QA_MINIMAL = '1'; else process.env.QA_EXTRA = '1';
} else if (options.mode === 'touch') {
  process.env.QA_TOUCH = '1';
} else if(options.mode==='interaction-check'){process.env.QA_SMOKE='1';process.env.QA_EXTRA='1';} else if (options.mode === 'full') process.env.QA_EXTRA = '1';else throw Error('Unknown mode ' + options.mode);
if (options.width) process.env.QA_WIDTH = options.width;
if (options.side) process.env.QA_SIDE = options.side;
if (options.audit) process.env.QA_AUDIT = '1';
// Real-product fixture capture. Source tree is read-only. No production host or credentials.
const {
  reverseLink,
  b64u
} = await import(pathToFileURL(resolve(FIXTURE_ROOT, 'protocol/wire.js')));
const {
  hooksHandler
} = await import(pathToFileURL(resolve(FIXTURE_ROOT, 'web/test/p366-fixture.mjs')));
const {
  keysHandler
} = await import(pathToFileURL(resolve(FIXTURE_ROOT, 'web/test/p362-fixture.mjs')));
import { familyAudit } from './family-audit.mjs';
import { auditRuntime } from './runtime-audit.mjs';
const {
  seatId
} = await import(pathToFileURL(resolve(FIXTURE_ROOT, 'web/public/js/seats.js')));
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { existsSync, mkdirSync, writeFileSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve,dirname } from 'node:path';
const {
  startWebServer
} = await import(pathToFileURL(resolve(FIXTURE_ROOT, 'web/test/serve.mjs')));
const {
  startFakeHost,
  sampleFriends
} = await import(pathToFileURL(resolve(FIXTURE_ROOT, 'web/test/fakehost.mjs')));
const {
  chromium
} = requireTool('playwright');
const source = resolve(options.source),
  out = resolve(options.out);
mkdirSync(out, {
  recursive: true
});
const MEDIA_EXTRA = [
  ['C','sample.md','text/markdown','file',Buffer.from('# Synthetic document\n\nA fixed **Markdown** paragraph.\n\n- First\n- Second')],
  ['D','sample.pdf','application/pdf','pdf',readFileSync(fileURLToPath(new URL('./sample.pdf',import.meta.url)))],
  ['E','sample.wav','audio/wav','audio',readFileSync(fileURLToPath(new URL('./sample.wav',import.meta.url)))],
  ['F','sample.webm','video/webm','video',readFileSync(fileURLToPath(new URL('./sample.webm',import.meta.url)))],
  ['G','sample.csv','text/csv','file',Buffer.from('Item,Count\nPublic sample,42\nSecond sample,7')],
  ['H','sample.html','text/html','html',Buffer.from('<!doctype html><html><body><h1>Public synthetic HTML</h1><p>Fixed empty-sandbox document.</p></body></html>')],
  ['I','sample.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document','file',readFileSync(fileURLToPath(new URL('./sample.docx',import.meta.url)))],
  ['J','sample.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','file',readFileSync(fileURLToPath(new URL('./sample.xlsx',import.meta.url)))],
  ['K','sample.js','text/javascript','file',Buffer.from('const publicSample = 42;\nconsole.log(publicSample);')]
];
const MEDIA_BYTES = new Map([['A'.repeat(22),readFileSync(source+'/assets/five-robot.png')],['B'.repeat(22),Buffer.from('Fixed synthetic text preview.\nSecond line.')],...MEDIA_EXTRA.map(([id,name,mime,kind,bytes])=>[id.repeat(22),bytes])]);
const MEDIA_ITEMS = [{mid:'A'.repeat(22),name:'sample.png',mime:'image/png',kind:'image',ref:'out/sample.png'}, {mid:'B'.repeat(22),name:'sample.txt',mime:'text/plain',kind:'file',ref:'out/sample.txt'},...MEDIA_EXTRA.map(([id,name,mime,kind])=>({mid:id.repeat(22),name,mime,kind,ref:'out/'+name}))].map(x=>({...x,bytes:MEDIA_BYTES.get(x.mid).length,sha256:createHash('sha256').update(MEDIA_BYTES.get(x.mid)).digest('hex')}));
const fingerprint = () => Object.fromEntries(readdirSync(source, {
  recursive: true
}).filter(n => statSync(resolve(source, n)).isFile()).sort().map(n => [n, createHash('sha256').update(readFileSync(resolve(source, n))).digest('hex')]));
const sourceBefore = fingerprint();
writeFileSync(out + '/source-before.json', JSON.stringify(sourceBefore, null, 2));
const ID_OWNERS = JSON.parse(readFileSync(resolve(FIXTURE_ROOT, 'web/css-node-owners.json'))).idOwners;
const OWNERSHIP_AUDIT_SOURCE = options.audit && !options['environment-only'] ? (await import(pathToFileURL(resolve(FIXTURE_ROOT, 'web/tools/ownership-audit.mjs')))).ownershipAuditSource : null;
const NATIVE_CONTRACT_IDS=JSON.parse(readFileSync(resolve(FIXTURE_ROOT,'web/native-control-contracts.json'))).entries.map(x=>x.id);
const KNOWN_OWNERS = JSON.parse(readFileSync(resolve(FIXTURE_ROOT, 'web/control-ownership-sites.json'))).owners;
const {canonicalRoots:CANONICAL_ROOTS,semanticContracts:SEMANTIC_CONTRACTS}=fixtureSceneContracts(FIXTURE_ROOT);
const SCENE_MANIFEST = JSON.parse(readFileSync(fileURLToPath(new URL('./scene-manifest.json', import.meta.url))));
const INLINE_CASES = JSON.parse(readFileSync(fileURLToPath(new URL('./inline-fixtures.json',import.meta.url))));
const SOURCE_COMBINED = JSON.parse(readFileSync(source + '/version.json')).combined;
const SIDE = options.side;
const ownershipAuditScope=criticalOwnershipScope(selectedCritical,SOURCE_COMBINED);
const formalPre577=ownershipAuditScope==='formal-pre577-source';
const SETUP_FIXTURE = JSON.parse(readFileSync(resolve(FIXTURE_ROOT, 'web/test/fixtures/p115-setup.json')));
const FAMILY_REGISTRY = JSON.parse(readFileSync(resolve(FIXTURE_ROOT, 'web/component-ownership.json')));
const FX = JSON.parse(readFileSync(resolve(FIXTURE_ROOT, 'host/tests/fixtures/p360_cards.json')));
const WF = JSON.parse(readFileSync(resolve(FIXTURE_ROOT, 'host/tests/fixtures/p244_wf.json')));
const allConfigs = critical ? criticalConfigs() : process.env.QA_TOUCH ? ['light', 'dark'].flatMap(theme => ['zh', 'en'].map(lang => ({
  width: 390,
  height: 844,
  theme,
  lang
}))) : process.env.QA_SMOKE ? [{
  width: Number(process.env.QA_WIDTH || 390),
  height: Number(process.env.QA_WIDTH || 390) === 360 ? 800 : Number(process.env.QA_WIDTH || 390) >= 768 ? 900 : 844,
  theme: options.theme || 'dark',
  lang: options.lang || 'zh'
}] : [360, 390, 768, 1440].flatMap(width => ['light', 'dark'].flatMap(theme => ['zh', 'en'].map(lang => ({
  width,
  height: width === 360 ? 800 : width === 390 ? 844 : width === 768 ? 1024 : 900,
  theme,
  lang
}))));
const configIndex = options.config === undefined ? null : Number(options.config);
if (configIndex !== null && (!critical && !['full','touch'].includes(options.mode) || !Number.isInteger(configIndex) || !allConfigs[configIndex])) throw Error('config requires a valid full/touch matrix index');
const configs = configIndex === null ? allConfigs : [allConfigs[configIndex]];
Date.now = () => 1793980000000; // deterministic fakehost timestamps; timers remain real
const session=captureSession();
if(session&&options['environment-only'])throw Error('Environment-only cannot consume a pair browser');
const launchFlags = captureLaunchFlags();
const fixtureFingerprint=()=>({...fingerprintFixtures(FIXTURE_ROOT),...(critical?criticalFixtureFingerprint(FIXTURE_ROOT):{})});
const fixtureBinding = fixtureFingerprint();
const executionResources=collectExecutionResources(),executionPolicy=chooseExecutionJobs(executionResources);
writeFileSync(out+'/execution-resources.json',JSON.stringify({resources:executionResources,...executionPolicy},null,2)+'\n');
const environment = {
  versionInput:versionInput.binding,
  temporaryDirectory:process.env.TMPDIR || null,
  ...collectFontEnvironment(),
  ...collectRuntimeEnvironment([resolveTool('playwright/package.json'),resolveTool('playwright-core/package.json')]),
  sourceOwnedAssets:[{flag:'--use-file-for-fake-video-capture',path:fileURLToPath(new URL('./camera.y4m',import.meta.url)),sourcePath:'web/test/p577/camera.y4m',sha256:fixtureBinding['p577/camera.y4m']}],
  nodeVersion:process.version,
  playwrightVersion:requireTool('playwright/package.json').version,
  playwrightChromiumContract:JSON.parse(readFileSync(resolve(dirname(resolveTool('playwright-core/package.json')),'browsers.json'))).browsers.filter(b=>['chromium','chromium-headless-shell'].includes(b.name)).map(b=>({name:b.name,revision:b.revision,browserVersion:b.browserVersion})),
  platform: process.platform,
  architecture: process.arch,
  fontProbes: {},
  chromium: options.chromium || process.env.CHROMIUM || chromium.executablePath(),
  launchFlags,
  deviceScaleFactor: 1,
  timezoneId: 'Asia/Tokyo',
  locales: {
    zh: 'zh-CN',
    en: 'en-US'
  },
  mediaPlayback: 'Native AV control hosts at metadata-ready t=0; isolated fixture play/pause/load muted; synthetic static payloads; no claim of playback correctness',
  motion: 'scenario reduce/no-preference; infinite animations t=0; finite animations/transitions finished',
  serviceWorkers: 'block',
  syntheticOrigin: 'http://localhost:57780',
  fontFiles: Object.fromEntries(Object.entries(sourceBefore).filter(([p]) => /\.(woff2?|ttf|otf)$/.test(p)))
};
const browser = session?.browser || await launchOwnedBrowser({chromium,executablePath:environment.chromium});
const observedBrowser=await browserEnvironment(browser,environment.chromium);environment.browser=observedBrowser.stable;environment.renderer=observedBrowser.stable.renderer;writeFileSync(out+'/browser-arguments.json',JSON.stringify(observedBrowser.raw,null,2));
environment.browserVersion=browser.version();
if(options['environment-only']){
 try{
  const sourceAfter=fingerprint(),fixtureAfter=fixtureFingerprint();
  const sourceStable=JSON.stringify(sourceBefore)===JSON.stringify(sourceAfter),fixtureStable=JSON.stringify(fixtureBinding)===JSON.stringify(fixtureAfter);
  const receipt={schema:'p577-environment-only-v1',source,sourceCombined:SOURCE_COMBINED,sourceStable,fixtureStable,fixtureBinding,environment,stableEnvironment:stableEnvironment(environment),environmentFingerprint:environmentFingerprint(environment),executionResources:{resources:executionResources,...executionPolicy},pagesCreated:0,screenshots:0};
  writeFileSync(out+'/environment-only.json',JSON.stringify(receipt,null,2)+'\n');
  if(!sourceStable||!fixtureStable)process.exitCode=1;
 }finally{await browser.close();}
 process.exit(process.exitCode||0);
}
const web=await startWebServer({publicDir:source}),fake=await startFakeHost({channelSeed:new Uint8Array(32).fill(42),visualSeed:'p577-public-v1'});
let endpointsStopPromise;
const stopEndpoints=()=>endpointsStopPromise||=stopOwnedEndpoints(fake,web);
const BASE = process.env.QA_FIXED_ORIGIN ? 'http://localhost:57780/' + (await seatId(fake.channel)) + '/' : web.url;
registerPhaseOrigins([web.url,BASE,fake.relay]);
const phaseResponse=(url,body,transformation=null)=>{if(!session)return;let path=decodeURIComponent(new URL(url).pathname);const seatPath=new URL(BASE).pathname;if(path.startsWith(seatPath))path=path.slice(seatPath.length);path=path.replace(/^\//,'');if(!path)path='index.html';if(!sourceBefore[path])return;const responseSha256=createHash('sha256').update(body).digest('hex');recordPhaseResponse({requestUrl:url,path,sourceSha256:sourceBefore[path],responseSha256,noStore:true,transformation:transformation||(responseSha256===sourceBefore[path]?null:/^manifest(?:-dark|-light)?\.webmanifest$/.test(path)?'seat-manifest':(()=>{throw Error('Actual source response bytes differ: '+path)})())});};
let rows = [];
try {
  for (const cfg of configs) {
    fake.reset();
    fake.setUp(true);
    fake.st.remotes = {
      limit: 10,
      can_add: true,
      items: [{
        id: 'QA00000000000000',
        name: 'Synthetic QA device',
        me: true,
        online: true,
        paired_at: 1793980000
      }]
    };
    fake.st.friends = sampleFriends();
    fake.st.runCmds=['doctor','status']; // public read-only names: render actual rescue command toolbar; never execute
    fake.answer('mem_list', () => [{
      t: 'mem_sources',
      harness: 'claude',
      sources: [{
        id: 'qa',
        label: 'QA.md',
        path: '~/example/QA.md',
        n: 1
      }],
      trash: []
    }, {
      t: 'mem_items',
      items: [{
        src: 'qa',
        file: 'QA.md',
        fsha: 'a',
        iid: '1',
        kind: 'line',
        text: '- A synthetic memory item'
      }],
      more: false
    }]);
    fake.answer('act_list', () => ({
      t: 'act_page',
      more: false,
      items: [{
        k: 'decision',
        ts: 1793980000000,
        result: 'allow',
        by: 'QA',
        tool: 'Bash',
        text: 'Read sample.txt'
      }]
    }));
    fake.answer('task_list', () => ({
      t: 'tasks',
      more: false,
      agent: 'claude',
      paused: false,
      items: [{
        id: 'qa-daily',
        title: {
          zh: '测试日报',
          en: 'QA daily'
        },
        schedule: '0 9 * * *',
        tz: 'local',
        mode: 'research',
        enabled: true,
        next: 1793983600,
        tsha: 'x'
      }]
    }));
    fake.st.caps = ['wf1', 'jcards1'];
    fake.st.onApp = async (c, m, send) => {
      const reply = async (t, v) => {
        await send({
          t,
          r: m.r,
          ...v
        });
        return true;
      };
      const extra=critical?criticalReply(m):null;if(extra)return reply(...extra);
      if (m.t === 'setup_get') return reply('setup_card', {
        ...SETUP_FIXTURE,
        show: false
      });
      if (m.t === 'bots_read') return reply('bots_res', {
        ok: true,
        bots: [],
        host_timezone: 'Asia/Tokyo'
      });
      if (m.t === 'ct_list') return reply('ct_res', {
        ok: true,
        contacts: [{
          id: 'c000000000001',
          name: 'Synthetic Contact',
          nicks: [],
          emails: [{
            v: 'qa@example.test',
            label: 'work'
          }],
          phones: [],
          letter: 'S'
        }],
        count: 1,
        me: {
          id: 'c000000000000',
          name: 'QA Owner',
          emails: [{
            v: 'owner@example.test'
          }],
          phones: []
        },
        mail: {
          ready: true
        }
      });
      if (m.t === 'media_get') {
        const b = MEDIA_BYTES.get(m.mid);
        if (!b) return reply('media_err', {
          mid: m.mid,
          why: 'gone'
        });
        await send({
          t: 'media_chunk',
          mid: m.mid,
          o: m.o,
          d: b.subarray(m.o).toString('base64url'),
          last: true
        });
        return true;
      }
      if (m.t === 'wf_doc') return reply('wf_doc',{ok:true,folder:m.folder,name:m.name,title:'Public synthetic document',text:'# Public synthetic document\n\nFixed **prose**, a list and a code token.\n\n- First\n- Second\n\n`publicSample`'});
      if (m.t === 'skill_get') return reply('skill_get',{ok:true,id:m.id,name:'public-skill',text:'# Public synthetic skill\n\nFixed usage instructions.\n\n- Read the sample\n- Prepare a draft'});
      if (m.t === 'wf_list') {
        for (const x of WF.wf_list) await send({
          ...x,
          r: m.r
        });
        return true;
      }
      if (m.t === 'jcard_list') return reply('jcard_items', {
        cards: FX.list.cards.map(c => ({
          ...c,
          on: true
        }))
      });
      if (m.t === 'jcard_get') {
        let data = FX.get[m.lang === 'en' ? 'en' : 'zh'][m.id];
        if (m.id === 'calendar') data = {
          ok: true,
          id: 'calendar',
          template: 'calendar',
          events: [{
            id: 'qa-event',
            title: 'Synthetic meeting',
            start: '2026-11-07T09:00:00+09:00',
            end: '2026-11-07T10:00:00+09:00',
            all_day: false,
            readonly: false
          }],
          revision: '1',
          actions: [],
          anchors: [],
          links: []
        };
        if (m.id === 'files') data = {
          ok: true,
          id: 'files',
          template: 'files',
          actions: [],
          anchors: [],
          links: []
        };
        if (m.id === 'obsidian') data = {
          ok: true,
          id: 'obsidian',
          template: 'obsidian',
          entries: [{
            id: 'n1',
            path: 'Synthetic note.md',
            text: '# Synthetic note\nA fixed paragraph.'
          }],
          actions: [],
          anchors: [],
          links: []
        };
        return reply('jcard_get', data || {
          ok: false,
          why: 'not_found'
        });
      }
      if (m.t === 'jcard_files') return reply('jcard_files', {
        ok: true,
        root: 'qa',
        roots: [{
          id: 'qa',
          label: 'Sample files'
        }, {
          id: 'qa2',
          label: 'Other folder'
        }],
        path: '',
        label: 'Sample files',
        entries: [{
          name: 'folder',
          kind: 'dir'
        }, {
          name: 'sample.txt',
          kind: 'file',
          bytes: 123
        }]
      });
      if (m.t === 'files_get') return reply('files_state', {
        workdir: '/workspace/example/coding',
        mac: true,
        grants: [{
          path: '/workspace/sample',
          expires: 0
        }],
        tcc: {
          folders: {
            Downloads: 'readable',
            Desktop: 'blocked_or_unknown',
            Documents: 'readable'
          },
          automation: 'available'
        }
      });
      return false;
    };
    keysHandler(fake.st);
    hooksHandler(fake.st);
    fake.st.prefs = {
      appearance: {
        language: cfg.lang,
        theme: cfg.theme
      },
      voice: {
        speak_replies: false,
        wake_enabled: false
      }
    };
    const ctx = await acquireCaptureContext(browser,{
      viewport: {
        width: cfg.width,
        height: cfg.height
      },
      colorScheme: cfg.theme,
      reducedMotion: 'reduce',
      locale: cfg.lang === 'zh' ? 'zh-CN' : 'en-US',
      timezoneId: 'Asia/Tokyo',
      deviceScaleFactor: 1,
      hasTouch: !!process.env.QA_TOUCH,
      isMobile: !!process.env.QA_TOUCH,
      serviceWorkers: 'block',
      ...(cfg.width <= 390 ? {
        userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'
      } : {})
    });
    await recordCaptureContext(browser);
    await phaseInitScript(ctx,{content:'globalThis.__p577AuditRuntime=('+auditRuntime.toString()+');'});
    await phaseInitScript(ctx,publicVisualCrypto);
    await phaseInitScript(ctx,() => {
      const NativeDate = Date;
      globalThis.Date = class extends NativeDate {
        constructor(...a) {
          super(...(a.length ? a : [1793980000000 + (globalThis.__qaAdvance || 0)]));
        }
        static now() {
          return 1793980000000 + (globalThis.__qaAdvance || 0);
        }
      };
      window.Audio = undefined;
      for (const k of ['play', 'pause', 'load']) HTMLMediaElement.prototype[k] = function () {
        return k === 'play' ? Promise.resolve() : undefined;
      };
    });
    if (process.env.QA_TOUCH) await phaseInitScript(ctx,() => document.addEventListener('DOMContentLoaded', () => document.documentElement.dataset.qaTouch = '1'));
    const p = await captureMainPage(ctx);
    let latestAuditTransport;
    const auditArchive=async meta=>{const start=performance.now();const text=await p.evaluate(auditArchiveJson,JSON.stringify(meta));const data=JSON.parse(text);if(JSON.stringify(data)!==text)throw Error('JSON archive transport changed encoding');latestAuditTransport={milliseconds:performance.now()-start,archiveBytes:Buffer.byteLength(text)};return data;};
    const cssSession = registerPhaseCDP(await ctx.newCDPSession(p)),
      sheetURLs = new Map();
    cssSession.on('CSS.styleSheetAdded', ({
      header
    }) => sheetURLs.set(header.styleSheetId, header.sourceURL ? new URL(header.sourceURL).pathname : 'inline'));
    await cssSession.send('DOM.enable');
    await cssSession.send('CSS.enable');
    let navigationEpoch = 0; const fixtureNetworkErrors = [];
    phasePageOn(p,'framenavigated', frame => {if(frame === p.mainFrame()) navigationEpoch++});
    if (process.env.QA_FIXED_ORIGIN) await p.route('http://localhost:57780/**', async route => {
      if(/\.m?js(?:$|\?)/.test(route.request().url()))await phaseModuleBarrier(p);
      const requestEpoch = navigationEpoch;
      const u = new URL(route.request().url());
      const versionResponse=versionModuleResponse(versionInput,u.href,BASE);
      if(versionResponse){versionInputInterceptions++;phaseResponse(u.href,versionResponse.body,'same-version-combined-input');return route.fulfill({...versionResponse,headers:{'cache-control':'no-store'}});}
      try {
      const response = await route.fetch({
        url: web.url + u.pathname.slice(1) + u.search,
        headers: {
          ...route.request().headers(),
          host: 'localhost:57780'
        }
      });
      const responseBytes=await response.body();phaseResponse(u.href,responseBytes);
      const headers = {...response.headers(),'cache-control':'no-store'};
      if (headers['content-security-policy']) headers['content-security-policy'] = headers['content-security-policy'].replaceAll(new URL(web.url).origin, 'http://localhost:57780');
      await route.fulfill({
        response,body:responseBytes,
        headers
      });
      } catch (error) {
        fixtureNetworkErrors.push({path:u.pathname, navigationCancelled:requestEpoch !== navigationEpoch || p.isClosed(), error:String(error).split('\n')[0]});
        await route.abort('failed').catch(() => {});
      }
    });
    const browserErrors = [];
    phasePageOn(p,'console', m => {
      if (m.type() === 'error') browserErrors.push(m.text().replace(/#p=[^\s"']+/g, '#p=[redacted]'));
    });
    phasePageOn(p,'pageerror', e => browserErrors.push(String(e)));
    p.setDefaultTimeout(4000);
    const tag = `${cfg.width}-${cfg.theme}-${cfg.lang}`;
    const captureStable = async (path, preserveFocus = false, page=p) => {
      let previous = null;
      for (let attempt = 1; attempt <= 5; attempt++) {
        if (!preserveFocus && !process.env.QA_TOUCH) await page.evaluate(() => document.activeElement?.blur());
        const bytes = await page.screenshot({
          caret: 'hide',
          animations: 'disabled'
        });
        const ev = pngEvidence(bytes);
        if (previous === ev.rgbaSha256) {
          writeFileSync(path, bytes);
          return {
            attempts: attempt,
            rgbaSha256: ev.rgbaSha256
          };
        }
        previous = ev.rgbaSha256;
        await page.waitForTimeout(80);
      }
      throw Error('Scene did not settle to two consecutive identical RGBA frames');
    };
    const nativeSceneEvidence=async(data,row)=>{
          if (SIDE === 'after' && !formalPre577) {
            const extra = await p.evaluate(OWNERSHIP_AUDIT_SOURCE);
            mkdirSync(out + '/ownership', {
              recursive: true
            });
            writeFileSync(out + '/ownership/' + tag + '-' + row.name + '.json', JSON.stringify(extra));
            row.nativeControlEvidence=bindNativeRuntime(data,extra);
            row.nativeContractBinding=data.nativeContractBinding;
            row.productOwnerAudit = {
              ok: extra.ok === true && !data.nativeControlEvidence.errors.length && !data.nativeControlEvidence.conflicts.length,
              missing: extra.missing?.length || 0,
              semantic: extra.semanticFailures?.length || 0,
              conflicts: extra.conflicts?.length || 0
            };
          }

    };
    const capture = async (name, selector, details={}) => {
      const row = {
        ...cfg,
        name,
        target: selector,
        ...(details.activeMechanism?{activeMechanism:details.activeMechanism}:{})
      };
      try {
        if(name==='badge'&&versionInput.moduleText)await p.waitForFunction(expected=>document.getElementById('version-hash')?.textContent===expected,versionInput.binding.combined);
        await p.evaluate(async () => {
          if (!document.documentElement.dataset.qaNotice) (await import('./js/ui.js')).toastOff();
          await document.fonts.ready;
          if (!document.documentElement.dataset.qaTouch && !document.documentElement.dataset.qaPreserveFocus) document.activeElement?.blur();
          for (const a of document.getAnimations()) {
            try {
              if (a instanceof CSSTransition || a.effect?.getTiming().iterations !== Infinity) a.finish();else {
                a.pause();
                a.currentTime = 0;
              }
            } catch {
              a.pause();
            }
          }
        });
        if (!process.env.QA_TOUCH && !/reply-(?:hover|focus-visible|active)$/.test(name)) await p.mouse.move(0, 0);
        await p.waitForTimeout(150);
        if (name.startsWith('settings-')) {
          const blockers = await p.evaluate(() => [...document.querySelectorAll('[role=dialog]')].filter(e => e.id !== 'settings' && !e.closest('#settings') && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden').map(e => e.id));
          if (blockers.length) throw Error('dialogs mask settings: ' + blockers.join(','));
          if (await p.evaluate(() => document.body.dataset.sheet === '1')) throw Error('request sheet masks settings');
        }
        ;
        if (selector && !(await p.locator(selector).isVisible())) throw Error('scene target not visible: ' + selector);
        if (selector) {
          const hit = await p.locator(selector).evaluate(e => {
            const r=e.getBoundingClientRect(),x=Math.min(innerWidth-1,Math.max(1,r.x+r.width/2)),y=Math.min(innerHeight-1,Math.max(1,r.y+r.height/2)),n=document.elementFromPoint(x,y);
            return {ok:!!n&&(e===n||e.contains(n)),x,y,target:{id:e.id,tag:e.tagName,rect:r.toJSON()},actual:n?{id:n.id,tag:n.tagName,classes:[...n.classList],owner:n.getAttribute('data-aj-owner')}:null};
          });
          row.hitTest=hit;
          if (!hit.ok && selector !== '#ptt') throw Error('scene target is masked at center: ' + selector+' actual='+JSON.stringify(hit.actual));
          if (!hit.ok && selector === '#ptt') row.hitTestException = 'PTT is intentionally pointer-events:none; its visual overlay is expected by this scene';
        }
        ;
        row.selfStability = await captureStable(`${out}/${tag}-${name}.png`,/^reply-(?:hover|focus-visible|active)$/.test(name));
        row.fixtureNetworkErrors = fixtureNetworkErrors.filter(e => !e.navigationCancelled);
        if (row.fixtureNetworkErrors.length) throw Error('Fixture resource failure in active navigation');
        row.ok = true;
        row.state = await p.evaluate(() => ({
          status: document.body.dataset.status,
          kind: document.body.dataset.kind,
          conn: document.body.dataset.conn,
          reduced: matchMedia('(prefers-reduced-motion:reduce)').matches,
          input: {
            coarse: matchMedia('(pointer:coarse)').matches,
            fine: matchMedia('(pointer:fine)').matches,
            hover: matchMedia('(hover:hover)').matches,
            touch: navigator.maxTouchPoints
          },
          fontScale: getComputedStyle(document.documentElement).getPropertyValue('--reply-scale')
        }));
        row.families = await p.evaluate(familyAudit, {
          ...FAMILY_REGISTRY,
          canonicalRoots: CANONICAL_ROOTS
        });
        if (process.env.QA_AUDIT) {
          const data = await auditArchive({
            ...details, side: SIDE, assetBasePath: new URL(BASE).pathname,
            scene: name,
            paintSelectors:({recording:['#ptt','#ptt *'],'media-preview-webm':['.mview','.mview *'],'prose-math':['#words','#words .math','#words .katex *'],'hot-menu':['#hotMenu','#hotMenu *'],'media-message':['#input','.composer'],'memory-find-highlight':['#mem-view','#mem-view .pfind','#mem-view .pfind *'],'settings-contacts':['#settings','#set-contacts','.top','.top *'],'settings-section-set-s-rescue':['#rs-cmds','#rs-cmds *']})[name]||[],
            state: details.state || 'default',
            sourceCombined: SOURCE_COMBINED,
            contracts: SEMANTIC_CONTRACTS,
            canonicalRoots: CANONICAL_ROOTS,
            knownOwners: KNOWN_OWNERS,
            idOwners: ID_OWNERS,
            screenshot: resolve(out, tag + '-' + name + '.png'),
            screenshotSha256: createHash('sha256').update(readFileSync(resolve(out, tag + '-' + name + '.png'))).digest('hex'),
            viewport: {
              width: cfg.width,
              height: cfg.height
            },
            theme: cfg.theme,
            lang: cfg.lang,
            url: p.url().split('#')[0]
          });
          mkdirSync(out + '/runtime', {
            recursive: true
          });

          row.runtimeMissing = data.missing.length;
          row.runtimeRules = data.rules.length;
          row.auditTransport=latestAuditTransport;
          await nativeSceneEvidence(data,row);
          ;
          writeFileSync(out + '/runtime/' + tag + '-' + name + '.json.gz', gzipSync(JSON.stringify(data)));
          row.ownerAuditOK = SIDE === 'before' || formalPre577 ? null : ![data.directMissing, data.unknownOwners, data.semanticMismatches, data.actionContextMissing].some(x => x?.length) && row.productOwnerAudit?.ok !== false;
        }
        ;
        row.visible = await p.evaluate(() => [...document.querySelectorAll('[role=dialog],section[id],.view[id]')].filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden').map(e => e.id));
      } catch (e) {
        row.ok = false;
        await p.screenshot({
          path: `${out}/${tag}-${name}-FAIL.png`,
          caret: 'hide',
          animations: 'disabled'
        }).catch(() => {});
        row.layerState = await p.evaluate(async () => {
          const m = await import('./js/snap.js'),
            s = m.snapshot();
          return {
            sheet: document.body.dataset.sheet,
            card: document.getElementById('sheet')?.dataset.card,
            open: s.card && {
              id: s.card.id,
              kind: s.card.kind
            },
            ask: s.ask && {
              id: s.ask.id,
              state: s.ask.state
            },
            approval: s.approval && {
              id: s.approval.id,
              state: s.approval.state
            }
          };
        }).catch(() => null);
        row.error = String(e).replace(/#p=[^\s"']+/g, '#p=[redacted]');
      }
      rows.push(row);
      writeFileSync(out + '/result.json', JSON.stringify({
        source,
        browser: browser.version(),
        configs,
        rows
      }, null, 2));
      console.log(tag, name, row.ok ? 'PASS' : row.error);
    };
    const rawAboutPage=async()=>{const raw=await phaseAuxiliaryPage(ctx);await raw.route('http://localhost:57780/**',async route=>{const u=new URL(route.request().url());const response=await route.fetch({url:web.url+u.pathname.slice(1)+u.search,headers:{...route.request().headers(),host:'localhost:57780'}});const headers={...response.headers(),'cache-control':'no-store'};if(headers['content-security-policy'])headers['content-security-policy']=headers['content-security-policy'].replaceAll(new URL(web.url).origin,'http://localhost:57780');await route.fulfill({response,headers});});await raw.goto(BASE+'?lang='+cfg.lang);await raw.waitForFunction(()=>document.getElementById('version-hash')?.textContent.length===64);return raw;};
    const versionRoute=async route=>{const u=new URL(route.request().url());const response=await route.fetch({url:web.url+u.pathname.slice(1)+u.search,headers:{...route.request().headers(),host:'localhost:57780'}});const production=await response.text();const served=production.replace(/("version"\s*:\s*)"[^"]*"/,(_,prefix)=>prefix+JSON.stringify(comparisonInput.displayVersion));phaseResponse(u.href,Buffer.from(served),'fixed-interface-data-only');versionTransforms.push({...cfg,url:new URL(route.request().url()).pathname,productionSha256:createHash('sha256').update(production).digest('hex'),servedSha256:createHash('sha256').update(served).digest('hex'),field:u.pathname.endsWith('/version.json')?'version.json.version':'VERSION.version'});await route.fulfill({response,body:served,headers:{...response.headers(),'cache-control':'no-store'}});};
    if(critical){await p.route('**/version.js',versionRoute);await p.route('**/version.json',versionRoute);}
    try {
      await navigatePhaseSource(p,BASE + '?lang=' + cfg.lang);
      await observePhaseDocument(p);
      const served = await p.evaluate(async () => await (await fetch('/version.json')).json());
      if (served.combined !== SOURCE_COMBINED) throw Error('served source combined differs from frozen source');
      environment.fontProbes[cfg.lang] = await p.evaluate(async () => {
        await document.fonts.ready;
        const families = [getComputedStyle(document.body).fontFamily, getComputedStyle(document.documentElement).getPropertyValue('--font-mono') || 'monospace'];
        return Promise.all(families.map(async family => {
          const c = document.createElement('canvas');
          c.width = 512;
          c.height = 64;
          const x = c.getContext('2d');
          x.font = '20px ' + family;
          x.fillStyle = '#123456';
          x.fillText('Agent J 0123456789 ABC 中文测试', 4, 32);
          const data = x.getImageData(0, 0, c.width, c.height).data;
          const hash = await crypto.subtle.digest('SHA-256', data);
          return {
            family,
            rgbaSha256: [...new Uint8Array(hash)].map(x => x.toString(16).padStart(2, '0')).join('')
          };
        }));
      });
      await p.waitForFunction(() => window.__ajState === 'idle');
      if(!critical)await capture('login', '#pages');
      await p.evaluate(() => {document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click()});
      await p.locator('#pair-go').scrollIntoViewIfNeeded();
      if(!critical)await capture('login-form', '#pair-go');
      if(session)await navigateOwnedBlank(p);else await p.goto('about:blank');
      await p.goto(fake.newPairing(BASE + '?lang=' + cfg.lang));
      await p.waitForFunction(() => window.__ajState === 'awaiting-approval');
      const shownSas = (await p.locator('#sas').textContent()).replace(/\s/g, '');
      if (shownSas !== String(fake.sas).replace(/\s/g, '')) throw Error('Displayed SAS differs from real Noise transcript');
      if(!critical)await capture('pair-code', '#sas-view');
      await fake.approve();
      await p.waitForFunction(() => window.__ajState === 'ready');
      await p.evaluate(() => {
        document.getElementById('phone-env-dismiss')?.click();
        document.getElementById('a2hs-ok')?.click();
      });
      if(critical){rawAbout.push(await runCriticalFlow({p,ctx,cfg,fake,capture,captureStable,cssSession,rows,out,tag,sourceCombined:SOURCE_COMBINED,input:comparisonInput,versionRoute,dashboardSource:options['dashboard-source'],mediaItems:MEDIA_ITEMS,sheetURLs,rawAboutPage,scenes:selectedCritical.scenes}));continue;}
      await fake.addTurn({
        k: 'phone',
        dev: 'synthetic-owner',
        name: 'QA',
        text: '请检查这些引用 Please inspect these quotes',
        quote: {
          id: 1,
          who: 'QA',
          ts: 1793980000000,
          text: '第一条引用 First quote with a long beginning and a readable end.',
          more: [{
            id: 2,
            who: 'QA',
            ts: 1793980000000,
            text: '第二条引用 Second quote with a long beginning and a readable end.'
          }]
        }
      }, '界面核查 Interface check\n\n这是一条固定回复。A fixed response.\n\n请选：[继续](reply:继续) [取消](reply:取消)\n\n```js\nconst value = 42;\n```\n\n# Heading one\n\n## Heading two\n\n### Heading three\n\n#### Heading four\n\n##### Heading five\n\n###### Heading six\n\n> A fixed quoted paragraph.\n\n- First item\n- Second item\n\n| Column | Value |\n| --- | --- |\n| Sample | 42 |\n\n---\n\n**Strong** and *emphasis* with `inline code`.');
      await p.waitForFunction(() => document.querySelector('.rbtn'));
      await fake.send({
        t: 'status',
        s: 'idle',
        agent: 'claude',
        name: 'Agent J'
      });
      await capture('chat-idle', '#chat-view');
      if (process.env.QA_MINIMAL){
        if(options.mode==='version-check'){
          if(!versionInput.moduleText)throw Error('version-check requires explicit --version-input');
          await p.locator('#setBtn').click();await p.locator('#menu-about').scrollIntoViewIfNeeded();await p.locator('#menu-about').click();await capture('badge','#badge-panel');await p.locator('#badge-close').click();
          await p.locator('#rs-cmds-h').scrollIntoViewIfNeeded();if(!await p.locator('#rs-cmds').evaluate(e=>e.open))await p.locator('#rs-cmds-h').click();
          await p.waitForFunction(()=>document.getElementById('rs-cmds').open&&document.querySelectorAll('#rs-cmd-list .rscmd').length===2);await p.locator('#rs-cmd-list').scrollIntoViewIfNeeded();await capture('settings-rescue-expanded','#rs-cmd-list');
        }
        continue;
      }
      await capture('source-quotes-folded', '#omQuoteFold');
      await p.locator('#omQuoteFold')[process.env.QA_TOUCH ? 'tap' : 'click']();
      await capture('source-quotes-expanded', '#omQuoteMore');
      await p.locator('#omQuoteFold')[process.env.QA_TOUCH ? 'tap' : 'click']();
      await p.evaluate(() => import('./js/busy.js').then(m => m.setBusy({
        tasks: Array.from({
          length: 8
        }, (_, i) => ({
          id: 'qa' + i,
          who: 'QA',
          label: '界面核查',
          kind: 'temp',
          c: i
        })),
        task_count: 8,
        load: .3
      })));
      await fake.send({
        t: 'status',
        s: 'working',
        agent: 'claude',
        name: 'Agent J'
      });
      await capture('chat-working', '#chat-view');
      await p.emulateMedia({
        reducedMotion: 'no-preference'
      });
      await capture('chat-motion-full', '#chat-view');
      await p.emulateMedia({
        reducedMotion: 'reduce'
      });
      await p.locator('#replyBtn').click();
      await capture('chat-quote', '#qbar');
      if (!process.env.QA_TOUCH) for (const [name, sel] of [['reply', '.rbtn'], ['source-quote', '#omQuoteFold'], ['robot', '#busyDots']]) {
        for (const state of ['hover', 'focus-visible', 'active']) {
          const row = {
            ...cfg,
            name: name + '-' + state
          };
          let forcedNode = null;
          try {
            await p.locator(sel).first().scrollIntoViewIfNeeded();
            await p.evaluate(() => {
              for (const a of document.getAnimations()) {
                try {
                  if (a instanceof CSSTransition || a.effect?.getTiming().iterations !== Infinity) a.finish();else {
                    a.pause();
                    a.currentTime = 0;
                  }
                } catch {
                  a.pause();
                }
              }
            });
            await p.evaluate(() => document.activeElement?.blur());
            await p.mouse.move(0, 0);
            if (state === 'focus-visible') {
              await p.keyboard.press('Tab');
              await p.locator(sel).first().focus();
              if (!(await p.locator(sel).first().evaluate(e => e.matches(':focus-visible')))) throw Error('keyboard focus-visible did not activate');
            } else await p.locator(sel).first().hover();
            if (state === 'active') {
              if (await p.locator(sel).first().evaluate(e => e.matches(':focus-visible') || e === document.activeElement)) throw Error('Pointer CSS-active contaminated by keyboard focus');
              const {
                root
              } = await cssSession.send('DOM.getDocument');
              ({
                nodeId: forcedNode
              } = await cssSession.send('DOM.querySelector', {
                nodeId: root.nodeId,
                selector: sel
              }));
              await cssSession.send('CSS.forcePseudoState', {
                nodeId: forcedNode,
                forcedPseudoClasses: ['active']
              });
              row.activeMechanism = 'CDP visual pseudo-state, no gesture/longpress';
            }
            row.selfStability = await captureStable(`${out}/${tag}-${row.name}.png`, true);
            if (process.env.QA_AUDIT) {
              const forcedRules = forcedNode ? (await cssSession.send('CSS.getMatchedStylesForNode', {
                nodeId: forcedNode
              })).matchedCSSRules.filter(x => x.rule.origin === 'regular').flatMap(x => x.matchingSelectors.map(i => x.rule.selectorList.selectors[i].text).filter(t => t.includes(':active')).map(selector => ({
                selector,
                file: sheetURLs.get(x.rule.styleSheetId) || 'inline',
                properties: x.rule.style.cssProperties.filter(p => !p.disabled).map(p => p.name),
                conditions: (x.rule.media || []).map(m => m.text),
                line: (x.rule.style.range?.startLine || 0) + 1
              }))) : [];
              const data = await auditArchive({
                forcedSelector: sel,
                forcedRules,
                side: SIDE, assetBasePath: new URL(BASE).pathname,
                scene: row.name,
                paintSelectors:row.name==='reply-active'?['#input','.composer','.rmbar','.rmbar *']:[],
                state,
                sourceCombined: SOURCE_COMBINED,
                contracts: SEMANTIC_CONTRACTS,
                canonicalRoots: CANONICAL_ROOTS,
                knownOwners: KNOWN_OWNERS,
                idOwners: ID_OWNERS,
                screenshot: resolve(out, tag + '-' + row.name + '.png'),
                screenshotSha256: createHash('sha256').update(readFileSync(resolve(out, tag + '-' + row.name + '.png'))).digest('hex'),
                viewport: {
                  width: cfg.width,
                  height: cfg.height
                },
                theme: cfg.theme,
                lang: cfg.lang,
                url: p.url().split('#')[0]
              });
              await nativeSceneEvidence(data,row);
              mkdirSync(out + '/runtime',{recursive:true});
              writeFileSync(out + '/runtime/' + tag + '-' + row.name + '.json.gz',gzipSync(JSON.stringify(data)));
              row.runtimeMissing=data.missing.length;
              row.ownerAuditOK=SIDE==='before'||formalPre577?null:![data.directMissing,data.unknownOwners,data.semanticMismatches,data.actionContextMissing].some(x=>x?.length)&&row.productOwnerAudit?.ok!==false;
            }
            ;
            row.fixtureNetworkErrors = fixtureNetworkErrors.filter(e => !e.navigationCancelled);
        if (row.fixtureNetworkErrors.length) throw Error('Fixture resource failure in active navigation');
        row.ok = true;
          } catch (e) {
            row.ok = false;
            row.error = String(e);
          } finally {
            if (forcedNode) await cssSession.send('CSS.forcePseudoState', {
              nodeId: forcedNode,
              forcedPseudoClasses: []
            }).catch(() => {});
          }
          rows.push(row);
        }
      }
      if(options.mode==='interaction-check')continue;
      await p.evaluate(() => {
        document.getElementById('busyClose')?.click();
        document.getElementById('qbX')?.click();
      });
      await p.evaluate(() => import('./js/five-pages.js').then(m => m.toggleMenu()));
      await capture('five-menu', '#five-menu');
      await p.evaluate(() => import('./js/five-pages.js').then(m => m.closeMenu()));
      await fake.send({
        t: 'status',
        s: 'waiting',
        kind: 'permission',
        agent: 'claude',
        name: 'Agent J'
      });
      await fake.ask({
        id: 'a'.repeat(32),
        tool: 'Bash',
        summary: 'Read synthetic sample.txt',
        ttl: 120,
        cat: ['read']
      });
      await capture('approval', '#sheet');
      await p.emulateMedia({
        reducedMotion: 'no-preference'
      });
      await capture('approval-motion-full', '#sheet');
      await p.emulateMedia({
        reducedMotion: 'reduce'
      });
      await fake.askDone('a'.repeat(32), 'gone');
      await p.waitForFunction(async () => (await import('./js/snap.js')).S.lastAsk?.state === 'ended');
      await capture('approval-ended', '#sheet');
      await fake.send({
        t: 'status',
        s: 'waiting',
        kind: 'question',
        agent: 'claude',
        name: 'Agent J'
      });
      await fake.send({
        t: 'question',
        id: 'b'.repeat(32),
        ttl: 180,
        qs: [{
          q: 'Which sample?',
          h: 'Sample',
          m: true,
          o: [{
            l: 'A',
            d: 'First'
          }, {
            l: 'B',
            d: 'Second'
          }]
        }]
      });
      await capture('question', '#sheet');
      await p.locator('#sheet .opt').first()[process.env.QA_TOUCH ? 'tap' : 'click']();
      await capture('question-selected', '#sheet');
      if (process.env.QA_TOUCH) {
        await phaseTap(p.locator('#sheetTitle'));
        await capture('touch-question-move-focus', '#sheet');
      }
      await p.emulateMedia({
        reducedMotion: 'no-preference'
      });
      await capture('question-motion-full', '#sheet');
      await p.emulateMedia({
        reducedMotion: 'reduce'
      });
      await fake.send({
        t: 'question_done',
        id: 'b'.repeat(32),
        result: 'gone'
      });
      await p.waitForFunction(async () => (await import('./js/snap.js')).S.lastQ?.state === 'ended');
      await capture('question-ended', '#sheet');
      await p.waitForTimeout(100);
      writeFileSync(out + '/' + tag + '-question-model.json', JSON.stringify(await p.evaluate(async () => {
        const m = await import('./js/snap.js'),
          s = m.snapshot();
        return {
          sheet: document.body.dataset.sheet,
          card: s.card,
          ask: s.ask,
          approval: s.approval,
          order: m.S.order,
          asks: [...m.S.asks.values()].map(x => ({
            id: x.id,
            state: x.state,
            tool: x.tool
          })),
          qs: [...m.S.qs.values()].map(x => ({
            id: x.id,
            state: x.state
          }))
        };
      }), null, 2));
      await p.waitForFunction(async () => !(await import('./js/snap.js')).snapshot().card);
      await fake.send({
        t: 'status',
        s: 'idle',
        agent: 'claude',
        name: 'Agent J'
      });
      await p.waitForTimeout(100);
      await p.evaluate(() => document.getElementById('sheetClose')?.click());
      await p.keyboard.press('Escape');
      await p.waitForTimeout(50);
      writeFileSync(out + '/' + tag + '-dismiss-model.json', JSON.stringify(await p.evaluate(() => ({
        sheet: document.body.dataset.sheet,
        card: document.getElementById('sheet').dataset.card,
        close: document.getElementById('sheetClose').outerHTML,
        dialogs: [...document.querySelectorAll('[role=dialog]')].filter(e => e.getClientRects().length).map(e => e.id)
      })), null, 2));
      await p.waitForFunction(() => document.body.dataset.sheet !== '1');
      if (process.env.QA_TOUCH) {
        await phaseTap(p.locator('#busyDots'));
        await capture('touch-busy-dialog', '#busyDlg');
        await phaseTap(p.locator('#busyClose'));
        await capture('touch-busy-return', '#chat-view');
        await phaseTap(p.locator('#orb'));
        await capture('touch-menu', '#five-menu');
        await phaseTap(p.locator('#orb'));
        await phaseTap(p.locator('#setBtn'));
        await capture('touch-settings', '#settings');
        await phaseTap(p.locator('#setBtn'));
        await capture('touch-settings-return', '#chat-view');
      }
      if (!process.env.QA_TOUCH) for (const [index,scale] of ['min','max'].entries()) {
        await p.evaluate(scale=>import('./js/settings.js').then(m=>{m.open();const b=document.getElementById(scale==='min'?'set-font-minus':'set-font-plus');for(let i=0;i<30&&!b.disabled;i++)b.click();m.close()}),scale);
        await fake.send({t:'status',s:'working',agent:'claude',name:'Agent J'});
        await capture('critical-'+scale+'-working','#chat-view');
        const approvalId=String(index*2+1).repeat(32),questionId=String(index*2+2).repeat(32);
        await fake.ask({id:approvalId,tool:'Bash',summary:'Read a synthetic sample with a longer explanation to inspect wrapping',ttl:120});
        await capture('critical-'+scale+'-approval','#sheet');
        await fake.askDone(approvalId,'gone');
        await p.waitForFunction(async()=>!(await import('./js/snap.js')).snapshot().card);
        await p.evaluate(()=>document.getElementById('sheetClose').click());await p.keyboard.press('Escape');
        await fake.send({t:'question',id:questionId,ttl:180,qs:[{q:'Choose one synthetic option',h:'Sample',m:index===1,o:[{l:'A',d:'A deliberately long synthetic description that must preserve its own line-height, wrapping and clamp boundary in a narrow reply-choice card.'},{l:'B',d:'Another option with a distinct selected/disabled contract.'}]}]});
        await capture('critical-'+scale+'-choice','#sheet');
        await fake.send({t:'question_done',id:questionId,result:'gone'});
        await p.waitForFunction(async()=>!(await import('./js/snap.js')).snapshot().card);
        await p.evaluate(()=>document.getElementById('sheetClose').click());await p.keyboard.press('Escape');
        await p.waitForFunction(()=>document.body.dataset.sheet!=='1');
      }
      if(!process.env.QA_TOUCH){await p.evaluate(()=>import('./js/settings.js').then(m=>{m.open();document.getElementById('set-font-reset').click();m.close()}));await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});}
      for (const page of ['workflows', 'skills', 'memory', 'agentj']) {
        await p.evaluate(page => import('./js/five-pages.js').then(m => m.go(page)), page);
        await capture('page-' + page, '#five-page');
        if (process.env.QA_EXTRA) {
          if(page==='workflows'){
            const first=p.locator('#five-body .fp-card').first();
            await first.locator('.fp-card-head').click();await capture('workflow-expanded-agents','#five-page');
            await first.locator('.fp-tab[data-k=docs]').click();await capture('workflow-expanded-docs','#five-page');
            await first.locator('.fp-expanded .fp-file').first().click();await p.waitForFunction(()=>document.querySelector('#five-detail .fp-prose h1'));await capture('workflow-document','#five-detail');await p.keyboard.press('Escape');
            await first.locator('.fp-at').click();await p.waitForFunction(()=>!document.getElementById('workflow-compose').hidden);await capture('composer-workflow-mention','#workflow-compose');await p.locator('#clr').click();
            await p.locator('#input').fill('/agentj-cards ');await p.waitForFunction(()=>!document.getElementById('workflow-compose').hidden);await capture('composer-command-chip','#workflow-compose');await p.locator('#clr').click();
          }else if(page==='skills'){
            await p.locator('#five-body .fp-card-head').first().click();await p.waitForFunction(()=>document.querySelector('#five-detail .fp-prose h1'));await capture('skill-detail','#five-detail');await p.locator('#five-detail details summary').click();await capture('skill-raw','#five-detail');await p.keyboard.press('Escape');
          }else if(page==='memory'){
            await p.locator('#five-body .fp-file').first().click();await p.waitForFunction(()=>document.querySelector('#five-detail .fp-prose h1'));await capture('memory-document','#five-detail');await p.keyboard.press('Escape');
          }else if(page==='agentj'){
            await p.locator('#five-body .fp-card-head').first().click();await p.waitForFunction(()=>document.querySelector('#five-detail .fp-prose h1'));await capture('agentj-document','#five-detail');await p.keyboard.press('Escape');
          }
        }

        await p.evaluate(() => import('./js/five-pages.js').then(m => m.go('chat')));
      }
      await p.evaluate(() => import('./js/settings.js').then(m => m.open()));
      await capture('settings-main', '#settings');
      await p.evaluate(() => document.getElementById('set-main').scrollTop = 100000);
      await capture('settings-main-end', '#settings');
      const subs = [['ios', 'set-a2hs'], ['remotes', 'set-remotes-open'], ['mail', 'set-mail-nav'], ['contacts', 'set-contacts-nav'], ['auth', 'set-auth-nav'], ['imessage', 'set-imsg-nav'], ['cards', 'set-cards-nav'], ['notify', 'set-notify-nav'], ['hooks', 'set-hooks-nav'], ['files', 'set-local-files-nav'], ['accts', 'set-accts-nav']];
      for (const id of ['set-s-agent', 'set-s-rescue', 'set-s-tools', 'set-s-look', 'set-s-read', 'set-s-notify', 'set-s-channels', 'set-s-safety', 'set-s-app', 'set-s-help']) {
        await p.locator('#' + id).scrollIntoViewIfNeeded();
        if(id==='set-s-rescue'){if(!await p.locator('#rs-cmds').evaluate(e=>e.open))await p.locator('#rs-cmds-h').click();await p.waitForFunction(()=>document.getElementById('rs-cmds').open&&document.querySelectorAll('#rs-cmd-list .rscmd').length===2);await p.locator('#rs-cmd-list').scrollIntoViewIfNeeded();await capture('settings-section-'+id,'#rs-cmd-list');}else await capture('settings-section-' + id, '#' + id);
      }
      for (const [name, id] of subs) {
        if (name === 'ios' && cfg.width > 390) {
          rows.push({
            ...cfg,
            name: 'settings-ios',
            ok: true,
            notApplicable: 'Desktop user agent uses browser install menu, not iOS instructions'
          });
          continue;
        }
        await p.evaluate(() => import('./js/settings.js').then(m => {
          m.close();
          m.open();
        }));
        await p.evaluate(id => document.getElementById(id)?.click(), id);
        await p.waitForTimeout(200);
        const target = name === 'files' ? '#set-local-files' : '#set-' + name;
        await capture('settings-' + name, target);
      }
      if (process.env.QA_EXTRA) {
        await p.evaluate(() => import('./js/settings.js').then(m => m.close()));
        for (const scale of ['min', 'max', 'default']) {
          await p.evaluate(scale => import('./js/settings.js').then(m => {
            m.open();
            if (scale === 'default') document.getElementById('set-font-reset').click();else {
              const b = document.getElementById(scale === 'min' ? 'set-font-minus' : 'set-font-plus');
              for (let i = 0; i < 30 && !b.disabled; i++) b.click();
            }
            m.close();
          }), scale);
          await capture('chat-font-' + scale, '#chat-view');
        }
        await p.evaluate(() => {
          if (!document.getElementById('qbar').hidden) document.getElementById('qbX').click();
          const root = document.getElementById('words');
          const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
          let node; while ((node = walker.nextNode()) && !node.textContent.trim()) {}
          if (!node) throw Error('No actual reply text to select');
          const range = document.createRange(); range.selectNodeContents(node);
          const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        });
        await p.waitForFunction(() => !document.getElementById('qbar').hidden);
        await p.locator('#qbJump').click();
        await p.waitForFunction(() => (CSS.highlights?.get('aj-quote')?.size || 0) > 0);
        await capture('quote-excerpt-highlight', '#chat-view');
        await p.waitForFunction(() => !CSS.highlights?.has('aj-quote'));
        await p.locator('#qbX').click();
        await p.locator('#readBtn').click();await p.waitForTimeout(400);
        for(const [position,fraction] of [['start',0],['middle',0.5],['end',1]]){await p.evaluate(fraction=>{const e=document.getElementById('rdScroll');e.scrollTop=(e.scrollHeight-e.clientHeight)*fraction},fraction);await capture('rich-reader-'+position,'#rd')}
        await p.keyboard.press('Escape');await p.waitForFunction(()=>document.getElementById('rd').hidden);
        await p.setInputFiles('#fDoc', {
          name: 'sample.txt',
          mimeType: 'text/plain',
          buffer: Buffer.from('Synthetic attachment')
        });
        await capture('file-tray', '#tray');
        await p.evaluate(() => [...document.querySelectorAll('#tray .chip .x')].forEach(b => b.click()));
        await p.locator('#mic').click();
        await p.waitForFunction(() => !document.getElementById('ptt').hidden);
        await capture('recording', '#ptt');
        await p.evaluate(() => document.getElementById('pttCancel').click());
        const mt = await fake.addTurn({
          k: 'phone',
          dev: 'synthetic-owner',
          text: 'Show sample media',
          att: [{
            kind: 'file',
            name: 'sample.txt'
          }]
        }, '![Sample](out/sample.png)\n\n[Sample text](out/sample.txt)');
        await fake.updateTurn(mt.id, {
          media: MEDIA_ITEMS.slice(0,2)
        });
        await p.waitForFunction(() => document.querySelector('#words .mslot .mcard[data-state=ready] img'));
        await capture('media-message', '#words');
        await p.locator('#words .mslot img').click();
        await capture('media-preview', '.mview');
        await p.keyboard.press('Escape');
        for (const item of MEDIA_ITEMS.slice(1)) {
          const mediaTurn=await fake.addTurn({k:'phone',dev:'synthetic-owner',text:'Show '+item.name},'['+item.name+']('+item.ref+')');
          await fake.updateTurn(mediaTurn.id,{media:[item]});
          await p.locator('#pgLatest').dblclick();
          await p.waitForFunction(mid=>document.querySelector(`.mcard[data-mid="${mid}"][data-state=ready]`),item.mid).catch(e=>{throw Error('Media card not ready: '+item.name+' '+e)});
          await p.locator('.mcard[data-mid="'+item.mid+'"] .mact.pv').click();
          const kind=item.name.split('.').pop();
          const target=['txt','md','js'].includes(kind)?'#rd':'.mview';
          await p.waitForFunction(target=>{const e=document.querySelector(target);return e&&!e.hidden},target);
          if(kind==='pdf')await p.waitForFunction(()=>document.querySelector('.mview [data-drawn=ok] canvas'));
          else if(['wav','webm'].includes(kind))await p.waitForFunction(()=>{const v=document.querySelector('.mview audio,.mview video');return v&&v.readyState>=1&&Number.isFinite(v.duration)&&v.currentTime===0});
          else if(['csv','xlsx'].includes(kind))await p.waitForFunction(()=>document.querySelector('.mview table td'));
          else if(['docx','html'].includes(kind))await p.waitForFunction(()=>document.querySelector('.mview iframe'));
          else await p.waitForFunction(()=>document.getElementById('rdWords')?.textContent.trim().length>0||document.querySelector('#rd .words')?.textContent.trim().length>0);
          await capture('media-preview-'+kind,target);
          await p.keyboard.press('Escape');
          await p.waitForFunction(target=>{const e=document.querySelector(target);return !e||e.hidden},target);
          await p.evaluate(()=>import('./js/media.js').then(m=>m.afterPop()));
        }

        for(const fixture of INLINE_CASES){
          await fake.addTurn({k:'phone',dev:'synthetic-owner',text:'Render '+fixture.name},'```agentj\n'+JSON.stringify(fixture.value)+'\n```');
          await p.locator('#pgLatest').dblclick();
          await p.waitForFunction(kind=>document.querySelector('#words .reply-component[data-component="'+kind+'"]'),fixture.value.component);
          await capture('inline-'+fixture.name,'#words .reply-component');
          if(fixture.name==='form'){
            await p.locator('#words .reply-component input[type=text]').fill('Public fixed draft');await p.locator('#words .reply-component select').selectOption('Second');await p.locator('#words .reply-component input[type=date]').fill('2026-11-07');await p.locator('#words .reply-component input[type=checkbox]').check();
            await capture('inline-form-filled','#words .reply-component');
            await p.locator('#words .reply-component .rc-draft').click();await capture('inline-form-draft','#input');await p.locator('#clr').click();
            await p.locator('#readBtn').click();await p.waitForFunction(()=>!document.getElementById('rd').hidden&&document.querySelector('#rd .reply-component input[disabled]'));await capture('inline-form-readonly','#rd');await p.keyboard.press('Escape');await p.evaluate(()=>import('./js/media.js').then(m=>m.afterPop()));
          }
        }
        for(const [name,text,selector]of [['math','$$\nx^2 + y^2 = z^2\n$$','#words .katex'],['mermaid','```mermaid\ngraph TD;\n A[Public input] --> B[Fixed draft];\n```','#words .diagram img']]){
          await fake.addTurn({k:'phone',dev:'synthetic-owner',text:'Render '+name},text);await p.locator('#pgLatest').dblclick();await p.waitForSelector(selector);await capture('prose-'+name,'#words');
        }
        for (const [kind, extra] of [['sudo', {
          cmd: 'gzip -dc /workspace/sample.gz',
          why: 'Read a synthetic sample',
          effect: ''
        }], ['secret', {
          name: 'QA_EXAMPLE',
          purpose: 'Synthetic visual fixture',
          dest: 'temporary test memory',
          verify: ''
        }]]) {
          await fake.send({
            t: 'elev',
            id: (kind === 'sudo' ? 'c' : 'd').repeat(32),
            kind,
            n: 'e'.repeat(32),
            epk: Buffer.alloc(32, 1).toString('base64url'),
            ttl: 120,
            tries: 3,
            ...extra
          });
          await p.waitForFunction(() => document.getElementById('elev')?.hidden === false);
          await capture('card-' + kind, '#elev');
          await fake.send({
            t: 'elev_done',
            id: (kind === 'sudo' ? 'c' : 'd').repeat(32),
            result: 'done',
            code: 0
          });
          await p.waitForFunction(() => document.getElementById('elev').hidden);
        }
        await fake.send({
          t: 'login_qr',
          id: 'f'.repeat(32),
          n: 'b'.repeat(32),
          site: 'mp-weixin',
          title: 'Synthetic login',
          img: readFileSync(source + '/assets/five-robot.png').toString('base64'),
          ttl: 120,
          bind: 'c'.repeat(64),
          tg: 'text',
          channel: 'Telegram'
        });
        await p.waitForFunction(() => document.getElementById('lqr')?.hidden === false);
        await capture('card-login', '#lqr');
        await fake.send({
          t: 'login_qr_done',
          id: 'f'.repeat(32),
          site: 'mp-weixin',
          result: 'done'
        });
        await p.waitForFunction(() => document.getElementById('lqr').hidden);
        for (const id of ['notes', 'todo', 'calendar', 'files', 'obsidian', 'reading-list']) {
          await p.evaluate(id => import('./js/five-pages.js').then(m => m.openTarget(id)), id);
          await p.waitForTimeout(250);
          await capture('wallet-' + id, '#five-page');
          await p.evaluate(() => import('./js/five-pages.js').then(m => m.go('chat')));
        }
        const scene = async (name, target, enter, leave) => {
          try {
            await p.evaluate(enter);
            await p.waitForTimeout(220);
            await capture(name, target);
          } catch (e) {
            rows.push({
              ...cfg,
              name,
              ok: false,
              error: String(e)
            });
          } finally {
            if (leave) await p.evaluate(leave);
          }
        };
        for (const page of ['mem', 'act', 'tasks', 'friends', 'bots', 'setup']) {
          if (page === 'setup') await p.evaluate(() => document.getElementById('open-setup').click());else await p.evaluate(v => import('./js/controls.js').then(m => m.openPanel(v)), page);
          await capture('legacy-' + page, '#' + page + '-view');
          if (page === 'mem') {
            await p.locator('#srchBtn').click();
            await p.locator('#pfind-mem-view-input').fill('QA');
            await p.waitForFunction(() => (CSS.highlights?.get('aj-find')?.size || 0) > 0);
            await capture('memory-find-highlight', '#mem-view');
            await p.keyboard.press('Escape');
          }
          await p.evaluate(() => import('./js/controls.js').then(m => m.openPanel('chat')));
        }
        await p.evaluate(() => import('./js/controls.js').then(m => m.openPanel('bots')));
        await p.waitForTimeout(250);
        await p.locator('#bots-side button').nth(1).click();
        await capture('bot-wizard-template', '#bots-main');
        await p.locator('#bots-main button').first().click();
        await capture('bot-wizard-details', '#bots-main');
        await p.evaluate(() => {
          document.querySelectorAll('#bots-main input')[0].value = 'Synthetic QA bot';
          document.querySelectorAll('#bots-main input')[1].value = 'qa-sample';
          document.querySelector('#bots-main button').click();
        });
        await capture('bot-wizard-policy', '#bots-main');
        await p.evaluate(() => import('./js/controls.js').then(m => m.openPanel('chat')));
        await scene('models', '#models-view', () => document.getElementById('open-models').click(), () => document.getElementById('models-back').click());
        await scene('busy-dialog', '#busyDlg', () => import('./js/busy.js').then(m => m.open()), () => import('./js/busy.js').then(m => m.close()));
        await scene('load-dialog', '#loadDlg', () => import('./js/busy.js').then(m => m.openLoad()), () => import('./js/busy.js').then(m => m.closeLoad()));
        await scene('name-dialog', '#nameDlg', () => import('./js/agentname.js').then(m => m.open()), () => import('./js/agentname.js').then(m => m.close()));
        await scene('rename-dialog', '#renameDlg', () => import('./js/dev-rename.js').then(m => m.open()), () => import('./js/dev-rename.js').then(m => m.close()));
        await scene('usage-dialog', '#usageDlg', () => import('./js/usagedlg.js').then(m => m.openUsage(null)), () => import('./js/usagedlg.js').then(m => m.closeUsage()));
        await scene('confirm-dialog', '#confirm', () => import('./js/ui.js').then(m => {
          void m.confirmSheet('Synthetic action', 'Fixed confirmation text', 'Confirm', true);
        }), () => import('./js/ui.js').then(m => m.closeConfirm()));
        await scene('full-editor', '#fsEd', () => import('./js/fsedit.js').then(m => m.open()), () => import('./js/fsedit.js').then(m => m.close()));
        await scene('search', '#srch', () => import('./js/search.js').then(m => m.open()), () => import('./js/search.js').then(m => m.close()));
        await scene('reader', '#rd', () => document.getElementById('readBtn').click(), () => document.getElementById('rdClose').click());
        await p.keyboard.press('Escape');
        await p.waitForTimeout(400);
        await p.locator('#slashBtn').click();
        await capture('command-menu', '#menu');
        await p.keyboard.press('Escape');
        await scene('keys', '#keys', () => document.getElementById('keysBtn').click(), () => document.getElementById('keysClose').click());
        const reverse = reverseLink(b64u(new Uint8Array(16).fill(8)), new Uint8Array(32).fill(7), new Uint8Array(16).fill(6), Math.floor(Date.now() / 1000) + 300, fake.relay);
        await p.evaluate(link => {
          void import('./js/rvpair.js').then(m => {
            void m.approve(link);
          });
        }, reverse);
        await capture('reverse-pair-dialog', '#rvDlg');
        await p.evaluate(() => document.getElementById('rvNo').click());
        await fake.send({
          t: 'pk_peer_ask',
          id: 'P'.repeat(22),
          device: 'D'.repeat(22),
          name: 'Synthetic QA peer',
          at: 1793980000,
          exp: 1793980300,
          code: '123456',
          fa: 'cWE',
          fa_ch: 'cWE'
        });
        await capture('passkey-peer', '#pkp');
        await fake.send({
          t: 'pk_peer_close',
          id: 'P'.repeat(22)
        });
        await scene('seat-transfer', '#sxf', () => import('./js/seatxfer.js').then(m => m.open({
          token: 'QA-SYNTHETIC-PLACEHOLDER',
          zh: 'Synthetic transfer instruction',
          en: 'Synthetic transfer instruction',
          exp: 1793980300
        })), () => import('./js/seatxfer.js').then(m => m.close()));
        await scene('contact-editor', '#ctmEdit', () => {
          document.getElementById('ctm-add').click();
        }, () => document.getElementById('ctm-e-cancel').click());
        await scene('hot-menu', '#hotMenu', () => import('./js/hotkey.js').then(m => m.openHot()), () => import('./js/hotkey.js').then(m => m.closeHot()));
        await scene('badge', '#badge-panel', () => document.getElementById('menu-about').click(), () => document.getElementById('badge-close').click());
        await p.locator('#replyBtn').click();
        await fake.addTurn({
          k: 'phone',
          dev: 'synthetic-owner',
          text: 'Second synthetic message'
        }, 'Second fixed reply');
        await p.waitForTimeout(200);
        await p.locator('#replyBtn').click();
        await capture('composer-multi-quotes', '#qbar');
        await p.locator('.qc .qw').first().click();
        await capture('quote-dialog', '#quoteDlg');
        await p.keyboard.press('Escape');
        await p.evaluate(() => document.getElementById('qbX').click());
        await fake.addTurn({
          k: 'phone',
          dev: 'synthetic-owner',
          text: 'Silent synthetic message'
        }, '〔不回群〕');
        await p.waitForTimeout(200);
        await p.evaluate(() => document.getElementById('silentEntry')?.click());
        await capture('silent', '#silent');
        await p.evaluate(() => document.getElementById('silentClose')?.click());
        await p.evaluate(() => document.getElementById('scanBtn').click());
        await capture('scanner', '#scan-view');
        await p.evaluate(() => document.getElementById('scan-back').click());
        if (cfg.width >= 768) {
          fake.st.terminal = {
            on: true,
            os: true,
            me: true,
            web_ok: true,
            pk: true,
            kind: 'desktop',
            modes: ['pty'],
            minutes: 15,
            idle_s: 300
          };
          await fake.send(fake.prefsMsg());
          await p.evaluate(() => import('./js/settings.js').then(m => m.open()));
          await p.waitForFunction(async () => (await import('./js/termhook.js')).state() === 'ok');
          await p.evaluate(() => {
            void import('./js/termhook.js').then(m => {
              void m.enter();
            });
          });
          await p.waitForFunction(() => document.getElementById('term') && !document.getElementById('term').hidden);
          await capture('terminal-intro', '#term');
          await p.keyboard.press('Escape');
          await p.evaluate(() => import('./js/settings.js').then(m => m.close()));
          await p.evaluate(() => document.getElementById('tCamera').click());
          await capture('camera', '#cam');
          await p.keyboard.press('Escape');
        }
        await fake.send({
          t: 'estop_state',
          on: true
        });
        await capture('stopped', '#estop-banner');
        await fake.send({
          t: 'estop_state',
          on: false
        });
        fake.setUp(false);
        await p.waitForFunction(() => window.__ajState === 'waiting-host');
        await p.evaluate(() => globalThis.__qaAdvance = 5000);
        await p.waitForTimeout(5600);
        await capture('chat-offline', '#offDlg');
        await p.evaluate(() => globalThis.__qaAdvance = 1865000);
        await p.waitForTimeout(1000);
        await capture('chat-offline-long', '#offDlg');
        await p.evaluate(() => globalThis.__qaAdvance = 0);
        fake.setUp(true);
        await p.waitForFunction(() => window.__ajState === 'ready');
        await p.route('**/version.json', async route => {
          const m = JSON.parse(readFileSync(source + '/version.json'));
          m.version = '98.0.0';
          const body=JSON.stringify(m);phaseResponse(route.request().url(),Buffer.from(body),'version-notice-fixture');
          await route.fulfill({body,contentType:'application/json',headers:{'cache-control':'no-store'}});
        });
        await p.evaluate(() => import('./js/version-notice.js').then(m => m.setHostVersion('99.0.0')));
        await p.waitForTimeout(200);
        await capture('version-notice', '#version-notice');
        await p.evaluate(() => document.getElementById('version-dismiss').click());
        await p.unroute('**/version.json');
        await fake.sendRaw(Uint8Array.of(4, 1, 2, 3));
        await p.waitForFunction(() => window.__ajState === 'error');
        await capture('protocol-error', '#error-view');
        await p.evaluate(() => document.getElementById('retry').click());
        await p.waitForFunction(() => window.__ajState === 'ready');
        await fake.revokeAll();
        await p.waitForFunction(() => window.__ajState === 'revoked');
        await capture('revoked', '#revoked-view');
        await p.evaluate(() => document.getElementById('repair').click());
        await p.waitForFunction(() => window.__ajState === 'idle');
        await p.evaluate(() => {
          document.getElementById('pair-link').value = 'not a pairing link';
          document.getElementById('pair-go').click();
        });
        await p.locator('#pair-error').scrollIntoViewIfNeeded();
        await capture('pair-invalid', '#pair-error');
      }
    } catch (e) {
      rows.push({
        ...cfg,
        name: 'flow',
        ok: false,
        browserErrors,
        fixture: {
          connections: fake.conns,
          frames: fake.frames,
          appTypes: fake.log.map(x => x.t)
        },
        debug: await p.evaluate(() => ({
          path: location.pathname,
          state: window.__ajState,
          status: document.getElementById('status')?.textContent,
          error: document.getElementById('pair-error')?.textContent
        })).catch(() => null),
        error: String(e).replace(/#p=[^\s"']+/g, '#p=[redacted]')
      });
      console.log(tag, 'FLOW FAIL', String(e).slice(0, 180));
    } finally {
      await releaseCaptureContext(ctx,{stopEndpoints});
    }
  }
} finally {
  const browserVersion = browser.version();
  if(!session)await browser.close();
  await stopEndpoints();
  if(session?.isolation.cleanup){session.isolation.cleanup.fakeConnectionsRemaining=fake.conns;session.isolation.cleanup.sourceServerListening=web.server.listening;session.isolation.cleanup.fakeHostStopped=fake.conns===0;session.isolation.cleanup.sourceServerStopped=web.server.listening===false;if(fake.conns!==0||web.server.listening)throw Error('Old phase fixture endpoint remained active');}
  const sourceAfter = fingerprint();
  writeFileSync(out + '/source-after.json', JSON.stringify(sourceAfter, null, 2));
  const sourceStable = JSON.stringify(sourceBefore) === JSON.stringify(sourceAfter);
  const coverageErrors = [];
  for (const cfg of configs) {
    const actual = rows.filter(row => ['width','height','theme','lang'].every(k => row[k] === cfg[k]));
    const expected = critical?selectedCritical.scenes:[...(process.env.QA_MINIMAL ? [...SCENE_MANIFEST.minimalScenes,...(options.mode==='version-check'?['badge','settings-rescue-expanded']:[])] : process.env.QA_TOUCH ? SCENE_MANIFEST.touchScenes : options.mode==='interaction-check'?SCENE_MANIFEST.interactionScenes:SCENE_MANIFEST.scenes), ...(options.mode!=='interaction-check'&&!process.env.QA_TOUCH && !process.env.QA_MINIMAL && cfg.width >= 768 ? SCENE_MANIFEST.additionalDesktopScenes : [])];
    for (const scene of expected) {
      const matching = actual.filter(row => row.name === scene);
      if (matching.length !== 1) coverageErrors.push({cfg,scene,reason:`Expected exactly one row; found ${matching.length}`});
    }
    for (const row of actual) if (!expected.includes(row.name)) coverageErrors.push({cfg,scene:row.name,reason:'Unexpected scene or flow failure'});
  }
  const coverage = {schemaVersion:1, ok:coverageErrors.length === 0, errors:coverageErrors, mode:critical?'publish-critical':options.mode, scope:configIndex === null ? (critical?'publish-critical':options.mode) : 'config-shard', completeMatrix:configIndex === null && (critical||['full','touch'].includes(options.mode)), partition:configIndex === null ? null : {index:configIndex,total:allConfigs.length}, expectedConfigs:configs.length, requiredScenes:critical?{...SCENE_MANIFEST.publishCritical,scenes:selectedCritical.scenes}:SCENE_MANIFEST};
  if (!coverage.ok) process.exitCode = 1;
  const nativeControlCoverage=SIDE==='after'&&options.audit&&!formalPre577?aggregateNativeCoverage(rows.filter(r=>r.ok&&!r.notApplicable),NATIVE_CONTRACT_IDS):null;
  if(nativeControlCoverage)writeFileSync(out+'/native-control-coverage.json',JSON.stringify(nativeControlCoverage,null,2));
  writeFileSync(out + '/result.json', JSON.stringify({
    versionInput:{...versionInput.binding,moduleSha256:versionInput.moduleSha256,interceptions:versionInputInterceptions},
    versionInputStable:versionInputStable(versionInput),
    nativeControlCoverage,
    ownershipAuditScope,
    ...(critical?{criticalSceneSelection:selectedCritical,comparisonInput:{sha256:comparisonInput.sha256,policy:comparisonInput.policy,versionTransforms},rawAbout,fullRefactorQualification:false}:{}),
    coverage,
    ...(session?{pairedCapture:{topology:'one-owned-page-new-documents-per-side',contextId:session.isolation.contextId,pageTargetId:session.isolation.pageTargetId,isolation:session.isolation}}:{}),
    source,
    side: SIDE,
    sourceCombined: SOURCE_COMBINED,
    sourceStable,
    sourceFiles: Object.keys(sourceBefore).length,
    browser: browserVersion,
    captureVersion: 'p577-v2',
    auditEnabled:options.audit,
    executionResources:{resources:executionResources,...executionPolicy},
    environment,
    environmentFingerprint:environmentFingerprint(environment),
    fixtureBinding,
    fixtureStable: JSON.stringify(fixtureBinding) === JSON.stringify(fixtureFingerprint()),
    configs,
    rows
  }, null, 2));
  if (!sourceStable || JSON.stringify(fixtureBinding) !== JSON.stringify(fixtureFingerprint())) process.exitCode = 1;
}
if(!versionInputStable(versionInput)||(versionInput.moduleText&&!versionInputInterceptions))process.exitCode=1;
if (rows.some(x => !x.ok || x.ownerAuditOK === false)) process.exitCode = 1;
