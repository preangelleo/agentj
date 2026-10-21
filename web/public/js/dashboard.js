// P67: quota display comes from reported windows; missing measurements stay absent.
export function quotaWindows(usage = {}) {
  const windows = Array.isArray(usage.quota_windows) ? usage.quota_windows :
    [{window:'weekly',pct:usage.week_pct,at:usage.week_at,stale:usage.week_stale},{window:'5h',pct:usage.five_hour_pct,at:usage.h5_at,stale:usage.h5_stale}];
  return windows.filter(x => x && ['weekly','daily','monthly','5h'].includes(x.window) && Number.isFinite(x.pct))
    .slice(0,2).map(x => ({window:x.window,pct:Math.max(0,Math.min(100,x.pct)),
      // P433: when the reading was measured (unix s) and whether a confirmed limit made it stale
      ...(Number.isFinite(x.at) && x.at > 0 ? {at:x.at} : {}), ...(x.stale === true ? {stale:true} : {}),
      // P340 D: a provider window may carry its own reset (unix s) — Kimi / Ark / Bailian Coding Plan usage
      ...(Number.isFinite(x.reset) && x.reset > 0 ? {reset:x.reset} : {})}));
}
