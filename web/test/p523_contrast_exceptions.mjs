// Chairman-approved exceptions, 2026-10-25 via Jarvis P523 RESUME:
// 「0.25.10：设置页重排批准，两处配色保持现状登记例外」.
// Exact DOM locations AND composited colour pairs; all other contrast rules stay active.
export const APPROVED_CONTRAST = [
 {id:'P517',selector:'#setBtn > span.remote-count-badge[data-remote-count], #set-remotes-open > span > b.remote-count-label > span.remote-count-badge[data-remote-count]',fg:'rgb(98,152,135)',bg:'rgb(250,249,245)',ratio:3.13},
 {id:'P515',selector:'#busyDlg li > div.busy-identity > small',fg:'rgb(136,136,136)',bg:'rgb(44,43,38)',ratio:4},
];
export function approvedContrast(t) {
 return APPROVED_CONTRAST.find(x=>t.approvedLocation===x.id&&t.fg===x.fg&&t.bg===x.bg&&t.ratio===x.ratio&&t.bad.length===1&&t.bad[0]===`control label ${x.ratio.toFixed(2)}:1`)?.id||null;
}
