# agentj wire protocol v1 (alpha A2 + A3 §7 + L1 §8–§9; phone controls and slash commands in §8)

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
  host → relay: `0x01` data to device `cid` · `0x02` close device `cid` (relay closes it with 4010).
- Limits: 32 device sockets per channel (more → 4029), 65 536-byte payloads (more → close 1009), 60 frames / 10 s per device socket (more → 4029).
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
device ids, safety codes.

## 6. Known alpha limits (see `ARCHITECTURE.md` GAP)
No per-message ratchet (forward secrecy is per connection: fresh ephemerals every handshake). **msg1 has no forward secrecy**
(IK / IKpsk2 encrypt the device static key and label to the host's static key; the PSK enters only in msg2): whoever later
steals the host's X25519 key and has recorded msg1 learns that device's public key and label — never chat content, which is
protected by the ephemeral DH. Resume uses IK, not KK as the early design said: the host cannot know which device is calling
before msg1, and IK keeps the device's static key off the wire. No file/voice; relay rate limits are per socket and per
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
`…-plaza-pkg-report-v1`, `…-plaza-pkg-publish-v1` (distinct from the relay's
`agentjarvis-relay-auth-v1`, so no signature is valid in two places). Every inner body has `v:1`, `t`, `channel`, `ts` (unix s).
Server checks, in order: size and shape → `pk` is 32 bytes → `channel == channel_id(pk)` (§1) → signature → `|ts − now| ≤ 300 s`
→ strict schema (unknown keys = 400; the only optional keys are `report`'s `agent_name` and `machine`, plaza `search`'s `limit`, package `search`'s `type` / `sort` / `tag` /
`track` / `limit` / `offset` and package `get`'s `version`). Failures: malformed 400 `bad_request` · signature / derivation 401 `bad_signature` ·
stale 401 `stale` · then per endpoint. Vector: `protocol/vectors/host-envelope.json` (fixed key → exact `body`/`sig`).

| endpoint | inner body | answer |
|---|---|---|
| `POST /v1/host/login` | `{"v":1,"t":"login","channel","ts"}` | 201 `{"login_id","user_code":"XXXX-XXXX","verification_uri","expires_in":600,"interval":5}` · 409 `already_bound` · 429 `rate_limited` / `too_many_pending` |
| `POST /v1/host/poll` | `{"v":1,"t":"poll","channel","ts","login_id"}` | 200 `{"status":"pending"\|"bound"\|"rejected"\|"expired"\|"declined"}` + when bound `{"host_id","tenant":{"slug","name"},"agent_name"}` (A3.2: the canonical Agent name the owner gave; L2: `declined` = this host already declined it) · 429 `slow_down` (< 4 s apart) · 404 |
| `POST /v1/host/decline` (L2) | `{"v":1,"t":"decline","channel","ts","login_id"}` | 200 `{"status":"declined"}` (also for a repeat: idempotent) · 404 `not_found` (nothing to decline, see below) · 409 `already_confirmed` (this host has reported since the bind) · 429 `rate_limited` (≤ 10 per channel per hour, `retry-after`) |
| `POST /v1/host/sync` (A3.1) | `{"v":1,"t":"sync","channel","ts","results":[{"id","result"}]}` (≤ 10; result ∈ `revoked` `unknown_device` `disabled` `rate_limited`) | 200 `{"unbind":[{"id","device"}],"agent_name"}` (≤ 10, this host's pending requests; A3.2 `agent_name` = canonical name, string or null) · 403 `not_bound` · 429 `rate_limited` (L2: a sync **with** results costs one of ≤ 60 per host per hour; over it nothing is written — keep the results, retry later; a sync without results is never limited) |
| `POST /v1/host/report` | `{"v":1,"t":"report","channel","ts","seq","agent","devices":[{"id","name","paired_at","online"}],"pending":{"count","since"}}` + optional (A3.2) `"agent_name"` (string per §7 Agent-name rules, or null) and `"machine"` (hostname via `clean_label`, ≤ 64, or null) | 200 `{"ok":true}` · 403 `not_bound` · 409 `replay` (seq ≤ last) · 429 `rate_limited` |
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

Any endpoint may answer 500 `{"error":"internal"}` (no detail); the host treats it like any 5xx.

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
- **Replay**: the host keeps the last 100 chat entries **in memory only** (never on disk). After a device becomes ready it gets
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
