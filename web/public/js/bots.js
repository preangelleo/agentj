// Owner-only /bots. Every read/write is inside the paired Noise channel.
// This file is never loaded in the public chat or verification frames.
import { ask1, isReady } from './session.js';
import { signedWrite } from './controls.js';
import { lang } from './t.js';
import { el, mk, toast, confirmSheet } from './ui.js';
let hooks, selected=null, tab='settings', generation=0, pendingProposal=null,hostZone='UTC';
const copy=(zh,en)=>lang()==='en'?en:zh;
const text=(v,max=6000)=>typeof v==='string'?v.slice(0,max):'';
export const TABS=['settings','limits','knowledge','tools','conversations','statistics','embed'];
export function configure(h){hooks=h;}
export function open(){hooks.openPanel('bots');}
const button=(label,fn)=>{const b=mk('button','aj-btn',label);b.type='button';b.addEventListener('click',fn);return b;};
function status(message){el('bots-status').textContent=message;}
async function read(request){if(!isReady())throw new Error(copy('电脑未连接','Computer is disconnected'));const m=await ask1({t:'bots_read',request},'bots_res');if(!m?.ok)throw new Error(m?.why||'offline');return m;}
async function write(request){
  const m=await signedWrite('bots_write',{request},{t:'bots_write',request});
  if(!m.ok)throw new Error(m.why||'offline');return m;
}
async function act(fn){try{status(copy('处理中…','Working…'));await fn();status('');}catch(e){status(text(e.message,160));}}
function field(parent,label,value='',type='text'){
  const wrap=mk('label','bots-field'),title=mk('span','',label),input=mk(type==='textarea'?'textarea':'input');
  if(type!=='textarea')input.type=type;input.value=value;input.setAttribute('aria-label',label);wrap.append(title,input);parent.append(wrap);return input;
}
function checkbox(parent,label,value){const w=mk('label','bots-field'),input=mk('input');input.type='checkbox';input.checked=value===true;w.append(input,document.createTextNode(label));parent.append(w);return input;}
function privacy(parent){parent.append(mk('p','small',copy('Bot 使用你的模型账户，从你的电脑直连服务商。若服务商是 AgentsRelay，请求经其中转；其政策是不记录正文。Bot 共享主 Agent 当前的套餐或登录。个人订阅对外服务可能受服务商条款限制，由你确认。Jev 安全审核使用你自己的 OpenRouter 账户，费用由你承担。聊天记录仅在你的电脑保留 30 天，举报只发元数据。','Bots use your model account, directly from your computer. AgentsRelay requests pass through its relay; its policy is not to record bodies. Bots share your main Agent’s current plan or login. Public service with a personal subscription may be limited by provider terms; you must confirm them. Jev safety reviews use your own OpenRouter account at your expense. Chats stay on your computer for 30 days; reports contain metadata only.')));}
export async function refresh(){
  if(pendingProposal && pendingProposal.expires>Date.now()){paintProposal(pendingProposal);return;}
  pendingProposal=null;
  const g=++generation;await act(async()=>{
    const list=await read({op:'list'});hostZone=list.host_timezone||'UTC';if(g!==generation)return;
    const side=el('bots-side');side.replaceChildren(button(copy('← 主对话','← Main chat'),()=>hooks.openPanel('chat')),button(copy('新建客服 bot','Create customer service bot'),wizard));
    for(const bot of (list.bots||[]).slice(0,100))side.append(button(text(bot.title,80)+' · '+(bot.enabled?copy('已启用','Enabled'):copy('已暂停','Paused')),()=>{selected=bot.id;tab='settings';refresh();}));
    if(selected&&!list.bots?.some(b=>b.id===selected))selected=null;
    const main=el('bots-main');main.replaceChildren();
    if(!selected){main.append(mk('h2','',copy('主机上的常驻客服','Customer service on your computer')),mk('p','',copy('电脑在线时接待访客；首期每席位一个启用 bot。','Visitors are served while your computer is online. One enabled bot per seat.')));privacy(main);return;}
    const detail=await read({op:'detail',id:selected});if(g!==generation)return;draw(detail,main);
  });
}
function tabs(parent){const nav=mk('nav','bots-tabs aj-actions');nav.setAttribute('aria-label',copy('Bot 设置','Bot settings'));
  const labels={settings:['设置','Settings'],limits:['限额','Limits'],knowledge:['知识','Knowledge'],tools:['工具','Tools'],conversations:['会话','Conversations'],statistics:['统计','Statistics'],embed:['嵌入','Embed']};
  for(const name of TABS){const b=button(copy(...labels[name]),()=>{tab=name;refresh();});b.setAttribute('aria-current',name===tab?'page':'false');nav.append(b);}parent.append(nav);
}
function wizard(){selected=null;const main=el('bots-main');main.replaceChildren();let step=1;const config={slug:'',title:'',enabled:false,terms_accepted:false,subscription_risk_accepted:false,timezone:hostZone};
  const render=()=>{main.replaceChildren(mk('h2','',copy(`新建客服 · ${step}/3`,`Create customer service · ${step}/3`)));
    if(step===1){main.append(mk('p','',copy('客服模板：依据产品资料回答，不确定时转人工。','Customer service template: answer from product knowledge; ask a human when unsure.')),button(copy('继续','Continue'),()=>{step=2;render();}));}
    if(step===2){const title=field(main,copy('显示名称','Display name'),config.title),slug=field(main,copy('公开名字（小写字母、数字、连字符，3–32 位）','Public name (lowercase letters, numbers, hyphens; 3–32)'),config.slug);main.append(button(copy('继续','Continue'),()=>{config.title=title.value;config.slug=slug.value;step=3;render();}));}
    if(step===3){main.append(mk('p','',copy('默认 20 条/访客/天、100 条/bot/天，主机合计 100 条和 100k 模型 tokens/天，含安全审核。可以调低。','Defaults: 20 messages/visitor/day, 100/bot/day; 100 messages and 100k model tokens/day across your computer, including safety reviews. You can lower them.')));privacy(main);const profile=field(main,copy('API key 服务商档案（留空复用主 Agent）','API key provider profile (blank: use main Agent)'),'');const risk=checkbox(main,copy('我已确认服务商条款，接受个人订阅对外服务可能受限的风险','I checked provider terms and accept possible restrictions on public use of a personal subscription'),false);main.append(mk('p','',copy('创建后必须注册 OpenRouter 并通过手机密钥卡添加审核 key，验证成功才能上线。','After creation, register with OpenRouter and add a review key through the phone secret card. Activation requires a successful check.')));const terms=checkbox(main,copy('我接受对外服务与内容责任条款','I accept public service and content responsibility terms'),false);main.append(button(copy('创建并保持暂停','Create paused'),()=>act(async()=>{config.terms_accepted=terms.checked;config.subscription_risk_accepted=risk.checked;config.provider=profile.value?{source:'profile',id:profile.value}:{source:'main'};const m=await write({op:'create',config});selected=m.bot.id;await refresh();})));}
  };render();
}
function draw(detail,main){const c=detail.bot;main.append(mk('h2','',text(c.title,80)));tabs(main);
  if(tab==='settings'){
    const title=field(main,copy('名称','Name'),c.title),desc=field(main,copy('公开简介','Public description'),c.description),prompt=field(main,copy('业务范围和回答要求','Business scope and instructions'),c.prompt,'textarea'),background=field(main,copy('始终提供的背景（例如价格表）','Always included background (e.g. price list)'),c.background,'textarea');
    const zone=field(main,copy('服务时区','Service timezone'),c.timezone),language=field(main,copy('语言（auto / zh / en）','Language (auto / zh / en)'),c.language),profile=field(main,copy('自带 key 服务商档案名称（留空复用主 Agent）','API key provider profile (blank: use main Agent)'),c.provider.source==='profile'?c.provider.id:'');
    const enabled=checkbox(main,copy('接待访客','Serve visitors'),c.enabled),terms=checkbox(main,copy('接受对外服务条款','Accept public service terms'),c.terms_accepted),risk=checkbox(main,copy('已确认个人订阅对外服务的条款和风险','I checked terms and risks of public subscription use'),c.subscription_risk_accepted);privacy(main);
    const register=mk('a','',copy('注册 OpenRouter（审核费用自付）','Register OpenRouter (you pay review costs)'));register.href='https://openrouter.ai/';register.target='_blank';register.rel='noopener noreferrer';main.append(register,mk('p','',copy('密钥仅用手机密钥卡写入电脑。每次启用均真实验证 decisions，失败则保持暂停。','The phone secret card writes the key only to your computer. Each activation verifies decisions; failure keeps the bot paused.')),button(copy('添加 / 更新 OpenRouter 审核 key','Add / update OpenRouter review key'),()=>act(async()=>{await write({op:'audit_key',id:c.id});await refresh();})));
    if(detail.provider_problem)main.append(mk('p','',copy('请检查主 Agent 的登录，或用手机密钥卡添加自带 key。','Check your main Agent login or add your own key using the phone secret card.')));
    const allDay=checkbox(main,copy('全天接待','Serve all day'),c.hours===null),start=field(main,copy('开始时间（0–23 点）','Start hour (0–23)'),String(c.hours?.start??9),'number'),end=field(main,copy('结束时间（0–23 点）','End hour (0–23)'),String(c.hours?.end??18),'number');
    const days=[];for(const [i,labels] of [['周一','Monday'],['周二','Tuesday'],['周三','Wednesday'],['周四','Thursday'],['周五','Friday'],['周六','Saturday'],['周日','Sunday']].entries())days.push(checkbox(main,copy(...labels),c.hours?.days.includes(i)??true));
    const slug=field(main,copy('公开名字（改名后旧名冻结 30 天）','Public name (old name frozen for 30 days)'),c.slug);
    main.append(button(copy('删除 bot（名字冻结 30 天）','Delete bot (name frozen for 30 days)'),()=>act(async()=>{if(await confirmSheet(copy('删除 bot','Delete bot'),c.slug,copy('删除','Delete'))){await write({op:'delete',id:c.id});selected=null;await refresh();}})));
    main.append(button(copy('保存','Save'),()=>act(async()=>{await write({op:'save',id:c.id,config:{...withoutId(c),slug:slug.value,title:title.value,description:desc.value,prompt:prompt.value,background:background.value,timezone:zone.value,language:language.value,hours:allDay.checked?null:{start:Number(start.value),end:Number(end.value),days:days.flatMap((e,i)=>e.checked?[i]:[])},provider:profile.value?{source:'profile',id:profile.value}:{source:'main'},enabled:enabled.checked,terms_accepted:terms.checked,subscription_risk_accepted:risk.checked}});await refresh();})));
  }else if(tab==='limits'){
    const labels={visitor_messages:['每访客每日消息','Daily visitor messages'],bot_messages:['Bot 每日消息','Daily bot messages'],host_messages:['主机每日总消息','Daily host messages'],host_tokens:['主机每日模型 tokens（含审核）','Daily host model tokens (including reviews)'],concurrency:['同时接待人数','Concurrent visitors']},fields={};
    for(const [k,v] of Object.entries(c.limits))fields[k]=field(main,copy(...labels[k]),String(v),'number');
    main.append(button(copy('保存限额','Save limits'),()=>act(async()=>{const limits=Object.fromEntries(Object.entries(fields).map(([k,e])=>[k,Number(e.value)]));await write({op:'save',id:c.id,config:{...withoutId(c),limits}});await refresh();})));
  }else if(tab==='knowledge'){
    main.append(mk('p','',copy('由你的 Agent J 同步本机目录或 URL。资料用于检索；固定价格表可放在设置中的背景段。','Ask your Agent J to sync a local folder or URL. Documents are retrieved as needed; put a fixed price list in the settings background.')));
    const upload=field(main,copy('添加资料（md / txt / pdf / csv）','Add knowledge (md / txt / pdf / csv)'),'','file');upload.accept='.md,.txt,.pdf,.csv';
    upload.addEventListener('change',()=>act(async()=>{const file=upload.files?.[0];if(!file)return;if(file.size>2*1024*1024)throw new Error('2 MiB maximum');const bytes=new Uint8Array(await file.arrayBuffer());let raw='';for(const byte of bytes)raw+=String.fromCharCode(byte);await write({op:'knowledge_add',id:c.id,name:file.name,data:btoa(raw)});await refresh();}));
    for(const file of detail.knowledge||[]){const row=mk('div','bots-row',text(file.name,100));row.append(button(copy('删除','Delete'),()=>act(async()=>{if(await confirmSheet(copy('删除资料','Delete knowledge'),file.name,copy('删除','Delete'))){await write({op:'knowledge_remove',id:c.id,name:file.name});await refresh();}})));main.append(row);}
  }else if(tab==='tools'){
    main.append(mk('p','',copy('请让 Agent J 配置公司 API；查询工具可以自动调用。写入工具默认关闭，每次都需你在手机批准。','Ask Agent J to configure company APIs. Read tools run automatically. Write tools default to off and always require your phone approval.')));
    act(async()=>{const usage=await read({op:'statistics',id:c.id});if(tab!=='tools'||selected!==c.id)return;for(const tool of usage.tools||[])main.append(mk('p','',`${tool.day}: ${tool.tool} · ${tool.calls} ${copy('调用','calls')}`));});
    for(const tool of detail.tools||[]){const row=mk('div','bots-row');row.append(mk('h3','',text(tool.name,40)+(tool.level==='write'?copy(' · 写入：每次需批准',' · WRITE: approval every time'):copy(' · 查询',' · Read'))),mk('p','',text(tool.description,1000)));const definition=mk('pre','',JSON.stringify(tool,null,2));row.append(definition,button(tool.enabled?copy('停用','Disable'):copy('启用','Enable'),()=>act(async()=>{await write({op:'tool_save',id:c.id,tool:{...tool,enabled:!tool.enabled}});await refresh();})));main.append(row);}
  }else if(tab==='conversations')act(async()=>{const m=await read({op:'history',id:c.id});for(const session of m.sessions||[])main.append(button(session.visitor.slice(0,8)+' · '+session.messages,()=>act(async()=>{const m=await read({op:'history',id:c.id,visitor:session.visitor});const history=mk('div','');for(const msg of m.messages||[])history.append(mk('p','',msg.role+': '+text(msg.text,12000)));main.append(history);})));});
  else if(tab==='statistics')act(async()=>{const m=await read({op:'statistics',id:c.id});for(const day of m.days||[])main.append(mk('p','',`${day.day}: ${day.calls} ${copy('调用','calls')} · ${day.charged_tokens} ${copy('已计费/预留 tokens','charged/reserved tokens')}`));for(const tool of m.tools||[])main.append(mk('p','',`${tool.day}: ${tool.tool} · ${tool.calls}`));});
  else if(tab==='embed'){
    const url='https://agentj.app/bots/'+c.slug;main.append(mk('p','',copy('免费公开网址','Free public URL')+': '+url));
    const code=`<iframe src="${url}" title="AI customer service" width="400" height="600" loading="lazy" referrerpolicy="no-referrer"></iframe>`;
    main.append(mk('pre','',code),button(copy('复制嵌入代码','Copy embed code'),()=>act(()=>navigator.clipboard.writeText(code))));
    const domains=field(main,copy('工具出站域名（逗号分隔）','Tool outbound domains (comma separated)'),c.outbound_domains.join(',')),origins=field(main,copy('允许嵌入的网站（https origin，逗号分隔）','Allowed embedding websites (HTTPS origins, comma separated)'),c.embed_origins.join(','));main.append(button(copy('保存','Save'),()=>act(async()=>{await write({op:'save',id:c.id,config:{...withoutId(c),outbound_domains:domains.value.split(',').map(x=>x.trim()).filter(Boolean),embed_origins:origins.value.split(',').map(x=>x.trim()).filter(Boolean)}});await refresh();})));
  }
}
function withoutId(c){const {id,...value}=c;return value;}
function paintProposal(m){
  if(!m||typeof m.id!=='string'||!m.request||typeof m.request!=='object'||m.expires<=Date.now())return;
  const main=el('bots-main');main.replaceChildren(mk('h2','',copy('Agent J 请求修改 bot','Agent J proposes a bot change')),mk('pre','',JSON.stringify(m.request,null,2).slice(0,60000)),button(copy('批准这次修改','Approve this change'),()=>act(async()=>{if(m.expires<=Date.now())throw new Error('expired');await write(m.request);pendingProposal=null;await refresh();})),button(copy('返回','Back'),()=>{pendingProposal=null;refresh();}));
}

export function proposal(m){if(!m?.request || typeof m.expires!=='number' || m.expires<=Date.now())return;pendingProposal=m;open();}

export function toolRequest(m){
 if(!m?.digest || typeof m.expires!=='number' || m.expires*1000<=Date.now())return;
 const req={op:'tool_approve',approval:m.id,digest:m.digest,allow:true};
 const main=el('bots-main');hooks.openPanel('bots');++generation;
 main.replaceChildren(mk('h2','',copy('批准 bot 写入操作','Approve a bot write operation')),mk('p','',text(m.tool,40)),mk('pre','',text(m.args,4000)),button(copy('批准这一次','Approve once'),()=>act(async()=>{await write(req);await refresh();})),button(copy('拒绝','Deny'),()=>act(async()=>{await write({...req,allow:false});await refresh();})));
}
