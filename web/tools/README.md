# Web component CSS contracts (#577)

The Web owns `styles/order.json` and its ordered CSS modules. `build.mjs`
concatenates them into the existing single `public/brand/base.css` request;
`webComponentCss().sources` maps generated lines to source modules. The Web-owned
`brand/lang.js` is the language component source. Other brand assets remain
byte-identical to the shared upstream. Generated public assets are not their own
protection against an upstream sync.

Design globals contain tokens only. Exact reset/accessibility/selection/gesture
contracts live in `technical-policy.json`, with ordered declaration digests.
Component appearance belongs to a registered semantic owner, never a default
module/page owner. `component-ownership.json`, explicit element metadata and
reviewed exact roots are the source of `css-node-owners.json`; check, not write,
is the acceptance operation. Private busy/quote/choice/action tokens cannot
reference another owner's private tokens. Standard actions may share within their
own family; a nested independent action is different from skinning the busy or
quote root itself. Modifier-only borrowing is also forbidden.

Run from the repository root:

```sh
node web/tools/css-namespace-lint.mjs --output reports/namespace.json
node web/tools/generate-node-owners.mjs --check
node web/tools/sync-native-contracts.mjs --check
node --test web/test/p577*.test.mjs
```

These top-level `*.test.mjs` files run through ordinary `web-unit` discovery.
No claim is made that the standalone impact/full screenshot command is already a
central p44 release hook. Integration/release owner must invoke the full matrix,
compare and impact commands for the exact candidate, retain receipts, and require
their qualification independently; a local unit PASS is not a release PASS.

## Impact evidence

```sh
node web/tools/css-impact.mjs \
  --before /immutable/before/public --after /immutable/after/public \
  --runtime-dir /evidence/before/runtime --runtime-dir /evidence/after/runtime \
  --before-owners-dir /evidence/before-owners \
  --coverage /evidence/coverage.json --scope /evidence/planned-owners.json \
  --visual-receipt /evidence/full-visual-receipt.json --output reports/impact.json
```

`--scope` is an explicit nonempty registered namespace array or `{owners:[...]}`.
Absent scope reports `scopeChecked=false`, never an implicit all-components PASS.
Observed/conservative owners outside the plan fail with scenes and examples.

Coverage schema 1 requires exact `scenarios` keys
`scene|width|height|theme|lang|state` and explicit `unusedRules`. An unused rule is
not an arbitrary reason-string exemption: only `not-instantiated` or
`media-inapplicable` with source hashes and the exact, unique set of both-side
snapshot/source/screenshot SHA references can qualify. Uncovered selectors fail.

Runtime files are individual JSON or `.json.gz` snapshots. Aggregate arrays are
rejected. The CLI holds one snapshot and one owner-map shard at a time, aggregates
rule→owner→scene/count, and writes per-snapshot compressed evidence locators
rather than copying every descendant into the report. Original snapshot SHA,
rule JSON pointer and node dictionary pointer retain complete provenance.

Dictionary v2 `nodes.n0` contains full `{nodeId:'n0',path,...}` labels. Every
reference in rules and element lists must have an own, nonempty string `nodeId`
resolving to the dictionary. Keys must equal nodeId; paths must be unique. Inline
fields cannot override dictionary ownership, and unknown/falsy IDs cannot fall
back to inline data. Complete legacy inline `{tag,path,...}` nodes are accepted
only without a dictionary or explicit nodeId. `canonicalRoot` is a separate
semantic proof descriptor, not a node reference. `assetPath` is the canonical
resource path without the served seat prefix.

Before metadata is attributed through source-bound, per-scene owner shards,
never by changing the baseline DOM. The shard index binds each compressed shard
and its exact after snapshot SHA. Raw missing-owner evidence remains present;
unresolved entries fail. Actual neutral content resolves through independently
reviewed canonical roots, not a blanket body-owner fallback. Only exact native
document-resource boundaries may be attributed technically.

HTML/JS/other asset changes conservatively include every observed CSS consumer,
including unchanged selectors. They require independent full visual evidence;
`changedRules=0` is not a zero-impact result. Visual receipts require complete
source bindings, the exact scene set, full matrix scope, all 16 fixed configs,
and zero differences in actual decoded PNG pixels. A boolean summary cannot
replace pixel verification. `fullVisualReceiptRequired` and
`fullVisualReceiptQualified` are separate: an unrun receipt remains false even
when this branch did not require it. `cssConsumerEvidenceQualified` is narrower
than whole-page visual qualification; `ok` is not a release verdict.

At-rule definition changes (`@keyframes`, `@font-face`, `@property`) and relative
cascade order changes are listed in `unsupportedCssChanges`. Without a dependency graph, all actual
snapshot consumers are conservatively affected, and the actual full visual
receipt is mandatory. Missing/failed evidence fails closed. Unexplained CSS byte
changes use the same conservative policy; they cannot claim zero impact. The
report remains explicit about this imprecision even if all real pixels pass.

## Parser and cost limits

`css-syntax.mjs` is a structural CSS parser with quote/comment-aware offsets,
at-rule contexts and declaration parsing. It rejects unsupported nested styles.
It is not a complete standards CSS grammar. Factory checks are JavaScript pattern
guards, not a full JavaScript AST; dynamic behavior additionally needs actual DOM
owner/semantic auditing and the full visual matrix. Vendor sources are an
explicit boundary, not proof that their runtime descendants are irrelevant.

The frozen refactor composes modules at their intended cascade positions into one
existing request. Relative rule-order changes are conservatively reported by
impact; equivalence still requires the actual visual gate. It incurs size:
base.css 26,409 → 708,667 bytes; reproducible gzip approximately 6,775 → 45,213
bytes. Most growth is explicit owner-opt-in copies for native/focus/typography
contracts plus per-owner private fallback tokens. This prevents cross-owner token
coupling but is not zero cost. Future work can author a compact, reviewed
per-owner contract table and generate equivalent rules with source maps; avoid
introducing a shared appearance token or a broad owner-descendant selector to
save bytes. That optimization needs a fresh immutable build and full pixel gate.

Historical stream interface smoke (two real v2 snapshots, old freeze2, not full
qualification) used roughly 452 MB peak RSS. Full matrix scale/performance and
current qualified before-owner shards still require the actual run. No reduction
of hidden descendants, pseudo-state coverage or missing-owner checks is allowed
to obtain a smaller report.

## Freeze4 closure: animation symbols and rescue toolbar

`animation-ownership.json` now registers 52 private keyframe definitions and 72
named references in 70 declaration rules. Five shared definitions became 15
owner-private definitions; the other 37 received their own owner's name. The
frame bodies retain their audited original byte SHA; name-normalized consumer
values preserve durations, delay, easing, iterations, fill mode and trigger/media
contexts. `animation-contract.mjs` checks definitions, consumers, frame-local
private token references, unknown names/tokens, duplicate definitions and the
finite literal `animationName` equality shape used by `busy.js`. This is not a JS
AST or a Web Animations API ownership analysis. Generic runtime-generated names
need an explicit supported contract; they cannot silently qualify. Keyframe
bodies are no longer skipped by namespace lint. Unregistered definition at-rules
such as a new `@property` fail pending a real owner contract; font-face remains
the separately bounded typography resource policy.

The command toolbar in settings uses `rscmdbar/rscmdact`, not reply's
`rmbar/act`. Its equivalent base rules are copied at the original cascade
positions, including the later SVG alignment rule. Its private spacing/size
variables use `--aj-rescue-command-*`; none reference reply's rm-gap/rm-icon/
rmbar-base or aj-reply tokens. The only producer/query is cmdrun's row factory;
run/terminal callbacks, disabled state and signed control calls are untouched.
The legacy reply classes remain unique to their actual role where appropriate.
`classOwners`, exact contextual roots and generated IDs use the same semantic
source. Historical unseparated-prefix guesses remain labelled rejected history;
the old rmbar correction records its stale evidence explicitly and provides
current source evidence separately, rather than rewriting old history.

Normal discovered tests include `p577-animation.test.mjs` and
`p577-rescue-isolation.test.mjs`. The latter compares old/new actual browser
computed styles and geometry at 390/1440 widths, light/dark, in five states, and
rejects reintroducing reply classes on rescue DOM. The immutable original menu
fixture retains the previous settings overrides for that comparison. Tests also
execute busy's actual pop callback with matching/unrelated animation events,
lost-event fallback and reduced motion. These targeted controls do not replace
the full App pixel matrix.

Historical P154_ONLY=k remains a recorded business-test gap: original #576 and
the candidate both time out at the identical six-robot update wait on line317
with the original timeout. Candidate pop/event-loss/bow assertions preceding
that wait passed; no waiting threshold or business assertion was changed.

Animation manifests do not authorize their own consumers. `auditAnimations`
requires the separate normative component registry and derives the selector's
actual semantic owner through reviewed class/ID/exact-selector contracts. That
result must be unique and match both the manifest consumer and keyframe owner.
Unknown or ambiguous selectors fail even if the manifest asserts a known owner;
known quote selectors falsely registered as busy are covered by negative tests.


## Freeze5 native evidence checkpoint (not structural completion)

Public freeze5 remains unchanged at combined
`8b03677ca5dcceaf858228b65fc754d67ed2e571a74b34d7a49e0fb5751132b9`.
The costs above are freeze5 actual bytes and Python gzip with `mtime=0`.
Reader-context controls now opt in themselves as reader/prose/inline-component;
dialog footer controls explicitly opt in to `aj-dialog-action`, a role in the
existing dialog family. Bots field/navigation/row actions and flow slots have
separate context tokens. This does not establish whole-App pixel equivalence.

The stricter subject guard intentionally exposes **66 unowned rules**. Reading
an owner's private token cannot authorize an appearance declaration; neither can
the last or outermost known ancestor. These rules remain a failing structural
gate pending their source-reviewed composition/typography/role closure. Older
0-unknown receipts describe the earlier checker, not current qualification.

`native-control-contracts.json` binds the exact source-derived authored rule set by exact file, selector,
context, occurrence and raw body SHA. Independent normative scope roles supply
actor meaning; the policy cannot claim its own owner. Scope class/ID tokens are
matched positively, not as substrings or quoted attribute contents, and cannot
cross another known content/control boundary. Full actual consumers remain
required: an empty or partial matrix cannot qualify a dormant contract.

After runtime snapshots must carry `nativeContractBinding` with schemaVersion,
sourceCombined, policySha256, semanticSha256, compiledSha256 and the exact sorted
source-derived contractIds. `nativeControlEvidence.records` must contain each contract once,
its actual contextActive, match count, and all elements as
`{nodeId,scopeNodeId,bindingIndex}` references to the authoritative v2 dictionary.
The sampler maps original subject/scope DOM paths to those exact IDs. Impact
rechecks the source binding, dictionary nodes, direct actor, independent class
meaning, actual subject/scope selector, ancestor relation, intervening boundaries,
media state and count. It recomputes observed/missing coverage across after
snapshots; summary `qualified` or `complete` booleans confer no authority. Missing
contracts and conflicting consumers fail; before legacy DOM still uses its
separate verified after-path sidecar and is not required to gain new metadata.
Ordinary ownerOK, native coverage and the full 16-configuration visual receipt
are independent qualifications.

Offline native matching deliberately supports a finite selector subset, actual
ancestor/sibling paths, eight safe state attributes and actual focus/hover/active/
disabled pseudos. Unsupported complex positive :is/:where branches fail closed;
:not/:has cannot supply actor meaning. Escaped selector identifiers and unquoted
escaped declaration identifiers are rejected rather than partially interpreted.
Quoted strings/URLs retain their literal contents. This is a bounded structural
CSS parser plus explicit selector contracts, not a complete browser grammar or
JavaScript AST. Full-source dynamic consumer changes still require runtime and
visual evidence.

## Freeze6 composition closure

The checkpoint5 paragraph above is historical: its stricter guard exposed66
unowned rules and correctly failed. Source6 closes those reviewed rules without
a default actor:32 exact contracts comprise19 layout rules and13 same-kind
subcomponent rules. `composition-contracts.json` binds file/selector/context/
occurrence/bodySHA and producer sources; independent `component-ownership.cssRoles`
restricts each role's selectors, purpose and properties. Layout additionally has
a hard property allowlist. A role cannot authorize native controls or a different
role's private tokens, and it never becomes a source for descendant actor lookup.
Source-reviewed remaining slots explicitly retain their actual semantic actor.
The roles are CSS contracts, not new data-owner defaults or new product families.
Dormant legacy brand layouts are labelled without claiming current consumers.

Known composition classes are excluded from ancestor-based actor inference.
A new `.ajpages h1` or `.aj-flow .new-widget` cannot borrow the shell/layout actor.
Actual page h1/h2/subhead/code use7/6/5/2 source-reviewed self-actor sets and their
own private context tokens. Page paragraph spacing requires both a self actor and
`data-aj-page-copy`; the helper only forwards that explicit marker. Error messages
use5 actual owner contexts across6 producer sites. Approval and notice command
classes are independent and preserve the original .fold/ID/business behavior.
Supporting text, guidance callouts, copy decoration and host provenance are
bounded same-kind CSS roles; copying never changes the content node's actor.

Native impact also independently enumerates every current active contract's
actual matching button/select subjects in the authoritative dictionary and
requires exact equality with reported element refs/counts. A second matching
consumer cannot be omitted, regardless of its owner. This complements validation
of each reported subject/scope and does not turn an incomplete snapshot dictionary
or incomplete full matrix into a qualification claim. New controls retain a
positive complete-two-consumer case as well as good/wrong-owner omission negatives.

## Freeze7 purpose corrections

Actual capture controls `.cam` belong to recording, distinct from the pairing
QR camera page; local filesystem grant fields belong to file, not media viewer.
The unpaired settings header can contain the existing menu back button and the
exact header/search/settings controls reparented by settings.open(). Their shared
contract is only the existing `visibility:hidden` state. An independent normative
declarationContract rejects padding/background additions even if the per-rule
body hash is forged to match. Canonical root descriptors may name a registered
subowner only within their registered family; when specified, direct markers must
match that exact actor as well as its family. Same-family media cannot replace
file, and settings-control cannot replace the settings root. Root caches remain
derived/checkable from this single semantic source.

## Source8 font-size states

The three actual font controls keep their distinct actors: minus/plus are
settings-control, reset is settings. A dedicated exact-ID disabled-opacity slot
permits only the original `opacity:.35`. The three original focus clones are
split at the same cascade positions with zero-specificity self-owner filters;
step controls retain their tokens, while reset has independent
`--aj-settings-fontstep-focus-*` tokens and identical primitive fallbacks.
Self-owned native-context contracts still require a separately reviewed actor/ID
map and exact declarations; a direct marker alone cannot qualify them. Their
actual source references are independently listed in the normative scopes, and
policy removal/shrinkage fails rather than changing the expected coverage.
Historical freeze7 bound93 entries; source8 has96 actual entries derived from source/compiled bytes. This
number is a receipt fact, never an authorization constant. Runtime dictionaries
must prove each exact actor ID, and ordinary owner qualification remains separate.
Tests exercise actual renderFont states, keyboard `data-kbd` focus (not a ring
masked by touch policy), original token coupling, isolation and four-view geometry/
RGBA evidence. They do not substitute for the full source-bound matrix.

## Source9 writer migration evidence

`css-token-writers.mjs` compares the actual before/after CSS consumer graphs with
CSS definitions and literal JavaScript custom-property setters. Impact therefore
catches future private x→y renames with a stranded x writer, not only the legacy
select-padding case. A JS getter does not conceal disappearance of a CSS consumer.
Quoted CSS strings/comments are not consumers/writers. The small legacy fixture
is independently reproducible from0f9619b actual CSS structure; it is only a normal
unit invariant for the old interfaces, not the authority for future migrations.

The actual source audit found one orphan writer: compact mail-role's8px override
still wrote the retired select token. It now writes the channel-private reader's
name at the same local element scope. The textarea token match in a comment was
not a writer.45 literal JS custom-property setter sites remain;3 dynamic sites
are independently reviewed by exact source SHA:2 forward computed names/values
into detached, redacted SVG copies, and the theme helper emits only37 actual
sanitized palette/send/dot keys. `token-writer-policy.json` records those concrete
source semantics, not an arbitrary global setter permission. New sites/source
drift remain unresolved and fail impact qualification. This finite pattern scan
is not a JS AST or a full CSS-in-JS/JS-dataflow proof; counts alone confer nothing.

`writerMigration.literalLossQualified` and `afterDynamicReviewQualified` describe
these bounded checks. They do not replace source-independent semantic consumers,
full native coverage or the complete visual gate. A fresh private-token negative
has valid scope/source/PNG evidence and still fails solely on its orphan writer.

Father sprite/hint symbols and their element-scoped sheet now use header-private
names; the only receiver isorb. This corrects a mistaken busy attribution, not a
new shared animation incident or a global appearance-setting accident. Original
frame SHA/timings are retained and logo.js restores byte-for-byte after reversing
only the private token rename. Both reader toolbars also require a directreader
actor even beneath a correctly marked parent; ancestor attribution cannot hide a
missing technical selection scope.
Dynamic review is independently closed over the actual approved source shape:
theme map literals and prop calls derive the complete key set, and receiver/
producer structure decides finite-theme versus detached-copy meaning. Policy
kind/list fields must match those results; retaining the real source SHA while
shortening the list, duplicating a key or relabeling the helper still fails.
The `.fontstep` physical numeric-stepper also has two existing legacy Jev
minus/plus producers. Source9's independent HTML census therefore binds the actual
five IDs/actors (four step controls plus the settings reset); source8's narrower
three-ID evidence remains historical. Unknown extra IDs/wrong actors still fail,
and opacity/focus declarations and product appearance are not broadened.
