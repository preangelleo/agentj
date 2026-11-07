// Narrow existing ceremony harness, plus missing-binding display and recovery regression.
process.env.P574_CEREMONY_ONLY = '1';
process.env.P0284_SAME_OWNER = '1';
process.env.P574_OUT ||= 'reports/qa/same-owner-revoked-20261107/web/';
await import('./p574.mjs');
