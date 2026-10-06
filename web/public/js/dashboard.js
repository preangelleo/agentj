// P67: quota display comes from reported windows; missing measurements stay absent.
export function quotaWindows(usage = {}) {
  const windows = Array.isArray(usage.quota_windows) ? usage.quota_windows :
    [{window:'weekly',pct:usage.week_pct},{window:'5h',pct:usage.five_hour_pct}];
  return windows.filter(x => x && ['weekly','daily','monthly','5h'].includes(x.window) && Number.isFinite(x.pct))
    .slice(0,2).map(x => ({window:x.window,pct:Math.max(0,Math.min(100,x.pct))}));
}
