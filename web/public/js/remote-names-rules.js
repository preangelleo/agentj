// Display names only; the host independently validates the same preference.
export function validRemoteNames(rows) {
  return Array.isArray(rows) && rows.length <= 32 && rows.every(r=>
    r && typeof r === 'object' && typeof r.host === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$/.test(r.host) && typeof r.name === 'string' &&
    r.name.trim() === r.name && r.name.length > 0 && [...r.name].length <= 40 && !/[\u0000-\u001f\u007f]/.test(r.name)) && new Set(rows.map(r=>r.host)).size === rows.length;
}
