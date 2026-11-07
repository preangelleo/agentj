import {readFileSync} from 'node:fs';
import{fileURLToPath}from'node:url';
import{nativeContractBinding,loadNativeControlContracts,auditNativeControlConsumers}from'./native-control-contract.mjs';
const nativeContracts=await loadNativeControlContracts(fileURLToPath(new URL('../',import.meta.url)).replace(/\/$/,''));
const nativeBinding=await nativeContractBinding(fileURLToPath(new URL('../',import.meta.url)).replace(/\/$/,''),nativeContracts);
const semanticContracts = JSON.parse(readFileSync(new URL('../component-ownership.json', import.meta.url), 'utf8')).semanticContracts;
const nativeActors=JSON.parse(readFileSync(new URL('../component-ownership.json',import.meta.url),'utf8')).nativeActorClasses||{};
const allowedOwners = JSON.parse(readFileSync(new URL('../control-ownership-sites.json', import.meta.url), 'utf8')).owners;
// Run in a real fixture page. Unknown ownership is an error, never an implicit standard action.
export function ownershipAudit(owners = [], contracts = [], nativeRules = [], nativeAudit = null, nativeBinding = null, actorClasses = {}) {
  const selector = 'iframe,h1,h2,h3,h4,h5,h6,a,small,hr,code,kbd,samp,pre,button,select,input,textarea,a[href],summary,[tabindex],[role="button"],[role="tab"],[role="toolbar"],audio[controls],video[controls],[contenteditable]:not([contenteditable="false"])';
  const forbidden = ['busy-dots','busy-one','busy-org-person','busy-org-root','omq','omqfold','qc','opt','qmore','lt-opt'];
  const missing = [], conflicts = [], matched = [], vendor = [], semanticFailures = [];
  const hasSkin = n => [...n.classList].some(c => /^aj-(?:action|icon-action|navigation-action|dialog-action)(?:[-_]|$)/.test(c));
  const technicalRoots='.top,.rmtop,.omtools,.tools,.rdacts,[data-no-select]';
  const targets = new Set([...document.querySelectorAll(selector),...document.querySelectorAll(technicalRoots),...document.querySelectorAll('.rmbar,.rscmdbar'), ...[...document.querySelectorAll('*')].filter(n => n.isContentEditable || hasSkin(n))]);
  for (const node of targets) {
    const record = {tag: node.localName, id: node.id || null, classes: [...node.classList], owner: node.getAttribute('data-aj-owner')};
    if (node.closest('.xterm,.katex')) { vendor.push({...record, reason: 'Pinned third-party DOM boundary'}); continue; }
    if (!record.owner || !owners.includes(record.owner)) missing.push(record);
    for(const c of record.classes)if(actorClasses[c]&&actorClasses[c].owner!==record.owner)conflicts.push({...record,reason:'Direct owner conflicts with independently registered actor class '+c});
    if([...node.classList].some(c=>/^aj-dialog-action(?:[-_]|$)/.test(c))&&(!node.matches('button.aj-dialog-action')||!node.parentElement?.classList.contains('gdlg-row')||record.owner!=='aj-dialog-action'))conflicts.push({...record,reason:'Dialog action requires an explicitly owned footer button; modifiers alone cannot authorize it'});
    if(record.owner==='aj-rescue'&&node.matches('.rmbar,.act'))conflicts.push({...record,reason:'Rescue command control borrows reply toolbar appearance'});
    if (hasSkin(node) && forbidden.some(c => node.classList.contains(c))) conflicts.push(record);
    if (hasSkin(node) && /^(?:aj-busy|aj-quote|aj-choice)$/.test(record.owner || '')) conflicts.push(record);
    if (hasSkin(node) && !node.matches('.aj-dialog-action,.aj-dialog-action--primary') && node.closest('.ajmodal') && node.getAttribute('data-aj-action-context') !== 'dialog') conflicts.push({...record, reason:'Missing explicit dialog action context'});
    matched.push(record);
  }
  for (const contract of contracts) {
    for (const selector of contract.selectors) for (const node of document.querySelectorAll(selector)) {
      const actual = node.getAttribute('data-aj-owner');
      if (actual !== contract.owner) semanticFailures.push({contract:contract.name,selector,id:node.id||null,classes:[...node.classList],expected:contract.owner,actual});
    }
  }
  for (const node of targets) if (node.getAttribute('data-aj-owner') === 'aj-shell' && !node.matches('html,body')) semanticFailures.push({id:node.id||null,reason:'Viewport shell cannot own a component control'});
  const nativeControlEvidence=nativeAudit?nativeAudit(nativeRules):null;
  if(nativeControlEvidence?.conflicts.length)conflicts.push(...nativeControlEvidence.conflicts);if(nativeControlEvidence?.errors.length)conflicts.push(...nativeControlEvidence.errors);
  return {nativeContractBinding:nativeBinding,nativeControlEvidence,ok: missing.length === 0 && conflicts.length === 0 && semanticFailures.length === 0, missing, conflicts, semanticFailures, matched, vendor};
}
export const ownershipAuditSource = '(' + ownershipAudit.toString() + ')(' + JSON.stringify(allowedOwners) + ',' + JSON.stringify(semanticContracts)+','+JSON.stringify(nativeContracts)+','+auditNativeControlConsumers.toString()+','+JSON.stringify(nativeBinding)+','+JSON.stringify(nativeActors) + ')';
