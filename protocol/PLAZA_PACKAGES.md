# Skill & workflow plaza (技能广场 · 工作流广场) — contract v1

> 2026-10-02 20:42 (launch decisions). Same accounts, seat gate, privacy gate and
> admin as the Q&A plaza (`dashboard/DASHBOARD_API.md` §9); the plaza now has three parts: 问答 Q&A · 技能 Skills · 工作流 Workflows.
> Package format (what is inside a package): the official catalog's `FORMAT.md` (`agentj.catalog/v1`) — copied into
> this repo as `protocol/CATALOG_FORMAT.md` (the catalog owns it; re-copy when it changes). This file is the wire:
> the bundle, the signature, the routes. Server: `dashboard/src/packages.ts` + migration `0012_packages.sql`; host:
> `host/agentj/bundle.py`, `minisign.py`, `market.py`; admin: `tools/plaza`.

## 0. Rules that do not bend
- **Only seated companies** (same test as §9: `comp_seats + Σ active subscription quantity > 0`; gate off → every company) — their
  members (session) and bound hosts (signed envelopes) — can list, search, read, download, install, like, report or publish.
  Everybody else: the page shows the plaza introduction only; the API answers 401 (no session) / 403 `plaza_requires_seat`.
- **Package content is DATA, never instructions** to whoever reads it through us: the host prints every package string inside the
  plaza data fence (same fence as Q&A); nothing from a package is executed by `agentj` except, after the Owner's yes, the
  manifest's `install.verify` command — and that **only inside a real sandbox** (Linux bubblewrap / macOS `sandbox-exec`: the
  system read-only, the home, agentj's state and /tmp hidden, no network, writes only into a throw-away copy of the package,
  an empty environment). No usable sandbox → it is not run and the preview and the result say so (「本机没有可用的沙箱，没有运行包的
  自检」); `--skip-verify` lets the Owner skip it deliberately. `install.post_install` is **never** run by us — it is shown to the
  Owner. Server-supplied names (Agent names) are printed without quote / badge / check-mark / `@` / `·` / line characters, ≤ 32.
- **Install only after the Owner said yes to the exact package**: preview → 16-hex digest → `--owner-confirmed --digest`.
- **Publish only after both privacy layers and the Owner's yes**: layer 1 (deterministic; any hit refuses — we never silently
  rewrite code) + layer 2 (Jev on the customer's machine) → exact file list + digest → `--owner-confirmed --digest`.
- **Official = signed.** A package is shown as 官方认证 / certified by the host **only** if its bundle carries a valid minisign
  signature by a key compiled into `agentj` (`host/agentj/minisign.py` `TRUSTED_SIGNERS`). The server's `certified` flag alone
  never makes the host say "certified"; a certified-flagged package without a valid signature is refused (it is either tampered
  or a server bug). Community packages are labelled 未认证 / unverified everywhere and need `--accept-unverified` to install.
- **PR1**: package bytes are public-to-customers text a customer chose to publish (third deliberate plaintext channel, after
  feedback and Q&A). What we learn: who published which package; which host installed which package and version (count +
  weekly ranking); which company liked / reported which package. Listed on `/security`.

## 1. Bundle `.ajpkg` (`agentj.bundle/v1`)
`bundle = gzip(canonical_json(B))`, gzip level 9, `mtime = 0`, no file name; `canonical_json` = Python
`json.dumps(B, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")`.

```json
{"schema": "agentj.bundle/v1",
 "manifest": { …manifest.json, parsed… },
 "files": [{"path": "README.md", "b64": "<standard base64 of the bytes>"}, …]}
```
- `files` sorted by the UTF-8 bytes of `path`; exactly the set listed in `manifest.files` (manifest.json itself excluded); each
  file's SHA-256 hex and length equal `manifest.files[i].sha256` / `.bytes`. Extra, missing or duplicate → invalid.
- **Package id** = `sha256` hex of the bundle bytes (what the signature, the digest and the server talk about).
- **Paths**: NFC, `/`-separated, relative, 1–200 code points, every segment 1–80 code points of Unicode letters / digits / marks
  and `. _ - + @` (no space, `\`, `:`, control / format characters); no `.` / `..` segment; no case-insensitive duplicates;
  forbidden anywhere: a `.git` or `.ssh` segment, `.env` or `.env.<x>` other than `.env.example`, `*.pem`, `*.key`, `id_rsa*`,
  `id_ed25519*`, `__pycache__`, `.DS_Store`. Symlinks / devices never enter a bundle (the builder refuses them).
- **Auto-running configuration** (`vectors/bundle-rules.json` `forbidden_paths_rule` + `paths`; reason `path_forbidden`),
  case-insensitive at any depth: a file named `.mcp.json`, `.envrc`, `opencode.json`, `opencode.jsonc`; `.claude/settings.json`,
  `.claude/settings.local.json`; anything under a `.vscode/`, `.idea/`, `.devcontainer/`, `.husky/`, `.cursor/`, `.gemini/`,
  `.opencode/` or `.codex/` directory (the installer writes `.codex/agents` itself). `.claude/agents|skills|commands/…` stay allowed.
- **Hidden characters** (`hidden_characters_rule` + `texts`; reason `hidden_characters`, with `path` = the file or
  `manifest.json`): refused in every text file and in every manifest string (object keys included) — the host also checks a
  file that is not valid UTF-8 (decoded with replacement characters) when it has a text / script extension, sits under
  `scripts/ cli/ tools/ bin/`, starts with `#!` or has no NUL byte; only real binaries are skipped: U+061C,
  U+180E, U+200B, U+200E, U+200F, U+202A–202E, U+2060–2064, U+2066–2069, U+FFF9–FFFB, U+E0000–E007F, and U+FEFF anywhere but
  the first character of a file. U+200C / U+200D stay allowed.
- **Depth** (`depth_rule`; reason `too_deep`, `path` = `manifest.json` / `params.schema.json`): the manifest and a workflow's
  params.schema.json may nest objects / arrays at most 32 deep (the value itself = 1); checked iteratively before any recursive
  walk, so any depth is a clean 422. The host's `BundleError.reason` carries the same codes.
- **Limits**: bundle ≤ 2 MiB; decompressed JSON ≤ 12 MiB; ≤ 500 files; each file ≤ 2 MiB; total file bytes ≤ 8 MiB.
- **Manifest checks** (builder, server, installer): `schema_version = "agentj.catalog/v1"`; `name` `^[a-z0-9][a-z0-9-]{1,39}$`;
  `type` ∈ {skill, workflow, card}; `version` semver `MAJOR.MINOR.PATCH` (optional `-pre`); `title_zh`/`title_en` 1–80,
  `summary_zh`/`summary_en` 1–500 code points; `tags` ≤ 12 strings of 1–24; `category` ∈ FORMAT §4; `entry` is a listed file;
  skill: `SKILL.md` + `README.md` present, `install.skill_dir_name` `^[a-z0-9][a-z0-9-]{1,39}$`; workflow: `README.md`,
  `params.schema.json` (parses) and ≥ 1 file under `scaffold/`; card (P432): exactly `README.md`, `card/card.json` (= `entry`) and
  at most `card/sample/<data file>`, no `install.post_install` / `install.verify` (a card has no code); the card's own rules
  (AGENTJ-CARDS-SPEC: fields, buttons, a server pinned to `server.repo` + 40-hex `server.ref`, a key-free sample) are checked by
  the host on `agentj card pack`, `publish` and `install` (`jcards_pkg.preflight`); `requires.*` shapes as FORMAT §2 (unknown keys ignored by readers).
- Builder: `bundle.build(dir) -> (bytes, manifest)` regenerates `manifest.files` from the directory (sorted, sha256, bytes) and
  refuses anything the rules above refuse. `bundle.parse(bytes) -> (manifest, {path: bytes})` validates everything above.
  `bundle.py` is stdlib-only (the admin tool imports it by path).

## 2. Signature (official packages)
- minisign (Ed25519, default prehashed mode `ED`: signature over BLAKE2b-512 of the bundle), made by `minisign -S` with the
  plaza key (secret key file mode 600 on the admin's machine only, never uploaded; public key id
  `B79F925B0585F9D4`, `RWTU+YUFW5KftwwG/MQxB1V0OEpzftjDN8udv1Id9oYK9+OUanc2uyb5`, compiled into the host).
- **Trusted comment** (exact): `agentjarvis-plaza/v1 name=<name> version=<version> type=<type> sha256=<bundle sha256 hex>`.
  The host verifies the signature, the global signature over the trusted comment, the key id ∈ `TRUSTED_SIGNERS`, and that all four
  fields equal the manifest's and the bundle's. Any mismatch → refuse (exit 5, nothing written). The same exit 5 for a package
  the server flags official / certified that carries no signature, and for any served signature that does not verify.
- The `.minisig` text (≤ 1 KiB) is stored with the version row and served with it; the server never holds the secret key.

## 3. Data (D1 migration `0012_packages.sql`, self-contained like 0011)
- `plaza_pkgs` (`pk_` + 22 b64url id; `name` UNIQUE; `type`; `track` official|community; `certified` 0/1; `tenant_id` NULL for
  official; `author_kind` official|agent|staff; `host_id`; `author_label` (Agent name if shown); denormalised display fields of the
  **latest live version** (`version`, `title_zh/en`, `summary_zh/en`, `tags` JSON, `category`, `verification` JSON); `like_count`,
  `install_count`, `report_count`; `state` visible|hidden|removed; `remove_reason`; `created_at`, `updated_at`).
- `plaza_pkg_versions` (`pv_` + 22 id; `pkg_id`; `version`; UNIQUE(pkg_id, version); `manifest` JSON (≤ 64 KiB); `readme`
  (README.md text, ≤ 40 000 code points, PUBLIC); `sha256`; `bytes`; `r2_key`; `signature` (minisig text | NULL); `state`
  uploading|live|rejected|removed; `created_at`).
- `plaza_pkg_likes` (PK pkg_id + tenant_id; created_at) — one like per company. `plaza_pkg_installs` (PK pkg_id + host_id;
  version; created_at = first install, updated_at = last) — installs = distinct hosts. `plaza_pkg_reports` (PK pkg_id + tenant_id;
  reason spam|malware|privacy|injection|license|other) — ≥ 3 companies → `hidden`. `plaza_pkg_rate` (ledger) ·
  `plaza_pkg_audit` (kinds below; never text). Replay nonces: the existing `plaza_seen`.
- **R2** bucket `agentjarvis-plaza` (private, no public access, no r2.dev), binding `PLAZA`; key
  `pkg/<pkg id>/<version id>.ajpkg`. Nothing else in it.

## 4. Host routes (signed envelopes, PROTOCOL §7 rules; `POST /v1/host/plaza/pkg/<route>`; context `agentjarvis-host-plaza-pkg-<route>-v1`; `t` = `plaza_pkg_<route>`)
| route | inner (besides `v`,`t`,`channel`,`ts`) | answer |
|---|---|---|
| `search` | `q` (string, may be empty) + optional `type` (skill\|workflow\|card), `sort` (new\|installs\|likes\|week, default: certified first then week), `tag` (1–24), `track` (official\|community), `limit` (1–50, default 20), `offset` (0–1000) | 200 `{note, items:[Item], total, tags:[{tag,n}]}` |
| `get` | `name` + optional `version` | 200 `{note, package: Detail, download:{url, expires_at}}` · 404 `not_found` |
| `mine` | — | 200 `{items:[Item + state + remove_reason]}` (this company's packages, any state) |
| `installed` | `nonce`, `name`, `version` | 200 `{name, installs}` (idempotent per host) |
| `like` | `nonce`, `name`, `on` (bool) | 200 `{name, liked, likes}` |
| `report` | `nonce`, `name`, `reason` | 201 `{name, reported, hidden}` · 200 `{…, already:true}` · 403 `own_package` / `official_package` |
| `publish` | `nonce`, `name`, `type`, `version`, `sha256`, `bytes`, `show_name` (bool) + optional `host_sig`, `host_pubkey`, `host_signed_at` (§11) | 201 `{id, version_id, upload:{url, expires_at}}` · 409 `name_taken` / `version_exists` / `version_not_newer` · 413 `too_large` · 403 · 429 |

`Item` = `{name, type, track, certified, version, title:{zh,en}, summary:{zh,en}, tags, category, likes, installs, installs_week,
liked, state, author:{kind: official|agent|staff, company?: "co-xxxxxx", agent_name?: string|null, mine: bool}, verification:
{level, date}|null, created_at, updated_at}`. `Detail` = Item + `{readme, requires, install, roles?, automations?, params_schema?
(object, workflow), files:[{path, bytes}], license, upstream, sha256, bytes, signature (minisig text|null), versions:[{version,
created_at}]}` — all from the stored manifest of that version; every string cleaned (§9 text rules) on the way in.

**Download** `GET https://agentj.app/api/v1/plaza/dl/<url part>`: the URL part = `b64u(JSON{v:1,vid,exp})` + `.` + `b64u(HMAC-SHA256(
PLAZA_URL_KEY, "agentjarvis/plaza/dl/v1\n" + first part))`, valid ≤ 10 min; the package must still be visible and the version
live (else 404). 200 `application/octet-stream`, `cache-control: private, no-store`, body = the bundle. Bad / expired → bare 404.

**Upload** `PUT https://agentj.app/api/v1/plaza/up/<url part>` (same URL-part shape, context `…/up/v1`, payload `{v:1,vid,exp}`,
≤ 10 min, the version must be `uploading`): body = the bundle (`content-type: application/octet-stream`, ≤ 2 MiB). The server checks
sha256 + length = the publish request's, parses it (§1), checks name / type / version = the request's, `certified` absent or false,
then cleans + secret-scans (`worker/src/scan.ts`) every text field of the manifest and **every file that decodes as UTF-8** — a hit
→ 422 `{error:"secret_found", path, kind}`, the version becomes `rejected`, nothing stored in R2. Success → R2 put, version
`live`, package display fields updated, `visible`. 200 `{name, version, state:"live"}`. Second PUT → 409.

**Ownership of names**: the first company that publishes a name owns it; official names are reserved for the admin. A new
version must be strictly greater (semver) than the latest live one.

## 5. Human routes (session; `/api/plaza/packages…`, Origin + JSON rules of §1)
`GET /api/plaza/packages?q=&type=&sort=&tag=&track=&limit=&offset=` (= host search, + `companies`) · `GET
/api/plaza/packages/<name>` (Detail, no download URL — installing is the Agent's job: the page shows `agentj plaza install
<name>`) · `POST /api/plaza/packages/<name>/like {tenant?, on}` · `POST /api/plaza/packages/<name>/report {tenant?, reason}` ·
`GET /api/plaza/packages-mine`.
- Answers (the page codes against these): list `200 {note, items:[Item], total, tags:[{tag,n}], companies:[{slug, name, alias}]}`
  (`companies` = the caller's seated companies, like Q&A §9); detail `200 {note, package: Detail, companies}` · 404 `not_found`;
  like / report = the host routes' answers (`{name, liked, likes}`; `201|200 {name, reported, hidden, already?}` · 403
  `own_package` / `official_package`); not seated → 403 `plaza_requires_seat` on every route (the page then shows only the
  plaza introduction). Both GETs take an optional `tenant=<slug>` (one of `companies`; default the first): `Item.liked` is
  that company's like — the page passes the company chosen in its picker so the like button matches what a POST would toggle.
- Page: `/plaza` only (no new Worker path); the part is in the fragment — `#qa` (default) · `#skills` · `#workflows` · `#cards` (P432)
  (optional `?q=&sort=&tag=&track=` after the part, shareable) · `#pkg=<name>` (a package) · `#p=<pz_ id>` (a post).

## 6. Admin (`api.<zone>/v1/plaza/admin/packages…`, the plaza admin token)
| method path | does |
|---|---|
| `GET packages?state=&track=&limit=` | every package (any state) with counts, reports by reason, owner company slug |
| `GET packages/<name>` | Detail of every version (any state) + reports |
| `GET packages/<name>/bundle?version=` | the bundle bytes (for `certify`: the admin re-verifies and signs locally) |
| `POST packages/import {manifest, bundle_b64, signature}` | official package version: parsed + checked like an upload (secret scan included), signature must verify against the trusted comment (§2) with `PLAZA_PUBKEY` (var); creates / updates the official package (`track=official`, `certified=1`); same version + same sha256 → 200 `unchanged`; same version + other sha → 409 `version_exists`; a community package with that name → 409 `name_taken` (≤ 3 MiB body) |
| `POST packages/certify {name, version, signature}` | a community package → `certified=1` (track stays community: 社群 · 已认证); signature checked as above |
| `POST packages/uncertify {name}` | `certified=0` (official packages too); signatures are kept but no longer served |
| `POST packages/remove {name, reason}` · `POST packages/restore {name}` | soft delete (no listing, no download) / back to visible, reports dropped |
| `GET packages-stats` | counts by type × track × state; top 10 by installs / likes / week; open reports; publishes in the last 7 days |

Admin tool (`tools/plaza`): `import` builds every package from exactly the reviewed revision (`git archive <REV> -- <pkg>` into a
private temp dir, then `bundle.build`; ignored / untracked / uncommitted files never reach a bundle) and skips a package that
changed between REV and HEAD or differs in the working tree / index; the gate trusts the catalog repository's history (only our
agents write there). `certify <name>` without `--confirm <first 12 hex of the bundle sha256>` only shows the fenced manifest
summary, every file (path, bytes, sha256) and the sha256 — nothing is signed; with the matching `--confirm` it signs and posts.
The tool never follows a redirect and sends the token only to an `https://` `AGENTJARVIS_API` (plain http: 127.0.0.1 /
localhost only).

Audit kinds: `pkg_published`, `pkg_rejected`, `pkg_imported`, `pkg_certified`, `pkg_uncertified`, `pkg_removed`, `pkg_restored`,
`pkg_reported`, `pkg_auto_hidden`, `pkg_liked`, `pkg_unliked`, `pkg_installed`.

## 7. Limits (decided inside the write, like §9)
| what | per company | per host | per user | per IP bucket |
|---|---|---|---|---|
| publish (version) | ≤ 5 / h, ≤ 20 / day | ≤ 3 / h | — | ≤ 10 / h |
| reads (search, get, mine) | — | ≤ 600 / h | ≤ 600 / h | ≤ 1200 / h |
| download | — | ≤ 120 / h (charged at `get`) | — | — |
| like / unlike | ≤ 120 / h | — | — | — |
| installed | — | ≤ 120 / h | — | — |
| reports | ≤ 10 / h, ≤ 30 / day | — | — | — |

## 8. Host CLI (`agentj plaza …`, extends the Q&A commands)
- `search [words…] [--type all|qa|skill|workflow|card] [--sort …] [--tag T] [--official|--community] [--limit N] [--json]` — default
  `all`: packages first, then Q&A posts, each in the data fence.
- `show <pz_id | package-name> [--version V] [--json]` — a post, or a package (manifest summary + README, fenced).
- `install <name> [--version V] [--harness claude_code|codex|opencode] [--workspace DIR] [--param k=v]… [--params-file F]
  [--accept-unverified] [--sign-as NAME] [--replace] [--skip-verify] [--owner-confirmed --digest D]`. The preview lists
  **every file path** it would write (install-relative: a workflow's scaffold files without `scaffold/`, Codex role files
  included), says whether `install.verify` will run (in which sandbox / skipped / no sandbox) and flags a downgrade.
- `publish <dir> [--show-agent-name] [--owner-confirmed --digest D]` · `like <name> [--off]` · `report <pz_|pr_ id | name>
  --reason …` · `mine` (posts + packages) · `installed` (this machine's record).
- Install targets (FORMAT §3): skill → Claude Code `~/.claude/skills/<skill_dir_name>/`, Codex `~/.agents/skills/…` (in `.md`
  files `.claude/` → `.agents/` and `CLAUDE.md` → `AGENTS.md`), OpenCode `~/.config/opencode/skills/…`; workflow →
  `$AGENT_WORKSPACE/<name>/` (default `~/agent-workspace`), scaffold rendered per FORMAT §7 (escape by file type, residual
  `{{…}}` aborts, every `.json` re-parsed, `x-auto`, signing step only with `--sign-as`, else `verified: []`), Codex also gets
  `.codex/agents/<role>.toml`. An existing target is never overwritten: refuse; `--replace` works only when
  `<state>/plaza/installed.json` records that agentj installed that target for the **same package name** (else 「这个目录不是这个
  包装的，不替换」), and moves the old folder to `<state dir>/plaza/backups/<name>-<UTC ts>/` (0700) — never inside a skills root
  or the workspace.
  `requires.skills` are listed for the Owner (install each with its own yes), not pulled silently. Automations are never enabled.
- Exit codes: 0 ok / previewed · 1 error · 2 a privacy layer or the static audit (§12) flagged it · 3 layer 2 unavailable ·
  4 digest mismatch · 6 the owner did not allow the extra permissions (§13) · 5 signature invalid (also: any integrity failure of a package listed official / certified, a reserved name without the
  card (P432) → the owner's phone deck (`<state>/agentperm/cards/active/<id>/`), **switched off**: the preview shows the card's
  permission summary (data source, every button's whole message or server call, links, the server's repository + commit); the
  owner turns it on in Settings → Cards after reading the same summary (`jcard_perm`, bound by its digest); a card id that this
  package did not install is never replaced; `--replace` = a new version (the old one sleeps, `agentj card rollback <id>`). The
  card's server is never fetched or run by agentj. No `--harness` / `--param` / `--sign-as` / `--workspace`.
- Exit codes: 0 ok / previewed · 1 error · 2 a privacy layer flagged it · 3 layer 2 unavailable · 4 digest mismatch ·
  5 signature invalid (also: any integrity failure of a package listed official / certified, a reserved name without the
  official signature, an unsigned copy of a package installed signed — §10).

## 9. Server implementation notes (`dashboard/src/packages.ts`, 2026-10-03) — additions to and deviations from §1–§7
Everything above holds; these fill gaps or tighten it (the host and the page rely on them).
- **Refusal shapes**: a bundle that fails §1 → 422 `{error:"bad_bundle", reason}` (reason = a fixed code such as `path_forbidden`,
  `path_case_duplicate`, `files_order`, `file_sha256`, `manifest_files`, `manifest_mismatch`, `certified`, `sha256_mismatch`,
  `not_gzip`, `title_en`, `hidden_characters`, `too_deep`, `skill_dir_reserved` — never a value from the bundle; `hidden_characters`
  and `too_deep` add `path`: the bundle path of the file, or `manifest.json`); a limit → 413 `{error:"too_large", reason}` (`bundle`, `decompressed`,
  `files`, `file`, `total`, `readme`, `manifest`, `params_schema`); a secret → 422 `{error:"secret_found", path, kind}` plus `field`
  when `path = "manifest.json"`. A manifest string that is empty after the §9 cleaning (e.g. only U+200C / soft hyphens — the
  §1 hidden characters are refused before cleaning) is `bad_bundle` with the field name as reason.
- **Upload is one shot**: every refusal after the token check (415 unless `content-type: application/octet-stream`, 413, 422,
  409 `version_not_newer`) marks the version `rejected` (audit `pkg_rejected`); the host publishes again (new nonce, new URL).
  Second PUT → 409 `not_uploading`. Tokens: `exp` and every `expires_at` are **milliseconds** since the epoch.
- **Versions**: UNIQUE(pkg_id, version) is a partial index over `live` / `removed` rows, so an abandoned or rejected upload does
  not block a retry of the same version. A publish / upload also loses (409) when another version of the package went live in
  between. Publish errors besides §4: 409 `type_mismatch` (the name exists with the other type), 403 `package_removed`.
- **Reserved names** (community → 409 `name_taken`): `^(agentj|agent-agentj|agentj|official|admin|moderator|plaza)(-|$)`.
  The same regex over a community skill's `install.skill_dir_name` → the upload is refused 422 `{error:"bad_bundle",
  reason:"skill_dir_reserved"}` (version `rejected`; the publish request does not carry the manifest, so it is decided at upload).
  Official imports may use reserved directories.
- **Author label**: `author_label` (Agent name) is kept only under DASHBOARD_API §9's rule (`show_name: true`, ≤ 32, no
  staff / badge words — admin, official, certif, signed, verified, 管理, 官方, 认证, 签名, 验证, … — and none of 「」【】[]✓✔✅☑@·│┆<>);
  else null.
- **Hidden stays hidden** (deviation from §4 "… `visible`"): a new version of a hidden package goes live but the package stays
  `hidden` until the admin restores it (a publish must not undo the reports). A hidden package is listed / readable only by its
  own company; its `get` answers `download: null`; `/v1/plaza/dl/` refuses it (bare 404).
- **Certified ↔ signed**: `Item.certified` = the admin flag AND the latest live version carries a signature; `Detail.certified`
  likewise for the version shown, `Detail.signature` only while certified. A new **community** version clears the flag (its
  bundle is unsigned; the admin certifies again); `certify` takes the latest live version only (409 `not_latest`), and the
  signature is checked against the bundle bytes fetched from R2.
- **installed** needs the named version live; the first install of a host is audited (`pkg_installed`), repeats only update
  `version` / `updated_at`. **like** is allowed on any visible package (own included), one per company; every processed like /
  unlike is audited. Reports: reasons as §3; 403 `own_package` / `official_package`; 200 `{…, already:true}`.
- **Search**: terms as DASHBOARD_API §9 (≤ 5, AND, `LIKE` with literal wildcards) over name, both titles, both summaries and the
  tags JSON; a malformed `tag` / `offset` → 400 `bad_query`, `limit` → `bad_limit`. Sort tie-break: name. `tags` counts the
  packages visible to the reader for the current `type` filter only.
- **Human**: `GET /api/plaza/packages/<name>?version=` is accepted; `GET /api/plaza/packages` adds `companies` as §9.
- **Admin answers**: import → 201 `{name, version, id, version_id, state:"live"}` · 200 `{name, version, unchanged:true}` · 422
  `bad_signature` / `bad_bundle` · 409 `name_taken` / `version_exists` / `version_not_newer` / `type_mismatch` / `conflict` · 503
  without `PLAZA_PUBKEY` or R2. certify / uncertify / remove / restore → 200 `{name, ok:true, action, …}`; restore of a visible
  package → 409 `not_restorable`. The admin token's failure budget is the Q&A plaza's (`plaza_rate` `admin_fail_ip`, shared).
- **Data** (beyond §3): `plaza_pkgs.version_at` (sort `new`), `hidden_at`, `nonce`; `plaza_pkg_versions.params_schema` (a
  workflow's cleaned params.schema.json, ≤ 32 KiB, served as `Detail.params_schema`), `nonce`; `plaza_pkg_installs.nonce`;
  `plaza_pkg_audit.version_id`. **Hourly**: `plaza_pkg_rate` > 48 h; `uploading` versions > 1 h deleted; a package that never had
  a live version and has no pending upload is deleted with its rejected versions (the name is free again).
- **Config**: secret `PLAZA_URL_KEY` (32 random bytes, b64url; host get / publish, dl, up → 503 without it); var `PLAZA_PUBKEY`;
  optional var `PLAZA_URL_BASE` (default `https://<API_HOST>`; the local e2e server points it at its own `apiUrl`).

## 10. Host implementation notes (`host/agentj/market.py`, 2026-10-03)
- Host specifics: install digest = first 16 hex of SHA-256 over canonical JSON {bundle sha256,
  target, harness, params (x-auto values excluded: `today` / `now` would change between preview and confirm), flags
  {replace, sign_as, skip_verify}}; `--accept-unverified` is required at confirm, not part of the digest. Publish digest =
  {bundle sha256, show_name}. `install.verify` grammar (no shell; a second layer behind the sandbox): simple commands joined by
  `&&`, a trailing `>/dev/null` / `2>/dev/null` / `2>&1` dropped (output is captured anyway); any other `;|&$\`<>(){}[]*?!~#`,
  quotes joining words, backslash, newline, absolute paths / `..`, `-c` / `-e` inline code, `-m pip|ensurepip|venv`, network /
  privilege / wrapper programs (curl, ssh, sudo, env, xargs, rm, bwrap, unshare, docker, …) and build / dependency tools (make,
  cmake, ninja, npm, npx, pnpm, yarn, pip, uv, cargo, go, …: verify is a quick self-test, not a build) are refused.
- **Verify sandbox** (`market.py` `sandbox_kind` probes once per process; there is no unsandboxed path). Linux: `bwrap
  --ro-bind / / --dev /dev --proc /proc`, `--tmpfs` over `$HOME`, the passwd home, agentj's state dir, `/tmp`, `/run`, `/var/tmp`
  (a folder inside another hidden one is skipped), then `--bind <scratch> <scratch>` on top, `--unshare-all` (network
  included), `--die-with-parent --new-session --clearenv`, only `PATH=/usr/local/bin:/usr/bin:/bin`, `HOME=<scratch>/home`,
  `TMPDIR=<scratch>/tmp`, `LANG=C.UTF-8` (bwrap adds `PWD`), `--chdir <scratch>/pkg`; bwrap missing or unable to create the
  namespaces (probe) → no sandbox. macOS: `sandbox-exec -p <profile per run> -D SCRATCH=… -D HIDE_n=…` (paths as parameters,
  never spliced): `(allow default) (deny network*) (deny file-write* (subpath "/"))`, writes allowed back for the scratch folder
  and `/dev/null` & co., `(deny file-read*)` on the homes and the state dir with the scratch folder and the system paths python
  reads (`/usr`, `/System`, `/Library`, `/opt/homebrew`, `/private/etc`, …) allowed back, no signals / process info outside the
  sandbox, no launchd jobs / Apple Events / LaunchServices; the probe also checks that the home is unreadable inside. Other
  systems → no sandbox. 60 s timeout, process group killed.
- **Against a compromised server** (beyond §2; checked at preview and confirm): a package whose `name` or
  `install.skill_dir_name` matches the reserved namespace (§9) without a valid signature by a `TRUSTED_SIGNERS` key → exit 5; a
  package `installed.json` records as installed signed/official, now offered without a valid signature → exit 5; a version lower
  (SemVer precedence) than the highest one `installed.json` records for that name → refused unless `--version` named it
  explicitly (then the preview says 降级 DOWNGRADE). Out of scope for now: revocation lists (a signed but withdrawn version, a
  compromised plaza key) — a later contract version adds a signed revocation list fetched with the search answers.
- Server answers that nest deeper than the JSON parser goes (`RecursionError`) are read as `{}` (→ a one-line "unexpected
  answer" error), never a traceback.
  Rendering also HTML-escapes values in `.html` / `.htm` / `.xml` / `.svg` (FORMAT §7.1 names json / toml / yaml only);
  YAML values are JSON-escaped (valid inside double quotes, never a raw newline). Array params render as `a, b`, booleans as
  `true` / `false`. A `verified: [ … {{…}} … ]` line becomes `verified: []`, or with `--sign-as NAME` keeps its own `at:`
  placeholder and gets `human:NAME`.


## 11. Publisher signature — the host signs its own package (P426 #47)
- **Why**: before #47 the operator's auto-certify (P338) signed bytes it downloaded from the server, so a compromised Worker / D1
  could pass a forged package off as one of the operator's own. Now the publishing host signs; the server can only pass it on.
- **What is signed**: the host's long-term Ed25519 identity key (the key of every host envelope, PROTOCOL §7; private half 0600,
  never leaves the host) signs the UTF-8 text (lines joined by `\n`, no trailing newline)
  `agentjarvis-plaza-host-sig/v1` · `name=<name>` · `type=<type>` · `version=<version>` · `sha256=<bundle sha256 hex>` ·
  `host=<channel id of that key>` · `signed_at=<unix s>`. The first line separates it from every envelope signature of the same
  key. Implementation: `host/agentj/plaza_trust.py` (stdlib; RFC 8032 verify fallback), `dashboard/src/packages.ts` `hostSigText`.
- **Wire**: `publish` inner gains optional `host_sig` (b64url, 64 B), `host_pubkey` (b64url, 32 B) and `host_signed_at`
  (integer, 0 < x < 2^41) — all three or none. The server verifies the signature with the envelope's own `pk` (`host_pubkey` must
  equal it) over the request's name / type / version / sha256 and the envelope channel, else 400 `bad_host_sig`; it stores the
  three values as sent and serves them unchanged: `Detail.host_sig` / `host_pubkey` / `host_signed_at` (null when unsigned), the
  admin `versions[]` rows likewise, and the admin package view's `host_pubkey` (latest live version). Storage needs no
  migration: the version row's `manifest` column keeps them under the reserved key `_agentj_host_signature` (a bundle whose own
  manifest has that key → 422 `bad_bundle`/`reserved_key`; admin views return the manifest without it). A server from before
  #47 answers the unknown fields 400 `bad_request`: the host then publishes once more without them and says so (unsigned).
- **Who checks**: the operator's `tools/plaza-certify auto` certifies only when the signing key's fingerprint (SHA-256 of the raw
  public key) is pinned in its local allow-list and the signature verifies over the bytes it downloaded itself; no signature /
  key not pinned / signature does not match → a human, with that reason in the daily digest. Installing hosts check it too
  (#181-2, §12).

## 12. Static audit and install-time publisher check (P426 #181-2)
- **One rule table**, `host/agentj/plaza_audit_rules.json` (`agentj.plaza-audit/v1`), two matchers: `host/agentj/plaza_audit.py`
  and `dashboard/src/plaza_audit.ts` (it imports the same JSON). Deterministic, zero model; every pattern is matched line by line
  against every file and every manifest string (`files` excluded). A file that is not valid UTF-8 is decoded non-fatally (bad
  bytes → U+FFFD) and scanned anyway; a program-like file (under `scripts/ cli/ tools/ bin/` — also below `scaffold/` —, a
  script extension, or a `#!` first line) that does not decode is itself a block, `undecodable-script` (built into both
  matchers, not a table rule). The table's `cases` are vectors both matchers must return identically (`first_hit` = the first
  rule in table order that any line matches).
- **block** rules: `remote-exec` (curl / wget … | sh, sh -c "$(curl …)", bash <(curl …), iex(irm …), exec(urlopen(…))),
  `decode-exec` (base64 -d … | sh, exec(b64decode(…)), eval(atob(…)), powershell -enc), `credential-files` (SSH private keys,
  ~/.aws/credentials, gcloud / kube / docker credentials, ~/.netrc, keychain dumps, browser cookie / password stores),
  `env-exfil` (all environment variables piped / posted out), `file-upload` (curl -F / --data-binary / -T of a home or absolute
  path, scp of a home file), `delete-everything` (rm -r of / or ~), `prompt-injection` (中英: drop earlier instructions, hide things
  from the user). **warn** rules: `role-override` (「你现在是…」 / "you are now"), `dotenv-read`, `secret-dirs` (~/.ssh, ~/.aws,
  saved passwords), `sudo`.
- **Where**: `agentj plaza publish` audits right after building — a block finding → exit 2, nothing sent, every finding with path
  + line in plain words; warn findings go into the preview and the receipt. The Dashboard upload refuses a block finding with
  422 `{error:"audit_blocked", rule, path, line}` (version rejected, nothing in R2). The operator's `tools/plaza certify` and
  `plaza-certify auto` never certify a block finding (`auto` logs reason `audit`). `agentj plaza install` audits the downloaded
  bundle: a block finding → exit 2, nothing written; warn findings are listed in the preview. A package carrying a valid Agent J
  signature (official / certified: reviewed by a person) only gets its findings shown — a block finding there is still tagged
  「BLOCK（已认证包仅提示）」 in the preview.
- **Publisher check at install**: when the Detail carries `host_sig` / `host_pubkey` / `host_signed_at` (§11) the host recomputes the
  signed text from the bytes it downloaded — malformed or not verifying → exit 5 (「发布方签名对不上」), nothing written. The preview
  shows 「发布方签名：<fingerprint>」 or 「未签名」 (packages published before #47 still install, labelled unsigned).
- **TOFU per package name**: `<state>/plaza/publishers.json` (0600) remembers the first publisher seen for each name (recorded
  after an install; `fp: null` = it was unsigned then). A later version signed by another key, signed where it was unsigned, or
  no longer signed is shown as 「发布方换了」, and the confirm needs the owner's signed allow on the phone — the §13 card (one
  card with any extra permissions) with `publisher: {prev, new}` listing the old and the new fingerprint (null = unsigned). No
  command-line flag accepts it; without serve the install is refused (exit 6) with the way to approve. The publisher
  fingerprint is part of the install digest; the allowed one replaces the remembered one and installed.json keeps
  `publisher_grant {rid, device}`. An official package dropping its host signature is not a change (the Agent J signature
  decides).

## 13. Permission declarations (P426 #181-1)
- **Manifest** `permissions` (optional; an addition to CATALOG_FORMAT §2 checked by `bundle.py` `check_permissions` and the
  Dashboard's `checkManifestFields`, reason `permissions` / `permissions.tools` / `permissions.network` / `permissions.exec` /
  `permissions.secrets`): `{"tools": [≤ 40 tool names ^[A-Za-z][A-Za-z0-9_]{0,63}$], "network": bool, "exec": bool, "secrets":
  [≤ 60 env names]}`, every key optional (default empty / false), no other key.
- **Inference** (`host/agentj/plaza_perms.py` `infer`) for a package without it: `exec` = files under `scripts/ cli/ tools/ bin/`
  (also below `scaffold/`), script extensions, a `#!` first line or an `install.verify`; `network` = code that talks to the
  internet (URLs, curl / wget, requests / httpx / urllib / fetch / axios / sockets) or docs that use curl / wget / WebFetch /
  WebSearch (a plain link in a doc is not network); `tools` = SKILL.md `allowed-tools:` + role files' `tools:` (argument
  qualifiers dropped) + `Bash` when exec; `secrets` = `requires.env` entries with `secret: true`. `agentj plaza publish` of a
  folder without a declaration publishes the inferred one inside the manifest (shown in the preview; the owner's yes to the
  preview is the author's confirmation; the folder itself is never rewritten). `install` of an old package uses the inference.
  A declaration never lowers what the content shows: install compares **declared ∪ inferred**; the left-out items are named
  in the preview (「包声明的少于实际内容」, also in the publish preview) and asked on the card (`under: true` adds that line) —
  `"permissions": {}` does not skip the card.
- **Allowed set** (`plaza_perms.allowed`, read-only, per harness): Claude Code `~/.claude/settings.json` + `settings.local.json`
  (`defaultMode: bypassPermissions` → everything; else bare tool names in `permissions.allow` — `Tool(args)` rules allow part
  of a tool and do not count; exec = Bash; network = WebFetch or Bash); Codex `~/.codex/config.toml` (`danger-full-access` +
  `approval_policy = "never"` → everything; `workspace-write` → tools + exec, network only with
  `sandbox_workspace_write.network_access = true`); OpenCode / unreadable → nothing. Always allowed: Read, Glob, Grep, LS,
  NotebookRead, TodoWrite. Secrets count as allowed only under full access.
- **Install**: (declared ∪ inferred) ⊆ (allowed ∪ what the owner approved for this package on this harness before) → installs as before.
  Otherwise the preview lists every extra item in plain words, and `--owner-confirmed` asks serve over the control socket
  (`{"cmd":"plaza_perm", name, version, sha256, items:[{kind: tool|network|exec|secret, name}], under?, publisher?}`); serve
  rebuilds the card text from the kinds / names / fingerprints, raises one approval card (tool `agentj plaza`, `batch=False` —
  a host card no approval level auto-allows, and in `goal.SOFT_EXEMPT` so a running goal does not auto-approve it either) and answers `{ok:true, approved:true, rid, device}` only for an allow signed by a paired device. Denied / timed
  out / serve not running → exit **6**, nothing written; the message says how to approve from the phone. installed.json keeps
  `permissions` (declared ∪ inferred) and `perm_grant` `{rid, device}`; a later version asks again only for what it adds. The
  curated starters (signed workflows seeded by install / upgrade) are pre-authorized and never ask.

## P79 — public metadata exception (ADR-A188)

The seat gate in §0 continues to cover Q&A, full package contents, downloads, installation and publishing. The only unauthenticated exception is read-only `GET|HEAD /api/v1/plaza/catalog` on the front Worker (`/v1/plaza/catalog` over the Dashboard service binding). It returns schema `agentj.public-catalog/v1`, `generated_at` (UTC epoch milliseconds), and `items`. Each item contains only name, type, track, certified, version, bilingual title/summary, tags, category, likes and installs. No author/tenant/host identity, README, manifest, parameters, command or download link is public through it. Community packages are always labelled unverified; a site badge is a catalog record, not proof of a valid bundle signature.

Only visible packages whose current version is live are eligible; malformed or privacy-flagged metadata is omitted. The existing publication privacy/Jev/owner-confirmation rules are unchanged. The endpoint accepts no query parameters or writes, uses HMAC-IP rate limiting (120/minute per Cloudflare location), and advertises a300-second shared cache. The front checks age explicitly, with no expired fallback: withdrawal removes list/sitemap entries within five minutes; detail pages return410/noindex, and upstream outage returns503. Public HTML contains summaries only. Limits exceeding10,000 packages fail closed. No schema migration.

## P195 — 优先推荐伙伴 (platform referral layer)

Catalog FORMAT「优先推荐伙伴」: a package's `partners` are DEFAULTS and never carry a code. The platform keeps overrides by
`(slot, name)` (D1 `partner_overrides`, migration 0026; every write logged in `partner_override_log`, whose max id is the config
`version`) and renders every link once, in `dashboard/src/partners.ts`:
- `Detail.partners?` (only when the stored manifest has a non-empty `partners`) = `[{slot, name, priority, url (official), link,
  referral: bool, self_owned, disclosure:{zh,en}, keep_account:{zh,en}, why:{zh,en}, pricing_hint_zh, alternatives:[{name,
  url|null, note_zh, status?:"coming_soon"}]}]`, sorted slot → priority → name; `Detail.partners_version` = config version.
- Host route `partners` (this §4 table, P195): inner optional `slot` (`^[a-z][a-z0-9-]{0,31}$`) → 200 `{schema:
  "agentjarvis.partners/v1", version, disclosure, keep_account, partners:[…same shape…]}` over the latest live version of every
  visible **official** package (first package by name wins per `(slot, name)`); read-charged like `get`; 400 `bad_request`.
- `link` = the override's `url_template` with `{code}` replaced (URL-encoded) only when the override is active, the code is
  `[A-Za-z0-9_-]{1,64}`, the template has exactly one `{code}` and the result is `https://` on `REF_HOSTS` (no port, no
  credentials; today `try.elevenlabs.io`, `elevenlabs.io`); anything else → the official URL, `referral:false`. A package's own
  `referral` is ignored; `self_owned` entries never get one; an override never adds a partner that no package names.
- Disclosure: 「这是 Agent J 的优先推荐伙伴，可能有推荐返佣」 (self-owned: 「这是 Agent J 出品方自己的免费网站」) on every
  entry. agentj prints its OWN compiled-in sentence after every link (never the server's text); the Dashboard page uses its
  dictionary sentence next to every URL (URLs stay plain text there, §5 rule). Already has an account → keep it.
- Admin (`api.<zone>/v1/plaza/admin/partners`, the plaza admin token, same failure budget): `GET partners` → `{version, ref_hosts,
  disclosure, overrides:[row + link], log:[last 50]}`; `POST partners/set {slot, name, priority?, url?, code?, url_template?,
  note?}` (patch: absent = keep, null = clear; code and url_template together; re-activates; 400 `bad_slot` / `bad_name` /
  `bad_priority` / `code_and_template_together` / `bad_code` / `url_not_allowed` / `bad_note`; 409 `conflict` on a concurrent
  write); `POST partners/disable {slot, name, note?}` (referral off; 404 without a row). CLI: `agentjarvis/tools/partners-ref`.
