# Seat API wire protocol (ADR-A193, P126)

Host-side authority for every byte below is `host/agentj/seat_api/`. The edge (dashboard Worker on
`api.agentj.app` + relay `SeatRestChannel`) only routes. Canonical JSON = keys sorted, no spaces (`canonical()` in
`bots/store.py`, `canonical` in `dashboard/src/bots.ts`). b64u = base64url without padding.

## 1. Two numbers per endpoint

| Field | Meaning | Changes when |
|---|---|---|
| `version` (contract) | the reviewed contract a caller key is bound to | owner publishes a reviewed contract change (`api_version_publish`) |
| `epoch` (route) | which host currently serves the endpoint | seat migration (§5); never on a contract change |

Transport frames, relay tickets, D1 `api_endpoints.epoch`, E2E outer headers and manifests carry the **route epoch**.
P108 used `version` for both; from P126 a registration sends `epoch` = route epoch (starts at 1).

## 2. Host transport frame (edge → host, unchanged shape)

`{endpoint, epoch, method, path, body, key, idem}`; `path` ∈ `''`, `chat/completions`, `jobs`, `jobs/<32hex>`,
`openapi.json`, `_e2e`, `_provision`. `openapi.json` answers 404 unless the owner published the spec (`api_spec_publish`). For `_e2e`, `_provision` and `openapi.json` the edge sends `key:null`, `idem:null`
and never reads an Authorization header (an Authorization header on those paths is ignored, never forwarded).
Host answer `{id, status, body}` as before.

Edge routing per endpoint privacy:

| privacy | plain paths (`''`, chat, jobs) | `_e2e` | `_provision` | `openapi.json` |
|---|---|---|---|---|
| `rest` | Bearer caller key → host | GET/POST forwarded (the SDK works on REST endpoints too) | forwarded (always E2E) | GET forwarded |
| `e2e` | **403 `e2e_required`** at the edge — never a plaintext fallback | GET/POST forwarded | forwarded | GET forwarded |

## 3. E2E envelope (`seat-api-v1`)

Per-endpoint long-lived keys live only on the host (and travel only inside an encrypted migration bundle):
Ed25519 `sign` and X25519 `kem`.

**Manifest** — `GET …/_e2e` → `{manifest, sig}`:
`manifest = {domain:"agentj-seat-api-manifest-v1", endpoint:<32hex uuid>, public_id, epoch, contract:<version>,
kem:<b64u>, sign:<b64u>, issued:<unix s>, expires:<unix s, ≤ issued+86400>}`,
`sig = Ed25519(sign, "agentjarvis/seat-api-manifest-v1\n" + canonical(manifest))`.

**Pin** (given to the customer out of band — key card / owner handoff, never fetched): 
`pin = "ajpin1_" + hex(sha256("agentjarvis/seat-api-pin-v1\n" + endpoint + "\n" + sign + "\n" + kem))[:40]`.
The SDK refuses any manifest whose recomputed pin differs, whose signature fails, or which is expired. No TOFU mode.

**Request** — `POST …/_e2e` (or `…/_provision`), `content-type: application/json`, ≤ 65536 bytes:
`{v:1, endpoint, epoch, key_id:<32hex>, request_id:<32hex>, eph:<b64u X25519>, nonce:<b64u 12 bytes>,
deadline:<unix s>, ct:<b64u>}`.
`header` = the object without `ct`. `aad = "agentjarvis/seat-api-v1\n" + canonical(header)`.
`shared = X25519(eph_sk, kem)`; `salt = sha256(aad)`;
`k_req = HKDF-SHA256(shared, salt, "agentjarvis/seat-api-v1 request", 32)`,
`k_resp = HKDF-SHA256(shared, salt, "agentjarvis/seat-api-v1 response", 32)`;
`ct = AES-256-GCM(k_req, nonce, inner, aad)`.

`_e2e` inner = `{key:<raw ajapi_ caller key>, method:"POST"|"GET"|"DELETE", path, body, idem:<str|null>}`.
`_provision` inner = `{cap:<raw ajprov_ capability>, op:"issue"|"revoke"|"list"|"usage", …op fields}`.

Host checks, in order (all failures are plain JSON `{error:{code}}`, never content): shape/size; `endpoint` and
`epoch` equal the host's current route; `now < deadline ≤ now+300`; `(key_id, request_id)` not seen (replay cache kept
until deadline+60, persisted); AEAD opens; inner key/capability id equals outer `key_id`.

**Response** (HTTP 200 for any envelope that opened): `{v:1, request_id, nonce:<b64u>, ct}` with
`ct = AES-256-GCM(k_resp, nonce, canonical({status, body}), "agentjarvis/seat-api-v1 response\n" + request_id)`.
The edge sees only length/timing/status 200.

## 4. Provisioning capability (`ajprov_`)

Owner-issued (signed owner op), scoped to one endpoint: `max_keys` (total issued), `max_days` (≤ 30), `expires`
(≤ 90 days), ops `issue`/`revoke` (only keys it issued), optional `usage` (export metadata events). Format
`ajprov_<endpoint32>_<cap_id32>_<random43>`; host stores only an HMAC digest. Only reachable through `_provision`
(E2E). `issue` requires `event_id` (payment event, ≤ 64 ASCII): the same event id never issues twice — a replay returns
`{key_id, replay:true}` without the secret (the owner backend must persist the first answer).

## 5. Seat migration (same account)

1. Owner signs `api_migrate_export {id, target_pk}` on the **source** host → source stops admitting (`migrating`),
   waits for running jobs, then returns an encrypted bundle (X25519 to the target host's static key + Ed25519 signature
   by the source host key) containing the endpoint row, per-endpoint pepper, key digests/metadata, E2E keys, idempotency
   tombstones and usage sequence. Never raw caller keys or request bodies.
2. Source host sends signed envelope `agentjarvis-host-seat-api-migrate-v1`
   `{v,t:"seat_api_migrate",channel,ts,seq,endpoint:{id, from_epoch, to_host_pk}}` to
   `POST /v1/host/seat-api/migrate`. D1 records `migrate_to`, `migrate_epoch = from_epoch+1`, `migrate_until = now+600s`
   only if the caller is the current host of that endpoint at `from_epoch` and the target host is bound in the **same
   tenant**.
3. Owner signs `api_migrate_import {bundle}` on the **target** host → verifies, stores with `epoch = from_epoch+1`, then
   registers. D1 accepts that registration only when `host_pk = migrate_to`, `epoch = migrate_epoch`, before
   `migrate_until`; it atomically switches `host_id`/`epoch` and clears the migrate fields.
4. From then on the source's registrations get 409 `route_conflict`; the relay DO keeps a persisted epoch floor and
   refuses tickets with `epoch < floor`, closing older host sockets when a higher epoch connects. Failure at any step
   keeps the old route (the source can `api_migrate_abort`).

## 6. Abuse report and takedown (metadata only)

`POST https://api.agentj.app/v1/<account>/<endpoint>/_report` `{category, request_id?, contact?}`; category ∈
`spam|fraud|malware|illegal|privacy|impersonation|other`; no free text, no attachments; IP-rate-limited; kept 90 days.
Takedown is an admin action (bots admin token): sets `status='removed'`, `takedown_reason` (enum); public calls then 410
`endpoint_removed`; the host's next registration gets 451 `taken_down` and the host stops serving and stops callbacks
(lease ≤ 60 s). Reports never auto-remove.

## 7. Signed callbacks (host → owner-registered URL)

Registered by owner op only (`api_callback_save`): HTTPS, port 443, no userinfo, hostname (not an IP literal), kind
`result` or `usage`. Each attempt resolves DNS, rejects any non-global address (private, loopback, link-local,
metadata, CGNAT, ULA, multicast, reserved, IPv4-mapped), connects the TCP socket to the vetted address with TLS
SNI/Host = hostname, no redirects (3xx = failure). Headers `X-AgentJ-Timestamp`, `X-AgentJ-Event`,
`X-AgentJ-Signature: v1=<hex HMAC-SHA256(secret, ts + "." + event_id + "." + raw_body)>`. Retries 1m/5m/30m/2h/6h,
max 5 within 24h, re-signed. Before each send: endpoint enabled, key not revoked, route lease fresh (< 60 s). For an
`e2e` endpoint the body is sealed to the customer X25519 key registered with the callback.

## Current restoration boundary (2026-11-14)

This is restored default-off source, not an enabled public service. Original approved REST opt-in is unchanged. Register and migrate host envelopes additionally carry `credential_epoch` (host binding/revocation epoch, never endpoint route `epoch` or contract `version`). Registration receipts pin `{tenant_id,host_id,host_pk,credential_epoch,bound_at}` and expire after at most60 seconds. The host independently captures its current account binding, credential journal epoch, route epoch, contract and bot lifecycle for each request and persistent delivery. Binding/revocation/generation drift refuses admission, cached output and callback send; a new Manager or a later lease never replaces the original request capture. No offline edge queue.

Owner management uses the existing `bots_write` signed paired frame. CLI `api request` only proposes. A fresh exact request+target digest UV card is bound to that same live remote; secret output is sealed to that remote and authority is rechecked after awaits. API caller/provisioning keys never enter owner management, main sessions, native permission proofs or UV approval. Current provider restrictions, device revocation and pairing cryptography remain authoritative. Namespace rename's fresh per-action UV is handled by the current Dashboard action contract. Production migrations/deployment and real-provider acceptance are outside source qualification.

**R-0050 C11 (relay side, still default-off).** A ready host socket may send `{"ticket":<ticket>}` alone to renew its
admission: the relay accepts it only for the same endpoint, the same route epoch, the same host key the socket proved at
connect, `epoch ≥ floor`, and an expiry within 60 s; it extends the socket's expiry and sends nothing back (the relay → host
frame grammar is unchanged). Anything else closes the socket 1008, like a bad first ticket. The channel wakes at the earliest
socket expiry or lease refresh instead of every 10 s. Hosts that keep reconnecting each minute are unaffected; using the
renewal frame needs a host change (re-register, then send the new ticket on the open socket).
