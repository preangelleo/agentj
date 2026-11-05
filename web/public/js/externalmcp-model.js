// Strict metadata and request allowlists for the one-way MCP settings page.
const ID = /^[A-Za-z0-9_-]{22}$/;
export function exactOrigin(s) {
  try { const u = new URL(s); return u.protocol === 'https:' && !u.hostname.includes('*') && !u.username && !u.password && u.origin === s; } catch { return false; }
}
export function createInput(name, text) {
  const origins = text.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  name = name.trim();
  return name && name.length <= 40 && origins.length > 0 && origins.length <= 16 && origins.every(exactOrigin)
    ? { name, origins: [...new Set(origins)] } : null;
}
export function parseConnectors(value) {
  return (Array.isArray(value) ? value : []).slice(0, 40).filter(c => c && ID.test(c.id) &&
    ['pending','active','revoked'].includes(c.state) && c.scope === 'request-only').map(c => ({
    id: c.id, name: typeof c.name === 'string' ? c.name.replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0,40) : c.id,
    origins: (Array.isArray(c.origins) ? c.origins : []).filter(exactOrigin).slice(0,16), state: c.state, scope: 'request-only',
    created: Number.isFinite(c.created) ? c.created : null,
    last: Number.isFinite(c.last?.at) ? c.last.at : Number.isFinite(c.last) ? c.last : null,
    url: typeof c.url === 'string' && /^https:\/\/hook\.agentj\.app\/[A-Za-z0-9_-]{22}\/[A-Za-z0-9_-]{22}$/.test(c.url) && c.url.endsWith('/'+c.id) ? c.url : ''
  }));
}
