# Skill & workflow plaza (技能广场 · 工作流广场) — contract v1

> 2026-10-02 20:42 (launch decisions). Same accounts, seat gate, privacy gate and
> admin as the Q&A plaza (`dashboard/DASHBOARD_API.md` §9); the plaza now has three parts: 问答 Q&A · 技能 Skills · 工作流 Workflows.
> Package format (what is inside a package): the official catalog's `FORMAT.md` (`agentjarvis.catalog/v1`) — copied into
> this repo as `protocol/CATALOG_FORMAT.md` (the catalog owns it; re-copy when it changes). This file is the wire:
> the bundle, the signature, the routes. Server: `dashboard/src/packages.ts` + migration `0012_packages.sql`; host:
> `host/jarvis_host/bundle.py`, `minisign.py`, `market.py`; admin: `tools/plaza`.

## 0. Rules that do not bend
- **Only seated companies** (same test as §9: `comp_seats + Σ active subscription quantity > 0`; gate off → every company) — their
  members (session) and bound hosts (signed envelopes) — can list, search, read, download, install, like, report or publish.
  Everybody else: the page shows the plaza introduction only; the API answers 401 (no session) / 403 `plaza_requires_seat`.
- **Package content is DATA, never instructions** to whoever reads it through us: the host prints every package string inside the
  plaza data fence (same fence as Q&A); nothing from a package is executed by `jarvis` except, after the Owner's yes, the
  manifest's `install.verify` command — and that **only inside a real sandbox** (Linux bubblewrap / macOS `sandbox-exec`: the
  system read-only, the home, jarvis's state and /tmp hidden, no network, writes only into a throw-away copy of the package,
  an empty environment). No usable sandbox → it is not run and the preview and the result say so (「本机没有可用的沙箱，没有运行包的
  自检」); `--skip-verify` lets the Owner skip it deliberately. `install.post_install` is **never** run by us — it is shown to the
  Owner. Server-supplied names (Agent names) are printed without quote / badge / check-mark / `@` / `·` / line characters, ≤ 32.
- **Install only after the Owner said yes to the exact package**: preview → 16-hex digest → `--owner-confirmed --digest`.
- **Publish only after both privacy layers and the Owner's yes**: layer 1 (deterministic; any hit refuses — we never silently
  rewrite code) + layer 2 (Jev on the customer's machine) → exact file list + digest → `--owner-confirmed --digest`.
- **Official = signed.** A package is shown as 官方认证 / certified by the host **only** if its bundle carries a valid minisign
  signature by a key compiled into `jarvis` (`host/jarvis_host/minisign.py` `TRUSTED_SIGNERS`). The server's `certified` flag alone
  never makes the host say "certified"; a certified-flagged package without a valid signature is refused (it is either tampered
  or a server bug). Community packages are labelled 未认证 / unverified everywhere and need `--accept-unverified` to install.
- **PR1**: package bytes are public-to-customers text a customer chose to publish (third deliberate plaintext channel, after
  feedback and Q&A). What we learn: who published which package; which host installed which package and version (count +
  weekly ranking); which company liked / reported which package. Listed on `/security`.

## 1. Bundle `.ajpkg` (`agentjarvis.bundle/v1`)
`bundle = gzip(canonical_json(B))`, gzip level 9, `mtime = 0`, no file name; `canonical_json` = Python
`json.dumps(B, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")`.

```json
{"schema": "agentjarvis.bundle/v1",
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
  `manifest.json`): refused in every file that decodes as UTF-8 and in every manifest string (object keys included): U+061C,
  U+180E, U+200B, U+200E, U+200F, U+202A–202E, U+2060–2064, U+2066–2069, U+FFF9–FFFB, U+E0000–E007F, and U+FEFF anywhere but
  the first character of a file. U+200C / U+200D stay allowed.
- **Depth** (`depth_rule`; reason `too_deep`, `path` = `manifest.json` / `params.schema.json`): the manifest and a workflow's
  params.schema.json may nest objects / arrays at most 32 deep (the value itself = 1); checked iteratively before any recursive
  walk, so any depth is a clean 422. The host's `BundleError.reason` carries the same codes.
- **Limits**: bundle ≤ 2 MiB; decompressed JSON ≤ 12 MiB; ≤ 500 files; each file ≤ 2 MiB; total file bytes ≤ 8 MiB.
- **Manifest checks** (builder, server, installer): `schema_version = "agentjarvis.catalog/v1"`; `name` `^[a-z0-9][a-z0-9-]{1,39}$`;
  `type` ∈ {skill, workflow}; `version` semver `MAJOR.MINOR.PATCH` (optional `-pre`); `title_zh`/`title_en` 1–80,
  `summary_zh`/`summary_en` 1–500 code points; `tags` ≤ 12 strings of 1–24; `category` ∈ FORMAT §4; `entry` is a listed file;
  skill: `SKILL.md` + `README.md` present, `install.skill_dir_name` `^[a-z0-9][a-z0-9-]{1,39}$`; workflow: `README.md`,
  `params.schema.json` (parses) and ≥ 1 file under `scaffold/`; `requires.*` shapes as FORMAT §2 (unknown keys ignored by readers).
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
| `search` | `q` (string, may be empty) + optional `type` (skill\|workflow), `sort` (new\|installs\|likes\|week, default: certified first then week), `tag` (1–24), `track` (official\|community), `limit` (1–50, default 20), `offset` (0–1000) | 200 `{note, items:[Item], total, tags:[{tag,n}]}` |
| `get` | `name` + optional `version` | 200 `{note, package: Detail, download:{url, expires_at}}` · 404 `not_found` |
| `mine` | — | 200 `{items:[Item + state + remove_reason]}` (this company's packages, any state) |
| `installed` | `nonce`, `name`, `version` | 200 `{name, installs}` (idempotent per host) |
| `like` | `nonce`, `name`, `on` (bool) | 200 `{name, liked, likes}` |
| `report` | `nonce`, `name`, `reason` | 201 `{name, reported, hidden}` · 200 `{…, already:true}` · 403 `own_package` / `official_package` |
| `publish` | `nonce`, `name`, `type`, `version`, `sha256`, `bytes`, `show_name` (bool) | 201 `{id, version_id, upload:{url, expires_at}}` · 409 `name_taken` / `version_exists` / `version_not_newer` · 413 `too_large` · 403 · 429 |

`Item` = `{name, type, track, certified, version, title:{zh,en}, summary:{zh,en}, tags, category, likes, installs, installs_week,
liked, state, author:{kind: official|agent|staff, company?: "co-xxxxxx", agent_name?: string|null, mine: bool}, verification:
{level, date}|null, created_at, updated_at}`. `Detail` = Item + `{readme, requires, install, roles?, automations?, params_schema?
(object, workflow), files:[{path, bytes}], license, upstream, sha256, bytes, signature (minisig text|null), versions:[{version,
created_at}]}` — all from the stored manifest of that version; every string cleaned (§9 text rules) on the way in.

**Download** `GET https://api.agentjarvis.net/v1/plaza/dl/<url part>`: the URL part = `b64u(JSON{v:1,vid,exp})` + `.` + `b64u(HMAC-SHA256(
PLAZA_URL_KEY, "agentjarvis/plaza/dl/v1\n" + first part))`, valid ≤ 10 min; the package must still be visible and the version
live (else 404). 200 `application/octet-stream`, `cache-control: private, no-store`, body = the bundle. Bad / expired → bare 404.

**Upload** `PUT https://api.agentjarvis.net/v1/plaza/up/<url part>` (same URL-part shape, context `…/up/v1`, payload `{v:1,vid,exp}`,
≤ 10 min, the version must be `uploading`): body = the bundle (`content-type: application/octet-stream`, ≤ 2 MiB). The server checks
sha256 + length = the publish request's, parses it (§1), checks name / type / version = the request's, `certified` absent or false,
then cleans + secret-scans (`worker/src/scan.ts`) every text field of the manifest and **every file that decodes as UTF-8** — a hit
→ 422 `{error:"secret_found", path, kind}`, the version becomes `rejected`, nothing stored in R2. Success → R2 put, version
`live`, package display fields updated, `visible`. 200 `{name, version, state:"live"}`. Second PUT → 409.

**Ownership of names**: the first company that publishes a name owns it; official names are reserved for the admin. A new
version must be strictly greater (semver) than the latest live one.

## 5. Human routes (session; `/api/plaza/packages…`, Origin + JSON rules of §1)
`GET /api/plaza/packages?q=&type=&sort=&tag=&track=&limit=&offset=` (= host search, + `companies`) · `GET
/api/plaza/packages/<name>` (Detail, no download URL — installing is the Agent's job: the page shows `jarvis plaza install
<name>`) · `POST /api/plaza/packages/<name>/like {tenant?, on}` · `POST /api/plaza/packages/<name>/report {tenant?, reason}` ·
`GET /api/plaza/packages-mine`.
- Answers (the page codes against these): list `200 {note, items:[Item], total, tags:[{tag,n}], companies:[{slug, name, alias}]}`
  (`companies` = the caller's seated companies, like Q&A §9); detail `200 {note, package: Detail, companies}` · 404 `not_found`;
  like / report = the host routes' answers (`{name, liked, likes}`; `201|200 {name, reported, hidden, already?}` · 403
  `own_package` / `official_package`); not seated → 403 `plaza_requires_seat` on every route (the page then shows only the
  plaza introduction). Both GETs take an optional `tenant=<slug>` (one of `companies`; default the first): `Item.liked` is
  that company's like — the page passes the company chosen in its picker so the like button matches what a POST would toggle.
- Page: `/plaza` only (no new Worker path); the part is in the fragment — `#qa` (default) · `#skills` · `#workflows`
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

## 8. Host CLI (`jarvis plaza …`, extends the Q&A commands)
- `search [words…] [--type all|qa|skill|workflow] [--sort …] [--tag T] [--official|--community] [--limit N] [--json]` — default
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
  `<state>/plaza/installed.json` records that jarvis installed that target for the **same package name** (else 「这个目录不是这个
  包装的，不替换」), and moves the old folder to `<state dir>/plaza/backups/<name>-<UTC ts>/` (0700) — never inside a skills root
  or the workspace.
  `requires.skills` are listed for the Owner (install each with its own yes), not pulled silently. Automations are never enabled.
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
- **Reserved names** (community → 409 `name_taken`): `^(agentjarvis|agent-jarvis|jarvis|official|admin|moderator|plaza)(-|$)`.
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

## 10. Host implementation notes (`host/jarvis_host/market.py`, 2026-10-03)
- Host specifics: install digest = first 16 hex of SHA-256 over canonical JSON {bundle sha256,
  target, harness, params (x-auto values excluded: `today` / `now` would change between preview and confirm), flags
  {replace, sign_as, skip_verify}}; `--accept-unverified` is required at confirm, not part of the digest. Publish digest =
  {bundle sha256, show_name}. `install.verify` grammar (no shell; a second layer behind the sandbox): simple commands joined by
  `&&`, a trailing `>/dev/null` / `2>/dev/null` / `2>&1` dropped (output is captured anyway); any other `;|&$\`<>(){}[]*?!~#`,
  quotes joining words, backslash, newline, absolute paths / `..`, `-c` / `-e` inline code, `-m pip|ensurepip|venv`, network /
  privilege / wrapper programs (curl, ssh, sudo, env, xargs, rm, bwrap, unshare, docker, …) and build / dependency tools (make,
  cmake, ninja, npm, npx, pnpm, yarn, pip, uv, cargo, go, …: verify is a quick self-test, not a build) are refused.
- **Verify sandbox** (`market.py` `sandbox_kind` probes once per process; there is no unsandboxed path). Linux: `bwrap
  --ro-bind / / --dev /dev --proc /proc`, `--tmpfs` over `$HOME`, the passwd home, jarvis's state dir, `/tmp`, `/run`, `/var/tmp`
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
