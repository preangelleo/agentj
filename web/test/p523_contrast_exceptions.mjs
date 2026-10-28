// Chairman-approved exceptions, 2026-10-25 via Jarvis P523 RESUME:
// 「0.25.10：设置页重排批准，两处配色保持现状登记例外」.
// Exact DOM locations AND composited colour pairs; all other contrast rules stay active.
// P564 (董事长 2026-10-28 10:4x 「看清看不清我说了算，不是机器算出来的数值，我说看得清就看得清」): the chat header gear's
// remote-count digit is the page's state colour on the Lotion circle in every state. Exact selector, Lotion ground, and the
// digit must equal that page's own resolved --state (stateFg, read by the audit) — nothing else on the page is exempt.
export const APPROVED_CONTRAST = [
 {id:'P564',selector:'#setBtn > span.remote-count-badge[data-remote-count]',fg:'state',bg:'rgb(250,249,245)'},
 {id:'P517',selector:'#setBtn > span.remote-count-badge[data-remote-count], #set-remotes-open > span > b.remote-count-label > span.remote-count-badge[data-remote-count]',fg:'rgb(98,152,135)',bg:'rgb(250,249,245)',ratio:3.13},
 {id:'P515',selector:'#busyDlg li > div.busy-identity > small',fg:'rgb(136,136,136)',bg:'rgb(44,43,38)',ratio:4},
];
export function approvedContrast(t) {
 return APPROVED_CONTRAST.find(x=>t.approvedLocation===x.id&&t.bg===x.bg&&t.bad.length===1&&(x.fg==='state'
  ?typeof t.stateFg==='string'&&t.fg===t.stateFg&&t.bad[0]===`control label ${t.ratio.toFixed(2)}:1`
  :t.fg===x.fg&&t.ratio===x.ratio&&t.bad[0]===`control label ${x.ratio.toFixed(2)}:1`))?.id||null;
}
