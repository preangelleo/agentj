// P98: one optional informational glance per followed native session. Storage
// contains only opaque host/session IDs, no history, credentials or notice text.
export class SharedNotice {
  constructor(storage = null) { this.storage = storage; this.last = new Map(); }
  update(host, follow, state, show) {
    if (!host || !follow || !['claude','codex','opencode'].includes(follow.agent) || !/^[a-f0-9]{32}$/.test(follow.id)) return;
    const key = 'agentj.shared-follow.' + host;
    const id = follow.agent + ':' + follow.id;
    let storage = this.storage;
    try { storage ||= globalThis.localStorage; } catch (_) {}
    let previous = this.last.get(key);
    try { previous ||= storage?.getItem(key); } catch (_) {}
    if (previous === id) return;
    this.last.set(key, id);
    try { storage?.setItem(key, id); } catch (_) {}
    // Occupancy requires action and stays visible. Do not toast again on recovery.
    if (state !== 'desktop_writer') show(follow.agent);
  }
}
