# Agent J Cards - public wire contract

Canonical contract: [protocol](../../protocol/PROTOCOL.md). Host `host/agentj/jcards.py` (+ `cardpacks/`: `card.schema.json`, the
official `notes` / `todo`), page `web/public/js/jcards.js` + `cards.css`. A host that answers announces `jcards1` in
`ready.caps`; the page shows the owner's cards below the five system cards (§30.3) and Settings → Cards only then.

**Phase 1 = template cards**: a card is `card.json` (+ `sample/<data file>`), no code. The computer reads the card's one data
file (`<state>/agentperm/cards-data/<id>/<file>`: a regular file, not a link, real path inside that folder, no
credential-looking name, ≤ `maxBytes`), redacts notes (`privacy.redact`), parses and bounds a checklist, and renders every
button's finished chat message. The page draws one of the built-in templates and never runs card code.

Messages (p33 sessions of a paired, ready device; answered to the asking session only):

| direction | message | notes |
|---|---|---|
| device → host | `{"t":"jcard_list","r","lang"?}` | |
| host → device | `{"t":"jcard_items","r","cards":[C…]}` | `C` = `{"id","name":{zh,en},"desc":{zh,en},"icon","accent","template","official","on","plus"}` — official cards first (default off), then the owner's own by apply time (default on); no paths, no messages |
| device → host | `{"t":"jcard_get","r","id","lang"?}` | |
| host → device | `{"t":"jcard_get","r","ok":true,"id","template","path","text"?\|"items"?,"actions":[A…],"plus","links":[L…],"anchors":[N…],"problem"?,"cut"?}` | `notes` → `text`; `checklist` → `items:[{"id","text","done","acts":[A…]}]` (≤ 200); `problem` ∈ `empty` · `refused` · `unreadable` · `bad_data`; `ok:false` → `why` ∈ `not_found` · `off` |
| device → host | `{"t":"jcard_set","r","id","on"}` | one card's switch; display only |
| host → device | `{"t":"jcard_res","r","ok","id","on"}` | serve logs `jcard_set` (device, `id:on\|off`) |
| host → device | `{"t":"jcard_open","id","at"?}` | pushed to every ready p33 device: open that card (or the page `chat` · `workflows` · `skills` · `memory` · `agentj` · `cards`) and scroll to `at`; navigation only. Sent when `agentj card open <id> [--at <anchor>]` asks serve on the Agent-facing `agentperm/elevate.sock` (reachable inside the fence; `{"t":"jcard_open","id","at"?}` → `{"ok","delivered"}` / `{"ok":false,"why"}`); serve checks again (a switched-on card or a page; `at` `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`), pushes at most once per 2 s (`too_soon`) and logs `jcard_open` (the id only). The page ignores it while a confirm card or Settings is open; `cards` opens the wall (never toggles it) |

`A` = `{"id","label","prompt","draft"?}`. `prompt` is the whole chat message, rendered on the computer: the source label
`〔来自卡片：<name>〕` / `[From card: <name>] ` + the manifest text with `{{card.name}}` `{{data.path}}` (+ for item buttons
`{{item.id}}` `{{item.text}}` `{{item.done}}`; item text one line, `「」` swapped, `{{` broken, ≤ 200 chars, wrapped in `「…」`).
The page shows a non-draft prompt whole on the official confirm card and sends it as an ordinary `say` only on 发送;
`draft:true` puts it in the message box. A card has no other way to act: no approval, command, network or file write — the
Agent changes the data file when asked, and the next `jcard_get` reads it.

`plus` = the skill the page header's + puts in the message box on this card's page (`/<plus> `, never sent; default
`agentj-cards`). `L` = `{"id","label","href"}` (http/https, ASCII host, no `user:password@`, no credential-like `name=` anywhere, no key shape; the page shows
the address on the confirm card, then opens a new tab `noopener,noreferrer`) or `{"id","label","jlink"}` — the internal
address form shared by everything the page opens: `skill/<name>` · `workflow/<folder>` · `doc/[<folder>/]<file>.md` ·
`card/<id>[#<anchor>]` · `page/<page>`. `N` = `{"id","label","block"}` (`top` · `content` · `actions` · `links`) or
`{"id","label","heading"}` (notes); every card also has `top` · `actions` · `links`, a checklist item's id is an anchor.
A card's address is its id (unique on the computer; names may repeat). In messages the page links `card/<id>[#<anchor>]`
and `@<card name>` (the composer writes `@<name>（card/<id>）`).