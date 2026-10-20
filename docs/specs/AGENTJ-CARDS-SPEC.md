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
| device → host | `{"t":"jcard_note","r","id","entry","revision","op":"edit"\|"delete","text"?}` | paired-owner direct entry edit/delete only; no phone add; no arbitrary file path |
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

P386 colour UI: ≥44px invisible circular area at the deck card's upper-right, held with finger or mouse; no mark or icon.
B2 hue ring, palette tones and HEX/RGB fields; Escape closes. The UI chooses Noir/Lotion ink and refuses a colour below
4.5:1 contrast. Defaults remain Leo palette values; explicit owner overrides survive upgrade and resetting removes only
the override. Card creation recommends one-record-per-card stacks, requires a management skill per new custom card,
and checks slash routing, long-text wrapping, spacing, Escape and post-add focus (skill contract, not schema migration).
