// P303 · where a quota window stands in its own reset cycle, and how loud to show it. Pure functions (test/p303.test.mjs).
// The owner (2026-10-15): WEEKLY gets 「（第 N 天）」 of the 7-day credit cycle; the value glows faintly only when it is
// used faster than the even pace (about 14 % a day), Mellow Marrow at ≥ 80 %, Peachy Feeling at ≥ 90 % (the amount wins
// over the pace); 5H gets the 80 / 90 % glow too. The reset time comes from the harness (Claude Code `rate_limits.*.resets_at`,
// Codex `rateLimits.*.resetsAt` → meter `week.reset` / `h5.reset`, unix seconds); no reset time = no day, no pace, the
// amount tiers still apply.
export const DAY_S = 86400;
export const PERIOD_S = { weekly: 7 * DAY_S, '5h': 5 * 3600 };
export const STEP_S = { weekly: DAY_S, '5h': 3600 };          // the unit the owner counts in: days of a week, hours of 5 h
export const TIGHT_PCT = 80, EMPTY_PCT = 90;

/** {period, elapsed, left, step, steps, frac} for a window resetting at `reset` (unix s), or null when that is unknown or
 *  does not fit the window (already passed, or further away than one period + 10 min of clock skew). */
export function cycle(window, reset, nowMs = Date.now()) {
  const period = PERIOD_S[window];
  if (!period || typeof reset !== 'number' || !Number.isFinite(reset) || reset <= 0) return null;
  const now = nowMs / 1000, left = reset - now;
  if (left <= 0 || left > period + 600) return null;
  const elapsed = Math.max(0, period - left), unit = STEP_S[window], steps = Math.round(period / unit);
  return { period, elapsed, left: Math.min(left, period), step: Math.min(steps, Math.floor(elapsed / unit) + 1), steps,
           frac: Math.min(1, elapsed / period) };
}

/** The share an even pace allows by the end of the current step (day N of 7 → N/7, hour N of 5 → N/5), in %. Comparing
 *  against the shown 「第 N 天」 keeps the rule the owner can check by eye and does not flash at the very start of a cycle. */
export const allowedPct = (c) => (c ? (c.step / c.steps) * 100 : null);

/** Faster than the even pace? null when the cycle is unknown. */
export function fast(pct, c) {
  const a = allowedPct(c);
  return a == null || !Number.isFinite(pct) ? null : pct > a + 1e-9;
}

/** '' (no glow) | 'fast' | 'tight' (≥ 80 %) | 'empty' (≥ 90 %); the amount wins over the pace. */
export function tier(pct, c, { pace = true } = {}) {
  if (!Number.isFinite(pct)) return '';
  if (pct >= EMPTY_PCT) return 'empty';
  if (pct >= TIGHT_PCT) return 'tight';
  return pace && fast(pct, c) ? 'fast' : '';
}

/** At this pace: {atReset: % used when it resets} or {emptyIn: seconds until 100 % (before the reset)}; null when the
 *  cycle is unknown, nothing is used yet, or under an hour of it has passed (too little to extrapolate from). */
export function projection(pct, c) {
  if (!c || !Number.isFinite(pct) || pct <= 0 || c.elapsed < 3600) return null;
  const rate = pct / c.elapsed;                              // % per second
  const atReset = pct + rate * c.left;
  if (atReset < 100 || pct >= 100) return { atReset: Math.min(100, Math.round(atReset)) };
  return { emptyIn: Math.max(0, (100 - pct) / rate) };
}

/** {d, h, m} of a span in seconds (rounded down to the minute). */
export function span(s) {
  const t = Math.max(0, Math.floor(s / 60));
  return { d: Math.floor(t / 1440), h: Math.floor((t % 1440) / 60), m: t % 60 };
}
