# Agent J Cards - public wire contract

Canonical contract: [protocol](../../protocol/PROTOCOL.md). Host `host/agentj/jcards.py` (+ `cardpacks/`: `card.schema.json`, the
official `notes` / `todo` / `automation`), page `web/public/js/jcards.js` + `cards.css`. A host that answers announces `jcards1` in
`ready.caps`; the page shows the owner's cards below the five system cards (§30.3) and Settings → Cards only then.

**Phase 1 = template cards**: a card is `card.json` (+ `sample/<data file>`), no code. The computer reads the card's one data
file (`<state>/agentperm/cards-data/<id>/<file>`: a regular file, not a link, real path inside that folder, no
credential-looking name, ≤ `maxBytes`), redacts notes (`privacy.redact(owner=True)`: ordinary phone numbers/e-mail retained, credential shapes still hidden), parses and bounds a checklist, and renders every
button's finished chat message. The page draws one of the built-in templates and never runs card code.

Messages (p33 sessions of a paired, ready device; answered to the asking session only):

| direction | message | notes |
|---|---|---|
| device → host | `{"t":"jcard_list","r","lang"?}` | |
| host → device | `{"t":"jcard_items","r","cards":[C…]}` | `C` = `{"id","name":{zh,en},"desc":{zh,en},"icon","accent","template","official","on","plus"}`; `jcard_items` also carries `colors:{id:"#rrggbb"}` — official cards first (default on since P393 #129), then the owner's own by apply time (default on); no paths, no messages |
| device → host | `{"t":"jcard_get","r","id","lang"?}` | |
| host → device | `{"t":"jcard_get","r","ok":true,"id","template","path","text"?\|"items"?,"actions":[A…],"plus","links":[L…],"anchors":[N…],"problem"?,"cut"?}` | `notes` → legacy `text` plus `entries:[{id,text,editable}]` and SHA256 `revision`; `checklist` → `items:[{"id","text","done","acts":[A…]}]` (≤ 200); the official `todo` card (P394) adds `todo:{counts:{all,agent,owner}}` and per item `status` (pending · active · done · cancelled; `done` = closed), `owner`/`by` (agent · owner), `start`/`end` (null or `{kind:"at",at:<ms>,date,text}` · `{kind:"after",id,met,text}` · `{kind:"when",text}`), `note`, `source`, `created`, `finished`, `state` (overdue · due · active · ready · open · waiting · done · cancelled — the order the items come in) and `flags` (overdue · due_soon · end_met · ready · waiting · check), all redacted; `problem` ∈ `empty` · `refused` · `unreadable` · `bad_data`; `ok:false` → `why` ∈ `not_found` · `off` |
| device → host | `{"t":"jcard_set","r","id","on"}` | one card's switch; display only |
| host → device | `{"t":"jcard_res","r","ok","id","on"}` | serve logs `jcard_set` (device, `id:on\|off`) |
| device → host | `{"t":"jcard_note","r","id","entry","revision","op":"add"\|"edit"\|"delete","text"?}` | paired-owner direct notes/todo add/edit/delete; add uses text, edits/deletes require revision; add only on enabled official notes/todo; existing enabled custom notebook edits/deletes preserved; no arbitrary file path |
| host → device | `{"t":"jcard_note","r","ok","id","why"?,"entries"?,"revision"?}` | stale revision → `conflict`; no write on refusal; redacted entries cannot be edited; delete targets the exact original byte span |
| device → host | `{"t":"jcard_color","r","id","color":"#rrggbb"\|null}` | user override; null removes it; system page ids and installed cards only |
| host → device | `{"t":"jcard_color","r","ok","id"?,"color"?,"why"?}` | saved under `agentperm/cards/colors.json`, separately from card packs |
| host → device | `{"t":"jcard_open","id","at"?}` | pushed to every ready p33 device: open that card (or the page `chat` · `workflows` · `skills` · `memory` · `agentj` · `cards`) and scroll to `at`; navigation only. Sent when `agentj card open <id> [--at <anchor>]` asks serve on the Agent-facing `agentperm/elevate.sock` (reachable inside the fence; `{"t":"jcard_open","id","at"?}` → `{"ok","delivered"}` / `{"ok":false,"why"}`); serve checks again (a switched-on card or a page; `at` `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`), pushes at most once per 2 s (`too_soon`) and logs `jcard_open` (the id only). The page ignores it while a confirm card or Settings is open; `cards` opens the wall (never toggles it) |

P386 notes updates use the same no-follow directory boundary and bounded regular-file reads as card data. An exclusive
write lock, revision check and second read precede atomic replacement; headings and all unrelated original bytes stay.
External editors do not share the lock, so the second read detects changes before replacement but is not a universal
external-editor transaction. Logs contain device / result only, never entry text. CLI additions use
`agentj card note add --id notes --text TEXT --json`; edit/delete additionally require `--entry ID --revision REV`.

The host-owned `automation` response uses `template:"automation"` and bounded `items:[{id,name,source,schedule,repeat,
next,harness,model,description,prompt}]`. It reuses `systasks.scan` (Linux user systemd + user cron; macOS user launchd +
cron; Windows Task Scheduler) and Agent J `tasks.rows`. Native tools/models are null; Agent J plans use the configured
Agent tool/model. No scheduler writes, executable command lines or arbitrary manifest-defined sources. Missing sources
or metadata stay unavailable. The prompt is `/agentj-automation @<source-qualified-unique-name>`. The management skill resolves it via
`agentj card automation list --json`, the same catalogue even when the card is off.

`A` = `{"id","label","prompt","draft"?}`. `prompt` for new official management buttons is a bare slash-skill command with an operation / item id, without a host path or source prefix. Legacy custom prompts remain compatible: the source label
`〔来自卡片：<name>〕` / `[From card: <name>] ` + the manifest text with `{{card.name}}` `{{data.path}}` (+ for item buttons
`{{item.id}}` `{{item.text}}` `{{item.done}}`; item text one line, `「」` swapped, `{{` broken, ≤ 200 chars, wrapped in `「…」`).
The page shows a non-draft prompt whole on the official confirm card and sends it as an ordinary `say` only on 发送;
`draft:true` puts it in the message box. Card packs have no executable code, approval or network capability. Management actions go to the Agent; the narrowly
scoped paired-owner note and colour operations above are the only new direct writes.

`plus` = the skill the page header's + puts in the message box on this card's page (`/<plus> `, never sent; default
`agentj-cards`). `L` = `{"id","label","href"}` (http/https, ASCII host, no `user:password@`, no credential-like `name=` anywhere, no key shape; the page shows
the address on the confirm card, then opens a new tab `noopener,noreferrer`) or `{"id","label","jlink"}` — the internal
address form shared by everything the page opens: `skill/<name>` · `workflow/<folder>` · `doc/[<folder>/]<file>.md` ·
`card/<id>[#<anchor>]` · `page/<page>`. `N` = `{"id","label","block"}` (`top` · `content` · `actions` · `links`) or
`{"id","label","heading"}` (notes); every card also has `top` · `actions` · `links`, a checklist item's id is an anchor. `latest` selects the first note entry or last checklist item; management skills call
`agentj card open <id> --at latest` (or the new item id) after a successful add, separately from the data write.
A card's address is its id (unique on the computer; names may repeat). In messages the page links `card/<id>[#<anchor>]`
and `@<card name>` (the composer writes `@<name>（card/<id>）`).

**P432 — a card's own server on this computer.** `card.json` may add `server:{port,routes?:[{id,path}],repo?,ref?}`;
`data:{"get":"/path"}` replaces `data.file`, and a button `{"id","label","call":"<route id>"}` replaces `prompt`. serve alone
asks `http://127.0.0.1:<port><path>` (GET for data, POST for a button) with `X-AgentJ-Card: <id>` and shows nothing unless
the answer is `200` and carries the same `X-AgentJ-Card: <id>`; a button's POST goes out only after `OPTIONS <route>`
answered the same way (a local service that is not this card's server never receives it); port 1024–65535 except
well-known local services (debuggers, container / database daemons, admin APIs); paths are plain segments (no query, no
`..`); no redirects; ≤ 5 s for the whole exchange (a watchdog closes the socket); data ≤
`maxBytes`, a button's reply ≤ 4 KiB; one request per card at a time, data at most once a second, a button at most once
per 2 s. The answer goes through the same template pipeline (redaction, parsing, bounds; server notes are never
editable). In `jcard_get` such a button is `A` = `{"id","label","call":true,"where":"POST http://127.0.0.1:<port><path>"}`
and `path` is the GET address; `problem` adds `server_down` · `server_timeout` · `not_card` · `server_status` · `too_big` ·
`busy` · `bad_reply` (and `stopped` for a button or a download during an emergency stop). The page shows label + `where`
on the confirm card and, on 运行, sends

| direction | message | notes |
|---|---|---|
| device → host | `{"t":"jcard_call","r","id","act","item"?,"lang"?}` | `item` = a checklist item id (`^[A-Za-z0-9_-]{1,32}$`) for an item button |
| host → device | `{"t":"jcard_call","r","ok","id","act","message"?,"why"?}` | POST body made by the host: `{"card","action","item"?,"lang"}`; the reply `{"ok"?,"message"?}` (JSON) or text → one redacted line ≤ 200; `why` ∈ `not_found` · `off` · `bad_item` · `too_soon` · `busy` + the server problems; serve logs `jcard_call` (device, `id:act:result`) |

The phone never reaches the server; it is a plain loopback client of the same Noise session (the Agent fence already
shares the network, so this adds no reach). `server.repo` + `server.ref` (a GitHub repository and its 40-hex commit) say
where the server's source lives; the host never fetches or runs it.

**P432 — the official Files card** (`files`, default off). `jcard_get` answers `{"ok","id":"files","template":"files",…}`
without data; the page then asks one folder at a time:

| direction | message | notes |
|---|---|---|
| device → host | `{"t":"jcard_files","r","id":"files","root"?,"path"?}` | `root` = a root id from the answer (default the first), `path` = `/`-joined names under it |
| host → device | `{"t":"jcard_files","r","ok","roots":[{id,label}],"root","path","label","entries":[{name,kind:"dir"\|"file",mtime,bytes?,dl?}],"more"?,"why"?}` | roots = the Agent's working folder (`r0`) + folders the owner added on the computer (`agentj card files add`, `<state>/cards/files-roots.json`, outside agentperm); never `/`, `/proc` `/sys` `/dev`, the state dir or a folder holding it, nor anything the Agent fence hides (fence.protected_paths: preferences, other instances, other seats' folders, the shared ASR folder — never entered, never listed); a root is opened once and its fd's real path must be the checked one; ≤ 500 rows, folders first; links, devices, hidden names and credential names (media `DENY_*`, jcards `DENY_RE`) never appear; every level opened with O_NOFOLLOW; `why` ∈ `no_root` · `not_found` · `refused` · `gone` · `off` |
| device → host | `{"t":"jcard_file","r","id":"files","root","path"}` | |
| host → device | `{"t":"jcard_file","r","ok","item"?,"why"?,"bytes"?}` | `item` = a §13 media item (`mid,name,mime,kind,bytes,sha256,ref`) registered after `Media.check(owner=True)` against that root — the same type sniffing, caps and key-file / key-content refusal (`secret`) as a reply's attachment; the page then pulls it with `media_get` and saves it; `why` ∈ `refused` · `gone` · `secret` · `too_big` · `type` · `not_found` · `off`; serve logs `jcard_file` (device, result; no name) |

A file's `@` puts `@<label>/<name> ` in the message box (never sent) so the Agent reads it on the host.

**P432 — the official Obsidian card** (`obsidian`, default off). `jcard_get` answers `{"ok","id":"obsidian","template":"obsidian",
"vault"?,"entries":[{"id","path","title","text","problem"?,"cut"?}],"actions","plus":"agentj-obsidian","problem"?}`: the notes
picked in `<state>/agentperm/cards-data/obsidian/selection.json` (`agentj card obsidian vault|pick|unpick`, ≤ 20). The
vault must be a real folder with `.obsidian/` (not `/`, the state dir or a parent of it); a note is a `.md` path inside it,
read one level at a time with O_NOFOLLOW, nothing hidden on the way (never `.obsidian/`), ≤ 32 KiB (cut at a word), then
`privacy.redact(owner=True)`; the selection is checked again on every read. `problem` ∈ `not_set` · `no_vault` · `refused` ·
`not_a_folder` · `no_notes`; an entry's `problem` ∈ `refused` · `gone`. A note's `@` drafts `/agentj-obsidian @<path> `
(never sent); the Agent edits the vault on the host.

**P432 — cards from the plaza** (package type `card`, PLAZA_PACKAGES §1 / §8). `agentj plaza install` puts such a card in the
deck **switched off** and records its source — and later the digest the owner approved — in `<state>/cards/plaza.json`
(outside agentperm: an Agent inside the fence can neither forge a reviewed record nor switch a plaza card on by editing the
switches; a plaza card counts as on only while the approved digest matches the card as it is now); `jcard_items` marks it
`"src":"plaza"`. Author strings in the summary are one quoted line each.
Before switching it on the page asks for its permission summary and shows it word for word on the confirm card:

| direction | message | notes |
|---|---|---|
| device → host | `{"t":"jcard_perm","r","id","lang"?}` | the owner's own (non-official) cards only |
| host → device | `{"t":"jcard_perm","r","ok","id","text","digest"}` | `text` = the data source (file or server GET), every button's whole message template or server call, links, the server's `repo @ ref`, the plaza name / version / review state; `digest` = 16 hex of SHA-256 over the language-independent summary |
| device → host | `{"t":"jcard_set","r","id","on":true,"seen":<digest>}` | a plaza card goes on only when `seen` equals the digest of the card as installed now (a changed card needs a new reading); `agentj card on` refuses it (`review`) |

`agentj card pack <id> <folder>` builds the package from the owner's card (sample only, never the data folder) after the
preflight (only README + card/card.json + card/sample/, a valid card, a server pinned to `server.repo` + 40-hex `server.ref`,
a key-free sample that renders, every button message renders); `agentj plaza publish` repeats it. `--replace` installs a new
version (the old one sleeps; off again); `agentj card rollback <id>` brings the newest sleeping version back, off.

P386 colour UI: ≥44px invisible circular area at the deck card's upper-right, held with finger or mouse; no mark or icon.
B2 hue ring, palette tones and HEX/RGB fields; Escape closes. The UI chooses Noir/Lotion ink and refuses a colour below
4.5:1 contrast. Defaults remain Leo palette values; explicit owner overrides survive upgrade and resetting removes only
the override. Card creation recommends one-record-per-card stacks, requires a management skill per new custom card,
and checks slash routing, long-text wrapping, spacing, Escape and post-add focus (skill contract, not schema migration).


P413 calendar extension to §34 (jcards1): official calendar is default on, owner can turn off.
`jcard_get {id:"calendar",r}` → `{t:"jcard_get",r,ok,template:"calendar",revision,events,plus:"agentj-calendar"}`.
`jcard_cal {r,id:"calendar",op:"add"|"edit"|"delete",entry?,revision,event?}` →
`{t:"jcard_cal",r,ok,why?,revision?,events?}` only to the requesting paired session.
Timed events require explicit offsets, all-day end exclusive; reminder is minutes before, null disables.
No sync, network or secret operation is accepted over this message; source snapshots and To-do projections are read-only.
Mutations require current revision and card on. Host logs device/status only. No model call or added approval authority.


### §34.4 P420 global palette library

Host capability `jpalette1`; paired ready remotes may send `{t:"jpalette",r,op:"list"|"save"|"delete"}` over the
existing Noise application channel. `save` includes `palette:{id,colors}`; id is a client-generated UUID-shaped
36-character lower-case hex/hyphen string and colors is 5–10 #rrggbb values. No names, code, paths or theme assets.
`delete` includes id. Reply `{t:"jpalette",r,ok,palettes? ,why?}`; failure reasons bad_op/bad_value/full/refused.
The global host library holds ≤32 sets, newest first, in `agentperm/cards/palettes.json` (0600, bounded/no-follow
read, atomic write under card-palettes lock). Repeating save with one id replaces only that id. Malformed files
are never overwritten. Changes are read on component open/Refresh, with no live push guarantee.
`jcard_get official-palette` returns built-in template `palette` only while its official switch is on (default off).
Generating/selecting does not write a card background; existing `jcard_color` saves that separately.


P428 two-way calendar additions to `jcard_cal` (supersedes "no sync" above for two-way remotes only; ICS stays CLI-only):
`{r,id:"calendar",op:"undo",revision,entry?}` reverts the newest history entry (or that event's newest; undo twice = redo);
`{r,id:"calendar",op:"resolve",revision,entry,keep:"local"|"remote"}` settles one conflict; both answer like add/edit/delete.
`{r,id:"calendar",op:"sync",name}` (no revision; `name` of an enabled google/caldav remote, else `why:"not_two_way"`) →
`{t:"jcard_cal",r,ok:true,...listing,sync:{ok,name,pulled,pushed,queued,conflicts,linked_duplicates,offline?,why?}}`;
`sync.why` ∈ offline | auth | conflict | gone | failed | full | disabled | not_found (counts and words only).
Listing fields (every `jcard_cal` / `jcard_get` calendar answer): each event adds `remote` (remote name or null), `pending`
(queued for that remote), `conflict`; `readonly` also marks a remote's recurring instance. `subscriptions[]` =
`{name,kind:"ics"|"google"|"caldav",enabled,last_sync,last_error,queued,conflicts,push_new,auto}`; `conflicts[]` =
`{id,remote,at,remote_state:"changed"|"deleted",local:<event fields>|null}`; `undo` = history length. Never a key name, server,
calendar id, remote event id, href or etag. A change that queued an op makes the host sync that remote 5 s later (debounced).
Host log for every op stays `jcard_cal {device,status}`; elevate.sock `{"t":"calsync","op":"sync"|"kick"|"disconnect","name"}` /
`{"t":"calsync","op":"connect","name","kind","calendar"?,"push_new"?,"auto"?}` serves the fenced CLI.
CalDAV (`kind:"caldav"`, P428 item 3): connect adds `server` (https URL, no userinfo/query/fragment), `user_key`, `pass_key`
(key-card NAMES `…_SECRET`; values read on the host only) and `calendar` = a display name; any of these on a google connect is a
`shape` refusal. Connect answers `why` ∈ bad_server | bad_key_name | key_missing | no_calendar | auth | offline | failed. The
discovered calendar href and partition host stay in calendar.json; no `jcard_cal` / `jcard_get` answer ever carries them.

### P447 first-remote success hint

After first approval and ready, host sends encrypted `{t:"pair_guide",kind:"phone"|"computer"|"other"}` once per host using the existing onboarding welcome marker. It is display-only. Phone suggests reverse-pairing the usual computer at m.agentj.app; computer suggests Settings → Remotes → Add a remote for the phone. Unknown kind is ignored. No control signature, password, key or authorization is conveyed. Reconnect and subsequent remotes do not trigger it.


P474 — Official `notes` responses advertise `noteFields:true` with entries `{id,title,text,date,editable}`; title may be empty, migrated undated entries have an empty date. Paired `jcard_note` add/edit accepts optional `title` and ISO `date`; `text` is content only. Add defaults to the host local date, edit preserves omitted fields; explicit empty title clears it. CLI `--date` defaults to today on add/edit. Old devices omitting title/date remain compatible. New web clients enable title editing only when the host advertises noteFields. A first read migrates legacy leading dates after an exclusive byte-exact 0600 backup; cut/malformed input is refused. Other notebook templates retain byte-span edits and legacy fields.

## P490 — credential incident control (candidate 0.25.5)

Account owner endpoints: POST `/api/security/credentials/preview` and `/revoke` accept ≤32 public
`{id|fingerprint,rp}` targets; revoke also requires `keep_id`, a current-RP credential freshly proven by the
calling session. Account deletion is one transaction, retains that key, ends other sessions and clears add codes,
WebAuthn challenges and pending P78 requests. Registration and new P78 requests recheck live sessions at write time.
POST/GET `/api/hosts/:host/credential-revoke` requests/reads durable host revocation. POST
`/api/hosts/:host/credential-preview`, GET `.../credential-preview/:id` maps locally; replies carry only
fingerprints, fixed statuses and device short IDs. Unknown vs overwritten history cannot be inferred from a missing record.

Host POST `/v1/host/credential-revoke`, context `agentj-credential-revoke-poll-v1`, signed existing host envelope:
`{v:1,t:"credential_revoke",channel,ts,seq,epoch,results}`. Snapshot context `agentj-credential-revoke-v1`:
`{v:1,host,seat,account,channel,seq,nonce,created_at,expires_at,epoch,fingerprints,queries}`; each target is
`rp:sha256(raw credential ID)`. At most 256 persistent targets; ≤1 preview query per poll, ≤32 targets.
The next host poll acknowledges the journal epoch only after removal/session cleanup. Expired envelopes are never applied;
a new signed snapshot safely re-delivers the permanent targets. Same-epoch changes and epoch regression fail closed.
P78 gains `epoch` when supported; a host with a nonzero epoch rejects earlier grants without that epoch.

Phone controls `security_begin`, `security_verify`, `security_retire` sign `{epoch,keep}` as
`epoch + "\n" + sorted(keep).join("\n")`. Each requires that remote's Ed25519 key and a fresh WebAuthn assertion
using the existing elevate challenge with the action, nonce and object digest. `security_status` is a paired read.
Verification requires a new device not in the start-time old set, a distinct passkey and an ordinary resumed connection.
Retirement requires the calling verified device in the nonempty keep set; it removes only start-time old devices.
## P489 — owner-local layout diagnostic

A ready, allowlisted p33 device sends `{t:"layout_diag",r,sample}` through the existing Noise channel; host answers `{t:"layout_diag_res",r,ok,why?}` only after writing `<state>/layout-diagnostic-last.json` (0600, atomically replace last, 12288-byte cap). Fixed v1 fields/enum/numeric/CSS-colour schema in host `layout_diag.py`; no identifiers, raw UA, URL, storage, tickets or content. Host log: `layout_diag {status:"saved",bytes}` only. Five-second per-session limit, no Agent turn, no approval authority. `agentj diag layout --last` validates and reads; fenced callers use the existing host_diagnostics socket fixed `layout_last` operation.

Old host compatibility: after 8 seconds without the new reply, the same ready session uploads the captured sample as `layout-diagnostic.json` using ordinary `blob_*` att frames. Existing inbox staging/30-minute expiry applies; no say/claim, no automatic Agent turn, no new host log or permanent file. UI labels temporary staging separately from dedicated persistence. No fallback across a changed Noise generation or host. Host-specific persistence/log/CLI ships with a later host release; B-only deploy includes only web assets.

## P498 additions — peer reports and exact-run workflow resume

READY p33 hosts announce `friendreport1` and `wfresume1`. Only the current registered READY Noise session, still on the allowlist, supplies phone/owner provenance. Request `source`, `yes`, remote card labels and peer text never authorize anything.

`wf_resume_get {r,run_id}` (exact32hex run, no latest alias) returns `ctl_res {r,action,ok,challenge}`. Challenge keys are exactly action/channel/device/id/run_id/tsha/run_sha/nonce/issued/expires, seconds TTL120, domain `agentjarvis-wf-resume-v1\n` plus UTF-8 canonical sorted compact JSON. `wf_resume_do {r,challenge,sig}` signs these bytes with the paired Ed25519 key. It returns bounded `ctl_pending timeout_ms:620000`, then `ctl_res` with the normal resume result. Persistent nonce consumption is fsynced before enqueue; replay, changed checkpoint/contract, wrong device/channel and friend/delegated sources refuse. Non-idempotent repeats retain the owner card; enqueue rechecks contract and pairing key. History task cards carry host-constructed `wf_resume:{run_id}`; task_list exposes only actual failed/interrupted checkpoints.

Report history uses `src.k:friend_report`, owner-local remark or friend ID, and host-constructed `card.friend_report:{friend,name,ids}`. The same main-thread page is queued with unknown/group provenance, never system/phone/Same owner. Peer JSON cannot create cards. Private `peer/reports.json` owns receipts and the original authenticated thread. Ordinary batches wait60s; mandatory decisions/errors are prompt; bounds are500 records,8 rows/batch,3 batches per friend/min,20 global/min. Original messages remain in history with report_pending on backpressure. Pending batches recover on restart; uncertain callbacks never replay.

`fr_report_reply {r,id,text,n,ts,sig}` signs controlObject `id\ntext`; `fr_report_remark {r,friend,name,n,ts,sig}` signs `friend\nname` in the existing control-v1 domain. Bounds: report64hex, reply2000chars, remark80chars. Nonce, signature and trusted Noise source are all required. Replies send only explicit text on the original peer thread, after outbound policy/E2E guards. Promotion to Same owner refuses instead of forwarding to its main Agent; main context is never copied. `agentj friends reports --json` is a bounded read-only untrusted-data projection.

P498 data-card boundary: peer reports append the local peer result as a completed history card and push the owner, without submitting a prompt to the main harness. This prevents native auto-approved tools from executing peer content. The main Agent can read the bounded untrusted reports when handling an owner's request; only an explicit signed phone reply leaves via the original peer thread.
Report metadata also carries `source:{kind:unknown,role:group}`, `untrusted:true`, `execute:false` and per-ID `states` (replied/reply_uncertain). The body is rendered as inert plain text, including in the full-screen reader. Signed replies recheck the authenticated device after asynchronous privacy review; no report text creates an owner reply/component.

P498g reply recovery: an explicit unknown-result resend uses `fr_report_reply {id,text,resend:true,n,ts,sig}`. Its controlObject is `resend\nid\ntext`; ordinary replies keep `id\ntext`. The report ID is exactly64hex, so the `resend` first-line discriminator cannot collide with an ordinary reply, regardless of reply text. Removing or adding resend invalidates the signature. Only an unknown receipt accepts resend; an ordinary reply cannot reopen it. Startup changes persisted sending to unknown before ingress and updates the original card. Known-unsent failures return pending with fixed reason; uncertain transport is never automatically retried.
