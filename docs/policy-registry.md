# Host action policy

Generated from `host/agentj/policy_registry.py`.

A registry decision is a minimum requirement, never an authorization or proof of owner identity.
Final executors retain signature, target, nonce and expiry checks and may require stronger confirmation.
Unknown action and tool IDs are denied. Opaque commands require fresh passkey verification; ordinary resolved commands retain native permissions.
External channels hand actions requiring confirmation back to the owner conversation; friends remain tool-less.
Application policy does not isolate code already running under the same OS account.

| Action | Effect | Confirmation | Main | Channel | Friend | Support |
|---|---|---|---|---|---|---|
| admin | irreversible | uv | uv | handoff | handoff | deny |
| answer | none | none | allow | allow | allow | allow |
| calendar.write | irreversible | uv | uv | handoff | handoff | deny |
| company.read | none | none | allow | deny | handoff | allow |
| company.write | irreversible | uv | uv | handoff | handoff | uv |
| device.pair | irreversible | uv | uv | handoff | handoff | deny |
| device.revoke | irreversible | uv | uv | handoff | handoff | deny |
| file.delete | irreversible | uv | uv | handoff | handoff | deny |
| file.read | none | none | allow | deny | handoff | deny |
| file.trash | reversible | hold | hold | handoff | handoff | deny |
| file.write | reversible | hold | allow | handoff | handoff | deny |
| image.edit | none | none | allow | allow | handoff | deny |
| image.generate | none | none | allow | allow | handoff | deny |
| mail.send | irreversible | uv | uv | handoff | handoff | deny |
| memory.restore | reversible | hold | hold | handoff | handoff | deny |
| payment | irreversible | uv | uv | handoff | handoff | deny |
| publish | irreversible | uv | uv | handoff | handoff | deny |
| rewrite | none | none | allow | allow | allow | allow |
| secrets.read | irreversible | uv | uv | handoff | handoff | deny |
| secrets.write | irreversible | uv | uv | handoff | handoff | deny |
| settings.write | reversible | hold | hold | handoff | handoff | deny |
| summarize | none | none | allow | allow | allow | allow |
| translate | none | none | allow | allow | allow | allow |
| web.search | none | none | allow | allow | handoff | deny |
