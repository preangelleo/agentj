---
type: Protocol
status: draft
generated: { by: Relay/codex, at: 2026-10-05T06:36:46.400617+00:00 }
---
# F19 fleet and official-notice contract

Fleet endpoints on the existing host API origin: GET `/v1/fleet/list?latest=VERSION`, `stats`, `show/HOST`,
`notices?active=1`, `receipts/ID`; POST `push`, `cancel/ID`. Reuses plaza admin token auth and IP-failure budget.
List omits content, names, email, keys, channel and machine label. Online means a signed report within 150 seconds.
Version comparisons cover major.minor.patch with a/b/rc prereleases; unknown versions never match. Comma-separated
comparison ranges are ANDed; versions/harness/accounts/hosts are also ANDed. Accounts name account slugs, hosts use ids.
`all` requires `yes_all`. The send-time recipient set is fixed; report-time targeting rechecks version/harness/account.

Push fields: type, version (upgrade/security), title_zh/title_en (120 codepoints), body_zh/body_en (2000 codepoints),
priority normal/urgent, target, expires_in (60..2592000 seconds), yes_all, send. Skill additionally needs category
content/app/commerce/general, package name and HTTPS agentj.app plaza link. Security requires affected version range
and is always urgent. Missing send=true is a read-only preview. Empty target is refused. At most 1000 active notices.
Server deterministic scan supplements CLI's mandatory private-fragment/privacy and Jev phishing/danger checks.
No matched values, body or account lists in fleet audit; audit stores action, notice id, recipient count and time.

Signed payload = base64url(UTF-8 JSON), signature = Ed25519(context + LF + payload), context `agentj-official-notice-v1`.
The raw Ed25519 public key comes from the existing compiled official minisign public-key ring. NOTICE_SIGNING_KEY is
server secret (base64url PKCS8); a wrong key refuses signing. Key/public-pin provisioning is the release operator's task,
never CLI/client key generation. Individual payload contains v,id,type,version,title_zh,title_en,body_zh,body_en,priority,
category,package,link,created_at,expires_at (milliseconds). The client never adopts a public key from a response.

A capable signed report adds notices_v=1,harness,notice_receipts (at most 20 {id,state=delivered|read}). Its response
adds notices={body,sig}, context `agentj-official-notice-v1-snapshot`; signed body = {v:1,host,seq,at,notices,active}.
It contains at most two undelivered envelopes and all active addressed ids. Client requires its linked host id,
exact report seq and timestamp within 300 seconds. Report response bound = 128 KiB. Snapshot and each individual
signature must verify. Missing/invalid snapshot never authorizes local delivery or removes durable pending records.
Older hosts omit notices_v and receive their original answer. Older servers: retry removing F19 fields first,
then F13 language only if still refused; persist capability marker and reprobe after 24 hours/version/API change.

Local state: notices.json 0600, flock + atomic write, at most 1000 records, expired records retained one day for dedup.
Delivered means verified and saved to the host inbox. Read means an authenticated paired phone displayed the official
history page while foreground; it does not mean the human understood it or approved an upgrade. Agent delivery has a
separate processed marker, set after native transport accepts it; uncertain writes are marked to avoid unsafe replay.
Queued notices require active, unexpired data and a snapshot no older than 90 seconds at the native delivery boundary.
Cancellation stops future delivery after the next verified report; it cannot undo data already handed to the Agent.

Trusted local policy is separate from escaped JSON title/body: upgrade auto/ask, security auto even in ask mode,
skill relevant -> ask to install (no automatic install), irrelevant -> local silent record, billing/announce/support ->
relay information, no automatic payment. Fixed updater command is agentj update apply --version VERSION; the existing
latest/public release checks, noninteractive install/restart/doctor and native permissions remain authoritative.
Skill relevance does not upload local memory/preferences/package inventory. Explicit updates.skill_categories covers
commerce and legacy packages without category metadata. Content/media/SEO map to content; agent-ops/security/browser/
data/workflow map to app; communication/storage map to general.

Heartbeat 60 seconds, 45 seconds for ten minutes after an urgent notice, 2-second trigger debounce, 100/h local cap.
Expected first online arrival ~0..62 seconds plus HTTPS time; not a measured SLA. Receipt normally takes one additional
triggered report. Urgent phone pushes use encrypted/padded fixed security kind and high urgency, no notice body.
An occupied or absent native Agent waits for its queue to accept the data; no harness preemption or authority bypass.
Jarvis invokes fleet push after the 09:00 upgrade-mail digest; emergency fixes are sent immediately to an explicit range.
That operator schedule is outside this feature's deployment-free worktree.
