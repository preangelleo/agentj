// Keep the shared geometry audit intact and extend its explicit Web App candidates.
// The upstream selector is checked so an upstream contract change cannot silently drop coverage.
import {buttonAudit} from '../../brand/test/button-audit.mjs';
const upstream = buttonAudit.toString();
const oldSelector = "button,.aj-btn,[role=\"tab\"]";
if (!upstream.includes(oldSelector)) throw new Error('Shared button audit selector changed; review the Web App adapter');
export const buttonAuditSource = '(' + upstream.replace(oldSelector,
  'button,.aj-btn,.aj-action,.aj-icon-action,.aj-navigation-action,[role="tab"]') + ')()';
