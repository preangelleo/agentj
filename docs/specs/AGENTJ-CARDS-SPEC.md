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

P519 adds optional `cards[].aliases:[string…]` (≤12 unique single-line names, each ≤40 characters) and
`system:[{id,name:{zh,en},aliases:[string…]}]` to `jcard_items`. Both `name` languages always participate in Agent-prose
card-name links; the page filters switched-off ordinary cards, uses longest-first matching and links each card once.
Code, existing links, URLs and owner plain text are excluded. These fields are registry metadata over the existing
paired session; older pages ignore them, and newer pages retain ordinary `name` matching without aliases on old hosts.
No new capability, request, permission or persisted phone state is introduced.

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

### P447 / P525 live second-remote suggestion

Host sends encrypted `{t:"pair_guide",kind:"phone"|"computer"|null}` to ready p33 allowlisted sessions on device
changes and reconnect. The existing device-name classifier derives the currently paired kinds: computer without phone suggests a phone;
phone without computer suggests a computer; both kinds present or neither recognized sends null to hide it. Unknown kinds remain ignored by the web client. The hint carries no authority.
`Remotes.changed` sends `devs`, preferences and device-dependent setup rows after successful pairing (every route),
unbinding and renaming; passkey registration also refreshes the setup rows. Eviction/replacement sends the final list.
No checklist is started or reopened automatically; closed flags and signed owner choices are preserved. Reconnect
resends the list, suggestion and refreshed existing checklist, including its closed state. P72's persisted welcome
and historical onboarding milestones remain one-time facts; they do not own this live hint.



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

## P501 — WeChat ClawBot channel

Tencent iLink 2.4.10 is host-to-Tencent HTTPS, not the Agent J Noise channel. The paired Web App sends
`wechat {op:status}` for an ephemeral state projection. Writes use existing control-v1 action `wechat`, target
`{op,id,code}` spelled `op\nid\ncode`; only setup/unbind/verify are accepted, with nonce/time/device/signature checks.
Verification digits and QR PNG are ephemeral; status CLI returns counters/bound/pending/error only. Host QR PNG travels only
inside ready device Noise messages, never history. Terminal QR projection requires the existing ctl claim plus actual tty.

Out-of-scope messages become encrypted host-private pending records with inbox source=wechat/untrusted=true/execute=false.
`wechat_confirm` uses existing Ask approvals-v1 and optional passkey.elevate_challenge; summary and request ID are signed,
no batch/full/goal bypass. No decision → no main queue entry, expires24h. Explicit owner Web App natural-language claiming
uses the existing Agent-facing elevate socket `t:wechat,op:pending|claim,id`; claim requires current Send.source kind phone,
role owner (None refuses), not a boolean in the untrusted payload. HandoffSend records a normal main history turn, retains
wechat_task delegated provenance, and rechecks generation/expiry/stop in wait_ready. Existing sensitive-action approvals
apply with no owner grants. Uncertain transfer is never replayed; unbind invalidates pending/queued work and credentials.

### P508 — verified busy provenance (addendum to §22)

The paired `busy` message adds `task_count` (active tasks, native children remain separate) and `live_process_count`
(unique detected harness PIDs, or the verified source PID when no harness is identified). Each runtime row has `source`
`{kind:pid|native_thread,pid,start,thread_id?}`, `live_pids`, `detected` `{harness,model,effort,account,subscription,model_source}`,
`reported` `{harness?,model?}` and boolean `conflict`. Unknown detected fields are null; only detected values populate
legacy harness/model fields. Native child models are never copied from the parent. PID/start validation and native terminal
notifications remove lost sources; Codex also reconciles child IDs via thread/read without turns. No argv/env/auth body
crosses the wire; accounts are hashed short IDs and plan values are allowlisted. Existing read-only busy_get/detail is retained.
`busy list --json` returns this object through the local control socket (`busy_list`); `complete:false` marks the local-file
fallback when the running host cannot be reached, and must not be reported as a complete native-task count.

P508c: native Claude/OpenCode child rows optionally carry `status:running|unknown`. Unknown rows remain briefly inspectable but are excluded from `task_count`; they expire after TEMP_TTL from last child activity or confirmed running observation. `live_process_count` still counts verified host processes. Claude bounded native transcript/task results and OpenCode v1 status/v2 idle records reconcile lost end events. Empty workflow selections never overwrite argv/config detection; unknown child models never inherit host models.

P508e: `busy.counts(tasks)` is the sole active/unknown/process counter over published rows: `task_count` excludes unknown, `unknown_count` counts those rows, and `live_process_count` deduplicates their verified live PIDs including unknown children. Board/serve, CLI JSON/text (with or without `--no-units`) and capacity use it. Unit exclusion changes the selected snapshot, not the rule. Phone titles consume host counters; only pre-P508 hosts without counters retain the legacy all-running row total. Agent reports must cite these fields.

### P536 — reply destinations and WeChat forwarding

This supersedes §10.14's implicit Telegram selection and fallback for the reply-card / reader button. Settings on each
remote saves `share` (default), `wx` or `tg` per host. Bound channels alone are selectable; unbinding the selected channel
resets to system share with one notice. A temporary send failure/expired context preserves the destination. No long-press
picker, success-based destination memory, cross-channel fallback or automatic replay. File-card Telegram actions remain.

`wxbound` / `tgbound` caps announce binding; `wxfwd` announces WeChat's current send eligibility. `wechat_state` includes
`bound`, `can_forward` and greeting metadata (`needs_message`, `attempted`,
`sent`); never credentials/context values. `status.channels={tg_bound,tg_forward}` updates Telegram binding/eligibility.

A paired allowed p33 ready session sends `{t:"wx_fwd",r:<rid>,id:<finished turn id>}`; no reply words travel upstream.
Host answers that same live session `{t:"wx_fwd_res",r,ok,why?}`. Reasons: `not_configured`, `not_found`, `in_flight`,
`recently_sent`, `rate_limited`, `send_failed`. Validate current session/device/stop, current account generation, context
and completed non-silent reply; owner-redact secrets. Six attempts/device/minute, one concurrent request per turn,
30-second success dedupe. Over 4000 UTF-8 bytes uses a complete text attachment; revalidate after upload, then exactly one
platform send call. Platform errors/unknown transport outcomes are failures; no automatic retry. Explicit retry may duplicate
an unknown delivery. Logs record only device, turn ID and result. Login without context requires an owner WeChat message;
first eligible reply includes one greeting and marks the attempt before sending so uncertain greeting is not replayed.

P554 (#327): `wx_fwd` is sent whenever `wxbound`. The host sets no time window (`forward_for` is gone): any stored context
is used — a two-day-old one was delivered. Bound but no context → `why:"needs_message"` with no platform call; a
`sendmessage` refusal (`ret` ≠ 0 except -14 session expiry) → `needs_message` and `context_stale` is stored
(`can_forward`/`wxfwd` false, still tried on the next explicit forward) until a send succeeds or the owner's next message
brings a fresh context. Timeouts/-14 stay `send_failed`; unbound, stopped or poll error stay `not_configured`. The phone answers
`needs_message` with "message ClawBot first" and a one-tap system share of that reply; the saved destination is unchanged.
Opening Settings sends `{t:"wechat",op:"status"}` once so a missed `wechat_state` push cannot leave a bound WeChat greyed.

P554 追加 c (chairman 10-27): the destination is one **host** setting, preference `ui.forward_destination` (`""` = not
chosen yet, `share`, `wx`, `tg`; schema default `""`, so a config.json5 written before it validates unchanged). A host that
keeps it lists `host.forward_set: true` in the `preferences` message; a remote then shows `value.ui.forward_destination`
and writes only the signed `forward_set {r, dest, migrate, n, ts, sig}` (§8 controls check; wrong dest / non-boolean
migrate → `shape`; no Face ID — the send itself stays the owner's tap). The host writes it like `agentj config set`, logs
controls.log, and broadcasts `preferences` to every ready remote, so all remotes agree at once and after a reload.
Migration: while the host value is `""`, a remote that has a pre-P554 local `aj.forward.destination.<host>` (as stored before
this page load) sends it once with `migrate:true`; the host takes it only while still `""` (first one wins) — otherwise
`ok:true, dest:<host value>, migrated:false`, nothing written. Once the host has a value the remote deletes its local key.
While the chosen channel is unbound a remote forwards by system share (one notice) and never rewrites the host value, so a
remote that missed a push cannot reset everyone; rebinding brings the choice back. Older hosts (no `forward_set`) keep the
remote-local P536 behaviour above.

### P537: heartbeat prompt editor
On an already ready, allowed p33 session, cap `heartbeat_prompt` advertises `heartbeat_get {r}` → `heartbeat_state {r,ok,text,default,sha,update_available,path}`. Text is redacted; `sha` is full SHA-256 of the local raw prompt, not the heartbeat's short digest. Reads do not create or reset files. `heartbeat_write {r,op,text,sha,n,ts,sig}` → `ctl_res {r,ok,why}` signs action `heartbeat_write`, canonical JSON `{op,text,sha}`; op=save/merge/reset. Nonce, timestamp, device signature and prior revision all must match. Merge appends only the missing factory block, reset replaces only on an explicit saved action, save enforces16KiB/secret/control rules. It does not use or alter the current Agent's source/privilege. No prompt text is logged. Older hosts lack the cap; the phone explains that editing requires an upgrade. Scheduling is unchanged.

## P539 — own passkey per remote, Computer Use, one pre-authorization (#300)

**Passkey state** of a device = `none` (no `pk`) | `local` (`pk.local` true) | `borrowed` (`pk.local` false) | `unknown` (a
record from before P539). Host → a ready p33 session `{"t":"pk_state","s":<state of its own record>}` after every `devs`
list it gets (resume / restore via the P525 resend, pairing, pk_reg ok, any allowlist change). Each `devs` row adds
`"pk":<state>` (never an id). `fa_s` on cards: §16.1.

**ask_done** (§8 / §10) adds optional `"why"`: `timeout` · `denied` · `passkey` (the card timed out after a refused passkey
answer) · `nobody` (parked while no remote was online) · `estop` · `serve_stop`. `result` is unchanged. The Agent's timeout line
names the likely cause and the next step (ask again, or 「设置 → Agent 与遥控器 → 本机 Face ID / 指纹」).

**computer.control** (policy registry, irreversible, `uv`): every operation of the MCP servers `computer-use`, `computer_use`,
`unified-computer-use`, and the exact operation names left/right/middle/double/triple_click, left_click_drag, mouse_move,
left_mouse_down/up, type_text, press_key, hold_key, perform_secondary_action, get_app_state on any server. Main window: a
passkey card any paired remote with its own passkey can approve. Other layers: channel / friend handoff, support deny. Every
other unknown tool still denies without a card. Codex: an `mcpServer/elicitation/request` whose `_meta.codex_approval_kind`
is `mcp_tool_call`, form mode, with an empty requested schema, AND that matches an open `mcpToolCall` item Codex announced
on the main thread (`item/started`, inProgress; same serverName, the tool `'to run tool "<tool>"?'` names, same turn; each
item at most once) is that tool's approval: `mcp__<item.server>__<item.tool>` with `item.arguments` (never `_meta` or the
message, which a server's own elicitation can forge) goes through the normal card; arguments over 8 000 chars are declined
without a card; allow → `{"action":"accept","content":{}}` (never a `persist` choice), otherwise `decline`. A 「免问」 grant
covers only the known Computer Use operations on the three servers above. An elicitation with any field to fill, or with
no matching item, is still declined (local page).
Codex plugin binding: when the matched item's `pluginId` (Codex's own field, `"<plugin>@<marketplace>"`; null for a
config server) is exactly `unified-computer-use@openai-bundled` or `computer-use@openai-bundled` AND `item.server` is
`cua_repl`, the tools `js` / `js_reset` are `computer.control` (passkey card; summary 「Computer Use：在这台电脑上点击、输入并
运行操控脚本」 + the script whole + one line saying a 免问 tick frees the plugin's scripts) and `turn_ended` (the plugin's own
Stop / Interrupt hook) is accepted without a card (host.log `mcp_lifecycle_allow`). Any other marketplace, server or a
null `pluginId`: the name decides (`mcp__cua_repl__*` is unknown → denied without a card).

**Pre-authorization 「免问」**. Grantable actions: `computer.control` only (a host constant; red lines never). A card for it adds
`"allow_offer":["preauth:computer.control"]`; an allow whose §8 signature covers `allow_add:["preauth:computer.control"]`
(P296 line) AND passed the card's passkey stores `{action, device, sk, at, rid, bind}` in `<state>/preauth.json` (0600, ≤ 16;
`bind` = `{"kind":"mcp_ops"}` for an MCP-name card, `{"kind":"codex_plugin","id":<pluginId>,"server":"cua_repl"}` for a Codex
plugin card; a grant covers only requests with the same binding — one kind never covers the other; an unknown binding in
the file is dropped),
approvals.log `reason:"preauth_add"` (the signed line's digest), host.log `preauth_add`. The pick is not returned to the
calling tool. Later requests of a covered tool (the allowlisted servers only) are allowed without a card while that remote is
listed with the same approval key, no emergency stop, approval level ≠ safe, owner turn (not delegated / group): approvals.log
`reason:"preauth"`, `auto:"preauth"`, `grant`=the granting card; host.log `preauth_use`; phones `{"t":"auto",…,"by":"preauth",
"action","device","name"}`. Device → host `{"t":"preauth_list"}` → `{"t":"preauth","items":[{"action","device","name","at","via"}]}`
(`via` = the binding kind, `mcp_ops` | `codex_plugin`; a remote may hold one of each, both revoked by the same `preauth_revoke`)
(also pushed to every ready p33 remote after a change); `{"t":"preauth_revoke"[,"action"][,"device"]}` from any ready paired
remote revokes (narrowing: no signature or passkey). Automatic revocation: unpair / eviction / replacement (`_drop_device`),
a new approval key or device id (checked at use), and the emergency stop (all grants). Logs carry ids, actions and counts only.

## 40. One address per seat — m.agentj.app/<seat id> (P545, FEEDBACK #309; the number is provisional until the merge)

**Seat id.** `seat_id(channel) = base32_lower(SHA-256("agentj/seat-url/v1\n" ‖ channel)[0:5])` — 8 letters of
`abcdefghijklmnopqrstuvwxyz234567`; `channel` is the 22-character b64url channel id. One-way (the channel is 128 random bits),
so the id is public: printed by `agentj pair` on approval and by `agentj` when set up, shown on the account page's seat card
(`seat_url` in the hosts list) and on the phone. Implementations: `host/agentj/seaturl.py`, `dashboard/src/seaturl.ts`,
`web/public/js/seats.js`; vectors `vectors/p545-seat-url.json`. Nothing on the wire changes: pairing, resume, passkey
(§12, the user handle stays relay ‖ channel ‖ host key — already per seat) and renewal (§12.1) messages are as before.

**Routes (web Worker).** `/<seat id>` → 308 `/<seat id>/` (query kept; a `#p=` fragment survives). `/<seat id>/<path>` serves
the same files as `/<path>` (page routes `/friends`, `/bots` included). `/<seat id>/manifest*.webmanifest` = the shipped
manifest with `id` = `/<seat id>/`, `start_url` = `./` (or `./?c=<credential id>` when the page passed a valid `c`), `scope`
= `./`, and — with `?n=<name>` (control / format characters dropped, ≤ 32 code points) — `name` "Agent J · <name>",
`short_name` <name>. `/<seat id>/.aj/rt` is the §12.1 endpoint with the cookie `Path=/<seat id>/.aj/rt`. CSP `connect-src`
on a seat page lists `/version.json` and that seat's `/.aj/rt`. No top-level name of `public/` may have the seat-id shape.

**Links.** A host whose web root is `https://m.agentj.app` prints pairing links (and draws QR codes) as
`https://m.agentj.app/<seat id>/#p=…` (`wire.pair_base`; the QR stays version 8-M); any other web root keeps its root. The
Worker must be deployed before hosts that print seat links.

**Page.** On `/<seat id>/` the page keeps everything per seat: localStorage keys get the suffix `@<seat id>` (`js/seatscope.js`,
first script in `<head>`; the first visit copies the root page's display preferences only), IndexedDB is `agentjarvis@<seat
id>` (and `agentj-rec@<seat id>`), the service worker and its push subscription are scoped to `/<seat id>/` (each worker takes
only its own scope's `share-target`). The root page `/` keeps its old names and pairs any link it is given, as before. A seat
page pairs and restores only its own seat: a pairing link of another channel opens that seat's page; a link with the same
seat id but another channel than its own pairing is refused; a passkey or renewal ticket whose user handle names another
channel is refused (`seat.pkOther`, the other seat's address offered). With a known own credential id (`aj.seatPk`, or the
installed app's start fragment `#c=`, taken off the address bar at once) the restore asks `get()` with `allowCredentials` =
that id only. The origin's seat list (`aj.seats`, root localStorage: `{s, n, t}` — seat id, display name, last ready): a root
page with no record of its own and exactly one known seat opens that seat's page; with ≥ 2 the pairing screen lists them
(「选择要连接的席位」) instead of a discoverable passkey prompt. Move (Settings 「改用本席位专属网址」, root page only): the seat
page copies the root database's records once (structured clones; no key is exported) when the root pairing's channel is that
seat's, the seat has none and the root record is not marked yet; the root record stays, marked `movedTo: <seat id>`, and the
root page opens the seat page from then on (one page per device key on the relay). Unpairing / removal on that seat page
deletes the root's moved copy (record, sealed records and their key; the device keys stay).
The seat id is an address, not a security boundary (40 bits can be searched for); every check above that matters compares
the full channel or the user handle.

### P547 — host-issued seat transfer (Dashboard side)

Three signed host routes (contexts `agentj-host-seat-transfer-v1`, `-cancel-v1`, `-status-v1`; details DASHBOARD_API §3):
`seat-transfer {token_hash, handover:{b,mac}|null}` → 201 `{setup_id, expires_at, tenant}`; `seat-transfer/cancel` →
`{status:cancelled|none|bound}`; `seat-transfer/status` → `{status:issued|bound|revoked|expired|none, expires_at, arrival?, agent_name?}`.
The code `ajh_…` (host-issued; the account page only ever mints `ajt_`) is made on the old host; the Dashboard keeps only
`sha256(code)` and the two opaque blobs. `b` = b64url(nonce(12) ‖ AES-256-GCM ciphertext+tag), key = HKDF-SHA256(ikm = code,
salt none, info `agentj-seat-handover-key-v1`, 32 B), AAD = `agentj-seat-handover-v1\n`, plaintext = the canonical JSON
remote list; `handover.mac = HMAC-SHA256(code, "agentj-seat-handover-v1\n" + b)`; seat-bind's optional
`arrival = {x25519, relay, mac = HMAC-SHA256(code, "agentj-seat-arrival-v1\n" + channel + "\n" + x25519 + "\n" + relay)}`.
Each side verifies with the code it holds; the Dashboard only stores and forwards, so it cannot forge either.
Security review B1 / M3 (Dashboard): seat-bind takes `^ajh_[A-Za-z0-9_-]{43}$` besides `ajt_` / `AJI-`; an `ajh_` code redeems only
a `host_issued = 1` setup and an `ajt_` code never one (mismatch = `404 invalid_setup`, a failed guess); `/api/seat-setups/claim`
never matches a host-issued setup. The moved seat stays with its person: new host `bound_by` = the old host's, and the setup
inherits the old setup's holder, so that holder's `seat_holder` membership survives the move.
Security review M2 (page): `host_moved` never switches silently. Each remote shows 「席位已转到新主机「<name>」（<HH:MM of `at`>）。
让这台遥控器切过去吗？」 / "The seat moved to the new host "<name>" (<HH:MM>). Switch this remote over?" with 「切过去」 / 「留在这里」
("Switch over" / "Stay here"); only 「切过去」 rewrites the stored pairing and reconnects. 「留在这里」 changes nothing (the old host
serves until its seat certificate ends) and the same move is not asked again until the page reloads. A
`{"t":"pk_reg_res","ok":false,"why":"peer_required"}` starts the page's `pk_peer_req` flow by itself (no failure toast).


### P547 — host and phone: Face ID approved by another remote · seat transfer from a remote

**A. `pk_peer` (app messages, p33 sessions only; host `pkpeer.py`).** The host decides. D → `{"t":"pk_peer_req","r"}`.
If no OTHER listed device holds a `pk` record (a record holding D's own credential does not count) and D has none:
`{"t":"pk_peer_res","r","ok":true,"mode":"direct"}` then a plain `pk_offer`. Otherwise a request (or D's still-open one, idempotent):
`{"t":"pk_peer_res","r","ok":true,"mode":"peer","id":b64u16,"code":"NNNNNN","exp":unix_s,"online":n[,"host":1]}` — P555: also
when D holds the only key or nobody can approve (then `host:1`) (`online` 0; before P555 that was `ok:false, why:"no_approver"`, which pages
still read); refusals `why:"rate"`
(5 new requests per device per 10 min) · `"busy"` (3 open per host). `code` = `%06d` of
`int.from_bytes(SHA-256("agentj/pk-peer/code/v1\n"+id+"\n"+D+"\n"+b64u(D's X25519 pub))[:4],"big") % 10^6`.
Every ready approver (another device, own `pk`, not D's credential) — also one that connects within the TTL (300 s) — gets
`{"t":"pk_peer_ask","id","device":D,"name","at","exp","code","fa":<its own credential id>,"fa_ch":b64u(passkey.elevate_challenge(channel, approver, id, "pk_peer", id, digest))}`,
digest = hex SHA-256(`"agentj-pk-peer-v1\n"+D+"\n"+name+"\n"+code`). Approver → `{"t":"pk_peer_answer","id","ok":true,"fa":{id,cd,ad,sig}}`
or `{"t":"pk_peer_answer","id","ok":false}`; it gets `{"t":"pk_peer_answer_res","id","ok":true}` or `ok:false,why:
passkey_failed | not_approver | gone | expired` (a failed assertion leaves the card open). Approved: D gets
`{"t":"pk_peer_done","id","ok":true,"by":<approver name>}` and `{"t":"pk_offer","n","peer":id}` (no `replace`); the approval
lasts another 300 s and is offered again when D reconnects or asks again; the first successful `pk_reg` (no `replace_fa`)
under that offer replaces D's record (old key → lineage) and ends the request. Denied / expired / D's
`{"t":"pk_peer_cancel","id"}`: D gets `{"t":"pk_peer_done","id","ok":false,"why":"denied|expired|cancelled"}`; the other
approvers `{"t":"pk_peer_close","id"}`. Each decision: approvals.log `tool:"pk_peer"`, `reason:"pk_peer"`, `fa` = SHA-256 of
the four assertion fields (`agentj approvals --verify` reads it as unsigned, never bad), notice to every remote, `security` push.
One offer rule (security review M1, model §2) for `pk_offer_req`, the post-pairing offer and the offer after a ticket /
passkey restore: a direct `pk_offer` only while no OTHER listed device holds a key (D's own credential on another record does
not count); otherwise none — `pk_offer_req` is answered `{"t":"pk_reg_res","ok":false,"why":"peer_required"}` (the page
starts `pk_peer_req`), the automatic offers are silently not made. A plain offer still cannot replace a key without
`replace_fa`. Approvers are only devices whose `pk` record they registered themselves (record field `dev` = the registering
device id; a record moved by a passkey restore keeps the old id and does not approve; a legacy record without `dev` does).
If every other key is held by such a moved record, the request still opens (P555) for the terminal approval below.

**P555 — a key the host already has is recognised; the Agent J host's terminal approves when no remote can.**
- `pk_state` (§P539) of a record without `local`: a key registered under this device id (`dev` absent or = the id) is
  `local` (pk_reg always asks for a platform authenticator; the stored record is not rewritten). Only a record a restore moved
  here (`dev` ≠ the id) stays `unknown`; its `pk_state` then also carries `"fa":<its credential id>,"fa_ch":b64u(elevate_challenge(
  channel, device, n, "pk_check", n, hex SHA-256("agentj-pk-check-v1"))),"n":b64u16` (one per session, fresh with every
  `pk_state`, 600 s). The page answers from the tap: `{"t":"pk_check","r","n","fa":{id,cd,ad,sig}[,"aa":"platform"|"cross-platform"]}`
  → `{"t":"pk_check_res","r","ok":true,"s":"local"|"borrowed"}` (record `local` noted, remotes list re-sent) or `{"ok":false}`.
  A check never opens a `pk_peer` request; the page goes on to `pk_peer_req` only when it fails.
- Any other verified assertion of this device's own key (a pickup card, an approval, a signed write…) does the same for an
  `unknown` record: `local` is noted and every remote gets the list and `pk_state` again — a remote that opens a card with
  Face ID never shows 「未确认」. Records with `local` already set (`borrowed` included) are left as they are.
- Control socket `{"cmd":"pk_peer_host","code":"NNNNNN","pass"}` (ctl_auth SENSITIVE: a claim — never an Agent — plus ALWAYS
  the remote pairing password, the bar of `agentj pair`; none set → `error:"no_passphrase"`; CLI `agentj faceid approve <code>`):
  approves the one open request with that code, only while no other listed remote could approve it (`pk_peer_res.host` is
  sent only then) → `{"ok":true,"name"}` | `{"ok":false,"error":"no_request"|"approver_exists"|"rate"}` (5 wrong codes in
  10 min pause it and tell every remote). D gets `pk_peer_done` with
  `"by":"host"` and the peer `pk_offer`; open approver cards close; approvals.log `reason:"pk_peer"`, `device:"host-terminal"`,
  `agent:"terminal"` (no `fa`); notice to every remote and a `security` push.

**B. Seat transfer (host `seat_transfer.py`; Dashboard routes above).** Phone controls (controls-v1 signatures, object
`"transfer"` / `"cancel"`, target `{}`): `{"t":"seat_transfer","r","n","ts","sig"[,"fa"]}` — needs this remote's own
passkey: none → `ctl_res {ok:false, why:"passkey_needed"}`; without `fa` → `why:"passkey_required", fa, fa_ch` =
b64u(elevate_challenge(channel, device, n, "seat_transfer", n, sha256hex("transfer"))); then the same request with `fa`.
Success → `ctl_res {r, action:"seat_transfer", ok:true, prompt_zh, prompt_en, token, expires_at}` to the asking session only;
failures `why`: shape | stale | replay | no_key | bad_signature | passkey_failed | busy | unbound | not_found | billing_busy |
rate_limited | offline | error. `{"t":"seat_transfer_cancel","r","n","ts","sig"}` (no Face ID) → `ctl_res {ok:true,
status:"cancelled"|"none"|"bound"}`. Unsigned read `{"t":"seat_xfer_state","r"}` → `{"t":"seat_xfer_state","r","state":
"none"|"issued"|"expired"|"bound"|"moved"|"cancelled"|"revoked","expires_at":unix_s|null}`; the same message without `r`
goes to every remote on each change, with a notice (issued / cancelled / revoked / moved / bound) and a `security` push.
The host polls status every 30 s while issued; on `bound` with an arrival whose HMAC verifies it sends every ready remote
(and later each one on resume) `{"t":"host_moved","relay","channel","hostPub","name","at":unix_s}`; a bound without a
verifiable arrival sends no `host_moved` and names nobody: the notice is only 「席位转移码已被使用。如果不是你本人，请到账户页确认。」 /
"The seat transfer code was used. If that was not you, check your account page." The relay accepted from an arrival is
`wss://<host>`, or `ws://` only to exactly `127.0.0.1` / `localhost`; never userinfo (same rule as dashboard
`seat_transfer.ts`). Arrival wire form: `arrival.relay` = `<relay URL>#c=<new host channel>`
(the Dashboard's arrival keeps exactly `x25519, relay, mac`); the mac covers channel, x25519 and the relay URL WITHOUT the
`#c=` part. The new host applies `handover` only when the code it bound with starts with `ajh_` (a handover beside an `ajt_`
code is ignored, host.log `seat_handover result=ignored` — the Dashboard minted that code and could forge a valid mac) and
only after its HMAC verifies and `b` decrypts (source `transfer`, ≤ 10 remotes, public keys, names and public passkey parts
only); seat-bind carries `arrival` only for an `ajh_` code. Terminal: `agentj seat transfer [--show] [--out FILE] [--cancel] [--status]`,
`agentj seat setup-code [--show]` (issuing needs the administrator password typed into `sudo -k` at a real terminal; the
code is masked as `ajh_••••…` + last 4 unless `--show`; `--out` creates a NEW 0600 file — an existing file or a symlink is
refused before anything is issued; an issued code past `expires_at` is settled `expired` and dropped when the CLI reads it;
a running serve is told through `seat_transfer_notice`). `agentj login` (`--seat`, `--seat-file`, a pasted prompt) and the
Olares setup page take `ajt_` and `ajh_` codes alike.

### P550 — 0.25.13 integration: widening needs this remote's Face ID · why a new key was not taken (#318 ③, #323 补)

- `approval_set` widening (`approval.level` = `full` · `harness`, `approval.goal_nocard` = `true`, `jev.mode` = `off`) is ③
  (`confirm_tier.owner_action("approval_widen")`, read at run time): a remote without its own passkey gets `ctl_res {ok:false,
  why:"no_passkey"}` (the page shows the reason + 「在本机设置 Face ID（需另一台批准）」 → `pk_peer_req`); with one, the two
  Face ID steps as for `harness`. Narrowing never asks. `dev_unbind` (another remote) and WeChat `unbind` read the same table.
  controls.log lines of a control that carried Face ID say `"passkey":"uv"`.
- `pk_reg_res {ok:false, why}`: `proof` = the record already holds a key and this registration was neither proven with it nor
  approved by another remote (→ the page offers the peer flow), `expired` = no live offer, `unknown` = the record changed
  (unpaired / revoked), `bad` = the registration did not verify. The page never says the bare 「没记住」 any more. The same
  device registers for the same user handle with `excludeCredentials: []`, so its password manager replaces a key left behind.
