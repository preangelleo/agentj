# 0.28.0 scope decision

The new `mail_fwd` per-message-signature slice (#324) is deferred in full pending #561 matching-web evidence. The previously published forwarding interface is retained unchanged; no unsigned fallback was added to the new slice. Its signed source and tests remain in commit c0662f57f8d969ffab0dabe3601d11f1aae37449 for a later coordinated release. Web signatures are additive data accepted by the existing interface. The documentation below describes that staged future slice; it does not claim its Host enforcement is active in 0.28.0. Private file controls remain active and independently signed.

# 0.28 paired protocol restoration (#324 / #329 / #374)

Existing control-v1 and Noise admission remain authoritative. The channel identifies
one host/seat; it is signed together with the current paired device, action, nonce,
120-second timestamp window and SHA-256 of canonical JSON. No changes to released
approval/UV preimages or permissions.

## Explicit mail forwarding

`mail_fwd` signs `{request: <all original mail arguments>}`. Only `t/r/n/ts/sig`
are transport metadata; recipients (including display names), turn, mid, input and
`again` are signed exactly as sent, before normalization. The existing forward
button signs the control. Unsigned old clients receive a refusal, not a send.
Host rechecks live pairing and the same signing key after content preparation.
This control grants one owner-selected forward; it is not an Agent outbound grant.

## Private large files

Capability `privatefiles1`. A completed reply's explicit oversized file reference
inside the validated working folder can become a private download card. The strict
existing type/secret scan remains in force, using bounded slices; no credential
pickup is promoted to a download. Directory traversal and symlink aliases refuse.
Regular media limits and legacy media messages are unchanged.

The existing fixed seat page has locator `/<seat-id>/#file=<random-128-bit-id>`.
The fragment is deliberately not an HTTP file path: the cloud never serves the
file or receives its identifier as an HTTP path. Copying the locator grants no
access. A browser still needs that seat's existing pairing, an online host and a
save tap. Unsupported streaming-save browsers show a limitation, not a Blob fallback.
Unknown/old hosts hide the entry. Offers expire after one hour, are memory-only,
and vanish on restart. Revocation uses the existing paired device signature.

Noise request actions `private_file_get` / `private_file_revoke` sign
`{request: ...}`. Requests carry seat channel and file ID; reads additionally carry
random transfer ID and byte offset. `info:true` returns metadata only. Reads are
sequential, tied to the same live Noise session object, device, working folder and
Agent session. Stop epoch changes, stop, unpair, key removal, revocation, expiry,
source inode/size/mtime/ctime changes reject subsequent chunks. Requests carry no
path. Each reply is at most one existing 45,056-byte chunk, with its SHA-256 and
last flag, inside Noise. The browser writes each verified chunk to its chosen
writable file and aborts on connection change or refusal; it never accumulates the
whole file. Initial offer hashing is bounded-memory but reads the full source.

## Owner-signed customer-service mail

Only the existing owner-signed `bots_write` handoff sends the reply. The text and
verified recipient keep the existing checks and durable send-id reservation. Code
verification messages keep the old automatic format. An owner reply uses
`owner-reply-v1; o=<sender hash>`, `X-AgentJ-Task` (hash of the original handoff
request), direction `service-to-customer` and matching task `In-Reply-To`. Widget
handoffs have task correlation IDs, not a fabricated original SMTP message ID.

A recognized external response must pass the current allowlist and strict provider
sender authentication even if ordinary mail authentication was configured off.
It enters the existing guest/untrusted notification path, with no mail reply route.
System receipts, own copies and unknown/legacy formats never gain automatic
execution; the log records a readable hold reason. Message-ID/UID processing and
duplicate prevention remain the existing mail protocol. Headers are correlation
metadata, not remote signature authority or permission to act on email commands.

Validation limitations and actual results are in `/work/REPORT.md`.
