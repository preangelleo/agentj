// Display-only projection. The full signed action remains below the title.
import { lang } from './t.js';
export function stepTitle(e,fallback='') {
 const source=e?.source||{},quoted=[source.text,source.root?.text].filter(x=>typeof x==='string'&&x.trim()).map(x=>x.replace(/\s+/g,' ').trim());
 const action=String((lang()==='en'&&e?.action_en)||e?.action||'').replace(/\s+/g,' ').trim();
 const details=String(e?.details||''),cut=details.lastIndexOf('\n— ');
 let description=cut>=0?details.slice(cut+3).split('\nAgent ')[0].trim():'';
 try {const parsed=JSON.parse(details);if(typeof parsed?.description==='string')description=parsed.description;} catch {}
 description=description.replace(/\s+/g,' ').trim();
 const title=String((lang()==='en'&&e?.title_en)||e?.title||'').replace(/\s+/g,' ').trim();
 const quotedText=value=>quoted.some(q=>q===value||q.includes(value)||value.includes(q));
 const value=(description&&!quotedText(description)?description:title&&Array.from(title).length<=20&&!quotedText(title)?title:action&&!quotedText(action)?action:fallback).split(/[。！？\n]/u)[0];
 const chars=Array.from(value||fallback);return chars.length>20?chars.slice(0,19).join('')+'…':chars.join('');
}
