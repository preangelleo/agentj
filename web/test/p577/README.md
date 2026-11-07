# P577 visual qualification

This is an explicit browser entry, separate from ordinary `web-unit`. `../p577-visual.test.mjs` runs only sixteen fast fixture/comparator guard tests. No production account, host, approval, deploy or install is used.

Requirements: Node with the project's existing `.ts` module support, Playwright (tested 1.62.1), and Chromium (the captured version is recorded, never assumed). `--modules` points to an existing `node_modules`; the runner installs nothing. The pixel comparator has no third-party dependency and reuses `tools/png-evidence.mjs` for strict PNG CRC/filter/RGBA decoding.

```sh
export TMPDIR="$HOME/.cache/577qa"
mkdir -p "$TMPDIR"
# Keep TMPDIR short: deep project/worktree paths exceed Chromium Unix socket limits.
node web/test/p577/capture.mjs \
  --source /path/to/read-only-576-public --out /path/to/before-a \
  --fixtures /path/to/frozen-577-repository --modules /path/to/node_modules \
  --chromium /path/to/chromium --mode full --side before --audit

# Repeat exactly with another output for before-b; then change only source/out/side for after.
node web/test/p577/compare.mjs --before /path/to/before-a --after /path/to/before-b --out /path/to/stability-diff
node web/test/p577/compare.mjs --before /path/to/before-a --after /path/to/after --out /path/to/product-diff

# Independent real touch emulation: 390 light/dark x zh/en.
node web/test/p577/capture.mjs \
  --source /path/to/read-only-public --out /path/to/touch \
  --fixtures /path/to/frozen-577-repository --modules /path/to/node_modules \
  --chromium /path/to/chromium --mode touch --side before --audit
```

`--mode smoke --width 390` runs one configuration, including rich content; it does not qualify the full matrix. Full mode is checked against `scene-manifest.json`, four widths, two themes, two languages. Runtime output binds each source selector to actual DOM paths, direct owners, independent semantic contracts, canonical content roots, and screenshot SHA. Before owners are resolved through an exact per-scene/path sidecar made by `make-before-owner-map.mjs`; raw missing metadata is retained and unknown paths fail.

Only public synthetic input is deterministic: fake channel seed, dates, messages, contacts, wallet data, camera frame, and public browser/host cryptographic test vectors. The displayed pairing SAS is derived by the real Noise transcript and asserted equal to the fake host’s computed SAS; no displayed text is substituted. The real seat hash algorithm and redirects run unchanged. A fixed localhost browser origin is routed to an ephemeral local server; only the corresponding synthetic CSP self URLs are rewritten for that fixture origin. The product source/header implementations remain unchanged and require their separate security review. Service workers are blocked to keep each capture cold and prevent the browser's localhost cache intercepting test routing.

The isolated Chromium disables LocalNetworkAccessChecks to permit this entirely local routed origin to reach the fake localhost Noise relay. It disables overlay-scrollbar features while retaining normal scrollbars. The final bounded runner retains Playwright’s existing sRGB default and does not add the exploratory duplicate sRGB or Skia runtime flags. Every actual launch argument, binary SHA, renderer, DPR, viewport, locale, timezone, first-party font hash and resolved font raster probe is recorded. This proves neither production LNA behavior nor a physical phone. Touch mode uses a separate `hasTouch/isMobile` context and preserves the tap's focus/hover state; mouse mode is explicitly distinguished.

Hover uses a mouse; keyboard focus-visible uses a real Tab followed by focus. CSS active uses CDP `CSS.forcePseudoState` and its actual matched stylesheet rules, avoiding a 550ms robot longpress sending an unrelated theme request. Recording/camera use Chromium's fake devices and the checked-in constant Y4M frame. Transitions and finite animations finish; infinite animations pause at time zero. Each image must produce two consecutive identical decoded RGBA frames, with bounded retries. Failure is kept, never accepted by increasing a pixel threshold.

Source bytes and all fixture/tool inputs are hashed before and after. Drift, absent/failed scenes, different environment inputs, missing images, different dimensions or a single changed pixel fails qualification. Build `VERSION.combined` is displayed by the real About page and remains real; the runner does not mask or replace it. A changed build identifier is therefore a real raw pixel failure under the original strict acceptance standard.

`TMPDIR` may point to an existing writable project-local temporary directory when the system temporary filesystem has a quota. Every server/browser created by this runner closes in `finally`; it does not use the shared CDP browser.

Fixture root accepts either a private repository containing `agentjarvis/web` and `agentjarvis/protocol`, or an exported product root containing `web` and `protocol`. The default resolves the latter relative to this package. Public deterministic crypto is injected only into the isolated Playwright context; application assets and protocol modules are not rewritten. Omitted fakehost `visualSeed` retains random keys, pairing IDs and PSKs.

The explicit narrow raster guard uses `--mode minimal --width 360 --theme light --lang zh`, once with `--audit` and once without, on the same frozen source; compare all pixels with `compare.mjs`. It captures only login, login-form, real SAS and chat-idle. A minimal guard cannot qualify full coverage. The historical native-profile red and single-variable evidence are retained in the #577 research record.

`matrix.mjs` is the full qualification entry: same capture options plus `--jobs 1|2` and `--mode full|touch`. It runs explicit isolated config shards and combines only after every required configuration occurs exactly once, all local scene receipts pass, all 367 asset fingerprints remain stable before/after capture and identical across shards on the same source side (before and after product assets are allowed to differ), and fixture/browser/environment bindings agree. Missing/duplicated configurations, nonzero child exit, owner failures or source drift keep `completeMatrix=false`. Raw PNG and runtime evidence is hardlinked without modification. Each shard retains `scope=config-shard`, so it cannot substitute for a full receipt.

The full raw receipt is produced by `make-visual-receipt.mjs --before <full-before> --after <full-after> --out <receipt-dir> --coverage <coverage.json>`. It independently requires all named scenes, 16 actual unique configs, stable inputs and exact decoded pixels. A real build hash difference keeps `complete=false`. Source-equivalent owners can be written with `make-before-owner-map.mjs <after-runtime> <before-combined> <output-dir> --sharded`. The impact CLI consumes that index and each original compressed snapshot; no aggregate drops hidden descendants.

A preliminary full configuration can be reused with `matrix.mjs ... --reuse <capture-shard-dir>` only when it came from `capture.mjs --mode full --config <0..15>`, the source assets, full current fixture hashes, side, audit mode and scene receipt still match. The matrix merges its original PNG/runtime evidence unchanged.

The environment investigation is still open: a 390-dark cold-run rounded-edge difference occurred even with sRGB/Skia options. Those flags are not claimed to be a proven fix. Full actual Chromium arguments and backend evidence are required in the final environment receipt, beyond the requested launch arguments. Historical narrow green images cannot override a later red run.

Runtime sampling sends the same complete archive as a JSON string instead of Playwright’s per-field tagged object transport. Every snapshot verifies parse/stringify round-trip identity. The pinned sampler runs unchanged against real CSSOM/DOM; no fields, hidden descendants, direct controls or semantic checks are removed. A measured ABBA diagnostic produced byte-identical archive SHA with substantially lower transport time.

Final bounded evidence returns to the original system Chromium 152 requested flags; exploratory duplicate sRGB/Skia options are not a fix and are removed. The actual full arguments, real executable SHA and renderer are recorded by `browser-environment.mjs`. If `Browser.getBrowserCommandLine` is unavailable without automation, the same browser’s `SystemInfo.getInfo.commandLine` is used and the limitation is explicit. No automation flag is added just for observation. Raw safe arguments are retained separately; only temporary profile/process identity is normalized in comparisons.

Native control evidence records all 93 exact contracts per actual after DOM. `native-control-coverage.json` aggregates instantiated consumers across captured scenes and lists uninstantiated gaps. Ordinary owner `ok` does not imply native-contract qualification. Before raw missing metadata stays intact and requires exact source-bound after path sidecars.

Native consumer `elements` are bound by exact observed DOM path to the primary v2 node dictionary; runtime evidence carries only nodeId/scopeNodeId/bindingIndex references for these actors. Missing or duplicate paths cannot inherit or use inline claimed ownership as fallback. Independent policy/semantic/compiled contract SHA bindings stay at runtime top level.

The dictionary also records only the finite UI attributes/pseudos required by native consumer contracts and `previousElementPath` for exact adjacent-element relationships. The actor, scope, required ancestors and previous siblings share the same primary dictionary; no input values or private text are collected for this check.

Optional `--version-input /path/to/public-version.json` is an explicit isolated fixture input authorized for comparing the same combined build identifier. Input v/version/algo must equal the actual source; all other VERSION fields, including the source file map, remain actual. Only combined is composed from the declared common input. Default raw mode intercepts no version module. Both sides must use the same input SHA, recorded in the environment; mismatched modes/bytes or input drift fail comparison. Only the localhost seat/version.js response composes that declared combined value; root version.json remains actual, so version-notice/settings continue to see the original version; source/public version.js/version.json and their SHA remain actual, and raw About images/hash differences must be preserved in a separate raw run. Missing/malformed input fails before browser start. No DOM text replacement or non-version resource override is used.

`--mode version-check --version-input ...` captures six explicit diagnostic scenes: the four minimal pairing/chat scenes, actual About, and the rescue commands opened through the real summary click (two synthetic whitelisted rows). It is not a full visual qualification. Native receipt IDs come independently from actual SHA-bound policy bytes and compiled contracts; a consistently shortened runtime binding/record list cannot define its own expected set.

`--mode interaction-check --width 390 --audit` is a short eighteen-scene diagnostic replay covering the nine separate reply/source-quote/robot hover, keyboard focus-visible and CSS-active states. Each state samples fresh native evidence before clearing its current pseudo state and binds actor/scope refs to that state’s primary dictionary; no idle snapshot is reused. It does not qualify the full matrix.
