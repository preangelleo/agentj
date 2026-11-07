// Display-only refusal memory. It never updates a card, signature, revision, timer or authority decision.
import {t} from './t.js';
const held=new Map();
export const waitsForNewRequest=id=>held.has(id);
export function refusalText(why,status){
 if(why==='authority_changed')return t(status==='compacting'?'p0284.refusal.compacting':'p0284.refusal.context');
 if(why==='stale_envelope')return t('p0284.refusal.stale');
 return t('fr.ask.failed');
}
export function rememberRefusal(id,why,status){if(typeof id==='string'&&['authority_changed','stale_envelope'].includes(why))held.set(id,why);return refusalText(why,status);}
export const waitingText=(id,status)=>held.has(id)?refusalText(held.get(id),status):'';
export const forgetRefusal=id=>held.delete(id);
export const forgetRefusals=()=>held.clear();
