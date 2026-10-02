# agentjarvis wire protocol v1 (alpha A2 + A3 §7 + L1 §8–§9)

> Source of truth for the blind relay, the host (`host/`) and the web client (`web/`). PR1: the relay only ever sees
> the bytes described in §2; everything a human types travels inside §3's Noise transport messages.

## 0. Primitives (no third-party crypto code at runtime)
| | Browser / Node | Host (Python) |
|---|---|---|
| X25519, AES-256-GCM, SHA-256, HMAC | WebCrypto (`crypto.subtle`) | `cryptography` (OpenSSL) |
| Ed25519 (host ↔ relay auth only) | WebCrypto in the relay Worker | `cryptography` |

Noise state machine: `protocol/noise.js` and `host/jarvis_host/noise.py`, ~150 lines each, written to the Noise spec rev 34
and pinned by the cacophony test vectors in `protocol/vectors/` (both sides) plus a JS↔Python interop test.
Suites: **`Noise_IKpsk2_25519_AESGCM_SHA256`** (first pairing) and **`Noise_IK_25519_AESGCM_SHA256`** (every later connection).

## 1. Identities
- **Host**: `host_x25519` (Noise static `s`), `host_ed25519` (proves channel ownership to the relay). Files in the host state dir, 0600.
- **Channel id** = `b64url(SHA-256("agentjarvis/channel/v1" ‖ host_ed25519_pub)[0:16])` (22 chars). Public, stable per host.
- **Device**: one X25519 static key. Browser: WebCrypto, `extractable: false`, kept in IndexedDB. **Device id** =
  `b64url(SHA-256("agentjarvis/device/v1" ‖ device_x25519_pub)[0:12])` (16 chars), computed by the host.

## 2. Relay (`wss://alpha-relay.agentjarvis.net`)
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
1. `jarvis pair` asks the running `jarvis serve` for a pairing: random `pairing_id` (16 B) and `psk` (32 B), expires in 5 min.
2. QR = `https://alpha-web.agentjarvis.net/#p=` + b64url(JSON `{"v":1,"r":relay_wss_url,"c":channel,"k":b64url(host_x25519_pub),
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
   **L2: a non-empty code also needs the approval passphrase** (`jarvis passphrase set`, scrypt hash in `approver.json`, 0600):
   the control-socket message is `{"cmd":"code","code":…,"pass":…}`; `serve` checks the passphrase (after the 5-remote cap,
   before the code). Wrong → `{"ev":"pass_wrong","left":n}` and the device keeps waiting (the code is not used up); 5 wrong in a
   row → locked 1 min, doubling up to 1 h (persisted), and the pairing ends `pass_locked`; none set → `pass_not_set`. The local
   admin page (`jarvis admin`) sends the same message, so a holder of its link or session still cannot approve without it.
5. Approve → device static key + label go into the host's allowlist (`devices.json`, 0600), host sends `approved`. Deny/timeout →
   host closes the device socket; it never sends that device an application message.
6. Later connections: RESUME_INIT (IK). The host learns the device static key from msg1 and **silently closes** the socket unless
   it is on the allowlist (logged as `unknown_device`). Accepted → HS_RESP, `hello` from device, then `ready` from host.
7. **Revoke** (`jarvis revoke <device id>`): removed from the allowlist, then every live session of that device is detached in one
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

## 7. Host ↔ control plane (A3, A3.1, A3.2, L2, seat setup): signed envelopes
The host talks to the Dashboard's control plane over HTTPS at `https://api.agentjarvis.net/v1/host/*` (public, no Access;
config `api`, env `AGENTJARVIS_API_URL`). It authenticates with its **existing** `host_ed25519` key (§1) — no bearer token, no
new secret. **Nothing the control plane answers can add or approve a device**: the host *pushes* metadata; the answers it
parses are `login` / `poll` / `seat-bind` / `seat-leave` status fields, — A3.1 — `sync`'s list of *unbind requests* (whitelisted) and — A3.2 — the Agent
name (a display string). An unbind request can
only remove a device, and only after the host's own checks (below); §4–§7 stay the only way in. A device that exists only in the Dashboard's database is unknown to the host and is silently refused (§4.6).

**Envelope** (every request body, `content-type: application/json`, ≤ 32 KiB):
`{"pk": b64url(ed25519_pub 32 B), "body": b64url(UTF-8 JSON), "sig": b64url(Ed25519(sk, CONTEXT + "\n" + body_field))}`
— the signed message is the ASCII bytes of the context string, a newline, and the `body` field **exactly as sent** (base64url text).
Contexts: `agentjarvis-host-login-v1` · `agentjarvis-host-poll-v1` · `agentjarvis-host-report-v1` · `agentjarvis-host-sync-v1` ·
`agentjarvis-host-rename-v1` (A3.2) · `agentjarvis-host-decline-v1` (L2) · `agentjarvis-host-seat-bind-v1` (seat setup) ·
`agentjarvis-host-seat-leave-v1` (seat setup, review SS-02) (distinct from the relay's
`agentjarvis-relay-auth-v1`, so no signature is valid in two places). Every inner body has `v:1`, `t`, `channel`, `ts` (unix s).
Server checks, in order: size and shape → `pk` is 32 bytes → `channel == channel_id(pk)` (§1) → signature → `|ts − now| ≤ 300 s`
→ strict schema (unknown keys = 400; the only optional keys are `report`'s `agent_name` and `machine`). Failures: malformed 400 `bad_request` · signature / derivation 401 `bad_signature` ·
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
  the report gets 403 `not_bound`). After a decline the channel is free: a new `jarvis login` works at once. Decline can only take
  the host *out* of a tenant; it never binds, approves or adds anything.
- **A3.2 confirm**: the bound answer's `agent_name` joins the question —
  「添加到公司账号 <slug>（<name>），Agent 名「<agent_name>」？[y/N]」; only `y` writes `cloud.json` **and** sets the local Agent name.
- **Seat bind (seat setup)** — the alternative to the 8-character code. A company owner who paid for a seat creates a **setup
  code** for that one seat in the Dashboard (`ajt_` + 43 base64url characters = 256 random bits; the server stores only its
  SHA-256; 7 days; single use; revocable) and gives it — inside one sentence for an Agent, or by email to an employee — to the
  computer that should take the seat. `jarvis login --seat <code> --name <name>` (also `--seat-file <path>`, a 0600 regular file,
  not a symlink, so the code stays out of argv and shell history; `--seat -` = one line on stdin) refuses locally, sending
  nothing, a code that does not match `^ajt_[A-Za-z0-9_-]{43}$` and a name that fails the Agent-name rules; otherwise it sends
  one signed `seat-bind`. **There is no y/N question: possession of the code is the human's consent** — they handed it to this
  computer's Agent. On 200 the host writes `cloud.json` exactly like the code path plus `via: "seat"` (the code path writes
  `via: "code"`; a file without `via` is read as `"code"`), sets the local Agent name to the answer's `agent_name` (or the name
  it sent), logs `cloud_linked kind=seat`, prints 「✓ 已添加到公司账号 <slug>（<name>）的席位，Agent 名「<name>」」 + an English
  line, and sends the first report; `jarvis status` / `jarvis doctor` show "linked via seat setup" vs "via code". Exit codes:
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
  output and in `jarvis status`; `jarvis unlink` takes the host out of that company (seat leave, below) and stops all
  reporting at once. install.md tells the Agent to show the human the company it joined.
  **Replay**: when the 200 is lost (timeout) the host has written nothing; running the same `jarvis login --seat` again sends the
  same code from the same key, and the server — seeing the channel bound by this key through the setup whose hash this code
  has (still `bound`) — answers the same 200 without writing anything (it costs the channel allowance a success costs). Any
  other seat-bind on a bound channel is 409 `already_bound`.
- **Seat leave (seat setup, review SS-02)** — the undo of a seat bind, for the host's human: the company was not the expected one
  (a planted or wrong code), or the employee leaves. `jarvis unlink` of a `via: "seat"` link first sends a signed `seat-leave`
  (best effort; it prints 「已通知 Dashboard 把本机移出公司 <slug>」 or why not — then the owner must recall the seat) and then removes
  `cloud.json` exactly as before; a `via: "code"` link is removed locally only, as before. Allowed **any time**, but only for the
  host bound on this channel with this same key **through a seat setup** (`setup_id` not null). Effect, one D1 batch whose first
  statement — the conditional update of the host to `unbound` — carries all of that and the budget (≤ 10 per channel per hour):
  the setup becomes `revoked` (its code can never bind again), the holder's `seat_holder` membership goes unless they hold another
  seat of that company, the host's device rows are deleted and its pending unbind requests `cancelled`, one `seat_leave` ledger
  charge, one audit row `seat_left`. Answers: 200 `{"status":"left"}`; 404 `not_found` (not bound / another key / code-bound — also
  for a repeat after a lost answer: the goal holds, nothing more is written); 429. It can only take the host *out*; it never
  binds, approves or adds anything.
- **Which URL the host prints**: its configured Dashboard (`AGENTJARVIS_APP_URL` → config `app` →
  `https://alpha-app.agentjarvis.net`). The server's `verification_uri` is printed only when its origin (scheme, host, port) equals
  that; otherwise it is ignored, so a compromised control plane cannot point the human at a look-alike page.
- **Report** = the host's own view, replaced wholesale on every accepted report: devices on its allowlist (`id` per §1, `name` =
  the device's self-chosen label after `clean_label`, ≤ 64 chars, `paired_at` unix s, `online` = has a ready session), at most 64;
  `pending` = how many pairings are waiting for the human at the host terminal and since when (no label, no code, no pairing id);
  `seq` strictly increasing per host (the host uses `max(last_seq + 1, now_ms)`); `agent` = `agentjarvis-host/<version>`
  (server rule `^agentjarvis-[a-z-]+/[0-9A-Za-z.+-]{1,32}$`). The server **refuses** (400) a `name` containing control, format
  (bidi, zero-width), surrogate, line/paragraph-separator characters or any space other than U+0020 — `clean_label` never emits
  them, so it does not clean on the host's behalf. The seq gate, the device-row replacement and the rate charge are one D1 batch:
  a report that finishes after a newer one, or races an unbind, changes nothing (409 `replay` / 403 `not_bound`); replays never use
  up the hourly budget.
- **Host-side state**: every `cloud.json` mutation (login write, seq update, `jarvis unlink`) holds an `fcntl` lock on
  `cloud.lock` (0600) in the state dir and re-reads under it, so `serve` and the CLI never interleave and a seq update can never
  recreate a file `jarvis unlink` removed. Requests honour the standard `HTTPS_PROXY` environment (a proxy sees the API hostname
  and timing; TLS is verified end to end); loopback never uses a proxy.
  Sent on `serve` start, on approve / revoke / pairing pending / pairing end / device online change (debounced 2 s), and every
  300 s as a heartbeat. Best effort: a failed report is logged as metadata (`report_fail`, HTTP status class) and never blocks `serve`.
- **Agent name (A3.2)** — one thing, three names: an Agent = 1 seat (billing) = 1 host (physical) = a name staff talk to.
  Rules (one copy per language: `dashboard/public/agentname.js`, `host/jarvis_host/text.py`): trim Unicode White_Space at both
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
  rename (`jarvis name <new>` or the host's local Agent 管理页): while linked the host calls `/v1/host/rename` first and writes
  locally only on 200 (on 409 it shows the suggestions; unreachable → 「连不上 Dashboard，名字没改」); unlinked → local only.
  **Replay**: `rename` carries no nonce or seq — only the ±300 s `ts` window. Accepted (review A3_2 A32-07): a replayed
  envelope can only set a name this host itself signed within the last 5 minutes, it still needs the host to be bound with
  the same key, and it is charged to that host's own budgets; whoever could capture it (TLS break or control-plane insider)
  could rename the Agent in the Dashboard anyway.
  **A name from the control plane is display-only on the host**: it is never interpolated into a shell command, a file path,
  an agent prompt or HTML, and it cannot add or approve a device, route, or run code (invariant 8).
- **Remote limit** (Q32, 2026-10-02): the allowlist holds at most **5** devices (`state.MAX_DEVICES`, enforced in
  `add_device`, the one writer). When it is full, `serve` refuses an approval with `device_limit` even if the code is right; the
  pending event `jarvis pair` receives then carries `full`, `limit` and the current devices (id, label, paired_at, online), and
  `jarvis pair` shows 「已达 5 台上限，需先解绑一台遥控器才能添加新的」, lists them, and on the human's number + y sends
  `{"cmd":"unbind","device"}` over the local control socket (= `jarvis revoke`) before asking for the code.
- **Unbind requests (sync, A3.1)**: `serve` syncs every 20 s while linked (60 s otherwise), off the event loop. For each
  request it decides itself: switch off (`jarvis remote-unbind off`, config `remote_unbind: false`) → `disabled`; device not on
  its allowlist → `unknown_device`; 3 already executed in the last hour → `rate_limited`; else it revokes exactly like
  `jarvis revoke` → `revoked`. Every decision is a `remote_unbind` line in `host.log` (request id, device id, result) and a line on
  the serve terminal; results go back on the next sync (≥ 2 s later — syncs are never closer, whatever the answers say; one
  HTTP call in flight). Beyond 30 decisions an hour the host stops deciding (requests stay pending, one `remote_unbind_throttled`
  line). Caps and decided request ids (7 days, ≤ 512) persist in `remote_unbind.json`, so a request id is never acted on twice
  and a restart resets nothing. Requests are authenticated by the control plane, not device-signed (ARCHITECTURE G-A18). The response can never
  add or approve: the parser accepts only `{id, device}` pairs, `cloud.py` never touches `devices.json` (AST test).
- **What the control plane learns** (disclosed on `/security`): channel id and host public key, bound tenant, the Agent name
  (the owner's, and the one the host reports) and the host's hostname (`machine`), device ids and
  self-chosen labels, pairing times, online flags, the count/start of pending pairings, report times, the host software version
  (`agent`), every login attempt with its poll times (kept 24 h) and whether the host declined it, sync times and unbind results, — seat setup — which setup code (by id; the code itself only as SHA-256) bound the host and
when, and — at Cloudflare's ingress — the request IP (stored only as a
  salted hash of the IPv4 address / IPv6 /64, 48 h). Never: keys other than the host's public key, device public keys, safety
  codes, pairing links, message text.

## 8. Agent bridge (L1): the phone ↔ the customer's own agent
The host drives the agent the human chose (`jarvis agent claude|codex --dir <dir> [--model M]`, config `agent`; read when
`serve` starts) **as the same OS user, with the human's own login, settings and permission rules**, through official headless
interfaces only:
- **Claude Code**: one long-lived `claude -p --input-format stream-json --output-format stream-json --verbose
  --permission-prompt-tool mcp__agentjarvis__approve --disallowedTools mcp__agentjarvis__approve --mcp-config <inline JSON>`
  in the chosen directory; restarted with `--resume <session id>` (kept in `agent.json`, 0600) after it exits. The MCP server is
  `python -m jarvis_host.permtool` (stdio, standard library only); it inherits `AGENTJARVIS_PERM_SOCK` / `AGENTJARVIS_PERM_TOKEN`
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
  `$XDG_RUNTIME_DIR`, the host's state directory replaced by an empty tmpfs with only `agentperm/` bound back, jarvis's code and
  the shell start-up / autostart / systemd-user / `authorized_keys` files read-only, session-bus / display / tmux variables
  unset, `no_new_privs`. Same user, same login, same settings — nothing the agent may do is widened, only jarvis is out of its
  reach. If bubblewrap cannot start, the agent is not started (notice on the phone) unless the human chose
  `jarvis agent … --unfenced` at the terminal (asks for the approval passphrase). Codex runs in the same fence. **Never** passed: `--dangerously-skip-permissions`, `--permission-mode`, `--allowedTools` or anything that
  widens what the session may do — the phone only answers questions Claude Code itself would have asked, and an approval
  returns the tool input unchanged (`updatedInput` = input).
- **Codex**: `codex exec --json --skip-git-repo-check [-m M] -` per message (prompt on stdin), continued with
  `codex exec resume … <thread id> -`. Text only: Codex's own approval / sandbox settings decide what runs; nothing is
  relayed for approval in v1, and the host passes no sandbox or approval flags.

App messages (all inside §3 transport messages):
| direction | message | meaning |
|---|---|---|
| host → device | `{"t":"msg","id","text","ts","seq":n,"from":"agent"\|"host"\|"you"\|"device"\|"notice"[,"name"]}` | chat; `seq` increases per serve run; `you` = this device said it, `device` = another paired device (`name` = its label), `notice` = a host-side note (agent missing / exited / turn failed) |
| host → device | `{"t":"status","s":"none"\|"idle"\|"working"\|"waiting"\|"down","agent":"claude"\|"codex"\|null}` | 未接 / 空闲 / 干活中 / 等你批准 / 没在运行; sent on ready and on every change |
| host → device | `{"t":"ask","id":"<32 hex>","tool":"<≤ 64>","summary":"<≤ 2000>","ttl":<s left>}` | a permission request; `summary` = what the phone shows (command / path / compact JSON) |
| host → device | `{"t":"ask_done","id","result":"allow"\|"deny"\|"timeout"\|"gone"}` | decided (by any phone, the timeout, or the agent withdrawing / serve stopping) |
| device → host | `{"t":"answer","id","ok":bool,"sig":"<b64url 64 B>"}` | the human's decision, signed |

- **Replay**: the host keeps the last 100 chat entries **in memory only** (never on disk). After a device becomes ready it gets
  the current status, the push key (§9), every entry with `seq > since` (all of them when `since` is absent or larger than the
  host's current `seq`, i.e. after a host restart) and every request still waiting. The page keeps chat in memory only too.
- **Signature**: `Ed25519(device sk, UTF-8 "agentjarvis-approve-v1\n" + channel + "\n" + device_id + "\n" + id + "\n" +
  ("allow"|"deny") + "\n" + hex(SHA-256(tool + "\n" + summary)))` — bound to the channel, the device, the request id, the decision
  and the exact text the phone displayed (the device hashes what it showed; the host hashes what it sent). The Noise session
  already proves which device sent the answer; the signature makes each decision a record that can be re-checked later.
  JS: `protocol/wire.js` `approveMessage`; Python: `host/jarvis_host/approvals.py`. Browser key: WebCrypto Ed25519,
  `extractable: false`, in IndexedDB (`generateSigningKeypair`).
- **Host rules**: a request is answered only by a ready session whose device is on the allowlist and has an approval key, for an
  id still open, before its deadline, with a signature that verifies; anything else is ignored and logged as metadata
  (`answer_refused`, reason). The first valid answer decides; the others change nothing. **Deadline 120 s, then deny.**
  No paired device with an approval key → deny at once. At most 16 open requests (more → deny). `serve` stopping or the agent
  withdrawing the request → deny. Every decision is one line in `approvals.log` (0600): time, id, channel, agent, tool name,
  SHA-256 of the tool input, SHA-256 of what was shown, decision, reason (`device` · `timeout` · `no_device` · `serve_stop` ·
  `agent_gone` · `too_many`), device id, its public key and signature — **never the tool input itself**.
  `jarvis approvals --verify` re-checks every signed line against the device's current key.
- **What leaves the host**: the chat text, the summaries and the decisions travel only inside §3 transport messages (the relay
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
  post to arbitrary URLs. (Tests add one exact local origin through `AGENTJARVIS_TEST_PUSH_ORIGIN`.)
- The host posts directly to the push service (RFC 8030) with a VAPID JWT (RFC 8292: `aud` = the endpoint's origin, `exp` 12 h,
  `sub` = `https://agentjarvis.net`) and an aes128gcm body (RFC 8291) whose plaintext is `{"k":"reply"}` or `{"k":"ask"}` padded
  with spaces to 32 bytes — every push has the same size. `TTL: 3600`; `Urgency: high` for `ask`, `normal` for `reply`.
- When: one `reply` push per finished agent turn that produced text, one `ask` push per permission request; never to a device
  whose page is visible (`{"t":"vis","fg":bool}` from the page; a ready session counts as visible until it says otherwise);
  at most one `reply` per 20 s and one `ask` per 2 s per device.
- The service worker (`web/public/sw.js`) has no fetch handler and no cache; it shows **Agent Jarvis — 有新回复** or **Agent
  Jarvis — 有一个请求等你批准**, nothing else, and focuses the page on tap.
- **What the push service (Google / Apple / Mozilla) sees**: that a push for this subscription was sent, when, its constant size,
  its urgency, the host's IP (or its proxy's) and the host's VAPID public key. Never text, never which kind.
