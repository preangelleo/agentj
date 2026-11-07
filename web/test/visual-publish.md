# Required publication visual coordinator (0033)

`node web/test/visual-publish.mjs --check` checks source prerequisites only. It always reports `qualified:false`; a successful preflight is not a visual qualification.

The normal entry runs one frozen baseline and one candidate critical matrix, then the existing strict RGBA comparator once. Within-page stable frames remain required. The earlier four full cold-start matrices were an added proof cost, not a 0033 requirement; prior nondeterminism evidence is retained. Olares output is below OLARES_RUN_OUT; --out permits a fresh directory. Nothing is installed. Synthetic clients only. The baseline is pinned by Git SHA/publicCombined and is never advanced to match a candidate.

This first integration is deliberately **not ready**. The #577 sources are not yet merged. Its same-VERSION/time CLI flag, fixture path, capture receipt field and real About receipt field are explicit pending values in the baseline contract. They must be adapted to the actual frozen #577 interface and proven with real matrices. No pending marker may be removed merely to obtain PASS. The proposed `--comparison-inputs` name is an interface placeholder, not a claim the current #577 runner implements it.

The final same-input producer must bind its fixture SHA/policy (`fixed-interface-data-only`) in both capture manifests and separately retain real About screenshots per configuration with actual source combined/hash. Production build identities stay real. Current expected manifest field names are contract-selected and pending; final API adaptation must match the real producer. Every capture row must have a real PNG, stable RGBA hash, correct dimensions and exact scene/configuration coverage, including when both sides are missing the same PNG. Existing #577 comparison must report zero changed pixels. Shared source/fixture changes during capture fail.

The ship suite is mandatory before old receipt reuse. Missing configuration, source, pending interface, old receipts, absent/changed artifacts or nonzero comparison fail. No visual known_red is allowed. Formal B, resumed external stages and B-only previews enforce the same requirement. A valid cached proof requires unchanged public tree, coordinator/baseline, all tracked fixture inputs and every artifact hash, so identical inputs do not repeat the matrix.

Contract/rejection tests do not constitute browser evidence:

```
node --test web/test/p0033-publish-gate.test.mjs
python3 -m unittest discover -s .jarvis -p 'test*ship*.py'
```

After #577 arrives, actual publish-critical normal execution and real one-pixel/missing-image/configuration negative controls remain required before release readiness.

Scope correction: publication uses 42 critical scenes × 12 configurations (390/834/1440, light/dark, zh/en), 504 rows minus eight existing product iOS/desktop N/A entries = 496 PNG per side. Full #577 all-page/93-native-contract qualification remains separate. No four full cold-start matrices are required by 0033. Each existing capture still settles to two equal RGBA frames; a fixed-input one-pixel difference still fails. Prior nondeterminism evidence remains intact; no resampling to choose a green baseline. Dashboard account uses real UI plus a synthetic local session through `p0033-dashboard-adapter.mjs`; C assets must remain byte-identical, with no Dashboard deployment/migration or #569 changes. The adapter does not take screenshots or mask elements.

Proposed runner seam (still pending #577, not claimed implemented): `matrix.mjs --profile publish-critical --dashboard-source <frozen-public> ... --comparison-inputs <fixture>`. The profile must skip unrelated setup flows, emit exact required-scenes and config receipts, and preserve source/fixture/environment binding. Dashboard rows use `sourceScope=dashboard-preserved` plus the frozen asset fingerprint, not fictitious aj-component ownership for an unchanged C product. The adapter supplies metadata, not PASS. Fresh remote environment validation before cache reuse remains an unresolved seam and must be completed before clearing readiness.

The late #369 automation page is explicitly required as wallet-automation; legacy-tasks is not a substitute. The business-before baseline will be frozen only after its Host and UI commits are integrated; it does not replace the original #576 formal baseline for #577 pure-refactor qualification.

Frozen business-before: 447cbfb7940f3de148478c88543d05f6e466a3eb, publicCombined 707727abf3f16a1b84adee7da8113154d795871397477deddaea8d9fbc6c8061. Standard Node 26 build reproduced the committed public bytes with no diff; no version bump or empty public commit. It contains all late business source including #369 Host/UI. The original #576 formal 0f9619bd / 562d9ec4 / Worker 93e142a1 is recorded separately in pureRefactorBaseline and remains required for #577 pure refactor qualification.

The existing ship suites.web command now actually runs p369-agentj-task.mjs at its tail; it is not a separate proof gate and has no known_red entry.
