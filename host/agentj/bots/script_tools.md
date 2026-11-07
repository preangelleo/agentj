# Owner script tool manifest

Submit `{"op":"tool_save","id":"<bot>","tool":{...}}` through the existing
`agentj bots request --file` proposal route. Local Agent proposals cannot save.
The paired signed phone handler additionally requires real passkey approval of
the complete canonical manifest digest. Execution requires a separate one-use
owner call approval; visitor binding, bot budgets and Jev remain in force.

Manifest fields:

- `kind`: `script`; `name` and `description`: existing tool rules.
- `code`: inline UTF-8 Python source, maximum 32768 bytes; no file path.
- `sha256`: lowercase SHA-256 of the exact UTF-8 code bytes.
- `parameters`: existing bounded object JSON schema, passed as JSON on stdin.
  No argument strings are interpreted as shell commands or command-line flags.
- `enabled`: defaults to false. `level`: only `write` is supported.
- `visible_roots`: only `[]` is supported. No owner/customer filesystem mounts.
- `network_domains`: only `[]` is supported. Nonempty lists are refused.
- `input_bytes`: 1–16384, default 8192.
- Existing `timeout` (1–30 seconds), `visitor_limit`, `daily_limit`,
  `visitor_bindings`, `response_fields`, `response_chars` apply.

The complete stored manifest must fit the existing 65536-byte registry limit.
Unknown fields, code digest changes, path manifests and symlink code references
are rejected. Code is revalidated at load and execution.

Linux uses `/usr/bin/bwrap` with all namespaces unshared, no capabilities,
UID/GID 65534, a read-only root and code, fixed read-only system Python/runtime
mounts, no proc or host devices, and a private 16 MiB `/scratch` tmpfs. There is
no inherited host environment or host scratch mount. OS resource hard limits,
bounded pipes and a host wall deadline apply. Windows/macOS or unavailable
bwrap/runtime refuse execution; there is no unsandboxed fallback.

Stdout must be a JSON object, at most 32768 bytes. Overflow kills the sandbox
and returns `script_output_truncated`, never partial output. Only declared scalar
response fields leave the adapter, with existing privacy redaction before final
character truncation and existing Engine safety/Jev review before model use.
Stderr is discarded. Timeout/unknown outcomes consume approvals and reservations;
there is no automatic retry.

`HostBroker` is a host-only named-tool adapter: it reuses registry identities,
approvals, quotas, secret references, filtered responses and DNS-pinned HTTPS.
It is **not** a script IPC service. Scripts cannot currently use network/broker
APIs; manifests requesting network are explicitly refused. HTTPS credentials
stay in Host. No proxy-only sandbox or direct socket exception exists. Script
network IPC would need a separately verified namespace-safe broker implementation.

Offline controls: `tests/test_script_tools.py`. Real bwrap runs are required;
namespace permission failures are test failures, never mocked passes. Owner must
run full `olares-run` with installed isolation/dependencies and verify the actual
paired-device/passkey UI; this isolated worker does not exercise real approvals,
external services, production data or credentials.
