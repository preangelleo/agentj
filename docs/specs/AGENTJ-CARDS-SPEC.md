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
#435 (2026-11-03) supersedes the kind rule: a kind is sent only while exactly ONE remote is paired (that device's kind,
from its own `self_name` label, not an owner rename); two or more remotes always send null. The web shows it as the shared
bottom notice (`notifyOnce`, rect, 10 s) once per device (`aj.pairGuide.seen`), only when its last `devs` list has one
item; there is no resident element, and nothing of it enters the composer or any message.
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

### P567 terminal command facts

The host may send `cmds_state {names: [fixed command ids]}` inside the paired encrypted session after asynchronous harness detection. This is read-only metadata, not a settings write or command invocation. New clients filter it through the same fixed command table as host.run_cmds; older clients may ignore it and receive the cached list in their next preferences message. It does not replace the pref_res → preferences acknowledgement of a settings write. Execution retains signed cmd_run and the existing UV repair path.

### P568 additive channel diagnostics
`wechat_state` may include `hint` (localized fixed recovery text) and `last_forward: {ok, why?, at}` (last explicit Web App forwarding attempt, no body). Missing fields mean no diagnostic or prior result. `bound=false` with `error=binding_taken_over|binding_expired` disables polling until explicit reconnection. Old encrypted state has neither field and remains readable; optional binding_error and last_forward are retained under the existing encrypted store. Primitive non-secret Codex elicitation forms reuse §10.7 signed questions, including existing typed-answer hashes. No new approval capability or secret-bearing answer is introduced.

### P569 #374 — the X-AgentJ origin marker
Every mail a host sends carries `X-AgentJ: <kind>; o=<12 hex>`, `o` = the first 12 hex of SHA-256(`"aj:"` + the sending mailbox
address, lowercased) — no secret, nothing beyond From. A receiver treats a mail as its own (skipped) when `o` is its own, the
marker is bare (pre-0.25.19 hosts: indistinguishable from its own older mail), or any X-AgentJ mail has Auto-Submitted other
than `no` (another host's meeting notes / auto-replies stay skipped). A deliberate `agentj mail send` from another host (`o`
foreign, no Auto-Submitted) is an ordinary letter: allowlist, sender authentication and de-duplication apply unchanged.

### P570c — system scheduler task controls and setup guides
`tasks.items[].sys` is `migrated` or `changed` when a task has moved to the system scheduler; absent on unmoved tasks and older hosts. `changed` means its current contract differs and runs refuse until moved again.
`task_migrate {r,id,tsha,n,ts,sig}` signs the §8 action `task_migrate`, object `id\ntsha`. A paired signed tap follows the page's plain confirmation (chairman exemption: no passkey), is logged in controls.log, and runs off the relay loop. `ctl_pending {r,action:task_migrate,timeout_ms:2200000}` extends this request only; final `ctl_res {r,action,ok,text? ,why?}` carries the result. Bad signature, replay, stale tsha, stop, no scheduler/agent, or another migration in progress refuses. A test failure rolls back. `task_set on:false` on a moved task removes its system timer and keeps it off in Agent J.
`Src.guide?: wsl | loop` is set only on host-written `sys` notices. It renders one fixed localized signed-say button: ask for WSL systemd setup or convert the loop to a scheduled task. It grants no approval. No arbitrary Markdown reply link on a sys/task/cmd page is activated; old phones simply show the prose instructions.

## P573 exact task approval plans

An `ask` may include `task_scope`, the exact canonical JSON string the host offered. It is distinct from legacy low-risk `batch` scope. For `allow` and `deny`, append `"\ntask:" + hex(SHA-256(UTF-8(task_scope)))` to the §8 approval signature message. `allow_batch` cannot carry `task_scope`. A changed, missing or added scope invalidates the signature. The shown-summary digest also binds the complete numbered list of concrete action summaries; any listed UV action makes the whole card require UV.

The scope is `{task,actions,target,expires,uv}`. `uv` is a required boolean calculated by the host and bound by the signature: true records a plan requiring user verification. `task` identifies the host epoch, current turn, harness and working directory. Each of 1–20 `actions` is `{tool,input_sha256,class}`; `input_sha256` hashes the canonical exact tool input and `class` contains the policy action and sorted danger categories/rules. `target` is null locally or `{sender,receiver,text_sha256,relationship}` for a bilaterally verified Same owner task. Expiry is at most 1800 seconds. A local grant permits at most 20 uses, ends with its owner turn, revocation, emergency stop or signing-device/key change, and matches the exact tool, input digest and class, never other same-class inputs.

`agentj friends approve-actions <file>` requests the local plan without execution. `agentj friends tell <friend> --actions <file> <text>` signs the receiver's concrete list on the initiating phone for a new task only. Input files are UTF-8 JSON arrays of strict `{tool,input}` entries, ≤24 KB; tools need policy mappings and cannot be own-card/trust-root tools. Do not store credential values in these files.

The Same owner proof includes the §8 signed request metadata and scope. The receiving host verifies it against the remote approval key bound by bilateral pairing, exact sender/receiver, task-text hash and relationship generation; its nonce is consumed once. During that receiver task it permits only the listed exact tool/input/class while the relationship and expiry remain valid. Unlisted/changed/expired actions retain their normal confirmation. Ordinary friends, reply messages and group mentions gain no plan authority.

Local `task_grant` and peer-plan audit rows retain the signed scope, parent proof and exact action metadata (digests, no input bodies). `agentj approvals --verify` checks the parent signature and scope, task linkage and exact membership, including peer relationship proof. This extends the §8 metadata-only log schema: the canonical task scope contains identifiers and hashes, never command bodies.

The receiver recalculates each action's current verification requirement, including its harness floor. If it requires UV but the signed plan has `uv:false`, it denies the action and asks the originator to obtain sufficient authorization; it never opens a second receiving-device card. Missing or non-boolean `uv` invalidates a plan. Audit verification binds the parent's recorded verification level to signed `uv` and rejects child actions exceeding it. 清单签名绑定主机计算的本人验证等级；接收端要求更高时直接拒绝，提示发起端重新授权，不补第二张卡。缺失或非布尔等级无效，审计同时校验父授权等级与子动作要求。

任务清单与旧的低风险批量范围分开：`allow`/`deny` 的签名额外绑定 `task_scope` 的 SHA-256，`allow_batch` 不可携带。卡面列出全部具体动作，任一项须本人验证则整卡验证一次。清单只按当前任务、具体工具、完整输入哈希和权限分类匹配，不扩展到同类其它输入。本机最多 30 分钟、20 次，回合结束或撤回即失效。同主人清单还绑定发起端、接收端、任务文字哈希与双边关系代次；接收端核验配对时固定的远端审批公钥，单次消费证明，只在对应任务中放行清单内动作。普通好友、回复和群 @ 不获得该授权。审计保留签名范围、父证明与动作哈希，不记命令正文。

P573 final refusal contract: explicit peer plans carry `task_plan:true` and `task_proof`. Presence of either marker requires a valid proof before ordinary routing. A missing, malformed, modified, oversized, expired or wrong-root proof is blocked before delivery/execution and returns the fixed `task_refused {mid,why:"root_reconfirm"}` receipt. Sender signatures must use the current approval root bound by bilateral pairing; another paired remote's signature returns `root_reconfirm` without sending. Reconfirm the Same owner approval root before retrying. Never downgrade a refused explicit plan into an ordinary task or ask for a second card on the receiver. A queued plan with expired scope or turn drift is denied by `Host.ask`. Legacy tasks with neither marker retain their ordinary route.

显式清单以 `task_plan:true` 与 `task_proof` 标记；任一标记存在就必须先验有效证明。证明缺失、无效、篡改、超长、过期或根不符时，路由前拒收并返回固定 `task_refused` 回执，不投递、不执行、不降级、不补接收端卡。发起签名须为双边配对固定的当前审批根，其它已配对遥控器也不能代替；根不符返回 `root_reconfirm`，须重新确认同主人审批根。排队清单过期或回合漂移直接拒绝。两标记都没有的旧任务保持原路由。

### 17.13 Owner Task Grants and same-owner key transfer (P574, #388 / #389 / #392)

This section adds a narrow exception to §17.10–17.12: a verified Owner Task Grant (OTG) covers the explicitly listed
steps of one same-owner task, for **24 hours**, including secret steps. Ordinary same-owner data remains non-owner
provenance. No ordinary friend, external/temporary peer, group text, unsigned claim, batch/goal setting or mail exemption
creates an OTG. Sudo/admin commands retain their fresh approval and cannot use a ten-minute sudo grant.

**Enrollment and compatibility.** Same-owner packets retain `v:2` and add `caps:["otg1","sx1"]`, `roster_v:3` and
`roster`. With both ends at roster-v3, `pairing_scope` is the exact Python `json.dumps(ends, separators=(',',':'))`
string, where sorted ends each contain `[host_id,host_ed_pub,device_ed_pub,nonce,roster]`. Challenge is
SHA-256(scope UTF-8)[:32]; the six-digit code is `int(challenge,16) % 1000000`, zero padded. The browser verifies both
end descriptors and entry bindings, requires its own tuple public key to equal its locally held Ed public key and its
complete local credential descriptor to match, and recomputes challenge and code locally. Missing modern scope cannot
approve an OTG signing roster. `fr_set {friend,op:"owner-confirm-v3",value:challenge}` uses `controls.signedWriteFa` and must be signed by the exact
device Ed key in that end's tuple. Its existing `{channel,device,pub,n,ts,sig}` proof adds `fa:{id,cd,ad,sig}`. Setup UV
challenge is `passkey.elevate_challenge(channel,device,n,"fr_set",n,controls.object_digest("fr_set",target))`, with target
`{friend:<other host id>,op:"owner-confirm-v3",value:SHA256(pairing_scope)[:32]}`. Both ends verify Ed signature, credential
id/device match and the UV assertion against that unique root's SPKI, requiring UP+UV. Initial root setup therefore takes
one existing-passkey UV confirmation on each side; daily OTG use still takes only the initiating remote's one UV signature.
Legacy Ed-only consent can retain natural-language Same owner behavior but cannot establish OTG signing roots.
Modern enrollment requires both sides' explicit confirmation; a `group:same-owner` start
alone is not consent. The old `owner-confirm` opcode cannot authorize a modern roster.

The **initial root is exactly one Ed→SPKI tuple per end**, matching that end's actual confirming Ed key. Other entries may
be displayed in the complete scope, but remain untrusted, labelled pending approval by a recognized remote. Neither a
self-signed descriptor nor its inclusion in a host-supplied list grants introducer authority. No first-sight/TOFU roster
trust. Existing same-owner relationships repeat the ceremony before OTG/key use. Check capabilities first (`old_peer`),
then established roster (`no_roster`); unsupported operations do not fall back to secret text or host account claims.

A core entry carries `{id,alg,spki,rp,dev,name,sign_pub,bind}`. The Ed binding signs
`"agentj-owner-roster-v1\n" + canonical JSON({id,alg,spki,rp,dev})`. `roster {list,caps}` / `roster_ack {h}` synchronize
over Noise KK. Updates may narrow established trust. Additions require a UV introducer proof from an already pinned
remote over the new device and `sign_pub`, plus that device's exact Ed→credential binding; self-binding alone cannot
introduce. Existing passkeys migrate with `roster_bind_get` / `roster_bind_offer` and a UV assertion over the binding
challenge, then `roster_bind` with `fa` and `roster_sig`; this uses WebAuthn get, not credential creation or vault reset.
An existing other remote is introduced with `pk_peer_req {roster:true}`, reusing the original introducer card and UV
approval. Ending, removing or blocking a relationship clears trusted grants/roster and advances the local
`owner-epochs.json` cutoff at `now+600`; received OTGs issued at or before it are refused, including old grants never
previously seen. Stop cutoff uses the same conservative future skew window. It also temporarily blocks newly issued grants
for approximately ten minutes after either cutoff event; ordinary chat is unaffected. `roster.grant_after` and
`sx_err.usable_after` advertise/cache the usable-after boundary, allowing CLI refusal with the deadline before asking for a
signature. `usable_after` is one integer second beyond the persisted cutoff.
Re-pairing cannot revive an earlier relationship's authority.

**Signed object.** OTG v1 has exactly `{v,origin,hosts,lang,title,why,iat,exp,nonce,steps,tid}`. `origin` is the initiating
host; `hosts` maps host ids to displayed names (2–6); `steps` contains 1–12 indexed entries. `exp-iat` is positive and at most
86400 seconds; future issuance tolerance is 600 seconds. `nonce` is random 128-bit hex. `tid` is the first 32 hex digits of
SHA-256(`"agentj-owner-grant-v1\n"` + canonical JSON of every field except `tid`), using sorted keys, compact separators and
UTF-8 unescaped Unicode. Secret entries are `{i,cat:"secret",name,from,to,dest,src}`; one endpoint must be the origin. An A-origin B→C step is rejected; express B→A and A→C as two listed steps under one signature. Other
entries have `cat:spend|delete|send|price`, `host`, `what` and `max`. Delete/send retain applicable goal-structured
`match/dir/paths/prefix/upload` scope. Spend/price additionally require `exact`, the complete action text; `usd` is refused.
Only identical full action text and remaining use count can match: changing parameters, command chaining, amount or currency
is uncovered. This is not a dollar-budget enforcement mechanism. The older goal `usd` matcher gap is separate existing
behavior, not fixed or relied on by this OTG path.

The existing approval `ask` frame adds public `owner_grant:otg`. Before producing its assertion the browser locally recomputes
canonical sorted JSON and tid, validates the summary's final id/warning lines, requires integer timestamps with
`iat <= now+600`, `exp-iat == 86400`, and `exp > now`. Missing or changed metadata is refused.
The existing approval card displays `owner_grant.card_text(otg)` under tool `agentj secret grant`, including the reason,
origin, all steps, validity and tid. Only the paired remote that started this owner task can answer. Its existing WebAuthn
UV assertion signs the existing approval digest and challenge. Forwarded proof is exactly
`{tool,summary,channel,device,rid,kind,sig,fa:{id,cd,ad,sig}}`; the receiving host independently reconstructs the displayed text,
checks tid/lifetime, looks up the credential in that friend's pinned roster, derives the ordinary elevate challenge and
verifies the assertion. `kind` must be sandbox or perm. Required outer `sig` is the existing Ed approval-answer signature,
verified against the same roster entry's `sign_pub` with `approvals.signed_message(channel,device,rid,"allow",shown_digest(TOOL,summary))`;
the roster dev must equal proof.device. Both the Ed allow answer and FA assertion must verify. A generic control-card UV
assertion repackaged as an OTG is insufficient. A claimed device name or boolean approval is not proof. `otg_put {otg,proof}` sends the signed
object to named target hosts; duplicates do not reset persisted usage. `otg_put {otg:null,proof:null,revoke:tid}` requests withdrawal; the receiver tombstones it and answers
`otg_revoke_ack {tid}`. The origin records `grant-revokes.json` retry intent before removing its local live grant, retains
per-target acknowledgements, and resends unacknowledged revocations after restart/reconnect until expiry. CLI reports local
withdrawal and the remaining pending-target count; it does not promise instantaneous global revocation. Tombstones prevent replay.

**Task binding and use.** Local `agentj secret grant --file <plan.json>` signs `{title,why,steps}`; `secret ask/give` can
create a single secret-step grant or use a matching unused step of the same initiating phone task. Local task scope uses the active named goal id when present, otherwise a stable owner-task id separate from the execution id. An authenticated paired-phone message joins that ordinary task only in the original device/session by replying to its recorded message, or by the exact whole-message continuation “继续同一任务” / “continue the same task” when exactly one signed task is eligible. Other new messages create a fresh task; ambiguous continuation does not select a grant. Continuation preserves the original 24-hour expiry and remaining counts. A fresh execution admits only the registered native tool call in that live turn; the UDS CLI alone, same-UID callers, old background processes and external sources cannot claim another execution or continuation. PID ancestry is not an authorization proof. A named
goal persists `owner-task-context.json` with original device, session, goal scope, grant ids and expiry. Only host-managed
compaction/reconnection `recovery_source` can issue an `otg_resume` handle for that same live context. The handle resumes
existing grants; ordinary system/scheduled/foreign sources cannot mint it, reuse unrelated grants or sign new grants. Only an explicit `agentj friends tell <friend> --grant <tid> <task>` attaches
`pmsg.otg:[tid]` plus `otg_bundle:[{otg,proof}]`; ordinary tell/group traffic does not automatically attach live grants.
The public signed bundle permits idempotent verification/recovery when the earlier otg_put was not seen; it carries no
secret values. Provenance retains only verified tid/fid and `otg_required`, never the full bundle. The sender binds that grant to one
outbound task message per target. On receipt, the host validates ids (at most four), origin, target and live roster and binds
them to that inbound task message before preserving `fid/otg` in same_owner provenance. Receiving approval policy checks
this verified task source and the constrained matcher, then durably consumes the matched step count. A nonempty `_REQUEST`
source must match the current same_owner `fid` and entire `otg` list; a child cannot borrow another task’s authority. Unmatched,
exhausted, opaque, suspicious or credential-bearing tool calls receive no grant exemption. Reuse in another task, replies,
ordinary friends or a different owner source does not gain approval authority. An `otg_required` task cannot open a holder-side approval card. For an unlisted key step, the verified receiving task sends `otg_more {r,tid,op,name,why,dest}` only to the original origin (`op:ask|give`, random 128-bit `r`). The origin requires the original task still active, the original signing device, a live grant, Same owner, rate limits and a previously unseen request id. It reverses ask/give and requests fresh UV on that original device for a new 24-hour OTG, without changing the old OTG. Standard `sx_*` frames carry the key; `otg_more_res {r,result}` returns only metadata or refusal. A changed task, revoked signer or confirmation timeout refuses; no alternative device or broadcast is permitted. Other uncovered/invalid sensitive steps are refused and reported for a new explicit plan.
`origin_tier` also consumes listed self-host goal steps only in the current origin task or exact named-goal recovery context,
rechecking the original live signer/assertion, nonce-bound OTG, 24-hour expiry and durable usage. Avatar/background calls or
a different `_REQUEST` cannot borrow that authority. Stop everything takes priority.

**Native execution receipt.** Secret CLI calls and `friends tell --grant` require a live admitted native tool call. They first return only `{result:"pending",ticket,request_sha256}` (grant-bearing tell also has `ok:true`); this is neither key delivery nor task dispatch. The broker matches the trusted successful tool result/PostToolUse to that exact call and its issued receipt, rechecks the live execution, then executes the operation. Native denial/failure/cancellation performs no transfer, dispatch or grant-count consumption. A bare UDS CLI/friends.sock call cannot attach grants. Final metadata arrives through `SECRET_TASK_RESULT`; grant-bearing tell returns its existing `{ok,friend,name,mid,route}` result there. Ordinary chat tell keeps its existing path and receipt. No second UV or new card layout is added. Harness qualification remains in REPORT-574.

**Transfer frames, all inside Noise KK.** `sx_req {r,otg,proof,i,rk}` pulls a key; holder replies
`sx_give {r,tid,i,epk,n,ct,fp}`. A push starts `sx_offer {r,otg,proof,i}`, receiver answers `sx_rk {r,rk}`, then holder sends
`sx_put {r,epk,n,ct}`. `sx_ack {r,fp}` completes a move; `sx_err {r,code}` refuses. `r` is a random 128-bit request id;
`rk` is a one-time receiving X25519 public key. The authenticated friend channel binds that key to the peer; it is not a
standalone unauthenticated key claim. Sender generates an ephemeral X25519 key `epk`, derives AES-256-GCM key via
HKDF-SHA256 with salt SHA-256(`tid + "\n" + i`), info `agentj-secret-xfer-v1`, and a random 12-byte nonce `n`.
AAD is `tid + "\n" + i + "\n" + name + "\n" + from + "\n" + to`. These inner sealed bytes remain inside Noise KK;
the relay sees ciphertext. Values are limited to 16384 bytes. Incoming transfers are limited to 5/600 seconds and 20/day
per friend, with at most 3 open requests and a 120-second peer wait; consuming a secret step is durable and one-use.

A **remote holder** reads only a registered name. A supplied `src` must canonicalize to that name’s registered source;
remote authorization does not open arbitrary same-user readable files. The origin may explicitly select a local `src` for a
newly signed give; the card shows it. When reusing a grant, including an empty CLI src, the writer must use the signed src
or registered source, and a different CLI src is refused. The receiver writes through the existing `agentj secret` destination validation, file permissions, registry and audit path.
The Agent sees names, result, length and fingerprint, never value bytes. Logs contain names, state, hashes and fingerprints;
no secret value is written to conversation, audit or activity. This is encrypted transfer, **not encrypted-at-rest storage**:
existing local secret files remain accessible to software with the same OS-user authority. A compromised authorized host
can access what that user can access; a lost remote needs WebAuthn user verification to sign a new task, but a compromised
unlocked remote is not made harmless. Holder notifications reuse existing notices: `owner_grant_notice {peer,grant,text}`. Signed
`fr_set {friend,op:"owner-stop",value:grant}` requires a currently paired device and a received grant from that friend; it
tombstones the grant, ends Same owner, clears requests/roster/grants, blocks the friend and disconnects its transport. This
disables this host’s recognition of that peer’s signatures; it does not delete the peer’s phone or globally revoke passkeys. Stop everything records a persistent stop epoch, queues withdrawal of outgoing grants, tombstones/clears incoming grants,
clears pending transfers and settles current transfer waits with estop, without ending the friendship. A later resume cannot
revive those existing grants or a pre-stop OTG. Revocation/stop prevent future use and cannot reverse completed actions or
undo an OS command already executed.

## 41. Official remote assist — signed tasks, never a terminal (0.26.9 #468; ADR-0269-w2-assist; number provisional until the merge)

Host: `host/agentj/assist.py` (+ `assist_keys.py`, `assets/assist_keys.json`). Page: `web/public/js/assist.js`. The helper is a
PeerNet peer (§17) whose authenticated static key equals a pinned `assist_keys.json` key, accepted ONLY while a grant for that
key id is live on this seat. Nothing terminal-shaped exists for it: no RESUME as a device, no `term_*`, no key input.

**Capability.** `ready` / `approved` caps carry `assist1` only while a usable key is pinned (the shipped placeholder = absent).

**Grant (phone → host, p33, passkey remotes only).** Step 1 `assist_grant {r, minutes}` (minutes ∈ 15 | 30 | 60, default 30) →
`ctl_res {action:"assist_grant", ok:false, why:"passkey_required", n, fa, fa_s, fa_ch, key_id, seat}`; `seat` = the §40 seat id;
`fa_ch` = `elevate_challenge(channel, device, n, "assist_grant", n, SHA-256hex("assist_grant_uv\n" key_id "\n" seat))` (minutes are
not in the Face ID challenge: the chip may change between the steps; they are in the signature). Step 2 `assist_grant {r, minutes,
n, ts, sig, fa}`: §8 control-v1 signature, action `assist_grant`, object text `minutes \n key_id \n seat` (wire.js
`controlObject`), nonce = the host's `n` (120 s, once). → `ctl_res {ok:true, until, id}` (`until` unix seconds) or `ok:false, why`
∈ shape | estop | unavailable (no pinned key / no usable seat) | no_passkey | busy (a grant is live) | replay | stale |
bad_signature | no_key | passkey_failed | io. Revoke: `assist_revoke {r, n, ts, sig}` (page nonce, object text `revoke`, no Face ID)
→ `ctl_res {ok}`. controls.log: action, device, result, object SHA-256, grant id, minutes — never text.

**Host → phones.**
- `assist_state {active, until, id?, key_id?, tasks:[{id, state}]}` — on grant, end, every task change and on (re)connect;
  `active:false` = no live grant (`until` 0). `tasks` = the live grant's last 20.
- `assist_task {id, ts (ms), gid, text, state: queued|running|done|refused|stopped, why?, turn?}` — `id` = the helper's message id
  (hex 16); `turn` = the history page carrying the task and its reply; `why` on refused/stopped/done-with-problem ∈ length |
  admission_filter | busy | quota | main_unavailable | submit_failed | ended | interrupted | failed | outbound_gate | not_sent. Sent
  on every change and replayed (live grant only) after a reconnect.
- `assist_notice {what: grant|expired|revoked|refused, minutes?, why?}` — `revoked` carries why ∈ revoked | estop | seat |
  unpaired | key; `refused` = a grant step refused.
- The task's chat page: src `{"k":"task", "via":"official_assist", "name", "text", "assist": <task id>}`; the reply fills that page.

**Helper ↔ host (PeerNet app frames, KK session with the pinned x25519 key).**
- `assist_hello {ts, sig}` — sig = pinned Ed25519 over `"agentj-assist-hello-v1\n" ‖ canonical_json({"to": <this host's Agent ID>,
  "ts"})` → `assist_ready {gid, seat, until, seq}` (grant id, seat id, end, last accepted seq). Nothing else is offered.
- task = `pmsg {mid, thread, text, ts, assist:{gid, seat, seq, ts, sig}}`, sig = pinned Ed25519 over `"agentj-assist-task-v1\n" ‖
  canonical_json({"gid","seat","seq","ts","mid","text"})` (sorted keys, no spaces, UTF-8). Accepted only when: the session peer is
  the live grant's key, signature valid, gid = grant id, seat = grant seat, |ts − now| ≤ 120 s and inside the grant, seq > last
  seq (persisted in grant.json before the task runs) and the `mid` not seen before in this grant (else `replay`). Then text ≤ the friend-task limit (8000), the friend admission filter, ≤ 4
  waiting, ≤ 64 per grant. → `pack {mid, s:"got"}` or `pack {mid, s:"blocked", rule:<why>}`.
- result = `pmsg {mid, thread, irt:<task mid>, text, ts, assist:{gid, seq}}` — the page's reply (≤ 6000) after the outbound
  credential gate AND the friend admission filter, like a same-owner `tell` (a hit → a fixed "withheld" line, why `outbound_gate`). Exactly one per finished task; none after the grant ended.
- Any other peer, frame, a frame outside a live grant or failing a check: no row, no answer, one host.log line
  `assist_refused reason=<no_grant|type|shape|bad_signature|wrong_grant|other_seat|stale|replay|bad_hello>`.

**End.** Revoke, expiry, stop everything, unpairing of the granting remote, a seat change or the key leaving the pinned list end
the grant: queued tasks cancelled, a running task's turn interrupted (state `stopped`), the helper's session and outbox dropped.
A serve restart keeps an unexpired grant; every ended grant id is appended to `revoke.json` (all reasons) and is never live
again. Files `<state>/assist/` (0700): `grant.json` (0600: id, key_id, seat, granted_by,
minutes, not_before, until, seq, revoked?), `revoke.json` (0600, append-only: every ended grant id; flock `revoke.lock`), `log.jsonl` (0600, 30 days, the owner's own audit incl.
task text). CLI `agentj assist status|log|revoke` — there is no grant from the CLI.

### 0269 wave 2 — phone side of #468 (§B3) and #471 (§D) (lane w2-web; CANDIDATE, the CEO aligns the host lanes)
What the page reads; the host lanes own the emitting side. Unknown fields are ignored, malformed messages dropped.
- `assist_state {active, until, id, key_id, tasks:[{id, state}]}` — `until` in s or ms; `tasks[].state` ∈ queued · running ·
  done · refused · stopped (others dropped); inactive or past `until` = no banner, feed wiped. The wave-1 fields `opened`,
  `viewers`, `cols`, `rows` are no longer read.
- `assist_task {id, ts, text, state, why?}` (host → phones, every state change; a later frame may omit `text`) — `id`
  `[A-Za-z0-9._:-]{1,64}`, `ts` s or ms, `text` display text (control characters stripped, ≤ 4000 shown), `why` a code
  `[a-z_]{1,32}` (worded on the page: expired · revoked · estop · too_long · replay · stale · busy, else a generic line). Read
  only while a grant is active; the page sends nothing back for it.
- The task's turn in history: `src.k:"official_assist"` or `src.k:"task", via:"official_assist"` (both labelled
  「官方远程协助」 / "Agent J support"); optional `src.assist_id` (or `task_id`) = the `assist_task` id links the feed entry to
  its reply; without it the page matches the newest such turn whose `src.text` contains the task text.
- Terminal frames carrying `assist:true` are dropped by the page (never routed to the owner's terminal). The page never
  sends `term_*` for assist.
- `msg_steered {mid, turn}` — `mid` = the owner message's turn id (integer) or its send id (`sid` string); the page drops it
  from its queue, shows 「已插入当前这一轮」 on that page, never the queued notice. A history `card.steered:true` on the
  message's turn reads the same (survives a reload).
- #471 card split is page-only (no wire): an open turn with a visible reply followed by an owner message (`src.k` phone ·
  telegram · wechat) → later output on a continuation page (same turn id). 「停下上一件」 on the owner's own queued page sends
  the signed `rescue_do {action:"end_turn"}` when the host has shown the turn watchdog (any turn carrying `waited_s`/`stuck`),
  else `/stop` as in P481.

## 42. Card placement, card scope line, day / month budget (0.26.9 #472 / #473 ③, CONTRACT §E6 / §E3; lane w3-cards, phone side; number provisional until the merge)
- **Placement (page-only, no wire):** every card the page pops renders under the current reply card (`#reply-notices`); registry
  `web/public/js/cardplace.js` `CARDS`. Registered full-screen exceptions: the operating system's passkey / Face ID sheet (WebAuthn),
  the owner-opened 「全屏」 reading of an unlocked key, the page's own disconnected state.
- **Optional card fields (host → phone, additive; older pages ignore them):** on `ask`, `elev`, `secret_out`: `cls` string (one of
  `spend_over_budget` · `secret_reveal` · `new_recipient` · `public_publish` · `permanent_delete` · `irreversible`; other values show no
  label) and `why_scope` string (one line, ≤ 300 shown) = why the owner's task scope does not cover this step. Not signed, display only.
- **`budget_state` (host → phone):** `{t:"budget_state", day, month, spent_day?, spent_month?, currency}` — `day` / `month` number ≥ 0
  or `null` (= no budget, nothing runs without asking), `spent_*` number ≥ 0 only when the host knows it (the page never estimates),
  `currency` ISO 4217 (3 capitals). Sent on connect and after every change. No `budget_state` = the page hides 预算.
- **`budget_set` (phone → host, §8 control-v1):** `{t:"budget_set", r, day?, month?, n, ts, sig[, fa]}` — only the changed fields;
  signed object = canonical JSON of the sent fields, e.g. `{"day":10}` / `{"day":10,"month":400}` (wire.js `controlObject`; the host
  mirrors it in `controls.object_text('budget_set')`). Raising either value → `ctl_res {ok:false, why:"passkey_required", fa, fa_ch}`,
  the page asks Face ID and resends the SAME signed object with `fa`; lowering → `ctl_res {ok:true}` without Face ID; then a fresh
  `budget_state`. Refusals: `bad_signature`, `shape`, `passkey_failed`, `no_passkey`.


## 43. Fewer interruptions — card class, owner budget (0.26.9 #473 / #469; ADR-0269-w3-policy; number provisional until the merge)
Host lane w3-policy. Unknown fields are ignored by older pages; nothing here changes what a signature covers.

### 42.1 Card class on every approval card (`ask`, additive; web lane w3-cards renders `cls` + `why_scope`)
- `cls` ∈ `spend_over_budget` · `secret_reveal` · `new_recipient` · `public_publish` · `permanent_delete` · `irreversible`
  (every other table row: self_protect, fetch_run, secret_read, opaque, admin, own_config, other).
- `why_scope` — one line (≤ 300 chars, card language): why the owner's task does not cover this step.
- finer, optional: `policy_class` (the row id of `host/agentj/approval_policy.py` ROWS), `policy_name` (plain words),
  `policy_provisional` (boolean: the row waits for the chairman's signature).
Display only: the signed `summary` / `shown_sha256`, `passkey_required`, `cat`, `why`, `reason` keep their meaning. Face ID
is still exactly `passkey_required`. In an owner-authorised completion turn (§42.3) the signed `summary` gains one last line
「主人原话：「…」」 / "The owner's words: “…”" (≤ 80 chars of the owner's message).

### 42.2 Budget (`host_info.budget: 1`)
- `budget_get {r}` (paired, p33) → `budget_state {r, day, month, currency}`; also pushed to every paired remote after a
  change (no `r`). `day` / `month`: number ≥ 0, ≤ 1 000 000, at most 2 decimals, or `null` (both `null` = no budget: every
  spend asks with Face ID). `currency` ISO (`USD` default, `CNY`; not settable from the phone yet). `spent_day` /
  `spent_month` appear only when the host knows the exact amount (constitution: never estimated) — today never.
- `budget_set {r, day?, month?, n, ts, sig[, fa]}` — only the changed fields; §8 control-v1, action `budget_set`, object
  text = wire.js `canonicalJson` of the fields sent (sorted keys, no spaces, integral numbers without `.0`), e.g.
  `{"day":10}` or `{"day":10,"month":400}` (host: `controls.object_text` → `budget.canonical`). The result = the budget in
  force with those fields replaced. Raising (any higher limit; from none counts as from 0; dropping one limit while the
  other stays) needs this remote's Face ID, two steps like `approval_set`: without `fa` → `ctl_res {action:"budget_set",
  ok:false, why:"passkey_required", fa, fa_s, fa_ch}` with `fa_ch` = `passkey.elevate_challenge(channel, device, n,
  "budget_set", n, object_digest)`; then the same signed object + `fa`. Lowering / clearing: one signed message. Results:
  `ctl_res {ok:true, day, month, currency}` or `ok:false, why` ∈ shape · estop · no_passkey · passkey_required ·
  passkey_failed · stale · replay · no_key · bad_signature.
- The host keeps the newest signed record per axis and re-verifies each with the device's current key on every read; a
  hand-edited file or an unpaired / re-keyed signer = no budget. The terminal (`agentj budget set --day N|clear --month
  N|clear`) can only lower.

### 42.5 Slot 0 card: who typed the values
- `ct_card {mode:"owner", …, proposed?:"agent"}` — present when the e-mail / phone on the owner's own card came from the Agent
  (`agentj contacts owner set --email/--phone` from an Agent shell); absent for the host's own allowlist suggestion and for the
  owner at a real terminal (a TTY; display hint only — the owner still confirms on the phone either way).

### 42.3 Owner-authorised follow-ups (host only, no new frame)
A background completion turn (`CompletionSend`, source `system`) whose every job was launched in an owner turn carries that
turn's own words (host-recorded, ≤ 300 chars, credentials redacted) for 24 h: the Agent may do the follow-ups the owner
asked for (e.g. `agentj mail send`); a step that needs confirmation raises the owner's normal card (parked while nobody
answers), never `group_source`. Group / visitor / friend / official-assist / unknown sources are unchanged.

### 42.4 Waiting instead of refusing
At the default level an owner-scoped card nobody answers within the approval timeout stays on the phone (parked, as when no
remote is online): `ask_done` is not sent until it is answered or withdrawn after 30 min; approvals.log `unanswered`.


<!-- generated: Relay/Codex/509-510-r2, 2026-11-06 -->
#510 r2 service restart: `service` / `service.restart` identifies the exact own-service restart or recognised bounded user-timer wrapper; classification alone does not remove UV. Only the Linux live host proof admits the exception. Signed Full access executes the host's fixed restart action and emits one short `auto` line; other levels require the existing paired-device signed answer, `require_passkey:false`, with the brief-disconnection warning. Proof failure, unsupported platform, overrides, mixed secret/hook/approval writes and dangerous combinations retain their existing rules. The original shell is suppressed: successful host scheduling returns `behavior:"deny",host_handled:"service_restart"`, never permission to execute it again. An uncertain scheduling result explicitly forbids retry. Approval audit, source, nonce, signature, stop, expiry and timeout-refusal checks remain. Private read-only ctl `service_restart_ready` exposes only `{ok:true,ready:boolean}` and changes no authority. The short note follows appearance language; the original signed description remains in expanded details.

## Feedback #547 — read-only proof for a publisher-selected seat preview

This additive `p33` Noise application message grants only a short web-resource navigation lease. It does not change §8 approval signatures, pairing, device restoration, relay routing, or host capabilities. Older hosts ignore the request and the browser keeps its formal page.

- Paired ready phone → host: `{t:"web_preview_attest", r, message}`. `r` uses the existing request-id rule. `message` is an exact compact JSON string (at most 1024 characters) with keys in this order: `{v:1, origin:"https://m.agentj.app", seat, manifestSha256, devicePublicKey, nonce, expires}`. `seat` must equal the host's channel-derived eight-character seat id. `manifestSha256` is 64 lowercase hexadecimal characters. `devicePublicKey` is the current device's stored Ed25519 approval public key, base64url. `nonce` is a fresh 32-byte base64url value issued by the web Worker's nonce-only Durable Object. `expires` is an integer Unix second strictly after the host's current time and at most 60 seconds ahead. Extra keys, noncanonical JSON, wrong origin/seat/key, invalid metadata, stale sessions and unpaired/revoked devices cannot obtain a signature.
- Host → asking phone only: `{t:"web_preview_proof", r, ok:true, signature}` or, for a valid ready request whose metadata cannot be attested, `{t:"web_preview_proof", r, ok:false}`. The signature is base64url Ed25519 under the existing host signing key over UTF-8 `"agentj/web-preview/host/v1\n" || message`. Pending or stale sessions receive no proof; a revoked device follows the existing connection rejection. The host rechecks its current allowlist before signing and before sending. No chat/approval content enters this proof.
- The browser additionally signs UTF-8 `"agentj/web-preview/device/v1\n" || message` under its existing nonextractable Ed25519 key. The Worker verifies both signatures against the device public key in the message and the publisher-pinned host key, exact configured seat/origin/candidate manifest and absolute expiry. It atomically consumes the issued nonce once before setting a Secure/HttpOnly/SameSite=Strict cookie scoped to `/<seat>/`. The cookie identifies the verified lease; it cannot choose a candidate independently of server configuration.

Each candidate-resource request checks the current publisher pointer and proof expiry. Renewal needs a fresh nonce and a new live-host proof, so revocation prevents renewal; an already issued lease may remain valid for at most sixty seconds. Previously downloaded code is not recalled. Public `version.json` hashes are integrity metadata, not a claim that all web code has a separate publisher digital signature. The preview's content hash and “Preview / 预览” labels remain visible, with the same CSP/SRI protections as the formal page and a fixed additional bootstrap SRI.

### #555 terminal command registry metadata (2026-11-06)

The paired `preferences.host` block additionally exposes `run_harnesses`, an ordered array of
`{name,label,installed,logged_in}` derived from `harness.SUPPORTED` and `harness.LABEL`. The initial installed/logged_in
values are null (unknown); background local detection updates those booleans or nulls in `cmds_state.harnesses` beside
the existing `names` array. Neither projection includes paths, credential hints or native diagnostic text. All supported
`switch_<name>` identifiers remain in `run_cmds` even when uninstalled or not logged in. The host's fixed argv and actual
switch target validation remain authoritative; this read-only enumeration grants no command execution. The webpage
generates switch rows from the registry metadata; diagnostic rows retain the existing fixed table. Older clients may
ignore the additional field.

### #468 official-assist public discovery (0.27.0 candidate)

`DASHBOARD_API.md` §#468 freezes the discovery interface. Customer host POST `/v1/host/assist/publish` is its ordinary host
signature under `agentj-host-assist-publish-v1`, inner `{v:1,t:"assist_publish",channel,ts,invite}`; revoke uses context
`agentj-host-assist-revoke-v1`, `{v:1,t:"assist_revoke",channel,ts,gid}`. `invite` contains only the public signed fields from
`assist_operator.invitation`, never shell/output/password/device material. Both signature layers and bound usable seat are
verified. Unknown fields are rejected. TTL <=3600s and <=official pin expiry; revoke is permanent through that grant window,
including revoke-before-async-publish. Cloud list never replaces the live customer-host grant check.

Official POST `/v1/assist/list` exact `{v:1,key_id,ts,nonce,sig}` uses the pinned official PeerKeys Ed25519 identity. Signature
bytes = UTF-8 `agentj-assist-discovery-v1\n` followed by compact canonical/sorted JSON of `{v,key_id,ts,nonce}`. Unix seconds
±120s and32-lowercase-hex nonce-once; valid list response `{ok:true,invites:[...]}` is limited128 metadata rows. New root,
account, command or access authority cannot be smuggled through discovery. Host validates signed invites and establishes
fresh Noise/hello before opening; no client pairing credential crosses the discovery service.

### §41.1 — official terminal assistance (#468, 2026-11-06)

- Capability `assist_term1`; paired customer `assist_term_grant` uses the existing two-step host nonce/UV contract,
  target `{minutes,key_id,seat,scope:"terminal"}`. Control object text is minutes/key_id/seat/terminal separated by newline;
  UV digest is SHA256(`assist_term_grant\n<key_id>\n<seat>\n<minutes>\nterminal`). Old task grant is unchanged.
- Official KK app frames `assist_term_open|in|resize|close` carry gid/seat/seq/ts and a pinned Ed25519 signature. Signed bytes:
  UTF8(`agentj-assist-terminal-v1\n`) + canonical JSON of every frame field excluding sig. open/resize have cols/rows;
  in has d, optional reply=true only for the existing strict terminal-report whitelist. close may carry bounded
  summary/diagnosis/verification statements. All actions persist sequence before effect, with grant/seat/time/pin checks.
- Customer receives `assist_term_event{gid,event:<existing terminal frame>}`. term_ready supplies id/fa/cols/rows/expires_at;
  term_out retains monotonically increasing seq; term_resize supplies actual rows/cols. Only the original granting paired
  session can send `assist_term_answer{r,gid,obj:<term_line|term_confirm_no|term_sudo_ok|term_sudo_no>}`. Existing per-command
  signature, nonce and UV/PRF requirements are retained; response is ctl_res action assist_term_answer. Trusted first-login
  `elev{kind:"login",assist_gid,term}` is customer-only and uses normal sealed/signed elev_answer, not an official peer frame.
- Helper gets term_ready/out/resize/close only, never fa/epk/pct/pepk or confirmation/password material. Metadata-only
  `assist_term_end{gid,reason}` may be sent immediately over an existing KK during teardown, then peer/outbox are dropped.
- `assist_state` adds scope and discovery (publishing/ready/failed); `assist_discover_retry{r}` only republishes the same
  public invitation, at most every15seconds. `assist_notice{what:discoverable|discover_failed}` reports publication outcome.
- Customer `assist_log{r}` returns assist_log_res{r,entries,report}, bounded64KiB tail/200records. On end, customer receives
  assist_report{gid,report}; report persists customer-local by grant. No report or output is published to Dashboard.
- Invitation is public, bounded4096B, {v:1,scope:"terminal",gid,seat,key_id,not_before,until,aid,x25519,ed25519,sig}; sig is
  customer's Ed25519 over UTF8(`agentj-assist-invite-v1\n`) + canonical JSON excluding sig. Import requires derived aid,
  signature, ≤60min duration and local possession of the specified pinned official identity. A live hello still governs use.
- Cloud discovery publish/revoke and official signed-list metadata contracts are implemented in Dashboard assist routes;
  discovery remains advisory and cannot mint/revive a local customer grant.

#### §41.1 audit pagination and reauthorization

`assist_log{r,gid?,cursor?}` returns `{t:"assist_log_res",r,gid,entries,report,next_cursor,more,why?}`. Missing gid selects the
current/latest local grant. A next_cursor is an opaque string for strictly earlier data; more=false has next_cursor=null.
Each page reads≤64KiB, returns≤200 records filtered by exact gid, and accepts no paths. why=bad_cursor rejects malformed or
cross-grant cursors; why=pruned rejects a changed inode or disappeared/truncated source. A client must explicitly restart
from latest after prune; the server never silently resets an invalid cursor. A page may contain zero selected-grant records
while more=true, allowing traversal over other grants without disclosing them.

Official local registries retain authenticated end tombstones only until the original invitation expiry. After an end marker,
read/input/open for that old gid return ended, and a fresh customer grant on the same Agent identity is independent immediately.
Display/ready/end processing and queued input guards bind exact gid as well as the customer peer identity.

## 0.30.6 (0304-frozen) — any-signer Same owner, first-binding continue, automation send grants

ADRs: `agentjarvis/ARCHITECTURE.md` ADR-0304-any-signer, ADR-0306-binding-continue, ADR-0304-A3. All additions are additive;
older pages ignore the new fields and fail closed on the v4 card shape.

### Same owner scope v4: every roster-bound remote signs (amends §17.13)

- When both hosts latched it for the round (`so_v == 4` here, the peer's `same_owner` packet carries cap `any_signer`, and
  `roster_v` 3 on both), `pairing_scope` is `["agentj-same-owner-v4", END, END]` with END = `[host_id, host_ed_pub, nonce,
  roster]`, sorted by host id: no single signer. Challenge and six digits are derived from the scope as in §17.13, so the
  code covers both complete rosters. Otherwise the v3 / v2 rules of §17.13 apply unchanged; relations recorded before 0.30.6
  keep their v3 challenge.
- `fr_set {op:"owner-confirm-v3"}` (op name kept; the version is inside the challenge) is accepted from any remote that is
  roster-bound now and is exactly one entry of this round's roster snapshot, with a fresh UV. First wins: a later confirm
  answers `already_confirmed`. Both confirm cards list every entry of both rosters as "will be able to sign".
- The peer pins the whole code-verified roster when the relation becomes active. Owner Task Grants, cross-host plans and
  incoming OTG grants verify against any pinned entry; `root_reconfirm` only when the signer is outside it.
- The OTG grant card has no `only`: every remote sees it. An allow from a remote that is not roster-bound gets
  `{t:"answer_refused", id, why:"roster_binding_required", ask}`, preceded by `{t:"roster_bind_offer", entry}` when that
  remote has a passkey; the card stays open.
- New refusals on the confirm write (`ctl_res.why`): `roster_binding_required` (this remote is not roster-bound),
  `peer_too_old` (the peer has no roster support), `peer_binding_required` (the peer has the code but no bound remote). A
  friends row may carry `same_owner_why: "peer_too_old" | "peer_binding_required"` while `same_owner === "pending"`.
- Revoke (unbind, replace, revoke of a device record): every open card bound to that remote (`only`) is denied at once and
  folded on every remote (`ask_done`); unfinished rounds it started or consented to end (`declined`); the shorter roster
  is sent to every Same owner peer at once. An active roster-less relation that rested on that remote ends, with one owner
  notice. Any bound remote may unbind another with its own UV (unchanged).

### First binding continues into the pairing (ADR-0306)

`fr_set {friend, op:"group", value:"same-owner"}` from a remote that is not roster-bound answers:

| remote | `ctl_res` |
|---|---|
| has a passkey | `{ok:false, why:"roster_binding_required", next:"bind", cont:<32 hex>}` plus the existing `roster_bind_offer` broadcast |
| no passkey (host with any UV) | `{ok:false, why:"roster_binding_required", next:"passkey"}` (no `cont`) |

`cont` is a host-memory ticket bound to this device, this connection and the resolved friend id, valid 120 s, single use.
After `{t:"roster_bind_res", ok:true}` the page re-sends a fresh signed start with `cont` outside the signed object:
`fr_set {r, friend, op:"group", value:"same-owner", cont, n, ts, sig}`. The host accepts it only after `controls.check`, when
device, session and friend match, the ticket is unexpired and unused, and the remote is roster-bound now; then the start runs
as a normal one (propose → six digits). A rejected ticket is not reported: the answer is the normal start answer (`ok:true`,
or `roster_binding_required` with a fresh `cont`). The ticket grants nothing; the six-digit confirm still needs its own UV.

### Automation send grants (ADR-0304-A3; Settings, same path as `preauth_list` / `preauth_revoke`, §P539)

- client → host `{t:"automation_grants"}`.
- host → client `{t:"automation_grants", items:[{id, kind:"hook"|"task", target, actions:["mail"] | ["mail","mail+files"],
  to:[…], domains:[…], expires:<epoch s|null>, permanent:<bool>, max_uses:<int|null>, uses:<int>, created:<epoch s>,
  state}]}`. `id` is 32 lowercase hex; `target` is the webhook's display name at grant time or the task id; `domains` are
  punycode; `permanent` is `expires === null`; `state` is one of `live | expired | stale | revoked | used_up` (`stale` also
  covers an unverifiable record, a deleted hook or task, Stop everything after issuance and a changed sending account). Live
  first, then newest; at most 100 items.
- client → host `{t:"automation_grant_revoke", id}` from any ready paired remote (signature only, no UV). The answer is the
  refreshed `automation_grants` list; on success it goes to every ready P33 remote, on an unknown id only to the asker. A newly
  approved grant also pushes the list to every ready P33 remote.
- Issuing is the CLI `agentj automation grant` (elevate.sock `{t:"automation_grant", op:"grant"|"list"|"revoke"}`), an owner
  turn only, one fingerprint card (`uv_list`). Refusals on `agentj mail send` from a hook or task turn:
  `automation_grant_required` (with `missing`, or `reason:"mail+files"`), `automation_grant_stale` (`reason` `estop` /
  `account` / changed target), `automation_grant_invalid`; issuance refusals `automation_grant_owner_turn`,
  `automation_grant_domain_refused {domain, reason}`, `automation_grant_context {reason}`. Logs carry ids, counts and an
  address hash only. Host: `host/agentj/automation_grant.py`.
