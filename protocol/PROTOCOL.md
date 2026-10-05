# agentj wire protocol v1 (alpha A2 + A3 §7 + L1 §8–§9; phone controls and slash commands in §8; relay parity §10)

> Source of truth for the blind relay, the host (`host/`) and the web client (`web/`). PR1: the relay only ever sees
> the bytes described in §2; everything a human types travels inside §3's Noise transport messages.

## 0. Primitives (no third-party crypto code at runtime)
| | Browser / Node | Host (Python) |
|---|---|---|
| X25519, AES-256-GCM, SHA-256, HMAC | WebCrypto (`crypto.subtle`) | `cryptography` (OpenSSL) |
| Ed25519 (host ↔ relay auth only) | WebCrypto in the relay Worker | `cryptography` |

Noise state machine: `protocol/noise.js` and `host/agentj/noise.py`, ~150 lines each, written to the Noise spec rev 34
and pinned by the cacophony test vectors in `protocol/vectors/` (both sides) plus a JS↔Python interop test.
Suites: **`Noise_IKpsk2_25519_AESGCM_SHA256`** (first pairing) and **`Noise_IK_25519_AESGCM_SHA256`** (every later connection).

## 1. Identities
- **Host**: `host_x25519` (Noise static `s`), `host_ed25519` (proves channel ownership to the relay). Files in the host state dir, 0600.
- **Channel id** = `b64url(SHA-256("agentjarvis/channel/v1" ‖ host_ed25519_pub)[0:16])` (22 chars). Public, stable per host.
- **Device**: one X25519 static key. Browser: WebCrypto, `extractable: false`, kept in IndexedDB. **Device id** =
  `b64url(SHA-256("agentjarvis/device/v1" ‖ device_x25519_pub)[0:12])` (16 chars), computed by the host.

## 2. Relay (`wss://relay.agentj.app`)
- `GET /v1/host/<channel>` and `GET /v1/dev/<channel>` with `Upgrade: websocket`; anything else (including `GET /`) → bare 404.
- **Host auth**: relay sends text `{"t":"challenge","n":b64url(32 random)}`; host answers text
  `{"t":"auth","pk":b64url(ed25519_pub),"sig":b64url(Ed25519(sk, "agentjarvis-relay-auth-v1\n" + channel + "\n" + n))}`.
  The host signs only an `n` matching `^[A-Za-z0-9_-]{43}$` (anything else → it drops the connection without signing).
  Relay checks the channel derivation and the signature, answers `{"t":"ok"}`, and closes any previous host socket (4001).
  No auth within 10 s or a binary frame before auth → close 4003.
- **Device ↔ relay**: binary frames, opaque payload (≤ 65 536 bytes). Relay text frames to devices: `{"t":"host","up":bool}`
  (sent on connect and whenever the host comes or goes). Device text frames are ignored (> 1 024 chars → close 1009).
  A device upgrade carrying an `Origin` header other than the web client's (`WEB_ORIGIN`) or a loopback page → bare 404;
  no `Origin` (native app, CLI) is allowed. Host auth text > 1 024 chars → 4003.
- **Host ↔ relay**: binary frames `[op u8][cid u32 BE][payload]`.
  relay → host: `0x01` data from device `cid` · `0x11` device `cid` connected · `0x12` device `cid` gone.
  host → relay: `0x01` data to device `cid` · `0x02` close device `cid` (relay closes it with 4010) · `0x03` BULK `cid` (§10.13;
  a relay that does not know it ignores it).
- Limits: 32 device sockets per channel (more → 4029), 65 536-byte payloads (more → close 1009), 60 frames / 10 s per device socket (more → 4029).
  §10.13 (PROMPT-33): host → relay op `0x03` BULK raises one device socket to 240 frames / 10 s (channel total 600 / 10 s) for uploads.
- Per-IP limits (L2; all channels, both roles), checked before the channel accepts the socket: ≤ 120 new upgrades per IP per
  fixed 60 s window, ≤ 64 open sockets per IP. Over either → the socket is accepted and closed at once with 4029 (reason
  `ip rate` / `ip busy`). IP = `cf-connecting-ip`: an IPv4 address, or an IPv6 /64 (IPv4-mapped → IPv4); missing or malformed →
  one shared bucket. The counters live in a per-bucket `IpLimiter` Durable Object named by a hash of the bucket; it stores only
  lease ids with expiry times and one window counter — no IP, no channel, no frame content. Leases end on close or after 5 min
  without refresh, and an idle bucket's storage is wiped.
- The relay **stores no frame content** (no logs, observability off; the only storage is the per-IP counters above). It cannot
  decrypt anything: it holds no keys.

## 3. End-to-end layer (device ↔ host, inside relay payloads)
First byte = message kind (visible to the relay — see §5):
| kind | bytes | meaning |
|---|---|---|
| `0x01` PAIR_INIT | `0x01 ‖ pairing_id(16) ‖ IKpsk2 msg1` | first contact, from a QR |
| `0x02` RESUME_INIT | `0x02 ‖ IK msg1` | reconnect of an approved device |
| `0x03` HS_RESP | `0x03 ‖ msg2` | host's handshake answer |
| `0x04` DATA | `0x04 ‖ AES-GCM ciphertext` | Noise transport message |
- **Prologues**: pairing `"agentjarvis/v1/pair\n" + channel + "\n" ‖ pairing_id`; resume `"agentjarvis/v1/resume\n" + channel`.
- **msg1 payload** (encrypted): pairing JSON `{"v":1,"name":"<device label ≤ 64 chars>","sk":"<b64url Ed25519 public key, 32 B>"}`;
  resume JSON `{"v":1,"sk":…}`. `sk` (L1, §8) is the device's approval key; optional (older clients omit it → that device
  can chat but not approve). The host stores it with the device at approval; on a resume it is recorded only if the device
  has none yet (never replaced). **msg2 payload**: empty.
- **Transport plaintext** = `len u16 BE ‖ UTF-8 JSON ‖ zero padding` so that the plaintext is a multiple of 256 bytes
  (ciphertext = 256·k + 16). JSON ≤ 16 KiB, enforced by sender **and** receiver; text ≤ 4 000 UTF-16 code units
  (what a browser's `String.length` counts). Over-long text is rejected (the session is closed), never truncated.
  **§10.0**: once both ends announced capability `p33`, JSON ≤ 60 KiB and text ≤ 20 000 code units; larger host → device
  messages travel as `frag` (§10.1). A peer that did not announce `p33` keeps exactly these 16 KiB / 4 000 limits.
- **App messages**: `{"t":"hello"}` (device → host, first DATA after every handshake — for pairing it is the host's proof that the
  device knows the PSK, because IKpsk2 mixes the PSK only into msg2; L1: a resume's hello may carry `"since":<seq>`, §8) ·
  `{"t":"approved"}` (host → device, once, after the human approves) · `{"t":"ready"}` (host → device, after a resume is accepted) ·
  `{"t":"msg","id":"<16 hex>","text":"…","ts":<ms>}` (both ways; host → device adds `seq` and `from`, §8). L1 adds the Agent and
  push messages of §8–§9. Unknown `t` values are ignored by both sides (forward compatible).
- Any decryption failure, bad kind, or out-of-order message closes that session (host sends op `0x02`).
- **Host restarts / relay reconnects**: all host-side sessions die with the host socket. The relay tells devices
  `{"t":"host","up":false}`; on the next `up:true` an approved device redoes RESUME on the same socket. The host treats data
  from a cid it has not seen `0x11` for as a new connection.

## 4. Pairing (QR) and approval — only on the host
1. `agentj pair` asks the running `agentj serve` for a pairing: random `pairing_id` (16 B) and `psk` (32 B), expires in 5 min.
2. QR = `https://m.agentj.app/#p=` + b64url(JSON `{"v":1,"r":relay_wss_url,"c":channel,"k":b64url(host_x25519_pub),
   "i":b64url(pairing_id),"p":b64url(psk),"x":expiry_unix}`). The fragment never reaches any server; the client strips it with
   `history.replaceState` right after reading it.
3. Device sends PAIR_INIT. The host **consumes the pairing on the first PAIR_INIT carrying its id whose msg1 decrypts** (one-time;
   a msg1 that does not decrypt was not made with the QR's host key — the relay never sees that key — so it does not use up the
   QR); unknown / used / expired ids are dropped and logged. msg1's payload must be a JSON object `{"v":1,"name":<string>}`,
   otherwise the session and the pairing end. Host answers HS_RESP; device decrypts it (proves the host knows the PSK and its
   static key) and sends `hello`; the host decrypting `hello` proves the device knows the PSK.
   **Deadlines** (host-enforced, monotonic clock): QR admission 5 min · every connection must reach `hello` within **30 s** of
   appearing (otherwise closed; a pairing in progress ends) · approval 120 s from `hello`, re-checked when the code is typed.
   The device label is shown to the human as one line, without control characters and with at most 4 digits, so it can never
   imitate host output or show a fake safety code.
4. The device shows the **safety code** `SAS = u32_be(HMAC-SHA256(key=h, "agentjarvis-sas-v1")[0:4]) mod 10^6`, 6 digits,
   `h` = final handshake hash. The host does **not** display it: the human types the phone's 6 digits into the host terminal
   within **120 s** (passkey entry — forces a real comparison). Match = approve; mismatch, empty input or timeout = deny (one try).
   **L2: a non-empty code also needs the approval passphrase** (`agentj passphrase set`, scrypt hash in `approver.json`, 0600):
   the control-socket message is `{"cmd":"code","code":…,"pass":…}`; `serve` checks the passphrase (after the 5-remote cap,
   before the code). Wrong → `{"ev":"pass_wrong","left":n}` and the device keeps waiting (the code is not used up); 5 wrong in a
   row → locked 1 min, doubling up to 1 h (persisted), and the pairing ends `pass_locked`; none set → `pass_not_set`. The local
   admin page (`agentj admin`) sends the same message, so a holder of its link or session still cannot approve without it.
5. Approve → device static key + label go into the host's allowlist (`devices.json`, 0600), host sends `approved`. Deny/timeout →
   host closes the device socket; it never sends that device an application message.
6. Later connections: RESUME_INIT (IK). The host learns the device static key from msg1 and **silently closes** the socket unless
   it is on the allowlist (logged as `unknown_device`). Accepted → HS_RESP, `hello` from device, then `ready` from host.
7. **Revoke** (`agentj revoke <device id>`): removed from the allowlist, then every live session of that device is detached in one
   step before any network I/O (nothing it sends afterwards is acted on, nothing more is sent to it), then closed via op `0x02`
   (best effort, 2 s each); its next RESUME_INIT is rejected. Ready sessions also re-check the allowlist on every message.
8. The host keeps at most 64 sessions (the relay allows 32 device sockets per channel; the cap also bounds a misbehaving relay).

## 5. What the relay (and Cloudflare) can see
Channel id, connection times and IPs, message kind byte, pairing id (once, one-time), frame sizes (padded to 256 B),
frame timing, the host's Ed25519 public key, the browser `Origin` of web-client connections. Never: keys, text, device labels,
device ids, safety codes. **§10 (PROMPT-33)** adds to what sizes and timing reveal: an upload or voice take shows as a run of
equal ~60 KiB frames (so its size is visible to ± one chunk, 44 KiB, and a voice take's length roughly), a history page or a
long reply as a run of `frag` frames, and a BULK op (§10.13) tells the relay that this socket belongs to a device the host
accepted. Never: file names, file types, file or audio content, transcripts, history text.

## 6. Known alpha limits (see `ARCHITECTURE.md` GAP)
No per-message ratchet (forward secrecy is per connection: fresh ephemerals every handshake). **msg1 has no forward secrecy**
(IK / IKpsk2 encrypt the device static key and label to the host's static key; the PSK enters only in msg2): whoever later
steals the host's X25519 key and has recorded msg1 learns that device's public key and label — never chat content, which is
protected by the ephemeral DH. Resume uses IK, not KK as the early design said: the host cannot know which device is calling
before msg1, and IK keeps the device's static key off the wire. Files and voice (since §10, PROMPT-33) travel end to end as
§10.3 blobs inside transport messages — the relay sees their approximate size and timing (§5), never their bytes, names or
types; relay rate limits are per socket and per
channel only (no per-IP limit); web client = "tampering would be detected" tier as a goal, not reached in alpha (source and
release hashes not public yet), and never zero-access (Z3). Per-IP relay limits exist since L2 (§2).

## 7. Host ↔ control plane (A3, A3.1, A3.2, L2, seat setup, L3.5, plaza P2): signed envelopes
The host talks to the Dashboard's control plane over HTTPS at `https://agentj.app/api/v1/host/*` (public, no Access;
config `api`, env `AGENTJ_API_URL`). It authenticates with its **existing** `host_ed25519` key (§1) — no bearer token, no
new secret. **Nothing the control plane answers can add or approve a device**: the host *pushes* metadata; the answers it
parses are `login` / `poll` / `seat-bind` / `seat-leave` status fields, — A3.1 — `sync`'s list of *unbind requests* (whitelisted) and — A3.2 — the Agent
name (a display string). An unbind request can
only remove a device, and only after the host's own checks (below); §4–§7 stay the only way in. A device that exists only in the Dashboard's database is unknown to the host and is silently refused (§4.6).

**Envelope** (every request body, `content-type: application/json`, ≤ 32 KiB):
`{"pk": b64url(ed25519_pub 32 B), "body": b64url(UTF-8 JSON), "sig": b64url(Ed25519(sk, CONTEXT + "\n" + body_field))}`
— the signed message is the ASCII bytes of the context string, a newline, and the `body` field **exactly as sent** (base64url text).
Contexts: `agentjarvis-host-login-v1` · `agentjarvis-host-poll-v1` · `agentjarvis-host-report-v1` · `agentjarvis-host-sync-v1` ·
`agentjarvis-host-rename-v1` (A3.2) · `agentjarvis-host-decline-v1` (L2) · `agentjarvis-host-seat-bind-v1` (seat setup) ·
`agentjarvis-host-seat-leave-v1` (seat setup, review SS-02) · `agentjarvis-host-templates-v1` · `agentjarvis-host-template-v1` (L3.5) · plaza P2: `agentjarvis-host-plaza-search-v1`,
`…-plaza-get-v1`, `…-plaza-mine-v1`, `…-plaza-post-v1`, `…-plaza-reply-v1`, `…-plaza-resolve-v1`, `…-plaza-report-v1` · skill & workflow plaza:
`agentjarvis-host-plaza-pkg-search-v1`, `…-plaza-pkg-get-v1`, `…-plaza-pkg-mine-v1`, `…-plaza-pkg-installed-v1`, `…-plaza-pkg-like-v1`,
`…-plaza-pkg-report-v1`, `…-plaza-pkg-publish-v1` · `agentjarvis-host-upgrade-auth-v1` (F12, 0.15) (distinct from the relay's
`agentjarvis-relay-auth-v1`, so no signature is valid in two places). Every inner body has `v:1`, `t`, `channel`, `ts` (unix s).
Server checks, in order: size and shape → `pk` is 32 bytes → `channel == channel_id(pk)` (§1) → signature → `|ts − now| ≤ 300 s`
→ strict schema (unknown keys = 400; the only optional keys are `report`'s `agent_name`, `machine`, `language` and `language_at` (A1, 0.15), plaza `search`'s `limit`, package `search`'s `type` / `sort` / `tag` /
`track` / `limit` / `offset` and package `get`'s `version`). Failures: malformed 400 `bad_request` · signature / derivation 401 `bad_signature` ·
stale 401 `stale` · then per endpoint. Vector: `protocol/vectors/host-envelope.json` (fixed key → exact `body`/`sig`).

| endpoint | inner body | answer |
|---|---|---|
| `POST /v1/host/login` | `{"v":1,"t":"login","channel","ts"}` | 201 `{"login_id","user_code":"XXXX-XXXX","verification_uri","expires_in":600,"interval":5}` · 409 `already_bound` · 429 `rate_limited` / `too_many_pending` |
| `POST /v1/host/poll` | `{"v":1,"t":"poll","channel","ts","login_id"}` | 200 `{"status":"pending"\|"bound"\|"rejected"\|"expired"\|"declined"}` + when bound `{"host_id","tenant":{"slug","name"},"agent_name"}` (A3.2: the canonical Agent name the owner gave; L2: `declined` = this host already declined it) · 429 `slow_down` (< 4 s apart) · 404 |
| `POST /v1/host/decline` (L2) | `{"v":1,"t":"decline","channel","ts","login_id"}` | 200 `{"status":"declined"}` (also for a repeat: idempotent) · 404 `not_found` (nothing to decline, see below) · 409 `already_confirmed` (this host has reported since the bind) · 429 `rate_limited` (≤ 10 per channel per hour, `retry-after`) |
| `POST /v1/host/sync` (A3.1) | `{"v":1,"t":"sync","channel","ts","results":[{"id","result"}]}` (≤ 10; result ∈ `revoked` `unknown_device` `disabled` `rate_limited`) | 200 `{"unbind":[{"id","device"}],"agent_name"}` (≤ 10, this host's pending requests; A3.2 `agent_name` = canonical name, string or null) · 403 `not_bound` · 429 `rate_limited` (L2: a sync **with** results costs one of ≤ 60 per host per hour; over it nothing is written — keep the results, retry later; a sync without results is never limited) |
| `POST /v1/host/report` | `{"v":1,"t":"report","channel","ts","seq","agent","devices":[{"id","name","paired_at","online"}],"pending":{"count","since"}}` + optional (A3.2) `"agent_name"` (string per §7 Agent-name rules, or null) and `"machine"` (hostname via `clean_label`, ≤ 64, or null) + optional (A1, 0.15) `"language"` (`zh`\|`en`) and `"language_at"` (integer ms, 0 = never set on this host) — see **Language sync** below | 200 `{"ok":true}` (A1: + `"language","language_at"` = the account's value after the merge) · 403 `not_bound` · 409 `replay` (seq ≤ last) · 429 `rate_limited` |
| `POST /v1/host/rename` (A3.2) | `{"v":1,"t":"rename","channel","ts","name"}` | 200 `{"ok":true,"name"}` · 400 `bad_name` · 409 `{"error":"name_taken","suggestions":[…]}` (≤ 3) · 403 `not_bound` · 429 `rate_limited` (≤ 20 successes per host per hour; and ≤ 60 failures — 400 / 409 — per host per hour, claimed before the name is checked, so past it nothing is checked or revealed) |
| `POST /v1/host/seat-bind` (seat setup) | `{"v":1,"t":"seat_bind","channel","ts","token","name"}` (`token` matches `^ajt_[A-Za-z0-9_-]{43}$`; `name` already normalised per the Agent-name rules) | 200 `{"status":"bound","host_id","tenant":{"slug","name"},"agent_name"}` (also for an idempotent **replay**: the channel is already bound by this same key through the setup this code belongs to — same answer, nothing written) · 404 `invalid_setup` (unknown, expired, revoked or already used — one answer) · 409 `{"error":"name_taken","suggestions":[…]}` · 402 `payment_required` (the company no longer pays for that seat) · 409 `already_bound` · 400 `bad_name` / `name_required` / `bad_request` · 429 `rate_limited` (`retry-after`; ≤ 10 failed seat-binds per IP bucket per hour, ≤ 10 per channel per hour, claimed before the code is looked at) |
| `POST /v1/host/seat-leave` (seat setup, review SS-02) | `{"v":1,"t":"seat_leave","channel","ts"}` | 200 `{"status":"left"}` · 404 `not_found` (this channel is not bound with this key, or it was bound with the 8-character code, not a seat setup) · 429 `rate_limited` (≤ 10 per channel per hour, `retry-after`) |
| `POST /v1/host/templates` (L3.5) | `{"v":1,"t":"templates","channel","ts","nonce"}` (`nonce` = 16 random bytes, b64url) | 200 `{"templates":[{"id","version","title":{"zh","en"},"sha256","files","bytes"}]}` · 403 `not_bound` · 402 `payment_required` (seat not paid / comp: grace or suspended) · 409 `replay` (this nonce was answered before) · 429 `rate_limited` (≤ 60 template requests per host per hour, list and packages together, `retry-after`) |
| `POST /v1/host/templates/<id>` (L3.5) | `{"v":1,"t":"template","channel","ts","nonce","id"}` (`id` = the path's, `^[a-z0-9][a-z0-9-]{0,39}$`) | 200 `{"id","version","title","sha256","files":[{"path","sha256","content"}]}` · 404 `unknown_template` · the same 403 / 402 / 409 / 429 |
| `POST /v1/host/plaza/search` (plaza P2) | `{"v":1,"t":"plaza_search","channel","ts","q"}` + optional `"limit"` (1–20) | 200 `{"note","query","items":[…]}` · 403 `not_bound` / `plaza_requires_seat` · 400 `bad_query` · 429 |
| `POST /v1/host/plaza/get` | `{"v":1,"t":"plaza_get","channel","ts","id"}` (`pz_` + 22) | 200 `{"note","post","replies","more_replies"}` · 404 · 403 · 429 |
| `POST /v1/host/plaza/mine` | `{"v":1,"t":"plaza_mine","channel","ts"}` | 200 `{"items"}` (this host's company's posts, any state) · 403 · 429 |
| `POST /v1/host/plaza/post` | `{"v":1,"t":"plaza_post","channel","ts","nonce","title","body","show_name"}` | 201 `{"id","status","created_at","author"}` · 400 `bad_title` / `bad_body` · 422 `secret_found` · 409 `replay` · 403 · 429 |
| `POST /v1/host/plaza/reply` | `{"v":1,"t":"plaza_reply","channel","ts","nonce","id","body","show_name"}` | 201 `{"id","post_id","created_at","author"}` · 404 · 400 · 422 · 409 `replay` · 403 · 429 |
| `POST /v1/host/plaza/resolve` | `{"v":1,"t":"plaza_resolve","channel","ts","nonce","id"}` | 200 `{"id","status":"resolved"}` (idempotent) · 403 `not_author` · 404 · 409 `replay` · 429 |
| `POST /v1/host/plaza/report` | `{"v":1,"t":"plaza_report","channel","ts","nonce","id","reason"}` (`pz_…` / `pr_…`; `spam` `privacy` `abuse` `injection` `other`) | 201 `{"id","reported","hidden"}` · 200 `{…,"already":true}` · 403 `own_post` / `admin_post` · 404 · 409 `replay` · 429 |
| `POST /v1/host/plaza/pkg/search` (packages) | `{"v":1,"t":"plaza_pkg_search","channel","ts","q"}` + optional `"type"` (`skill`\|`workflow`), `"sort"` (`new`\|`installs`\|`likes`\|`week`), `"tag"`, `"track"` (`official`\|`community`), `"limit"` (1–50), `"offset"` (0–1000) | 200 `{"note","items":[Item],"total","tags":[{"tag","n"}]}` · 400 `bad_query` / `bad_limit` · 403 · 429 |
| `POST /v1/host/plaza/pkg/get` | `{"v":1,"t":"plaza_pkg_get","channel","ts","name"}` + optional `"version"` | 200 `{"note","package":Detail,"download":{"url","expires_at"}\|null}` (`GET` the url ≤ 10 min) · 404 · 403 · 429 · 503 |
| `POST /v1/host/plaza/pkg/mine` | `{"v":1,"t":"plaza_pkg_mine","channel","ts"}` | 200 `{"items"}` (this company's packages, any state, + `remove_reason`) · 403 · 429 |
| `POST /v1/host/plaza/pkg/installed` | `{"v":1,"t":"plaza_pkg_installed","channel","ts","nonce","name","version"}` | 200 `{"name","installs"}` (idempotent per host) · 404 · 409 `replay` · 403 · 429 |
| `POST /v1/host/plaza/pkg/like` | `{"v":1,"t":"plaza_pkg_like","channel","ts","nonce","name","on"}` | 200 `{"name","liked","likes"}` · 404 · 409 `replay` · 403 · 429 |
| `POST /v1/host/plaza/pkg/report` | `{"v":1,"t":"plaza_pkg_report","channel","ts","nonce","name","reason"}` (`spam` `malware` `privacy` `injection` `license` `other`) | 201 `{"name","reported","hidden"}` · 200 `{…,"already":true}` · 403 `own_package` / `official_package` · 404 · 409 `replay` · 429 |
| `POST /v1/host/plaza/pkg/publish` | `{"v":1,"t":"plaza_pkg_publish","channel","ts","nonce","name","type","version","sha256","bytes","show_name"}` | 201 `{"id","version_id","upload":{"url","expires_at"}}` (`PUT` the bundle ≤ 10 min, `application/octet-stream` → 200 `{"name","version","state":"live"}` · 413 / 415 / 422 `bad_bundle` / `secret_found` · 409) · 409 `name_taken` / `version_exists` / `version_not_newer` / `type_mismatch` / `replay` · 413 `too_large` · 403 · 429 · 503 |

| `POST /v1/host/upgrade-auth` (F12, 0.15) | `{"v":1,"t":"upgrade_auth","channel","ts","code","version"}` (`code` = C1 `AJUP-XXXXX-XXXXX-XXXXX-XXXXX`, normalised by the host; `version` = the release the host is about to install) | 200 `{"ok":true,"version","expires_at"}` (ms) · 404 `invalid_authorization` (unknown, another account's, or this host is not bound — one answer) · 409 `{"error":"version_mismatch","version":<the grant's version>}` · 410 `expired` · 409 `already_used` (this host already spent it) · 429 `rate_limited` (≤ 10 tries per channel per hour) |

Any endpoint may answer 500 `{"error":"internal"}` (no detail); the host treats it like any 5xx.

- **Upgrade authorization (F12, contracts C1–C3, 0.15)**: the owner's upgrade email carries a code bound to (account, target
  version), valid 14 days, usable **once per bound host** of that account (every seat can upgrade with the same email). The
  server stores only a hash of it and logs nothing but an `upgrade_authorized` audit row without the code. The host never
  logs, prints or stores the code (only its SHA-256 next to the version, so a rerun after a failed install on this same host
  can continue — the server answers `already_used`, the local record matches). Host order (`agentj update apply
  --authorization <code> [--version <v>]` / `--from-email <file|->`): parse (case, spaces and dashes from mail clients are
  accepted; the mail's `目标版本：<v>` / `Target version: <v>` line) → refuse locally when agentj's own files are read-only
  (inside the fence) or the host is not linked → `update.check()` must say `newer` **and** latest == target (else refused:
  nothing installed, the code not used) → this call → only on 200: the same install commands as the interactive apply,
  pinned to `v<target>`, stdin closed (no y/N, no terminal) → the new `agentj --version` must be the target → marker
  `upgraded.json` → the service is re-installed (restart). The answer decides nothing but go / no-go: what is installed is
  pinned by the host. After the restart, `serve` sends the phones one notice in the owner's language —
  「已升级到 <v>（doctor: N ✓ / M ! / K ✗）」 / "Upgraded to <v> (doctor: N ✓ / M ! / K ✗)" — once (the marker is removed as
  it is read; ignored after 14 days). Exit codes: 0 ok · 1 failed or nothing could be checked (retry later) · 2 refused here ·
  3 invalid authorization · 4 version mismatch · 5 expired · 6 already used · 7 rate limited · 8 `unsupported` (a bare
  404 without a JSON `error`: a ≤ 0.14 server has no upgrade-auth route; nothing installed, the code not used); the last lines are a fixed
  `UPGRADE_RESULT ok|refused|failed` block (`reason`, `from`, `to`, `service`, [`latest`], [`authorized_version`], `why`,
  `next`) the Agent copies back. Without the two flags `apply` is unchanged: a human types y at a terminal.
- **Language sync (A1, 0.15) — one value, last write wins**: the account has exactly one `language` (`zh`|`en`) with
  `language_at` (ms); it decides the web UI, every platform mail and the language Agent J speaks with the owner. On the host
  it is `appearance.language`; `language_at` is recorded in the state dir (`language.json`, 0600) whenever the effective value
  changes locally (`agentj config`, the phone's `pref_set` §10.16, a hand edit picked up by serve); a host that never set it
  reports 0. Every report carries both. Server: host `language_at` > account `language_at` → the account takes the host's
  value (audit `language_synced`, from host); ties keep the account value; the 200 always carries the account's
  `{"language","language_at"}` after the merge. Host: answer `language_at` > local → applied through the same preferences
  write path as `agentj config set` (history, last-good, activation), stamped with the account's time (so it is not
  reported back as newer), and `preferences` is broadcast to the phones. Several hosts of one account converge through the
  account. A host not linked to the cloud keeps its local value. An older server sends neither key; the host ignores
  anything that is not `zh`/`en` plus a non-negative integer.
  A ≤ 0.14 server refuses the two unknown keys with 400 `bad_request` (before its seq gate): the host then retries once at
  once without them (fresh seq) and, if that is accepted, records `no_language` in `cloud.json` and leaves the keys out
  until the record is 24 h old, agentj's version or the API URL changes — no sync against such a server, no doubled reports.

- **Login = RFC 8628 device-code shape**: the host shows `user_code` + `verification_uri`; a signed-in owner types the code into the
  Dashboard, sees the host's **channel id** (the host prints it too — compare them), picks a tenant and binds (needs a free seat) or
  rejects. `login_id` is 16 random bytes; `user_code` = 8 characters from `BCDFGHJKLMNPQRSTVWXZ` + `23456789` (no vowels, no
  0/1), 10 min, single use, stored hashed (a hash collision → a new code). The host polls every `interval` seconds until
  `bound` / `rejected` / `expired`. On `bound` the host prints the tenant it was bound to and asks its human
  「绑定到租户 <slug>（<name>）？[y/N]」 (`--yes` for scripts); only `y` writes `cloud.json` (0600: `api`, `host_id`, `tenant`,
  `linked_at`, `last_seq`, `via: "code"`). On N (or EOF) nothing is written and nothing is reported, and — L2 — the host sends a signed
  `decline` for that `login_id`, which unbinds it from the tenant that bound it (below) — someone who read the code off this
  terminal can neither silently receive this host's metadata nor keep its channel (and a seat) in their company.
- **Decline (L2, G-A11)**: allowed only when (a) the login is this channel's own (same `channel`, same `pk`), (b) its status is
  `bound`, (c) the hosts row it bound is still bound with this same `pk`, and (d) that host has **never reported** since (a report =
  the host's human already answered y). Effect, atomically (one D1 batch whose first statement — the conditional update of the login
  to `declined` — carries (a)–(d) and the budget): the host is unbound (seat freed, its device rows deleted, its pending unbind
  requests `cancelled`), the login becomes `declined`, one audit row `host_declined`, one `host_decline` ledger charge. The
  Dashboard card simply disappears. Answers: 200 `{"status":"declined"}`; the same 200 for an already declined login (retry after
  a lost answer; nothing more written); 404 `not_found` when there is nothing to decline — unknown id, another channel's login,
  pending / rejected / expired, purged (> 24 h), or the hosts row is no longer bound (the owner already removed it: the goal holds,
  but nothing is changed); 409 `already_confirmed` when (d) fails; 429 `rate_limited` past 10 declines per channel per hour; the
  envelope errors as above. A report racing the decline: whichever batch runs first wins (report first → 409; decline first →
  the report gets 403 `not_bound`). After a decline the channel is free: a new `agentj login` works at once. Decline can only take
  the host *out* of a tenant; it never binds, approves or adds anything.
- **A3.2 confirm**: the bound answer's `agent_name` joins the question —
  「添加到公司账号 <slug>（<name>），Agent 名「<agent_name>」？[y/N]」; only `y` writes `cloud.json` **and** sets the local Agent name.
- **Seat bind (seat setup)** — the alternative to the 8-character code. A company owner who paid for a seat creates a **setup
  code** for that one seat in the Dashboard (`ajt_` + 43 base64url characters = 256 random bits; the server stores only its
  SHA-256; 7 days; single use; revocable) and gives it — inside one sentence for an Agent, or by email to an employee — to the
  computer that should take the seat. `agentj login --seat <code> --name <name>` (also `--seat-file <path>`, a 0600 regular file,
  not a symlink, so the code stays out of argv and shell history; `--seat -` = one line on stdin) refuses locally, sending
  nothing, a code that does not match `^ajt_[A-Za-z0-9_-]{43}$` and a name that fails the Agent-name rules; otherwise it sends
  one signed `seat-bind`. **There is no y/N question: possession of the code is the human's consent** — they handed it to this
  computer's Agent. On 200 the host writes `cloud.json` exactly like the code path plus `via: "seat"` (the code path writes
  `via: "code"`; a file without `via` is read as `"code"`), sets the local Agent name to the answer's `agent_name` (or the name
  it sent), logs `cloud_linked kind=seat`, prints 「✓ 已添加到公司账号 <slug>（<name>）的席位，Agent 名「<name>」」 + an English
  line, and sends the first report; `agentj status` / `agentj doctor` show "linked via seat setup" vs "via code". Exit codes:
  0 bound · 3 `name_taken` (suggestions printed) · 4 `invalid_setup` · 5 `payment_required` · 2 refused locally · 1 anything
  else. The code is never logged, printed, stored or sent anywhere but this endpoint.
  **What it can and cannot do**: it binds this one host — signed by its own key, so the server learns nothing it would not
  learn from the code path — to that one seat of that company, once. It cannot read anything, cannot add or approve a device,
  cannot add a passkey and is not a session; the answer passes the same whitelist as a bound `poll` answer. Pairing and
  approval stay on the host (§4). **What the server learns** beyond the code path: which setup bound which host and when
  (the setup row's `host_id`, `bound_at`; the host row's `setup_id`) — plus the seat metadata the owner's side creates (company,
  status, times, the invited email if the owner typed one, who claimed it in the browser). **Risk accepted (no y/N)**: an
  Agent tricked into using someone else's code binds this host to a stranger's company; the stranger then receives this
  host's metadata (channel id, public key, Agent name, hostname unless `report-hostname off`, device ids / labels / online
  flags once phones are paired) — never messages, keys or pairing codes, and no way in. The human sees the company in the
  output and in `agentj status`; `agentj unlink` takes the host out of that company (seat leave, below) and stops all
  reporting at once. install.md tells the Agent to show the human the company it joined.
  **Replay**: when the 200 is lost (timeout) the host has written nothing; running the same `agentj login --seat` again sends the
  same code from the same key, and the server — seeing the channel bound by this key through the setup whose hash this code
  has (still `bound`) — answers the same 200 without writing anything (it costs the channel allowance a success costs). Any
  other seat-bind on a bound channel is 409 `already_bound`.
- **Seat leave (seat setup, review SS-02)** — the undo of a seat bind, for the host's human: the company was not the expected one
  (a planted or wrong code), or the employee leaves. `agentj unlink` of a `via: "seat"` link first sends a signed `seat-leave`
  (best effort; it prints 「已通知 Dashboard 把本机移出公司 <slug>」 or why not — then the owner must recall the seat) and then removes
  `cloud.json` exactly as before; a `via: "code"` link is removed locally only, as before. Allowed **any time**, but only for the
  host bound on this channel with this same key **through a seat setup** (`setup_id` not null). Effect, one D1 batch whose first
  statement — the conditional update of the host to `unbound` — carries all of that and the budget (≤ 10 per channel per hour):
  the setup becomes `revoked` (its code can never bind again), the holder's `seat_holder` membership goes unless they hold another
  seat of that company, the host's device rows are deleted and its pending unbind requests `cancelled`, one `seat_leave` ledger
  charge, one audit row `seat_left`. Answers: 200 `{"status":"left"}`; 404 `not_found` (not bound / another key / code-bound — also
  for a repeat after a lost answer: the goal holds, nothing more is written); 429. It can only take the host *out*; it never
  binds, approves or adds anything.
- **Templates (L3.5)** — the closed template library for the workflow design wizard (`dashboard/DASHBOARD_API.md` §8). Read-only:
  an answer can only hand the host template text; the host writes it nowhere but `<work folder>/workflows/<id>/`, never runs it and
  never enables it (`agentj wizard add-template` writes `task.json` with `enabled: false` and refuses a package that says otherwise).
  **Replay**: unlike `rename`, these requests carry a 128-bit `nonce` the server accepts once (its SHA-256 with the channel is kept
  48 h; ±300 s `ts` bounds the window anyway); a replayed envelope gets 409 `replay` and costs no budget. **Integrity**: the host
  re-computes every file's SHA-256 and the package digest — SHA-256 over the sorted lines `path \0 sha256(file) \n` — and compares
  them with the package and with the listing it fetched first; any mismatch, a path that is not a plain relative path, a missing
  `TEMPLATE.md` / `RUN.md` / `DRYRUN.md` / `task.json` or a task.json that breaks the contract (ARCHITECTURE ADR-A61) → nothing is
  written. Both come from the same server: this is TLS + signed request + hashes, **not a publisher signature** (L7).
  The fenced Agent cannot call these (it cannot see the host key): the human or the installing agent runs `agentj wizard
  templates / add-template` in a terminal.
- **Agent plaza (P2)** — the only §7 routes that carry customer-written **text**, and only text a human chose to publish
  (Dashboard `DASHBOARD_API.md` §9). Allowed for a host bound with this key to a company with a paid or comp seat (else 403
  `not_bound` / `plaza_requires_seat`). **Writes** (`post`, `reply`, `resolve`, `report`) carry `nonce` = 16 random bytes as 22
  base64url characters, used once across all plaza routes (the server keeps it 1 h, > 2 × the ±300 s window): a replayed
  envelope → 409 `replay`, nothing written. The host sends a post / reply only after its human confirmed the exact text
  (`agentj plaza post|reply … --owner-confirmed --digest <d>`: the 16-hex digest = SHA-256 of the redacted, cleaned
  `{title?, body}` + `show_name` + target, printed by the preview; layer 1 + optional layer 2 on this machine, ARCHITECTURE
  ADR-A67). `show_name: true` lets the server show this Agent's canonical name as the author (never the company slug or name).
  **Reads** are DATA: the host parses answers through a whitelist (ids by regex, enums, booleans, integers, cleaned strings)
  and prints text only inside its data fence (`<<<PLAZA DATA — …>>>` … `<<<END PLAZA DATA>>>`; agentj lines start `│ ── `, text
  lines `│    ┆ `, `<<<` / `>>>` runs in text escaped); nothing read from the plaza is executed, written to the allowlist, used
  as a path or put into a prompt the host builds; the admin badge is printed only for `author.kind = "admin"` with
  `admin: true`. Answers up to 2 MiB. Vector: `agentjarvis-host-plaza-post-v1` in `vectors/host-envelope.json`.
  **What the control plane learns** from them: the published text, which host wrote it, whether the name is shown, what the
  host searched for and read (only counted per hour, not stored), reports and resolves.
- **Skill & workflow plaza** (`PLAZA_PACKAGES.md` is the wire; Dashboard `DASHBOARD_API.md` §10) — same seat rule and nonce rule
  (`installed`, `like`, `report`, `publish` carry a nonce, shared with the Q&A routes). Package bytes never travel inside an
  envelope: `get` answers a ≤ 10-minute signed `GET https://agentj.app/api/v1/plaza/dl/<token>` URL, `publish` a ≤ 10-minute
  signed `PUT …/v1/plaza/up/<token>` URL (bad / expired token → bare 404). The host verifies the bundle itself (`bundle.parse`, and
  the minisign signature before it says 官方认证) and installs only after its Owner's digest-bound yes. Vector:
  `agentjarvis-host-plaza-pkg-publish-v1` in `vectors/host-envelope.json`.
- **Which URL the host prints**: its configured Dashboard (`AGENTJ_APP_URL` → config `app` →
  `https://agentj.app/account`). The server's `verification_uri` is printed only when its origin (scheme, host, port) equals
  that; otherwise it is ignored, so a compromised control plane cannot point the human at a look-alike page.
- **Report** = the host's own view, replaced wholesale on every accepted report: devices on its allowlist (`id` per §1, `name` =
  the device's self-chosen label after `clean_label`, ≤ 64 chars, `paired_at` unix s, `online` = has a ready session), at most 64;
  `pending` = how many pairings are waiting for the human at the host terminal and since when (no label, no code, no pairing id);
  `seq` strictly increasing per host (the host uses `max(last_seq + 1, now_ms)`); `agent` = `agentj/<version>`
  (server rule `^agentjarvis-[a-z-]+/[0-9A-Za-z.+-]{1,32}$`). The server **refuses** (400) a `name` containing control, format
  (bidi, zero-width), surrogate, line/paragraph-separator characters or any space other than U+0020 — `clean_label` never emits
  them, so it does not clean on the host's behalf. The seq gate, the device-row replacement and the rate charge are one D1 batch:
  a report that finishes after a newer one, or races an unbind, changes nothing (409 `replay` / 403 `not_bound`); replays never use
  up the hourly budget.
- **Host-side state**: every `cloud.json` mutation (login write, seq update, `agentj unlink`) holds an `fcntl` lock on
  `cloud.lock` (0600) in the state dir and re-reads under it, so `serve` and the CLI never interleave and a seq update can never
  recreate a file `agentj unlink` removed. Requests honour the standard `HTTPS_PROXY` environment (a proxy sees the API hostname
  and timing; TLS is verified end to end); loopback never uses a proxy.
  Sent on `serve` start, on approve / revoke / pairing pending / pairing end / device online change (debounced 2 s), and every
  300 s as a heartbeat. Best effort: a failed report is logged as metadata (`report_fail`, HTTP status class) and never blocks `serve`.
- **Agent name (A3.2)** — one thing, three names: an Agent = 1 seat (billing) = 1 host (physical) = a name staff talk to.
  Rules (one copy per language: `dashboard/public/agentname.js`, `host/agentj/text.py`): trim Unicode White_Space at both
  ends (the exact set is spelled out in `agentname.js`; JS `trim()` and Python `strip()` differ, so neither is used as is) and
  collapse runs of U+0020; valid iff 1–32 code points with no `\p{Cc}` `\p{Cf}` `\p{Cs}` `\p{Zl}` `\p{Zp}` and no `\p{Zs}` other than U+0020.
  Anything the host **sends** (`report.agent_name`, `rename.name`) must already be in that normalised form — the server refuses
  (400), it does not clean. Unique per company among bound hosts by the key `NFKC(name).toLowerCase()` (Worker only; the unique
  index decides inside the write). `report` carries the host's current local name and its hostname (`machine`), stored as
  `reported_name` / `machine` in the same accepted-report batch (absent = null: the report replaces the host's view wholesale);
  the Dashboard shows 「主机上已同步」 when `reported_name` equals the canonical name. `sync` returns the canonical name; the host
  adopts it when it differs from its local one and passes the rules (else keeps its own and logs `agent_name_refused`), logs
  `agent_name_synced`, prints a serve line, and sends a report — the signed report back is the acknowledgement. The server sends
  `agent_name: null` (in `sync` and `poll`) for a canonical name that fails the rules (a legacy A3.1 name the owner has not yet
  renamed); the host then keeps its own. Host-initiated
  rename (`agentj name <new>` or the host's local Agent 管理页): while linked the host calls `/v1/host/rename` first and writes
  locally only on 200 (on 409 it shows the suggestions; unreachable → 「连不上 Dashboard，名字没改」); unlinked → local only.
  **Replay**: `rename` carries no nonce or seq — only the ±300 s `ts` window. Accepted (review A3_2 A32-07): a replayed
  envelope can only set a name this host itself signed within the last 5 minutes, it still needs the host to be bound with
  the same key, and it is charged to that host's own budgets; whoever could capture it (TLS break or control-plane insider)
  could rename the Agent in the Dashboard anyway.
  **A name from the control plane is display-only on the host**: it is never interpolated into a shell command, a file path,
  an agent prompt or HTML, and it cannot add or approve a device, route, or run code (invariant 8).
- **Remote limit** (Q32, 2026-10-02): the allowlist holds at most **5** devices (`state.MAX_DEVICES`, enforced in
  `add_device`, the one writer). When it is full, `serve` refuses an approval with `device_limit` even if the code is right; the
  pending event `agentj pair` receives then carries `full`, `limit` and the current devices (id, label, paired_at, online), and
  `agentj pair` shows 「已达 5 台上限，需先解绑一台遥控器才能添加新的」, lists them, and on the human's number + y sends
  `{"cmd":"unbind","device"}` over the local control socket (= `agentj revoke`) before asking for the code.
- **Unbind requests (sync, A3.1)**: `serve` syncs every 20 s while linked (60 s otherwise), off the event loop. For each
  request it decides itself: switch off (`agentj remote-unbind off`, config `remote_unbind: false`) → `disabled`; device not on
  its allowlist → `unknown_device`; 3 already executed in the last hour → `rate_limited`; else it revokes exactly like
  `agentj revoke` → `revoked`. Every decision is a `remote_unbind` line in `host.log` (request id, device id, result) and a line on
  the serve terminal; results go back on the next sync (≥ 2 s later — syncs are never closer, whatever the answers say; one
  HTTP call in flight). Beyond 30 decisions an hour the host stops deciding (requests stay pending, one `remote_unbind_throttled`
  line). Caps and decided request ids (7 days, ≤ 512) persist in `remote_unbind.json`, so a request id is never acted on twice
  and a restart resets nothing. Requests are authenticated by the control plane, not device-signed (ARCHITECTURE G-A18). The response can never
  add or approve: the parser accepts only `{id, device}` pairs, `cloud.py` never touches `devices.json` (AST test).
- **What the control plane learns** (disclosed on `/security`): channel id and host public key, bound tenant, the Agent name
  (the owner's, and the one the host reports) and the host's hostname (`machine`), device ids and
  self-chosen labels, pairing times, online flags, the count/start of pending pairings, report times, the host software version
  (`agent`), every login attempt with its poll times (kept 24 h) and whether the host declined it, sync times and unbind results, — seat setup — which setup code (by id; the code itself only as SHA-256) bound the host and
when, — L3.5 — which bound host asked for the template list or for which template, when (stored: host id + time and a hashed
nonce per request, 48 h; not the template id), and — at Cloudflare's ingress — the request IP (stored only as a
  salted hash of the IPv4 address / IPv6 /64, 48 h). Never: keys other than the host's public key, device public keys, safety
  codes, pairing links, message text.

## 8. Agent bridge (L1): the phone ↔ the customer's own agent
The host drives the agent the human chose (`agentj agent claude|codex|opencode --dir <dir> [--model M]`, config `agent`; read when
`serve` starts) **as the same OS user, with the human's own login, settings and permission rules**, through official headless
interfaces only:
- **Claude Code**: one long-lived `claude -p --input-format stream-json --output-format stream-json --verbose
  --permission-prompt-tool mcp__agentj__approve --disallowedTools mcp__agentj__approve --mcp-config <inline JSON>
  --settings <danger hook JSON>` in the chosen directory; restarted with `--resume <session id>` (kept in `agent.json`, 0600) after it exits. The MCP server is
  `python -m agentj.permtool` (stdio, standard library only); it inherits `AGENTJ_PERM_SOCK` / `AGENTJ_PERM_TOKEN`
  from the agent's environment (never on a command line). `--disallowedTools` hides the tool from the model, so only Claude
  Code's permission check can call it.
- **Permission socket (L2)**: `agentperm/perm.sock` (dir 0700, socket 0600). The token is **one-time, per agent start**: the
  tool claims the socket as soon as Claude Code starts it — `{"t":"claim","token":T}` → `{"t":"claimed"}` — and keeps that one
  connection; requests `{"t":"ask","id":n,"tool","input","tool_use_id"}` → `{"t":"answer","id":n,"behavior",…}`, withdrawals
  `{"t":"cancel","id":n}` (from MCP `notifications/cancelled`). `serve` gives the agent its first message only after the claim
  (≤ 30 s, else the agent is stopped), so by the time the model can run anything the token is spent: any later connection is
  closed without an answer and logged `perm_refused`. If the claimed connection drops, every open request is denied and the
  agent is restarted with a new token. The tool and `serve` make themselves non-dumpable (`PR_SET_DUMPABLE 0`).
- **Fence (L2)**: on Linux the agent runs inside bubblewrap (`fence.py`): private PID namespace and /proc, private `/tmp` and
  `$XDG_RUNTIME_DIR`, the host's state directory replaced by an empty tmpfs with only `agentperm/` bound back, agentj's code and
  the shell start-up / autostart / systemd-user / `authorized_keys` files read-only, session-bus / display / tmux variables
  unset, `no_new_privs`; other programs' control sockets (G-A56): the herdr / screen / wezterm / emacs / Jupyter folders are
  an empty tmpfs, every listening socket this user owns at start (from `/proc/net/unix`), systemd's local sshd socket and —
  unless the human chose `agentj agent … --allow-docker` (passphrase) — the docker / podman / containerd / lxd / libvirt sockets
  are a read-only `/dev/null`, and `HERDR_*` / `ZELLIJ*` / `WEZTERM_*` / `KITTY_*` / `NVIM*` / VS Code IPC / `DOCKER_HOST`
  variables are unset (macOS: the same folders are denied read, write and unix-socket connect). Same user, same login, same settings — nothing the agent may do is widened, only agentj is out of its
  reach. If bubblewrap cannot start, the agent is not started (notice on the phone) unless the human chose
  `agentj agent … --unfenced` at the terminal (asks for the approval passphrase). Codex runs in the same fence. **Never** passed: `--dangerously-skip-permissions`, `--permission-mode`, `--allowedTools`, a permission rule in `--settings` or anything that
  widens what the session may do — the phone only answers questions Claude Code itself would have asked, and an approval
  returns the tool input unchanged (`updatedInput` = input).
- **Danger list (PROMPT-26 item 2)**: `--settings` = `{"hooks":{"PreToolUse":[{"matcher":"*","hooks":[{"type":"command",
  "command":"<venv python> -P -m agentj.danger hook <b64url JSON of config danger_extra, or -> || exit 2","timeout":30}]}]},
  "disableAllHooks":false}` and nothing else. For a call in one of five fixed categories — `spend` (花钱), `delete` (删除),
  `send` (对外发送), `credentials` (改凭据), `price` (改价); `agentj/danger.py` — the hook prints
  `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":…}}`, which Claude Code
  routes to the permission tool even when the human's own rules allow the call; for any other call it prints nothing; it never
  answers allow. Hook failure = exit 2 = the call is blocked. If the human's user / project / local settings say
  `disableAllHooks: true` (or managed policy `allowManagedHooksOnly` / `disableAllHooks`), serve does not start Claude Code and
  sends a notice. OpenCode: covered by its session rules (every call that is not read-only asks; the host classifies each card).
  Codex: covered through app-server approvals (below) — except commands matching the human's own saved Codex allow rules.
- **OpenCode** (ADR-A55 – A57): one long-lived `opencode serve --hostname 127.0.0.1 --port <random>` inside the same fence, HTTP
  Basic `opencode` / a random per-start `OPENCODE_SERVER_PASSWORD`; models.dev fetch, LSP download, auto-update, share and the web
  UI off (`OPENCODE_DISABLE_*`, `OPENCODE_CONFIG_CONTENT` += `autoupdate:false`, `share:"disabled"`). The conversation is one
  session (id in `agent.json`): created with `POST /session {"permission": R}` or, on resume, `PATCH /session/{id}
  {"permission": R}` unless its rules already end with R, where R = agentj's rules (everything asks; read-only tools and a short
  read-only bash list allowed; `task`, `question` denied) followed by every `deny` rule of the human's own agent ruleset
  (`GET /agent`). Messages: `POST /session/{id}/prompt_async {"parts":[{"type":"text","text"}][,"model":{providerID,modelID}]}`
  (`--model provider/model`). Events (`GET /event`, SSE): finished `message.part.updated` text parts → replies; `session.idle` →
  turn end; `session.error` → notice; `permission.asked {id, permission, patterns, metadata}` → the same §8 card flow (tool /
  input mapped to the names in the table below: `Bash`, `Edit`, `Write`, `Read`, `WebFetch`, `ExternalDirectory`, `DoomLoop`,
  `mcp__<name>`) → `POST /permission/{id}/reply {"reply":"once"}` or `{"reply":"reject","message":<reason>}` — never `always`.
  A `permission.replied` once / always that the host did not send, or session rules that stop ending with R, stop OpenCode
  (notice, `agent_tamper` in `host.log`).
- **Codex** (ADR-A70): one long-lived `codex app-server` (JSON-RPC 2.0 over stdio) inside the same fence: `initialize` +
  `initialized`, `config/read {cwd}`; the conversation = one thread (id in `agent.json`, the key `codex exec` used before, so an
  older conversation goes on): `thread/resume {threadId, cwd, excludeTurns: true, …}` or `thread/start {cwd, …}` with
  `approvalPolicy: "untrusted"` (omitted when the human's own policy is `granular`) and `approvalsReviewer: "user"` (plus `model`
  when one is configured; `sandbox: "read-only"` and `ephemeral: true` only for a research / scheduled run) — nothing else; each
  message `turn/start {threadId, input:[{type:"text",text}]}`; finished `agentMessage` items → replies; `turn/completed` ends the
  turn. Server requests → the same card flow: `item/commandExecution/requestApproval` → `Bash {command}` (Codex's
  `bash -lc '…'` wrapper removed; network / stdin requests never batch), `item/fileChange/requestApproval` → the item's
  `changes` as `Write` / `Edit` / `Delete`, `item/permissions/requestApproval` → `CodexPermissions` (never batched) → answered
  `{"decision":"accept"|"decline"}` / `{"permissions": <exactly the requested profile> | {}, "scope":"turn"}` — never
  `acceptForSession`, an execpolicy / network amendment or a session-wide grant; elicitations are declined, user-input questions
  answered empty; `serverRequest/resolved` withdraws a card; `turn/interrupt` stops a turn.

App messages (all inside §3 transport messages):
| direction | message | meaning |
|---|---|---|
| host → device | `{"t":"msg","id","text","ts","seq":n,"from":"agent"\|"host"\|"you"\|"device"\|"notice"[,"name"]}` | chat; `seq` increases per serve run; `you` = this device said it, `device` = another paired device (`name` = its label), `notice` = a host-side note (agent missing / exited / turn failed) |
| host → device | `{"t":"status","s":"none"\|"idle"\|"working"\|"compacting"\|"waiting"\|"down"\|"stopped","agent":"claude"\|"codex"\|"opencode"\|null,"name":"<Agent name>"\|null}` | 未接 / 空闲 / 干活中 / 压缩中 / 等你批准 / 没在运行 / 已急停 (phone controls); sent on ready and on every change. `name` (0.10, additive): the Agent's display name (null = unnamed → show "Agent J"); sent again whenever it changes (`agentj name`, admin page, Dashboard rename) — older phones ignore it |
| host → device | `{"t":"ask","id":"<32 hex>","tool":"<≤ 64>","summary":"<≤ 2000>","ttl":<s left>,"cat":[…],"why":"<≤ 200>"[,"batch":"<scope>","batch_max":20,"batch_secs":600][,"task":"<≤ 80>"]}` | a permission request; `summary` = what the phone shows (command / path / compact JSON); `cat` = danger categories (`[]` = low risk), `why` = the rule that fired; `batch` (low risk only) = the scope a batch approval would cover; `task` = the scheduled task it comes from (display only, not signed) |
| host → device | `{"t":"ask_done","id","result":"allow"\|"deny"\|"timeout"\|"gone"\|"stopped"}` | decided (by any phone, the timeout, the agent withdrawing / serve stopping, or 全部停下) |
| device → host | `{"t":"answer","id","ok":bool,"sig":"<b64url 64 B>"[,"batch":true]}` | the human's decision, signed; `batch:true` (with `ok:true`) = `allow_batch`: this one and the rest of this turn's calls of that scope |
| host → device | `{"t":"auto","id","tool","summary":"<first line ≤ 200>","grant":"<id>"}` | a call approved by a batch grant (shown as one line 「已按你的授权自动批准：…」; no card, no push) |
| host → device | `{"t":"grant","id":"<the signed request id>","scope","left":n,"secs":s}` / `{"t":"grant_end","id","why":"turn_end"\|"revoked"\|"limit"\|"expired"\|"device_gone"}` | a live batch approval / its end; live ones are re-sent after ready |
| device → host | `{"t":"grant_off","id":"<id>"\|null}` | 「收回授权」: end one / every batch approval (only narrows, so any ready paired device may) |
| host → device | `{"t":"estop_state","on":bool,"at":<unix s>,"by":"<≤ 64>"}` | the stop switch (phone controls); sent on ready and on every change |
| device → host | `{"t":"mem_list","r"}` → `{"t":"mem_sources","r","harness","sources":[{id,label,path,kind,n,problem?,bad}],"trash":[{id,ts,label,text}]}` + `{"t":"mem_items","r","items":[{src,file,fsha,iid,kind,text,cut?,title?,desc?}],"more":bool}`… | 「它记住了什么」 (read) |
| device → host | `{"t":"act_list","r","before":"<cursor>"\|null}` → `{"t":"act_page","r","items":[…],"more":bool[,"next","on"]}`… | 「记录」: ≤ 50 entries per page, newest first (read) |
| device → host | `{"t":"task_list","r"}` → `{"t":"tasks","r","items":[{id,title,schedule,tz,mode,suggested,enabled,stale,problems,tsha,last,next?,running}],"more":bool[,"paused","agent"]}`… | 「定时任务」 (read) |
| device → host | `{"t":"slash","cmd":"clear"\|"compact"\|"model"\|"context"\|"cost"\|"usage"\|"status"\|"help"\|"stop"\|"undo_clear","arg":"<≤ 200>"?,"confirm":true?}` | a slash command (buttons, or typed; a `msg` whose text is `/name [args]` is treated the same, except that `/clear` needs `confirm` and `undo_clear` cannot be typed) |
| host → device | `{"t":"msg",…,"from":"cmd","cmd":"<name>"\|"refused","ok":bool,"kind":"ok"\|"info"\|"error"\|"refused"[,"models":[{"id","name","desc","cur"}]][,"undo":true][,"sep":true][,"by":"<≤ 64>"]}` | its result card (a chat entry: backlog, replay, every phone); `models` = buttons that send `/model <id>`; `undo` = 「撤销清空」; `sep` = a divider |
| device → host | `{"t":"mem_rm","r","src","file","fsha","iid",SIG}` · `{"t":"mem_undo","r","id",SIG}` · `{"t":"estop","r",SIG}` · `{"t":"resume","r",SIG}` · `{"t":"task_set","r","id","on":bool,"tsha",SIG}` with SIG = `"n":"<32 hex>","ts":<ms>,"sig":"<b64url 64 B>"` → `{"t":"ctl_res","r","action","ok":bool[,"why"][,"undo","label"]}` | signed writes (below) |

- **Phone controls (PROMPT-26 item 3; ARCHITECTURE ADR-A50 – A54)**. Reads (`mem_list`, `act_list`, `task_list`) are
  answered to the asking session only; `r` = the request id (`[A-Za-z0-9_-]{1,32}`) echoed on every answer; answers are split
  into app messages of ≈ 11 KiB (`more: false` on the last), a memory item longer than 6 000 characters is sent cut
  (`cut` = its full length; its id still covers the whole text). **Writes are signed like approvals**:
  `Ed25519(device sk, UTF-8 "agentjarvis-control-v1\n" + channel + "\n" + device_id + "\n" + action + "\n" + n + "\n" + ts +
  "\n" + hex(SHA-256(object)))`, action ∈ `mem_rm` · `mem_undo` · `estop` · `resume` · `task_on` · `task_off` (`task_set`
  with `on` true / false), object = `src\nfile\nfsha\niid` (mem_rm: the file's SHA-256 as shown = an optimistic lock) ·
  the trash id (mem_undo) · `all` (estop / resume) · `id\ntsha` (task: the contract hash as listed). The host acts only for a
  ready session of an allowlisted device with an approval key, `|now − ts| ≤ 120 s`, `n` not seen in the last 10 min, and a
  signature that verifies against what IT derives from the message; anything else → `ctl_res ok:false` with `why` =
  `shape` · `stale` · `replay` · `no_key` · `bad_signature`, and nothing changes. Then: `changed` (the file / task changed
  since the phone looked — refresh), `unknown_item`, `unknown_source`, `not_found`, `exists`, `symlink`, `invalid`,
  `unknown`, `io`. Every write, accepted or refused, is one line in `controls.log` (0600: time, action, device, result,
  SHA-256 of the object, nonce, ts, signature — never the object text). JS: `protocol/wire.js` `controlMessage` /
  `controlObject`; Python: `host/agentj/controls.py`.
- **Stop everything** (`estop`, or `agentj stop` at the terminal): the switch is written first (`estop.json`, 0600; a
  restart comes back stopped; an unreadable file reads as stopped), then every open request is denied (`ask_done` result
  `stopped`, approvals.log reason `estop`), every batch grant ends (`grant_end` why `estop`), the Agent's turn is
  interrupted (Claude Code: the stream-json `control_request` `interrupt`, ≤ 2 s, then its whole process tree is ended —
  measured with 2.1.285: an interrupt alone leaves a backgrounded Bash running), queued messages are dropped, a running
  scheduled task is ended. While stopped: a device `msg` is answered to that device only with a `notice` 「已急停：这条没有交给
  Agent，也不会排队。恢复后再发。」 and never reaches the Agent; every permission request is denied at once; no task runs;
  `status` = `stopped`. Only a signed `resume` from a phone or `agentj resume` (approval passphrase) turns it off.
- **Scheduled tasks** (`agentj tasks`, `host/agentj/tasks.py`): a run is the configured harness once, headless, in the
  fence, cwd = the Agent's folder, prompt = the task's `prompt_file` with a header naming the workflow; it takes the same
  lock as a chat turn. Claude Code gets the usual permission tool + `--settings` hook; `mode: research` adds `research`
  to the hook's argv, and the hook then answers `permissionDecision: "deny"` for every call that is not read-only
  (`danger.readonly`: read tools, an allowlist of read-only commands with no redirection to a file, `git` read
  sub-commands, `sed -n`, GET-only `curl`, MCP tools whose name starts with a read verb). Codex / OpenCode: the chat adapter once
  in a throw-away conversation (Codex `ephemeral` thread; OpenCode session 「Agent J 定时任务」, not stored), approvals on the
  phone; research — Codex `sandbox: "read-only"` and every request that is not read-only declined without a card, OpenCode our
  rules with every "ask" turned into "deny". The result reaches the phones as one `notice`: 「定时任务「<title>」：
  <verdict> — <the VERDICT sentence>」(+「（只读运行）」).
- **Slash commands** (PROMPT-26 7a; ARCHITECTURE ADR-A70 – A72; `host/agentj/slash.py`): only from a ready session of a
  paired device. Whitelist `/clear` `/compact` `/model [name]` `/context` `/cost` `/usage` `/status` `/help` `/stop` (+
  `undo_clear` from the clear card). A message `/name …` whose name is not on the list: Claude Code's own skill (init `skills`) →
  sent as an ordinary message; anything else → a `refused` card 「这个命令请在电脑上执行。…」 and nothing reaches the Agent. `/stop`
  and `/help` run at once; the others queue behind the running turn (a 「这一轮结束后执行。」 card first) and take the turn lock;
  while stopped only `/help`. How (measured): Claude Code — `/compact` `/cost` sent as text (Claude Code executes them;
  `compact_boundary` gives before / after / duration; the synthetic message it re-sends after the boundary is not a reply),
  control requests `get_context_usage` / `get_status` / `list_models` / `set_model` (serve sends no other subtype but
  `interrupt`), `/usage` = the last `rate_limit_event`; Codex — `thread/compact/start`, `thread/tokenUsage/updated`,
  `account/rateLimits/read`, `model/list`; OpenCode — `POST /session/{id}/summarize`, `GET /session/{id}`, the messages' tokens,
  `GET /config/providers`, `GET /global/health`. `/clear` (all): the conversation id is moved to `agent.json["<kind>.prev"]` and
  the next message starts a new conversation; `undo_clear` moves it back. `/model <name>` (checked against the harness's list
  when it has one) is stored in `config.json` `agent.model`. `/stop` = the stop switch's interrupt for the running turn and a
  running scheduled run, without pausing anything (Claude Code `interrupt`, Codex `turn/interrupt`, OpenCode `POST
  /session/{id}/abort`, then the process tree). Usage figures are only what the harness reports; none → 「—」. `host.log` /
  `activity.log` get `slash` lines with the command name and result class only.
- **Replay**: *(for a `p33` device superseded by the persistent history of §10.5; this paragraph stays true for older
  devices and for a host with `history off`)* the host keeps the last 100 chat entries **in memory only** (never on disk). After a device becomes ready it gets
  the current status, the push key (§9), every entry with `seq > since` (all of them when `since` is absent or larger than the
  host's current `seq`, i.e. after a host restart) and every request still waiting. The page keeps chat in memory only too.
- **Signature**: `Ed25519(device sk, UTF-8 "agentjarvis-approve-v1\n" + channel + "\n" + device_id + "\n" + id + "\n" +
  ("allow"|"deny") + "\n" + hex(SHA-256(tool + "\n" + summary)))` — bound to the channel, the device, the request id, the decision
  and the exact text the phone displayed (the device hashes what it showed; the host hashes what it sent). `allow_batch`
  appends one more line, `"\n" + hex(SHA-256(scope))` of the scope text exactly as shown; the host verifies it against the scope
  it offered for that id, so a wider scope does not verify. The Noise session
  already proves which device sent the answer; the signature makes each decision a record that can be re-checked later.
  JS: `protocol/wire.js` `approveMessage`; Python: `host/agentj/approvals.py`. Browser key: WebCrypto Ed25519,
  `extractable: false`, in IndexedDB (`generateSigningKeypair`).
- **Host rules**: a request is answered only by a ready session whose device is on the allowlist and has an approval key, for an
  id still open, before its deadline, with a signature that verifies; anything else is ignored and logged as metadata
  (`answer_refused`, reason). The first valid answer decides; the others change nothing. **Deadline 120 s, then deny.**
  `batch` is accepted only for a low-risk request that offered one (else `answer_refused` `no_batch`). A batch grant
  approves later calls whose `danger.batch_scope` falls inside it (Bash: command names ⊆ the granted ones; file tools: the same
  tool in the Agent's folder; WebFetch: the same host; other tools: the same name) and that classify as low risk, until
  `agent_turn_end`, 20 uses, 600 s, `grant_off` or the granting device's removal (≤ 8 grants at once).
  No paired device with an approval key → deny at once. At most 16 open requests (more → deny). `serve` stopping or the agent
  withdrawing the request → deny. Every decision is one line in `approvals.log` (0600): time, id, channel, agent, tool name,
  SHA-256 of the tool input, SHA-256 of what was shown, decision (`allow` · `deny` · `allow_batch`), reason (`device` · `batch` ·
  `timeout` · `no_device` · `serve_stop` · `agent_gone` · `too_many` · `estop`), device id, its public key and signature, `cats`, and for
  `allow_batch` `scope_sha256`, for an automatic approval `auto: "batch"` + `grant` (the signed request's id) — **never the tool
  input or the scope text itself**. `agentj approvals --verify` re-checks every signed line against the device's current key and
  every automatic line against the `allow_batch` it names.
- **What leaves the host**: the chat text, the summaries, the decisions — and (phone controls) memory items, activity
  entries, task rows and notices — travel only inside §3 transport messages (the relay
  sees sizes and timing, §5). `host.log` gets metadata (`ask` id + tool name, `ask_done` decision + reason, `turn_end`,
  `agent_start` / `agent_exit` with exit status) — never text.

## 9. Web Push (L1): "something happened", nothing more
- The host has its own VAPID key (P-256, `push_vapid.key`, 0600, created on first use) and sends `{"t":"push_key","k":b64url(65 B
  public key)}` on ready. A browser that the human lets show notifications subscribes with that key
  (`userVisibleOnly: true`) and sends `{"t":"push_sub","endpoint","p256dh","auth"}` inside the session (`{"t":"push_off"}` drops
  it). The host keeps one subscription per device in `push.json` (0600), drops it on revoke and when the push service answers
  404 / 410. **The subscription never reaches our servers.**
- Endpoints are accepted only on `https` (default port, no credentials) at `fcm.googleapis.com`, `updates.push.services.mozilla.com`,
  `push.services.mozilla.com`, `web.push.apple.com`, `*.push.apple.com`, `*.notify.windows.com` — so a device cannot make the host
  post to arbitrary URLs. (Tests add one exact local origin through `AGENTJ_TEST_PUSH_ORIGIN`.)
- The host posts directly to the push service (RFC 8030) with a VAPID JWT (RFC 8292: `aud` = the endpoint's origin, `exp` 12 h,
  `sub` = `https://agentj.app`) and an aes128gcm body (RFC 8291) whose plaintext is `{"k":"reply"}` or `{"k":"ask"}` padded
  with spaces to 32 bytes — every push has the same size. `TTL: 3600`; `Urgency: high` for `ask`, `normal` for `reply`.
- When: one `reply` push per finished agent turn that produced text, one `ask` push per permission request; never to a device
  whose page is visible (`{"t":"vis","fg":bool}` from the page; a ready session counts as visible until it says otherwise);
  at most one `reply` per 20 s and one `ask` per 2 s per device.
- The service worker (`web/public/sw.js`) has no fetch handler and no cache; it shows **Agent J — 有新回复** or **Agent
  Agent J — 有一个请求等你批准**, nothing else, and focuses the page on tap.
- **What the push service (Google / Apple / Mozilla) sees**: that a push for this subscription was sent, when, its constant size,
  its urgency, the host's IP (or its proxy's) and the host's VAPID public key. Never text, never which kind.

## 10. Relay parity (PROMPT-33): Agent J = a superset of relay
Leo's rule (2026-10-03): everything his own phone product *relay* (`pwa/`, `bridge/`) does, Agent J's phone does too — built on
relay's page (`agentjarvis/parity/DESIGN.md`), with relay's HTTP routes replaced by the end-to-end app messages below. The
inventory is `agentjarvis/parity/features.json`; the gate is `agentjarvis/parity/check.mjs` (DESIGN.md §c). Nothing here
changes §1–§9's security: every byte below travels inside §3 transport messages, the relay holds no key, approvals and
answers are signed, PR1 holds (history, uploads, transcripts and the ASR model live only on the customer's computer).
Product principle 「只给结果，不灌过程」: the phone gets replies, results and cards that need a human — never tool-call streams.

### 10.0 Capability, limits, text rules
- Device `hello` gains `"caps":["p33"]` (and, for a resume, `"hist":{"epoch":e,"last":id}` — §10.5). The host's `approved` /
  `ready` gains `"caps":["p33"]`, `"asr":"ready"|"not_installed"|"off"|"broken"` (§10.9) and `"hist":"on"|"off"` — only for a
  device whose hello announced `p33` (an older device gets exactly §3's `{"t":"approved"}` / `{"t":"ready"}`). A side
  sends a §10 message, or any app message larger than §3's 16 KiB, **only** to a peer that announced `p33`; to an older peer
  it behaves exactly as §3 / §8 (chat as `msg`, replies split at 4 000). Unknown `t` stays ignored both ways; unknown keys in
  a known message are ignored by the receiver (forward compatible) unless a row below says "strict".
- `p33` limits (both ends enforce, receiver too): app-message JSON ≤ **61 440 bytes** (60 KiB; padded plaintext ≤ 61 696,
  ciphertext ≤ 61 712, relay payload ≤ 61 713 < 65 536 — §2 is unchanged). **Text**: ≤ **20 000** UTF-16 code units per
  message (relay's limit), well-formed (no lone surrogate), no C0 control character except `\t` and `\n` (the sender turns
  `\r\n` / `\r` into `\n`; a receiver that finds one rejects the message — `why:"shape"`). In `say`, `text` + `excerpt`
  together ≤ 20 000. These rules make every UTF-16 unit cost ≤ 3 bytes of JSON, so a maximal `say` (≈ 60 400 bytes) always
  fits one frame — no device → host chunking of text exists. A violation is answered (`say_res ok:false why:"too_long"` /
  `"shape"`), not a closed session; an over-size *frame* still closes the session (§3).
- Ids: `r` (request id) `[A-Za-z0-9_-]{1,32}` echoed on every answer; `sid` / `bid` = 128 random bits as 22 base64url
  characters, chosen by the device, unique per device (the host keys every table by `(device id, sid|bid)`, so another
  device can neither guess nor touch them).

### 10.1 `frag` — host → device messages larger than one frame
`{"t":"frag","f":"<16 hex>","i":k,"n":N,"d":"<string>"}` (`0 ≤ k < N ≤ 128`): the inner message's JSON text, cut on code-point
boundaries into `N` slices so that each `frag` JSON ≤ 61 440 bytes; the receiver concatenates `d` for `i = 0 … N−1`, parses it
as one app message (never another `frag`) and handles it normally. The sender sends the `N` frames back to back, nothing in
between. Receiver bounds: reassembled ≤ 2 MiB of UTF-8, one open `f` at a time; any other message arriving while `f` is open,
a wrong `i`, `N` changing or the bounds exceeded → the partial is dropped (the other message is handled normally). Only the
host sends `frag` (device messages always fit one frame, §10.0). Used by `hist_page`, `hist_turn`, `menu`, `models`.
Before every fragment after the first the host re-checks that the session is still attached and its device still allowlisted;
a revoke in the middle stops the message there (P33-X07).

### 10.2 `say` — chat with attachments, quote and withdraw
| direction | message | meaning |
|---|---|---|
| device → host | `{"t":"say","sid","text","ts":<ms>[,"att":["<bid>",…]][,"reply_to":<turn id>][,"excerpt":"<≤ 2 000>"]}` | one message to the Agent. `att` ≤ 10 ids, each a finished blob of this device (§10.3), no duplicates; `reply_to` = a turn id of the current epoch (§10.5); `excerpt` only with `reply_to` (the text the human selected on that page). `text` may be empty only when `att` is not |
| host → device | `{"t":"say_res","sid","ok":true,"state":"queued"\|"delivered","turn":<id>}` · `{"t":"say_res","sid","ok":false,"why",["att":[ids]]}` | accepted (its source entry is history turn `turn`) / refused, nothing queued: `shape` · `too_long` · `stopped` (estop: the phone shows §8's 「已急停：这条没有交给 Agent，也不会排队。恢复后再发。」) · `att_gone` (ids not staged — the phone re-uploads exactly those) · `att_open` (still uploading) · `too_many_att` · `reply_unknown` · `dup` (this `sid` was used — the host remembers the newest 1 024 sids per device, beyond the 10-min table) · `too_many` (16 of this device's sends still wait for the Agent: refused, none evicted) · `no_agent` |
| host → device | `{"t":"say_state","sid","s":"transcribing"\|"delivered"\|"cancelled"\|"failed"[,"why"]}` | progress after `say_res`; `delivered` = handed to the harness (Claude Code stdin line written / Codex `turn/start` sent / OpenCode `prompt_async` accepted) |
| device → host | `{"t":"say_cancel","sid"}` | relay's 「× 取消」 |
| host → device | `{"t":"say_cancel_res","sid","r":"cancelled"\|"already_delivered"\|"not_found"}` | `cancelled` = it will never reach the Agent; its attachments go back to staged under the same ids (TTL continues) |
- **Withdraw semantics** (relay `compose.Sends`): one lock per send from "is it cancelled?" through the write to the harness;
  `say_cancel` takes the same lock, so `cancelled` is never answered for a message that went out and `already_delivered` never
  for one that did not. A `say` waits in Agent J's turn queue while a turn runs (§8) and can be withdrawn the whole time; a
  cancel during say-time transcription (§10.9) kills the ASR for it. Table: ≤ 16 sends per device not yet handed to the Agent (the 17th → `say_res why:"too_many"`; a waiting send is never evicted), finished ones kept 10 min. `cancelled` only when the message provably never reached the harness (still queued, or the harness refused it); a write that failed part-way (maybe delivered) answers `already_delivered` and its attachments stay with the Agent (P33-X08).
  (Host, PROMPT-33:) the say's page (`hist_turn`, §10.5) is sent before its `say_res`, so the phone always has the page the
  answer names; a withdrawn say's page ends `"end":"stopped"` with `"card":{"withdrawn":true}`; a say the stop switch drops
  from the queue gets `say_state` `cancelled` with `"why":"stopped"` (attachments staged again); a say whose text is a
  whitelisted slash command (no `att`, no `reply_to`) is that command: `say_res` `ok:true`, `state:"delivered"`, `turn` = the
  command's page. The kill of a running decode is best effort: the host stops waiting at once (G-A107).
- **What the Agent receives** (one message, host-built, in the host's language `config.json` `lang`, default zh): the quote
  block (if any), the text, then — with attachments — 「附件 N 个（已在你的工作目录里，内容没有经过聊天通道——需要时自己读这些路径）：」
  and one line per file `- <absolute path>（<mime>，<bytes> 字节）`, then for each `origin:"recording"` audio
  「语音转写（<s> 秒，<file name>）：」 + the transcript or 「转写失败（<reason>）——音频仍在上面的路径。」 (relay `compose.render`). The quote
  block is built by the host **from its own history** (the phone sends only the id): 「【回复 #<id> · <who> · <HH:MM>】」 + the
  first 300 characters of that turn's reply as `> ` lines, or the `excerpt` verbatim as `> ` lines marked 「（摘录）」. The phone
  cannot make the Agent believe a page said something it did not, except through `excerpt`, which is labelled as the
  human's own selection. The whole message obeys §8's queue, estop and slash rules (`text` starting `/name` is a slash
  command only when `att`, `reply_to` are absent).
- **Signing**: none — like `msg` (§8) the Noise session already proves which approved device spoke; `say_cancel` only
  withdraws that device's own unsent message (it narrows, like `grant_off`).
- The old device `msg` stays valid (text ≤ 4 000, or ≤ 20 000 from a `p33` device); a `p33` phone sends `say` only.

### 10.3 Blobs — files, photos and voice, end to end
| direction | message |
|---|---|
| device → host | `{"t":"blob_open","bid","purpose":"att"\|"asr","name":"<≤ 128>","mime","size":n,"sha256":"<64 hex>","origin":"file"\|"photo"\|"camera"\|"paste"\|"drop"\|"recording"[,"secs":x]}` |
| host → device | `{"t":"blob_ack","bid","next":<byte offset the host wants next>}` · `{"t":"blob_err","bid","why"}` |
| device → host | `{"t":"blob_chunk","bid","o":<offset>,"d":"<base64url of ≤ 45 056 bytes>"}` |
| device → host | `{"t":"blob_end","bid"}` |
| host → device | `{"t":"blob_done","bid","ok":true,"kind":"image"\|"file"\|"audio","bytes":n}` · `{"t":"blob_done","bid","ok":false,"why"}` |
| device → host | `{"t":"blob_drop","bid"}` → `{"t":"blob_err","bid","why":"dropped"}` (relay `/detach`; also aborts an open upload) |
- **Allowlist** (strict; = relay `inbox.ALLOWED`, the stored extension comes from the MIME, never from `name`): `image/jpeg`
  `.jpg` · `image/png` · `image/webp` · `image/gif` · `image/heic` · `image/heif` · `application/pdf` · `text/plain` `.txt` ·
  `text/markdown` `.md` · `text/csv` · `application/json` · `audio/webm` · `audio/ogg` · `audio/mpeg` `.mp3` · `audio/mp4`
  `.m4a` · `audio/aac` · `audio/wav` / `audio/x-wav` `.wav`. The host also checks the leading bytes of images, PDF and WAV
  against their type (`why:"type"` on a mismatch). `purpose:"asr"` accepts only `audio/wav` in the §10.9 shape.
- **Limits** (both ends): `size` 1 … 26 214 400 bytes (25 MiB; `asr`: ≤ 3 840 044 = 120 s); per device ≤ 20 staged + ≤ 2 open
  uploads, ≤ 200 MiB staged + partial (over either → `too_many`; blobs a queued `say` claimed count until it is delivered or withdrawn); staged TTL 30 min; host disk: the inbox ≤ 2 GiB (`quota`),
  free space ≥ twice the size + 64 MiB (`disk`). Errors (`blob_err` /
  `blob_done ok:false`): `shape` · `type` · `too_big` · `empty` · `too_many` · `quota` · `disk` · `unsafe_inbox` (§10.4) ·
  `sha_mismatch` · `size_mismatch` · `expired` · `dropped` · `asr_off`.
- **Flow**: chunks in order, `o` = the host's `next` (a chunk at any other offset is dropped and answered with `blob_ack`
  = resync). ≤ 8 chunks unacknowledged; the host acks every 4th chunk and after `blob_end`. Pacing: ≤ 22 frames/s and ≤ 220
  per 10 s of all the device's frames once the relay said `{"t":"rate","n":240,"w":10}` (§10.13), else ≤ 50 per 10 s. 45 056
  raw bytes → 60 075 base64url characters → a 60 160-byte padded plaintext: every chunk frame but the last has the same size.
- **Integrity**: at `blob_end` the host compares the byte count and SHA-256 of everything it holds with `size` / `sha256`;
  mismatch → `sha_mismatch`, the partial is deleted, the device retries once from 0, then shows the error.
- **Resume**: a partial lives in `<state>/uploads/<device>/<bid>.part` (0600, state dir = invisible to the Agent) for 30 min,
  its declared fields beside it in `<bid>.json`, so it survives a serve restart too (an `asr` partial does not).
  After any reconnect the device sends the same `blob_open` (same `bid`, `size`, `sha256`, `mime`); the host answers
  `blob_ack next` = bytes it already has (0 when it has nothing or any field differs) and the device continues from there.
- A finished `att` blob is moved into the inbox (§10.4) and stays *staged* (announced to nobody) until a `say` claims it or the
  TTL ends; claimed files are never deleted by a later `blob_drop`. A finished `asr` blob is transcribed at once (§10.9) and
  deleted; it never reaches the inbox.
- **Signing**: none (Noise session). Reason: an upload changes nothing the Agent sees until the same device's `say` names it,
  and its effect is bounded by the limits above; a signature would add per-chunk cost without adding a decision.

### 10.4 Inbox — where the fenced Agent finds uploads
- Path: **`<agent folder>/.agentj/inbox/<YYYY-MM-DD>/<HHMMSS>-<6 hex>-<slug>.<ext>`** (`agent folder` = `agentj agent … --dir`).
  `slug` = the client `name` reduced to `[A-Za-z0-9一-鿿._-]`, ≤ 48 (relay `inbox.py`); the name never chooses a path.
- Why there: the fence (§8) replaces the state dir with an empty tmpfs, and a path outside the Agent's folder would make
  Claude Code / OpenCode ask a permission card for every read (or need `--add-dir` / an `external_directory` allow rule —
  flags that widen the session, Invariant 11). Inside its own folder all three harnesses read without asking and under
  their normal rules; Codex's sandbox (read-only or workspace-write) includes it.
- The host (outside the fence) creates `.agentj/` 0700 with `.agentj/.gitignore` = `*` (so uploads never enter the
  customer's git), every component opened with `O_NOFOLLOW` / `O_DIRECTORY` relative to the previous one, the file with
  `O_CREAT|O_EXCL|O_NOFOLLOW`, 0600, written to `.part-<bid>` then renamed. A component that is a symlink, not a directory or
  not owned by the user → `unsafe_inbox`, nothing written (the Agent can write in its folder, so it could plant a link).
- Retention: day folders older than **30 days** are removed at `serve` start and daily; over 2 GiB the oldest day goes first,
  never today's. `agentj inbox [list|clear|path]`. The Agent may read, move or delete its inbox files — they are the human's
  gift to it, nothing of agentj's. Every cleanup (retention, quota, `inbox clear`, the listing / usage, deleting a staged file
  nobody was told about) goes through the same `O_NOFOLLOW` directory-fd chain as the write and removes entries relative to
  those fds — a link at `.agentj`, `.agentj/inbox`, a day folder or inside one is never followed: a linked `.agentj` / `inbox`
  → nothing removed, `inbox_unsafe` in host.log (`agentj inbox clear` exits non-zero); a link inside is removed as a link
  (P33-C01).

### 10.5 History — persistent pages
- **Store** (host, `config.json` `history`, default `on`): `<state>/history/current.jsonl` (dir 0700, file 0600; one turn per
  line, appended; rewritten atomically to the newest **500** when it passes 600) + `<state>/history/meta.json`
  `{"epoch":e,"next_id":n}`. A host restart loses nothing. `history off` = §8's 100 entries in memory only (pages in memory,
  `epoch` = the serve start time, so a phone never mixes two runs). (Host, PROMPT-33: one line per change of a page — the
  last line of an id wins — compacted to the newest 500 past 600 lines **or past 32 MiB**, keeping ≤ 16 MiB of pages (the
  newest one always), in memory too — P33-C06; `meta.json` also keeps the archive `undo` restores.)
- **Turn** = one page: `{"id":n,"ts":<ms>,"src":Src,"reply":{"text":"…","part":[k,N]?},"end":"open"|"done"|"stopped"|"failed",
  "card":Card?}`. `Src = {"k":"phone"|"host"|"agent"|"sys"|"task"|"cmd","dev":"<device id>"?,"name":"<label ≤ 64>"?,
  "text":"≤ 20 000","quote":{"id","who","ts","text ≤ 300","ex":bool}?,"att":[{"name","mime","bytes","kind"}]?,"local":bool?}`.
  Who: `phone` (a paired device; the page shows 「你」 when `dev` is its own id, else the label = 「另一台设备」), `host`
  (`agentj send` on the computer), `agent` (a turn the Agent started itself — e.g. Claude Code's background `task-notification`),
  `sys` (a host notice; `local:true` = 「在电脑上处理」, §10.8), `task` (a scheduled task's run and VERDICT), `cmd` (a slash
  command: `src.text` = `/compact`, `card` = §8's result fields `{cmd, ok, kind, models?, undo?, sep?}`). The reply of one
  turn = every finished reply text of that turn, joined by a blank line — results only, never tool calls. A reply over
  200 000 code units continues on the next page(s) (`part`); nothing is truncated.
- **Wire** (reads; answered to the asking session only):

| direction | message |
|---|---|
| device → host | `{"t":"hist_get","r"[,"before":id][,"after":id][,"limit":1–50]}` (neither = the newest) |
| host → device | `{"t":"hist_page","r","epoch","first","last","count","turns":[Turn…],"more":bool}` (oldest first; fewer than `limit` when 2 MiB would be passed; via `frag`) |
| host → device | `{"t":"hist_meta","epoch","first","last","count"}` (on ready and after every reset) |
| host → device | `{"t":"hist_turn","epoch","turn":Turn}` (to every ready `p33` session: a new turn, a reply part appended while `end:"open"`, the turn's end) |
- **Ready**: after `ready` a `p33` device gets `hist_meta`, then every turn after its `hist.last` (≤ 50; more → the newest 50,
  and it pages back with `hist_get before`), or the newest 50 when its `epoch` differs — each as one `hist_turn`, oldest first;
  a device whose `hello` carried no `hist` (a fresh page) gets the newest 50; it does not get §8's `msg` replay. A `p33` device receives no
  `msg` at all — every `msg` of §8 (`agent`, `you`, `device`, `host`, `notice`, `cmd`) is a turn or a turn update instead.
- **Reset** (`/clear`, any harness): `current.jsonl` → `<state>/history/archive/<epoch>-<UTC stamp>.jsonl` (newest 10 kept),
  `epoch + 1`, ids keep growing; every session gets `hist_meta`. `undo_clear` moves that archive back as current with
  `epoch + 1` again. `agentj history [show <id>|clear|off|on]`. `history off` stops writing; what is on disk stays (the CLI
  says how much and where); `agentj history clear --all` deletes `current.jsonl` and every archive at once — not undoable,
  `epoch + 1` (P33-C07).
- Text on disk: the customer's own computer, 0600, the same place the harness keeps its own transcript; never sent anywhere
  but inside §3. Invariant 23 covers it.

### 10.6 Quote, excerpt, jump
`say.reply_to` names a page; the host refuses an id that is not in the current epoch (`reply_unknown`). `excerpt` is ≤ 2 000
code units (relay `EXCERPT_MAX`), taken by the phone from the rendered reply of that page (also copied to the clipboard on the
phone); the host does not check it against the reply (the rendering differs from the stored Markdown), it labels it. The
stored `src.quote` lets every phone draw the quote above the input and jump to page `quote.id` (loading older pages with
`hist_get before` until it is there; gone after a reset = stay).

### 10.7 Questions — AskUserQuestion and its equivalents; an answer is an approval
| direction | message |
|---|---|
| host → device | `{"t":"question","id":"<32 hex>","qs":[{"q":"<≤ 1 000>","h":"<≤ 64>","m":bool,"o":[{"l":"<≤ 200>","d":"<≤ 500>"}]}],"ttl":s[,"task":"<≤ 80>"]}` — 1–8 questions, 1–16 options each, labels unique per question; `m` = several may be picked |
| host → device | `{"t":"question_done","id","result":"answered"\|"cancelled"\|"timeout"\|"gone"\|"stopped"}` |
| device → host | `{"t":"q_answer","id","pick":[[i,…],…],"sig"}` (1-based option numbers per question, ascending; exactly one when `m` is false, ≥ 1 when true) · `{"t":"q_answer","id","cancel":true,"sig"}` (取消问题: "I will type it instead") |
- **Signature** (`agentjarvis-question-v1`, like §8's approvals): `Ed25519(device sk, UTF-8 "agentjarvis-question-v1\n" + channel
  + "\n" + device_id + "\n" + id + "\n" + ("answer"|"cancel") + "\n" + hex(SHA-256(Q)) + "\n" + P)`, where `P` = the picks as
  `1,3;2` (questions separated by `;`, numbers by `,`) or `-` for cancel, and `Q` = the lines, joined by `\n`, of
  `"q " + hex(SHA-256(h)) + " " + hex(SHA-256(q)) + " " + ("m"|"s")` for each question followed by `"o " + hex(SHA-256(l)) + " " +
  hex(SHA-256(d))` for each of its options (UTF-8; `d` absent = empty) — bound to the exact text the phone showed. JS:
  `protocol/wire.js` `questionMessage`; Python: `host/agentj/approvals.py` `question_message` (vectors: `protocol/vectors/p33.json`).
- **Host rules** = §8's: a ready session of an allowlisted device with an approval key, open id, before the deadline
  (**180 s**, relay's default), a valid signature over what IT derives; first valid answer wins; anything else ignored and
  logged `answer_refused`. Every outcome is one `approvals.log` line (`kind: "question"`, SHA-256 of `Q`, `P`, device, key,
  signature — never the question or label text). While stopped (estop) a question is cancelled at once (`stopped`). Status =
  `waiting` with `"kind":"question"` while one is open (§10.10). A question never batches.
- **Claude Code**: `AskUserQuestion` reaches `--permission-prompt-tool` like any tool (input `{"questions":[{"question","header",
  "options":[{"label","description"}],"multiSelect"}]}`); an answer → `{"behavior":"allow","updatedInput": <the tool input as
  the host received it> + {"answers":{"<question text>":"<label>"}}}` (several labels of a multi-select joined with `", "` —
  relay's measured output shape, `bridge/relay_hook.py` / `ask.format_answers`, Claude Code 2.1.280); cancel →
  `{"behavior":"deny","message":"用户在手机上取消了这个问题，没有选择任何选项。请不要替他做选择，停下来等他直接输入文字。"}`;
  timeout → deny 「没有人作答，请改用文字列出选项」. **The one exception to §8's `updatedInput` = input**: only the key `answers`
  is added, built by the host from its own copy of the input and option labels chosen by number. **Proven** (PROMPT-33 probe,
  real Claude Code 2.1.285 `-p` stream-json, haiku, temp HOME — `reports/qa/parity/probe-askuserquestion.txt`): the call reaches
  the permission tool; allow + `answers` → tool result "Your questions have been answered: …"; deny + the cancel message → the
  model waits for typed text. A question the card cannot show exactly (bounds, duplicate texts) → deny with the timeout text.
- **Codex** (`item/tool/requestUserInput`, EXPERIMENTAL in app-server 0.160 schema): questions `{id, header, question,
  options[{label, description}] | null, isOther, isSecret}` → one card with `m:false`; answer `{"answers":{"<id>":{"answers":
  ["<label>"]}}}`; cancel / timeout → `{"answers":{}}` (today's answer). A request with any `isSecret` question or a question
  without options → no card: answered `{"answers":{}}` + a `sys` turn `local:true` 「这个问题需要在电脑上回答」. So are a
  repeated question `id` and an option that is not an object: the card's option *k* is always the request's option *k*, and
  the answer's label is taken from the signed card itself (P33-X04).
- **OpenCode** (`question.asked`, 1.18.32: `POST /question/{requestID}/reply {"answers":[["<label>",…],…]}`, `…/reject`): our
  session rule for `question` becomes **`allow`** → `question.asked` → card; answer = the picked labels per question; cancel /
  timeout → `reject`. (`ask` would first raise a permission card for the question tool itself — two cards for one question;
  `allow` only lets the tool publish `question.asked`, which the host answers. OpenCode's built-in defaults deny `question`
  and allow it again; that pair is not copied as a human deny, a human whose own rules end in a question deny keeps it; a
  read-only scheduled run denies it. Was `deny`; this lets the model *ask the human*, nothing more — Invariant 11.)
  The reply endpoint takes the server password, which the Agent's own tool processes inherit, so the host checks every
  `question.replied`: a reply it did not POST, or one whose `answers` differ from the signed ones it POSTed, is tampering —
  OpenCode is stopped at once (like a foreign `permission.replied`, G-A66). Residual: the model may read the forged answer
  before the stop (G-A122).

### 10.8 Approvals and "handle on the computer"
§8's `ask` / `answer` are unchanged (same signature). The phone's gesture becomes relay's: 拒绝 one tap, 批准 a **1.2 s hold**
(`APPR_HOLD_MS`); a tap alone never approves; the batch button keeps Agent J's wording. Requests the phone cannot answer
(Codex secret / free-text questions, elicitations, a refused-by-policy Codex call, `--unfenced` / passphrase steps, hooks
switched off) arrive as `sys` turns with `"local":true` and show relay's 「在电脑上处理」 card; nothing to sign.

### 10.9 Voice — the phone records, the customer's computer transcribes
- **Phone**: `MediaRecorder` records; the page decodes the take with `AudioContext.decodeAudioData`, renders it through an
  `OfflineAudioContext(1, ceil(seconds·16 000), 16 000)` (resample + mono) and writes **16 kHz mono PCM16 little-endian WAV**
  (44-byte RIFF header, `audio/wav`) — the host needs no ffmpeg. Takes ≤ 10 min (relay `LOCK_MAX_MS`). A take shorter than the
  field's threshold (relay `voiceThresholdS`, 20–90 s) is a blob with `purpose:"asr"`; a longer one is an attachment
  (`purpose:"att"`, `origin:"recording"`) transcribed at send time. If decoding fails (an old browser) the original container is
  sent as an `att` recording; the host transcribes it only if `ffmpeg` exists, else 「转写失败（格式）」 with the file kept.
  Say-time transcription reads a **host-private copy** of the verified bytes in `<state>/uploads/<device>/<bid>.voice` (kept
  until the say is delivered), never the inbox file the Agent can rewrite or replace with a link (P33-C02). ffmpeg, when
  needed: the demuxer pinned from the declared MIME (`webm`→`matroska`, `ogg`, `mp3`, `mp4`→`mov`, `aac`, `wav`; no content
  probing), `-protocol_whitelist file`, ≤ 119 s of audio, output ≤ 3 840 044 bytes (also RLIMIT_FSIZE / RLIMIT_CPU 60 s), one
  conversion per host at a time, the child killed and reaped on timeout and on withdraw (P33-C04 / X09).
- **Host WAV check** (strict): RIFF/WAVE, one `fmt ` PCM chunk (format 1, 1 channel, 16 000 Hz, 16 bit), one `data` chunk whose
  length matches the file; else `asr_res why:"bad_audio"`.
- **Result**: `{"t":"asr_res","bid","ok":true,"text":"<≤ 20 000>","engine":"sherpa"|"voxtype","ms":n}` · `{"t":"asr_res","bid",
  "ok":false,"why":"no_speech"|"not_installed"|"off"|"broken"|"busy"|"timeout"|"bad_audio"}`. The phone appends the text to the
  composer and never sends it (relay `appendText`); takes are transcribed in recording order (relay `asrQueue`). One decode at
  a time per host (FIFO), ≤ 3 takes of one device in flight (decoding or waiting; the 4th → `busy`, another device is not held
  back), 60 s per take (`timeout`); at `say` time all audio of one message within 60 s together (relay `SAY_ASR_BUDGET_S`),
  past it the message goes out with 「转写失败（超过 60 秒）」. A take longer than 20 s is cut in the middle of pauses ≥ 0.4 s
  (pieces ≤ 20 s; silence-only pieces and silence-only takes are not decoded — `no_speech`), because SenseVoice garbles long
  whole takes (`host/ASR.md`).
- **Engines** (`config.json` `asr: {"engine":"auto"|"sherpa"|"voxtype"|"off","threads":2}`; default `auto` = `sherpa` once
  installed, else `not_installed` — `auto` never picks voxtype):
  - `sherpa`: **sherpa-onnx 1.13.8** (Apache-2.0; wheels for Linux x86_64 / aarch64 glibc ≥ 2.17,
    macOS arm64 / x86_64, CPython 3.11–3.14 pinned (agentj's own range); no numpy needed) + **SenseVoice-Small int8 2024-07-17** (FunAudioLLM; zh / en / ja
    / ko / yue; FunASR Model License 1.1 — attribution kept). Measured numbers and hashes: `agentjarvis/parity/DESIGN.md` §d.
    Runs in a resident worker `<state>/asr/venv/bin/python -I <agentj>/asr_worker.py` (loads in 0.6 s, ends after
    10 idle minutes; ≈ 470 MB peak RSS for 16 kHz takes up to 10 min, freed when it idles out), outside the fence; the WAV
    reaches it as a file in `<state>/uploads/`, deleted after.
  - `voxtype` (opt-in, e.g. Leo's machine): `voxtype transcribe <wav>` with the human's own voxtype configuration; its progress
    lines are stripped (relay `transcribe.py`). Chosen with `agentj asr engine voxtype`; never automatic.
  - `off`: `asr_res why:"off"`; recordings still go as attachments, untranscribed.
- **Install** — `agentj asr install [--mirror auto|github|hf-mirror|modelscope] [--yes]` (shows size + sources, asks y/N; no
  terminal and no `--yes` → refuses): downloads (resumable) the two pinned wheels for this platform and the model, checks every
  SHA-256 (hashes in agentj's code), creates `<state>/asr/venv` with agentj's own Python and installs the two local wheels
  with `pip --no-index --no-deps --require-hashes`, extracts only `model.int8.onnx`, `tokens.txt`, `LICENSE`, `README.md` and
  `test_wavs/zh.wav` (no links, no absolute or `..` paths, each SHA-256-checked), transcribes `zh.wav` as a self-test, then sets
  `engine:"sherpa"`. Routes: `github` = PyPI + the GitHub release tarball
  `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2`
  (163.0 MB) — **≈ 178 MB** download; `hf-mirror` (Tsinghua / Aliyun PyPI mirrors + the same files singly from hf-mirror.com)
  and `modelscope` (Aliyun / Tsinghua + modelscope.cn; no sample WAV, the self-test decodes silence) — **≈ 255 MB** each, byte-
  identical (hash-checked). `auto` = GitHub unless the PyPI index is a mainland mirror or github.com does not answer in 5 s,
  then the others as fallbacks. ≈ 300 MB on disk. Why the state dir: the host runs this code **outside** the fence, so the Agent must not
  be able to write it (Invariant 14). `agentj asr status|test <wav>|engine <e>|remove`; `agentj doctor` row `asr`.
- **Fallback** (no wheel for the platform, no network, a hash mismatch, self-test fails): nothing is half-installed (the venv
  and model folders are removed), `asr` stays `not_installed` / `broken`, and the CLI prints why plus the two alternatives:
  「这台电脑装不了本地转写（<reason>）。可以：① 装 voxtype 后运行 `agentj asr engine voxtype`；② 先用手机输入法自带的听写。」.
  The phone, told by `ready.asr`, still shows the mic; a take then gives 「电脑上还没装语音转写：在电脑上运行 `agentj asr install`
  （约 180 MB）。装好前可以用手机输入法的听写。」 and a long take still travels as an untranscribed attachment.
- **Never** our servers: audio and text travel only inside §3; the model download comes from GitHub (or the mirrors above),
  pinned by hash.

### 10.10 Status and meters
- `status` (§8) gains `"kind":"question"` while a question is open (else absent). Phone colours (relay, unchanged): `idle`
  green · `working` / `compacting` blue (top shimmer; compacting = the water sinks) · `waiting` orange (edges breathe) ·
  waiting + `question` purple · `none` / `down` / `stopped` / no connection grey (`stopped` + Agent J's red banner).
- `{"t":"meter","model":"<id>"|null,"model_name":"<≤ 64>"|null,"effort":"<≤ 16>"|null,"ctx":{"used":n,"max":n}|null,
  "h5":{"pct":x,"reset":<unix s>}|null,"week":{"pct":x,"reset":<unix s>}|null,"at":<unix s>}` host → every ready `p33`
  session on ready and on change (≤ 1 per 2 s). Only numbers the harness reports exactly; `null` → 「—」. Sources: Claude Code —
  `rate_limit_event` (5 h / 7 day utilization + reset), `get_context_usage` after each turn end and compaction, the model of
  the init message / `set_model`, the effort agentj started it with; Codex — `thread/tokenUsage/updated` (`last` /
  `modelContextWindow`), `account/rateLimits/read|updated` (the 300-minute window → `h5`, 10 080 → `week`), thread model /
  effort; OpenCode — the newest assistant message's tokens and the model's `limit.context`; quotas `null`.

### 10.11 Model and effort pill
| direction | message |
|---|---|
| host → device | `{"t":"models","models":[{"id","name","efforts":["low",…]\|null,"cur":bool}],"effort":"<cur>"\|null,"default":{"model","effort"}}` (on ready and after a change; via `frag` when large) |
| device → host | `{"t":"model_set","r","model":"<id>"\|null,"effort":"<level>"\|null}` (null = unchanged) · `{"t":"model_set","r","default":true}` (long press) |
| host → device | `{"t":"model_res","r","ok":bool[,"why":"unknown_model"\|"unknown_effort"\|"busy"\|"unsupported"\|"stopped"]}`; the pill settles on the next `meter` |
- The phone gathers taps for 1 s and sends the final target once (relay `PILL_GATHER_MS`). Applied between turns (queued
  behind a running one, like `/model`); stored as `config.json` `agent.model` / `agent.effort` (default = both cleared).
- Claude Code: model by the `set_model` control request (already in `CONTROL_SUBTYPES`); effort has no allowed control request
  (`apply_flag_settings` stays forbidden, Invariant 26), so the host restarts the idle process with `--resume <id> --effort
  <level>` (2.1.285 `--effort low|medium|high|xhigh|max`, "for the current session"). Codex: `turn/start` `model` / `effort`
  ("Override the reasoning effort for this turn and subsequent turns", app-server 0.160 schema), choices from `model/list`
  (`supportedReasoningEfforts`); because an override sticks to the thread, the long press sends the human's own configured
  model / effort (`config/read`) once with the next `turn/start` (`default` in `models` = those values).
  OpenCode: model per `prompt_async`; effort = the model's variant only after a probe proves the field, until then
  `efforts:null` and the effort half is not shown.
- **Signing**: none — the same power as the unsigned `/model` (§8), cannot widen permissions, undone by one long press.

### 10.12 Command menu and `/` completion
- File: **`<agent folder>/.agentj/menu.json`**, relay's schema unchanged (`bridge/bridge/menu.py`, `contracts/menu.schema.json`):
  `{"version":1,"items":[{"cmd":"/x","desc":"…","group"?:"…","order"?:int}],"$schema"?,"comment"?}`, `cmd` matches
  `^/[A-Za-z0-9][A-Za-z0-9_:.\-]{0,62}$`, `desc` 1–200 printable, `group` ≤ 32, `order` ∈ [−100 000, 100 000], ≤ 60 items,
  ≤ 64 KiB; a file that fails is ignored whole (the reply carries `problems`). In the Agent's folder on purpose: "add this
  skill to my menu" is something the Agent may do — an item only **inserts text** into the composer, the human still sends it.
  Re-read on every request: edit, reload the page, done (no deploy).
- `{"t":"menu_get","r"}` → `{"t":"menu","r","source":"file"|"default","items":[…],"skills":[{"cmd","desc"}],"cmds":["clear",…],
  "problems":[…]?}`: `skills` = Claude Code's own skills (init `skills`; empty for Codex / OpenCode), `cmds` = §8's whitelist (the
  one-tap command buttons, which keep executing directly). `/` completion matches all three; picking one inserts it.
- Default items when no file: the whitelist commands with Chinese / English descriptions (never Leo's private skills).

### 10.13 Relay change — BULK
- Host → relay op `0x03` (`[0x03][cid u32 BE]`, empty payload) after the device on `cid` is ready and allowlisted (never for a
  pairing in progress). The relay then allows that socket **240 frames / 10 s** (instead of 60) until it closes, and tells the
  device text `{"t":"rate","n":240,"w":10}`. All boosted sockets of one channel together: ≤ 600 device → host frames / 10 s;
  the socket that crosses it is closed 4029 (`rate`). The channel total is counted per channel (a window kept on the host's
  socket, carried over when the host reconnects in place), so closing and reopening device sockets never resets it
  (P33-X11); it is a fixed 10 s window, so an unaligned 10 s span can see ≤ 2 × 600. Unboosted sockets, the payload cap, per-IP and per-channel socket limits:
  unchanged. A relay without BULK ignores op `0x03` (§2 drops unknown ops) and the device stays at ≤ 50 / 10 s.
- DoS bound: a stranger's socket never gets BULK (only the host grants it, after the Noise handshake and the allowlist), so the
  unauthenticated cost per IP is exactly L2's. A paired device's socket costs ≤ 240 × 64 KiB / 10 s ≈ 1.5 MiB/s; ≤ 5 devices.
  25 MiB ≈ 582 chunks ≈ 27 s at the boosted rate (≈ 120 s unboosted). The relay still stores nothing and holds no key.

### 10.14 Phone-only substitutes (no wire)
- **Forward to Telegram** (relay `/forward`, Leo's own bot) → the phone's share sheet: `navigator.share({text})` of the page's
  reply, fallback copy to the clipboard + toast. Nothing leaves the phone except where the human sends it.
- **Read aloud** (relay `/speak`: a Claude rewrite + Leo's cloned voice through a paid cloud API) → `speechSynthesis` with
  the phone's own voices (language from the text), Markdown stripped to plain text, play / pause / stop on the same button.
  Nothing leaves the phone. (A host-side TTS could come later as its own section; not part of parity.)
- **Drafts** (24 h) and **sent-input history** (last 50) are phone-local, like relay's `vr_draft` / `vr_hist`, but not stored
  as readable JSON: AES-256-GCM under a non-extractable WebCrypto key (record `local`) that lives in the **same origin's
  IndexedDB** as the ciphertext. What this protects: a casual read of the stored files (a backup or a copied store, a
  file browser). What it does not: code running in this origin (it can call the same unseal), or anyone who copies the
  whole browser profile and opens it — the same protection as the device's own keys, no more. Drafts, input history **and
  the `local` key** are deleted, together with the in-memory field, ↑↓ history, tray, quote and queued sends, when the phone is
  unpaired or re-paired (`#repair`, ≡ 解除配对, a new pairing) and as soon as the host revokes it (close 4010 / a RESUME
  refused); the removed screen says what was deleted.
- **Sends are bound to a session and a computer** (no wire change; P33-X01 / X02): every completed handshake starts a new
  send generation whose first message is `hello`; a host `up:false`, a close or a new session ends the generation, and
  anything queued on it is dropped, never encrypted under the next one. Words that were not sent stay in the composer (the
  human sends again); an upload interrupted by a reconnect resumes after `ready` with a fresh `blob_open` to the same
  computer only (it is staged, not delivered, until the human's Send). Unpair / re-pair / revoke drop every upload.
- Reader font size (`aj.readerFs`) and the global type size are phone-local display settings.

### 10.15 Which device → host messages are signed
| message | signed | why |
|---|---|---|
| `answer` (§8), `q_answer` (§10.7) | **yes** (`agentjarvis-approve-v1` / `agentjarvis-question-v1`) | they decide what the Agent may do or what it is told the human chose — a record that can be re-checked |
| `mem_rm` `mem_undo` `estop` `resume` `task_set` (§8) | **yes** (`agentjarvis-control-v1`) | they change the customer's files or the stop switch |
| `say`, `msg`, `blob_*`, `asr` blobs | no | chat input: the Noise session proves the approved device; nothing runs until the Agent decides, and every tool call it makes still needs §8's approval |
| `say_cancel`, `grant_off` | no | they only withdraw / narrow, and only for that device's own items |
| `hist_get`, `menu_get`, `mem_list`, `act_list`, `task_list` | no | reads, answered to the asking session only |
| `model_set`, `slash` | no | the human's own everyday settings, no permission change, reversible (`/clear` confirmed on the phone and undoable) |
| `pref_set` (§10.16) | no | four everyday display / voice settings only (language, theme, hands-free, read replies aloud); nothing that lowers safety |


### 10.15 Preferences and optional synthesized speech (0.12)

Only authenticated paired p33 sessions receive `preferences {value,problem}`. Value is bounded pure-data schema configuration plus generated wake phonemes; never named environment values, owner Telegram IDs, approval/device state or credentials. `problem` preserves runtime last-good and is visible. Default wake phrase derives from current Agent name; an explicit phrase overrides it. Older clients ignore this message.

`tts_get {r:<id22>,id:<completed turn>}` accepts only completed reply text, no arbitrary input/provider URL. Host uses its configured local engine or own-key fixed cloud provider. Response `tts_chunk {r,i,data:<base64>}` is ordered 24KiB chunks, capped at 8MiB total; `tts_end {r,ok,bytes,mime:"audio/wav"}` finalizes, or a redacted why fails. All travel inside Noise, with per-chunk session/allowlist checks. Client binds synthesis to session generation and discards out-of-order, oversized or stale audio. Revoke stops further delivery. Synthesized plaintext stays at endpoints/provider; ciphertext can cross the blind relay.

Telegram is a separate opt-in vendor-readable channel, not a Noise approval session. Owner-private text has src.k=telegram; no Telegram sender can sign an approval or become a paired device. Enrollment is human-only and keys remain local. Group/media parity is pending.

### 10.16 Settings from the phone — `pref_set` / `pref_res` (C6 / A1, 0.15)

Device → host (p33 sessions only, ready and allowlisted, inside Noise like every §10 message): `{"t":"pref_set","r":<rid
≤ 32>,"key","value"}`. Whitelist, exactly: `appearance.language` (`zh`|`en`, the one language value of A1),
`appearance.theme` (`system`|`light`|`dark`), `voice.wake_enabled` (boolean), `voice.speak_replies` (boolean), `agent.high_risk_warnings` (boolean, default false), `agent.session_mode` (`shared`|`independent`), `agent.isolation` (boolean,
default true: an independent session runs fenced) and `agent.allow_docker` (boolean, default false) — F14 (P45b). The value is
checked against the schema type / enum before anything is written. Anything that could lower safety —
every `human.*` / `security.*` key, approval timeouts — is **not**
settable this way: the panel shows it read-only with the `agentj config …` command the human runs at the computer. The
global type size (`appearance.font_scale`) stays phone-local. The host writes exactly like `agentj config set` (structural
JSON5 edit under `preferences.lock`, history, last-good, activation), then answers the asking session
`{"t":"pref_res","r","ok":true|false,"key"}` + `"problem"` on failure — `not_allowed` (not in the whitelist) · `bad_value`
· `busy` (the preferences file is locked by another writer for 5 s) · `rejected` (validation / activation failed) — and
then broadcasts the normal `preferences` message to every ready session. A no-op (same value) answers ok without a write.
A language change is hot: the main Agent speaks the new language from its next turn (A1), and a linked host reports it to
the account soon.

`preferences` (§10.15) gains `host` — read-only facts for the settings panel: `{"version": <agentj version>,
"language_at": <ms, 0 = never set here>, "settable": [the keys above], "devices": [{"id","name","paired_at","online"}]}`
(the paired phones as metadata, never keys). The safety switches the panel shows read-only are already in
`value.agent.high_risk_warnings` / `value.agent.session_mode` / `value.agent.isolation` / `value.agent.allow_docker` (F14: all phone-settable; warnings default off; native permissions unchanged). Isolation / docker apply from the next Agent start. There is no wire message to rename or unpair a phone from
another phone: pairing changes stay with the human at the computer (`agentj devices …`) or the account Dashboard's
host-checked unbind (§7 sync); the phone's own "forget this computer" is local to that phone.

## 11. Admin password and secret cards (F17, P46) — `agentj sudo` · `agentj secret request`
The two human-only steps the Agent cannot do itself go through a paired phone, never through chat or a terminal. Code:
`host/agentj/elevate.py`, `web/public/js/elevate.js`; wire helpers in `protocol/wire.js` (`elevateFields`, `elevateDigest`,
`elevateMessage`, `sealValue`).

**Agent → host.** One JSON line on `<state>/agentperm/elevate.sock` (dir 0700, socket 0600, `SO_PEERCRED` same uid; inside
`agentperm/` so a fenced Agent reaches it too): `{"t":"sudo","argv":[…],"why","effect","cwd","timeout"}` or
`{"t":"secret","name","purpose","dest","cwd","verify_url"?,"verify_header"?,"verify_cmd"?}`. One JSON line back, never the
value: sudo `{"result":"done","code","stdout","stderr","truncated"}` (≤ 256 KiB each) · secret `{"result":"saved","receipt":
{"name","dest","length","fingerprint":"sha256:<8 hex>","verify":"ok"|"fail"|"skipped","detail":"HTTP 200"|"exit 1"|…}}` ·
otherwise `denied` · `timeout` · `gone` (the CLI hung up before a decision: the card is withdrawn) · `stopped` · `locked`
(`secs`) · `bad_password` · `no_device` · `busy` (> 4 open) · `failed` (`why`) · `refused` (`why`, `detail`: the request
itself is malformed — argv 1–256 strings, shown command ≤ 2 000 chars, `why` required ≤ 300; `name` a variable name; `dest`
`env:<file>[#KEY]` (one `KEY=value` line, the rest of the file kept) or `file:<path>` (whole file), never inside the state
directory, never a symlink or another user's file, parent must exist; `verify_url` https (http only to loopback), header
template `Name: …{value}…`; one check at most). The CLI: exit = the command's status, 125 + `SUDO_RESULT:` / `SECRET_RESULT:`
on stderr for a card outcome, 3 = saved but the check failed.

**Host → device.** `{"t":"elev","id":<32 hex>,"kind":"sudo"|"secret","n":<32 hex one-time nonce>,"epk":<b64url X25519, this
card only>,"ttl":s,"tries":n[,"bad":k]` + sudo `"cmd","why","effect"` / secret `"name","purpose","dest","verify"`}` — sent
to every ready allowlisted session and again after a resume; a wrong password re-sends the same id with a NEW `n` and `epk`.
`{"t":"elev_done","id","result"[,"code"][,"verify"]}` ends it everywhere. Web Push kind `ask` wakes the phone.
TTL: sudo 120 s, secret 300 s (from each arming).

**Device → host.** `{"t":"elev_answer","id","ok":bool,"n","ts":<ms>,"sig"[,"epk","ct"]}`.
- digest `D` = hex SHA-256(`"agentjarvis-elevate-v1\n" + kind + "\n"` + one line per shown field `hex SHA-256(field)`, joined
  by `\n`); fields sudo: cmd, why, effect · secret: name, purpose, dest, verify (`""` when absent).
- seal (`ok` only): phone ephemeral X25519 `e`; `k = HKDF-SHA256(ikm = X25519(e, epk), salt = epk ‖ e_pub, info =
  "agentjarvis-seal-v1")`; `ct = iv(12) ‖ AES-256-GCM(k, iv, value, AAD = "agentjarvis-seal-v1\n" channel \n device \n id \n
  kind \n n \n D)`. The value is UTF-8 (≤ 8 KiB); the page clears the field and zeroes the bytes after sealing.
- signature: Ed25519(device approval key, `"agentjarvis-elevate-v1\n" channel \n device \n id \n kind \n (allow|deny) \n n \n
  ts \n D \n (hex SHA-256(ct) | "-")`).
- The host acts only for a ready session of an allowlisted device with an approval key, an open card before its deadline,
  `n` equal to the card's CURRENT nonce (then cleared: one attempt per nonce), `|now − ts| ≤ 120 s`, and a signature that
  verifies over what IT stored; anything else changes nothing (`elev_refused` in host.log, a `refused` line with the reason in
  `elevate.log`: `shape` · `late` · `stale` · `replay` · `no_key` · `bad_signature`). The seal is opened with the card's
  in-memory private key only, then: sudo → `sudo -S -k -p <random marker> -- argv` in `cwd` with `LC_ALL=C` and a
  minimal environment, the password + `\n` written to stdin from a bytearray that is zeroed, stdin closed; a second prompt (or
  sudo's "incorrect password") = wrong password → the card re-arms (≤ 3 tries per card); 3 wrong in a row across cards →
  every sudo card refused for 60 s, doubling to 1 h (`elevate.json`, 0600, survives restarts); secret → written through a
  temp file + rename (0600, `O_NOFOLLOW`), then the check the card showed (GET without redirects; or `/bin/sh -c` with the
  value only in that child's `$NAME`), only `ok`/`fail` + status class kept.
- Stop everything (`estop`) ends open cards (`stopped`); a command already running finishes.

**Logs.** `elevate.log` (0600): ts, kind, id, result, reason, device + approval key + signature + `n` + `sig_ts` +
`ct_sha256` + `channel` for signed outcomes (re-checkable: `agentj secret log`), `shown_sha256`, sudo `argv_sha256` + exit
code, secret `name` + `dest_sha256` + check result. Never a password, a secret, the command text or the destination path.
host.log: `elev_card` / `elev_done` / `elev_refused` (id, kind, result/reason, device).

**Admin helper (optional, `agentj sudo-helper install | sync | uninstall | status`).** One ordinary §11 sudo card (password on the
phone) installs, as root: `/usr/local/libexec/agentj-elevate` (standard-library Python run as `/usr/bin/python3 -I -S`),
`/etc/agentj/elevate-keys.json` (this channel + the approval keys of the phones paired now) and
`/etc/sudoers.d/agentj-elevate` (`<user> ALL=(root) NOPASSWD: /usr/bin/python3 -I -S /usr/local/libexec/agentj-elevate`,
`visudo -cf` first). The card's command snapshots the generated files into a root-owned staging folder and checks the SHA-256
values printed in the command before installing, so what the phone showed is what goes in. Afterwards a sudo card carries
`"helper":[device ids]`; a listed phone may answer `ok:true` with no `epk`/`ct` (signature line `ct` = `-`), and serve runs
`sudo -n -- /usr/bin/python3 -I -S /usr/local/libexec/agentj-elevate` with `{"channel","device","id","n","ts","sig","argv",
"why","effect","cwd"}` on stdin. The helper (root) recomputes `D` from argv / why / effect, verifies the Ed25519 signature
(pure-Python RFC 8032) against its root-owned allowlist, requires `|now − ts| ≤ 120 s` and an unseen nonce (root-owned
ledger, 10 min), logs `argv_sha256` and runs argv with a fixed PATH. The Agent (same user) can call the helper, but cannot
sign. A phone removed from Agent J must also be removed from the helper (`sync` / `uninstall`, each one password card).
Not covered: Face ID / WebAuthn user verification before 「同意」, Windows, a macOS SMAppService helper (macOS gets the same
sudoers helper, `root:wheel` + `shasum`; static tests only).
