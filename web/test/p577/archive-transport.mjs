// Passed by value to the isolated test page; the sampler itself is installed from the pinned imported function source.
export function auditArchiveJson(text) {
  return JSON.stringify(globalThis.__p577AuditRuntime(JSON.parse(text)));
}
