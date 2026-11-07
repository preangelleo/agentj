// Recovery facts only. A client outage cannot diagnose sleep, shutdown or a network failure.
export function createRecovery({now = () => Date.now()} = {}) {
  let started = null, day = '', count = 0;
  return {
    drop() { if (started === null) started = now(); },
    reset() { started = null; day = ''; count = 0; },
    up(evidence) {
      if (started === null) return null;
      const ms = Math.max(0, now() - started); started = null;
      if (ms < 5000) return null;
      const today = new Date(now()).toLocaleDateString('en-CA');
      if (day !== today) { day = today; count = 0; }
      count++;
      const stall = evidence && Number.isFinite(evidence.age_ms) && evidence.age_ms >= 0 && evidence.age_ms <= ms + 1000
        && Number.isFinite(evidence.ms) && evidence.ms >= 3000;
      return {seconds: Math.round(ms / 1000), stallSeconds: stall ? Math.round(evidence.ms / 1000) : null, frequent: count >= 3};
    }
  };
}
