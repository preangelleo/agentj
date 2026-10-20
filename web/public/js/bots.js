// Owner-only /bots. Every read/write is inside the paired Noise channel.
// This file is never loaded in the public chat or verification frames.
import { ask1, isReady } from './session.js';
import { signedWrite } from './controls.js';
import { lang } from './t.js';
import { el, mk, toast, confirmSheet } from './ui.js';
import { copyable, copyNow } from './copycode.js';   // P292: every pre can be copied (⧉)
let hooks, selected=null, tab='settings', generation=0, pendingProposal=null,hostZone='UTC',vendors=[];
const copy=(zh,en)=>lang()==='en'?en:zh;
const text=(v,max=6000)=>typeof v==='string'?v.slice(0,max):'';
export const TABS=['settings','limits','knowledge','tools','conversations','statistics','embed'];
export function configure(h){hooks=h;}
export function open(){hooks.openPanel('bots');}
const button=(label,fn)=>{const b=mk('button','aj-btn',label);b.type='button';b.addEventListener('click',fn);return b;};
function status(message){el('bots-status').textContent=message;}
async function read(request){if(!isReady())throw new Error(copy('Agent J 主机未连接','Computer is disconnected'));const m=await ask1({t:'bots_read',request},'bots_res');if(!m?.ok)throw new Error(m?.why||'offline');return m;}
async function write(request){
  const m=await signedWrite('bots_write',{request},{t:'bots_write',request});
  if(!m.ok)throw new Error(m.why||'offline');return m;
}
// P348: a support bot serves other people, so no Coding Plan / Token Plan key — the owner adds a pay-as-you-go one
const planProblem=['客服 bot 是在服务别人，不能用 Coding Plan / Token Plan 的 key。请在模型来源里选「主人自带模型 key」，再选一家服务商（可以和主 Agent 不同家），保存后用手机密钥卡填那家的按量付费 API key（国内按量 key 不贵）。','A support bot serves other people, so it cannot use a Coding Plan / Token Plan key. Choose “Owner’s model key” as the model source, pick a provider (it may differ from your main Agent’s), save, then add that provider’s pay-as-you-go API key with the phone secret card.'];
async function act(fn){try{status(copy('处理中…','Working…'));await fn();status('');}catch(e){status(e.message==='coding_plan_bot'?copy(...planProblem):text(e.message,160));}}
function field(parent,label,value='',type='text'){
  const wrap=mk('label','bots-field'),title=mk('span','',label),input=mk(type==='textarea'?'textarea':'input');
  if(type!=='textarea')input.type=type;input.value=value;input.setAttribute('aria-label',label);wrap.append(title,input);parent.append(wrap);return input;
}
function checkbox(parent,label,value){const w=mk('label','bots-field'),input=mk('input');input.type='checkbox';input.checked=value===true;w.append(input,document.createTextNode(label));parent.append(w);return input;}
function privacy(parent){parent.append(mk('p','small',copy('Bot 使用你的模型账户，从你的Agent J 主机直连服务商。若服务商是 AgentsRelay，请求经其中转；其政策是不记录正文。Bot 可共享主 Agent 当前的套餐或登录，也可用自带 key 独立计费与限额。个人订阅对外服务可能受服务商条款限制，由你确认。Jev 安全审核可选你的 TypeSafe 官方账户或 OpenRouter 账户，费用由你承担。聊天记录仅在你的Agent J 主机保留，默认 30 天，可调整，举报只发元数据。','Bots use your model account, directly from your computer. AgentsRelay requests pass through its relay; its policy is not to record bodies. Bots can share your main Agent’s current plan or login, or use an own key with separate billing and limits. Public service with a personal subscription may be limited by provider terms; you must confirm them. Jev safety reviews use your own TypeSafe or OpenRouter account at your expense. Chats stay on your computer; retention defaults to 30 days and is configurable; reports contain metadata only.')));}
export async function refresh(){
  if(pendingProposal && pendingProposal.expires>Date.now()){paintProposal(pendingProposal);return;}
  pendingProposal=null;
  const g=++generation;await act(async()=>{
    const list=await read({op:'list'});hostZone=list.host_timezone||'UTC';vendors=Array.isArray(list.vendors)?list.vendors.slice(0,30):[];if(g!==generation)return;
    const side=el('bots-side');side.replaceChildren(button(copy('← 主对话','← Main chat'),()=>hooks.openPanel('chat')),button(copy('新建 bot','Create bot'),wizard));
    for(const bot of (list.bots||[]).slice(0,100))side.append(button(text(bot.title,80)+' · '+(bot.enabled?copy('已启用','Enabled'):copy('已暂停','Paused')),()=>{selected=bot.id;tab='settings';refresh();}));
    if(selected&&!list.bots?.some(b=>b.id===selected))selected=null;
    const main=el('bots-main');main.replaceChildren();
    if(!selected){main.append(mk('h2','',copy('主机上的常驻客服','Customer service on your computer')),mk('p','',copy('Agent J 主机在线时接待访客；首期每席位一个启用 bot。','Visitors are served while your computer is online. One enabled bot per seat.')));privacy(main);return;}
    const detail=await read({op:'detail',id:selected});if(g!==generation)return;draw(detail,main);
  });
}
function tabs(parent){const nav=mk('nav','bots-tabs aj-actions');nav.setAttribute('aria-label',copy('Bot 设置','Bot settings'));
  const labels={settings:['设置','Settings'],limits:['限额','Limits'],knowledge:['知识','Knowledge'],tools:['工具','Tools'],conversations:['会话','Conversations'],statistics:['统计','Statistics'],embed:['嵌入','Embed']};
  for(const name of TABS){const b=button(copy(...labels[name]),()=>{tab=name;refresh();});b.setAttribute('aria-current',name===tab?'page':'false');nav.append(b);}parent.append(nav);
}
function wizard(){selected=null;const main=el('bots-main');main.replaceChildren();let step=1;const config={slug:'',title:'',enabled:false,terms_accepted:false,subscription_risk_accepted:false,timezone:hostZone,template:'customer_service'};
  const render=()=>{main.replaceChildren(mk('h2','',copy(`新建客服 · ${step}/3`,`Create customer service · ${step}/3`)));
    if(step===1){
      for(const [id,zh,en] of [['customer_service','客服：依据资料回答，不确定时转人工','Customer service: answer from knowledge; ask a human when unsure'],['companion','陪聊：温暖交流，明确 AI 身份与安全边界','Companion: warm conversation with clear AI identity and boundaries'],['paid_qa','付费问答：每日免费 N 问后展示你的付费链接','Paid Q&A: show your payment link after N free daily questions']])main.append(button(copy(zh,en),()=>{config.template=id;step=2;render();}));
    }
    if(step===2){const title=field(main,copy('显示名称','Display name'),config.title),slug=field(main,copy('公开名字（小写字母、数字、连字符，3–32 位）','Public name (lowercase letters, numbers, hyphens; 3–32)'),config.slug);main.append(button(copy('继续','Continue'),()=>{config.title=title.value;config.slug=slug.value;step=3;render();}));}
    if(step===3){main.append(mk('p','',copy('默认 20 条/访客/天、100 条/bot/天，主机合计 100 条和 100k 模型 tokens/天，含安全审核。可以调高或调低。','Defaults: 20 messages/visitor/day, 100/bot/day; 100 messages and 100k model tokens/day across your computer, including safety reviews. You can change them.')));privacy(main);const modelConfig=ownFields(main,{provider:{source:'main'},own_model:null});const risk=checkbox(main,copy('我已确认服务商条款，接受个人订阅对外服务可能受限的风险','I checked provider terms and accept possible restrictions on public use of a personal subscription'),false);main.append(mk('p','',copy('创建后选择 TypeSafe 或 OpenRouter，通过手机密钥卡添加审核 key，验证成功才能上线。','After creation, choose TypeSafe or OpenRouter and add a review key through the phone secret card. Activation requires a successful check.')));const terms=checkbox(main,copy('我接受对外服务与内容责任条款','I accept public service and content responsibility terms'),false);main.append(button(copy('创建并保持暂停','Create paused'),()=>act(async()=>{config.terms_accepted=terms.checked;config.subscription_risk_accepted=risk.checked;Object.assign(config,modelConfig());const m=await write({op:'create',config});selected=m.bot.id;await refresh();})));}
  };render();
}
// P348: 「主人自带模型 key」 starts with the provider (it may differ from the main Agent's): each preset brings its endpoint,
// a cheap default model and its own key name; 「其他」 = the owner's own OpenAI-compatible address (key BOT_MODEL_KEY)
const vendorName=id=>{const v=vendors.find(x=>x.id===id);return v?copy(v.zh,v.en):copy('其他服务（自填地址）','Other service (own address)');};
function ownFields(main,c){
  const choice=mk('select','aj-select');choice.setAttribute('aria-label',copy('模型计费通道','Model billing channel'));
  for(const [v,zh,en] of [['main','用主 Agent 套餐','Use main Agent plan'],['own','主人自带模型 key','Owner’s model key'],['profile','已有服务商档案','Existing provider profile']]){const o=mk('option','',copy(zh,en));o.value=v;choice.append(o);}choice.value=c.provider.source;main.append(choice);
  const vendorWrap=mk('label','bots-field'),vendor=mk('select','aj-select');vendor.setAttribute('aria-label',copy('客服 bot 用哪家的按量 key','Which provider’s pay-as-you-go key'));
  vendorWrap.append(mk('span','',copy('客服 bot 用哪家的按量 key（可以和主 Agent 不同家）','Which provider’s pay-as-you-go key (may differ from your main Agent’s)')),vendor);main.append(vendorWrap);
  for(const id of [...vendors.map(v=>v.id),'other']){const o=mk('option','',vendorName(id));o.value=id;vendor.append(o);}
  vendor.value=c.own_model?(c.own_model.vendor||'other'):(vendors[0]?.id||'other');
  const profile=field(main,copy('已有档案名称','Existing profile name'),c.provider.id||''),url=field(main,copy('模型 API 地址（OpenAI 兼容）','Model API URL (OpenAI compatible)'),c.own_model?.base_url||''),model=field(main,copy('模型名称','Model name'),c.own_model?.model||''),limit=field(main,copy('自带 key 每日模型 token 限额','Own-key daily model token limit'),String(c.own_model?.daily_tokens??100000),'number');
  const hint=mk('p','small');main.append(hint);
  let last=vendor.value;
  const update=()=>{const own=choice.value==='own',v=vendors.find(x=>x.id===vendor.value);profile.parentElement.hidden=choice.value!=='profile';for(const f of [model,limit])f.parentElement.hidden=!own;vendorWrap.hidden=hint.hidden=!own;url.parentElement.hidden=!own||!!v;
    hint.textContent=v?copy(`地址 ${v.base_url} · 默认模型 ${v.model} · 密钥卡会收 ${v.key}（只给这个 bot 用，和主 Agent 的 key 分开）· 去 ${v.console} 建按量 key`,`Address ${v.base_url} · default model ${v.model} · the secret card stores ${v.key} (this bot only, apart from your main Agent’s key) · create a pay-as-you-go key at ${v.console}`):copy('填一个 OpenAI 兼容的按量服务地址；密钥卡会收 BOT_MODEL_KEY。','Enter an OpenAI-compatible pay-as-you-go address; the secret card stores BOT_MODEL_KEY.');};
  vendor.addEventListener('change',()=>{const was=vendors.find(x=>x.id===last),v=vendors.find(x=>x.id===vendor.value);if(!model.value||(was&&model.value===was.model))model.value=v?v.model:'';last=vendor.value;update();});
  if(!model.value&&vendors.find(x=>x.id===vendor.value))model.value=vendors.find(x=>x.id===vendor.value).model;
  choice.addEventListener('change',update);update();
  const own=()=>({...(vendor.value==='other'?{vendor:'other',base_url:url.value,api:!c.own_model?.vendor||c.own_model.vendor==='other'?c.own_model?.api||'chat':'chat'}:{vendor:vendor.value}),model:model.value,daily_tokens:Number(limit.value)});
  const out=()=>({provider:{...(c.provider.chain?{chain:c.provider.chain}:{}),...(c.provider.docs_links?{docs_links:c.provider.docs_links}:{}),...(c.provider.thinking?{thinking:c.provider.thinking}:{}),...(choice.value==='profile'?{source:'profile',id:profile.value}:{source:choice.value})},own_model:choice.value==='own'?own():c.own_model});
  out.vendor=()=>vendor.value;return out;
}
function draw(detail,main){const c=detail.bot;main.append(mk('h2','',text(c.title,80)));tabs(main);
  if(tab==='settings'){
    const title=field(main,copy('名称','Name'),c.title),desc=field(main,copy('公开简介','Public description'),c.description),prompt=field(main,copy('业务范围和回答要求','Business scope and instructions'),c.prompt,'textarea'),background=field(main,copy('始终提供的背景（例如价格表）','Always included background (e.g. price list)'),c.background,'textarea');
    const zone=field(main,copy('服务时区','Service timezone'),c.timezone),language=field(main,copy('语言（auto / zh / en）','Language (auto / zh / en)'),c.language);
    const modelConfig=ownFields(main,c);
    const commercial=commercialFields(main,c);
    const enabled=checkbox(main,copy('接待访客','Serve visitors'),c.enabled),terms=checkbox(main,copy('接受对外服务条款','Accept public service terms'),c.terms_accepted),risk=checkbox(main,copy('已确认个人订阅对外服务的条款和风险','I checked terms and risks of public subscription use'),c.subscription_risk_accepted);privacy(main);
    const register=mk('a','',copy('审核服务商（费用自付）','Review provider (you pay review costs)'));register.href='https://openrouter.ai/';register.hidden=c.safety?.provider==='typesafe';register.target='_blank';register.rel='noopener noreferrer';main.append(register,mk('p','',copy('密钥仅用手机密钥卡写入Agent J 主机。按 设置 → 安全 的 Jev 总开关审核；主人确认关闭后只保留本地闸，启用时验证 decisions，失败保持暂停。','The phone secret card writes the key only to your computer. Uses the Jev mode in Settings → Safety. Owner-confirmed off keeps local gates only; enabled review verifies decisions and pauses on failure.')),button(copy('添加 / 更新审核 key（先保存服务商）','Add / update review key (save provider first)'),()=>act(async()=>{await write({op:'audit_key',id:c.id});await refresh();})));
    // P348: the card asks for the SAVED provider's key — a provider picked but not saved yet is saved first
    const saved=c.own_model?(c.own_model.vendor||'other'):null;
    main.append(button(copy(`添加 / 更新「${vendorName(saved||modelConfig.vendor())}」的 key`,`Add / update the key for ${vendorName(saved||modelConfig.vendor())}`),()=>act(async()=>{if(c.provider.source!=='own'||saved!==modelConfig.vendor())throw new Error(copy(`先在上面选「主人自带模型 key」和「${vendorName(modelConfig.vendor())}」并保存，再添加 key。`,`First choose “Owner’s model key” and “${vendorName(modelConfig.vendor())}” above and save, then add the key.`));await write({op:'model_key',id:c.id});await refresh();})),button(copy('添加 / 更换 Telegram token','Add / replace Telegram token'),()=>act(async()=>{await write({op:'telegram_key',id:c.id});await refresh();})));
    const telegram=checkbox(main,copy('在 Telegram 接待（停用后停止收发）','Serve on Telegram (disable to stop receiving and sending)'),c.telegram?.enabled);
    // P297: the owner's own Agent J Telegram bot — the owner's chat stays the main session; everyone else is this bot's visitor
    const guests=checkbox(main,copy('让我的 Agent J Telegram 机器人也接待客人','Let my Agent J Telegram bot receive guests'),c.telegram?.owner_bot);
    guests.addEventListener('change',()=>act(async()=>{await write({op:'attach_telegram',id:c.id,attach:guests.checked});await refresh();}));
    main.append(mk('p','small',copy('同一个机器人两种身份：对你是助理（主对话），对别人是这个客服（独立会话、这里的提示词、模型和限额）。只接待私聊；客人永远拿不到你的权限。关掉后陌生人只收到自己的数字 ID。','One bot, two roles: your assistant for you (main chat), this customer service for everyone else (separate sessions; this bot’s prompt, model and limits). Private chats only; guests never get your permissions. Off: strangers only get their numeric ID.')));
    // P275: a bot without a photo gets the official Agent J one automatically; one with its own photo keeps it, unless the owner taps here.
    if(detail.telegram?.configured)main.append(button(copy('换成 Agent J 头像','Use the Agent J avatar'),()=>act(async()=>{if(await confirmSheet(copy('换成 Agent J 头像','Use the Agent J avatar'),copy('把这个 Telegram bot 现在的头像换成 Agent J 官方 Logo。','Replace this Telegram bot’s current photo with the official Agent J logo.'),copy('换成 Agent J 头像','Use the Agent J avatar'))){await write({op:'telegram_avatar',id:c.id});toast(copy('头像已换成 Agent J 官方 Logo','The bot now uses the official Agent J logo'));}})),mk('p','small',copy('绑定后 bot 还没有头像的，会自动用上 Agent J 官方 Logo；已经有头像的不会动。','A bot with no photo gets the official Agent J logo when bound; a bot that already has a photo keeps it.')));
    main.append(mk('p','small',copy('先用密钥卡添加 token，再启用 Telegram。更换 token 会暂停 Telegram；保存后恢复。仅接待私聊，每位 Telegram 访客独立会话。Telegram 平台能看到此渠道的消息。','Add a token using the secret card, then enable Telegram. Replacing the token pauses Telegram; save to resume. Private chats only, with separate visitor sessions. Telegram can see messages on this channel.')));
    const free=field(main,copy('每天每位访客免费问答数','Daily free questions per visitor'),String(c.paid_qa?.free_questions??3),'number'),payment=field(main,copy('主人设置的付费链接（HTTPS，不接支付）','Owner’s payment link (HTTPS; no payment integration)'),c.paid_qa?.payment_url||'');free.parentElement.hidden=payment.parentElement.hidden=c.template!=='paid_qa';
    const nativeProblems={
      native_executable_missing:['找不到主 Agent 已选程序。请在Agent J 主机上检查安装路径。','The selected main Agent program was not found. Check its installation path on your computer.'],
      native_executable_not_executable:['主 Agent 已选程序不能执行。请在Agent J 主机上检查文件权限。','The selected main Agent program cannot run. Check its file permissions on your computer.'],
      native_executable_selection_failed:['主 Agent 程序路径无法确定。请在Agent J 主机上确认已选版本。','The main Agent program path is unresolved. Confirm the selected version on your computer.'],
      native_spawn_failed:['客服程序未能启动，本次模型预留已释放。请检查Agent J 主机运行状态。','The bot program could not start. This model reservation was released. Check your computer.'],
      coding_plan_bot:planProblem
    };
    const problem=detail.provider_problem||detail.native_start_failure;
    if(problem)main.append(mk('p','',nativeProblems[problem]?copy(...nativeProblems[problem]):copy('请检查主 Agent 的登录，或用手机密钥卡添加自带 key。','Check your main Agent login or add your own key using the phone secret card.')));
    const allDay=checkbox(main,copy('全天接待','Serve all day'),c.hours===null),start=field(main,copy('开始时间（0–23 点）','Start hour (0–23)'),String(c.hours?.start??9),'number'),end=field(main,copy('结束时间（0–23 点）','End hour (0–23)'),String(c.hours?.end??18),'number');
    const days=[];for(const [i,labels] of [['周一','Monday'],['周二','Tuesday'],['周三','Wednesday'],['周四','Thursday'],['周五','Friday'],['周六','Saturday'],['周日','Sunday']].entries())days.push(checkbox(main,copy(...labels),c.hours?.days.includes(i)??true));
    const slug=field(main,copy('公开名字（改名后旧名冻结 30 天）','Public name (old name frozen for 30 days)'),c.slug);
    main.append(button(copy('删除 bot（名字冻结 30 天）','Delete bot (name frozen for 30 days)'),()=>act(async()=>{if(await confirmSheet(copy('删除 bot','Delete bot'),c.slug,copy('删除','Delete'))){await write({op:'delete',id:c.id});selected=null;await refresh();}})));
    main.append(button(copy('保存','Save'),()=>act(async()=>{await write({op:'save',id:c.id,config:{...withoutId(c),slug:slug.value,title:title.value,description:desc.value,prompt:prompt.value,background:background.value,timezone:zone.value,language:language.value,hours:allDay.checked?null:{start:Number(start.value),end:Number(end.value),days:days.flatMap((e,i)=>e.checked?[i]:[])},...commercial(),...modelConfig(),provider:{...modelConfig().provider,chain:commercial().provider.chain,docs_links:commercial().provider.docs_links,thinking:commercial().provider.thinking},paid_qa:{free_questions:Number(free.value),payment_url:payment.value},telegram:{enabled:telegram.checked},enabled:enabled.checked,terms_accepted:terms.checked,subscription_risk_accepted:risk.checked}});await refresh();})));
  }else if(tab==='limits'){
    const labels={visitor_messages:['每访客每日消息','Daily visitor messages'],bot_messages:['Bot 每日消息','Daily bot messages'],host_messages:['主机每日总消息','Daily host messages'],host_tokens:['主机每日模型 tokens（含审核）','Daily host model tokens (including reviews)'],concurrency:['同时接待人数','Concurrent visitors'],max_chars:['单条字数（最多 4000）','Characters per message (up to 4000)'],per_minute:['每访客每分钟消息','Messages per visitor per minute']},fields={};
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
    for(const tool of detail.tools||[]){const row=mk('div','bots-row');row.append(mk('h3','',text(tool.name,40)+(tool.level==='write'?copy(' · 写入：每次需批准',' · WRITE: approval every time'):copy(' · 查询',' · Read'))),mk('p','',text(tool.description,1000)));const definition=copyable(mk('pre','',JSON.stringify(tool,null,2)),{click:false});row.append(definition,button(tool.enabled?copy('停用','Disable'):copy('启用','Enable'),()=>act(async()=>{await write({op:'tool_save',id:c.id,tool:{...tool,enabled:!tool.enabled}});await refresh();})));main.append(row);}
  }else if(tab==='conversations')act(async()=>{
    const handoffs=await read({op:'handoffs',id:c.id});
    for(const h of handoffs.handoffs||[]){
      main.append(mk('p','',h.visitor.slice(0,8)+' · '+h.status));
      if(!h.contact)continue;
      main.append(mk('p','',h.contact.email+' · '+(h.contact.verified?copy('已验证','Verified'):copy('未验证','Unverified'))+' · '+h.contact.sends+'/3'));
      const submit=async(op,text)=>{const to=h.contact.email;if(!await confirmSheet(copy('确认邮件收件人和正文','Confirm email recipient and body'),to+'\n\n'+(text||copy('发送客服邮箱验证码，10 分钟有效。','Send a customer service verification code, valid for 10 minutes.')),copy('签名发送','Sign and send')))return;await write({op,id:c.id,handoff:h.id,to,send_id:crypto.randomUUID().replaceAll('-',''),...(text?{text}:{})});await refresh();};
      if(h.contact.verified){const reply=field(main,copy('人工邮件正文','Human email body'),'','textarea');main.append(button(copy('签名发送回复','Sign and send reply'),()=>act(()=>submit('human_email_reply',reply.value))));}
      else main.append(button(copy('签名发送验证码','Sign and send verification code'),()=>act(()=>submit('human_email_code'))));
    }
    const m=await read({op:'history',id:c.id});for(const session of m.sessions||[])main.append(button(session.visitor.slice(0,8)+' · '+session.messages,()=>act(async()=>{const m=await read({op:'history',id:c.id,visitor:session.visitor});const history=mk('div','');for(const msg of m.messages||[])history.append(mk('p','',msg.role+': '+text(msg.text,12000)));main.append(history);})));});
  else if(tab==='statistics')act(async()=>{const m=await read({op:'statistics',id:c.id});for(const day of m.days||[])main.append(mk('p','',`${day.day}: ${day.calls} ${copy('调用','calls')} · ${day.charged_tokens} ${copy('已计费/预留 tokens','charged/reserved tokens')}`));for(const tool of m.tools||[])main.append(mk('p','',`${tool.day}: ${tool.tool} · ${tool.calls}`));});
  else if(tab==='embed'){
    const url='https://agentj.app/bots/'+c.slug;main.append(mk('p','',copy('免费公开网址','Free public URL')+': '+url));
    const code=`<script src="https://agentj.app/bots/embed.js" data-bot="${c.slug}" async></script>`;
    main.append(copyable(mk('pre','',code),{label:'r.md.copyAria'}),button(copy('复制嵌入代码','Copy embed code'),()=>act(async()=>{if(!await copyNow(code))throw new Error(copy('复制失败','Copy failed'));})));
    const domains=field(main,copy('工具出站域名（逗号分隔）','Tool outbound domains (comma separated)'),c.outbound_domains.join(',')),origins=field(main,copy('允许嵌入的网站（https origin，逗号分隔）','Allowed embedding websites (HTTPS origins, comma separated)'),c.embed_origins.join(','));main.append(button(copy('保存','Save'),()=>act(async()=>{await write({op:'save',id:c.id,config:{...withoutId(c),outbound_domains:domains.value.split(',').map(x=>x.trim()).filter(Boolean),embed_origins:origins.value.split(',').map(x=>x.trim()).filter(Boolean)}});await refresh();})));
  }
}
function commercialFields(main,c){
  c={...c,privacy:c.privacy||{retention_days:30},memory:c.memory||{history_hours:24,max_turns:40,max_chars:28000},outbound:c.outbound||{link_domains:[],block_commands:true},knowledge:c.knowledge||{cite:true,max_chars:6000},handoff:c.handoff||{reply_window_h:48},messages:c.messages||{},widget:c.widget||{position:'br',accent:'#629887',welcome:'',quick_replies:[],open_on_load:false,queue_merge:true}};
  const group=mk('details'),summary=mk('summary','',copy('商用配置：备用模型、话术和挂件','Business settings: fallback models, messages and widget'));group.append(summary);main.append(group);
  const f=(zh,en,v,type='text')=>field(group,copy(zh,en),v,type);
  const review=f('审核服务商：openrouter / typesafe','Review provider: openrouter / typesafe',c.safety?.provider||'openrouter'),criteria=f('判据：customer_service / strict','Criteria: customer_service / strict',c.safety?.criteria||'customer_service'),after=f('下班：refuse / ai_only / leave_message','After hours: refuse / ai_only / leave_message',c.service?.after_hours||'refuse');
  const email=checkbox(group,copy('允许人工邮件回复（验证邮箱、逐封签名，含验证码最多三封）','Allow handoff email (verified address, sign each mail, three including verification)'),c.handoff.email_enabled);
  const sources=f('定时资料源 JSON（url/name/cite_url/resync）','Scheduled sources JSON (url/name/cite_url/resync)',JSON.stringify(c.knowledge.sources||[]),'textarea');
  const handoff=f('人工回复窗口（小时）','Human reply window (hours)',c.handoff.reply_window_h,'number');
  const retention=f('聊天保留天数','Chat retention days',c.privacy.retention_days,'number'),hours=f('历史小时数','History hours',c.memory.history_hours,'number'),turns=f('历史消息条数','History messages',c.memory.max_turns,'number'),context=f('上下文字节上限','Context byte limit',c.memory.max_chars,'number');
  const cite=checkbox(group,copy('回答附资料原文链接','Append source links'),c.knowledge.cite),knowledge=f('检索字数上限','Retrieved characters',c.knowledge.max_chars,'number');
  const domains=f('回答可点击链接域名（逗号分隔）','Clickable answer domains (comma separated)',c.outbound.link_domains.join(',')),commands=checkbox(group,copy('拦截危险命令','Block dangerous commands'),c.outbound.block_commands);
  const chain=[];for(let i=0;i<2;i++){
    const e=c.provider.chain?.[i];const vendor=mk('select');vendor.setAttribute('aria-label',copy(`备用服务商 ${i+1}`,`Fallback provider ${i+1}`));const off=mk('option','',copy('不使用','Off'));off.value='';vendor.append(off);for(const v of vendors){const o=mk('option','',copy(v.zh,v.en));o.value=v.id;vendor.append(o);}vendor.value=e?.vendor||'';group.append(vendor);const model=f(`备用模型 ${i+1}`,`Fallback model ${i+1}`,e?.model||'');chain.push({vendor,model});
    if(e)group.append(button(copy(`添加 ${vendorName(e.vendor)} 备用 key`,`Add ${vendorName(e.vendor)} fallback key`),()=>act(async()=>{await write({op:'model_key',id:c.id,vendor:e.vendor});await refresh();})));
  }
  const docs=f('模型均失败后的文档 HTTPS 链接（每行一个）','HTTPS documentation links if every model fails (one per line)',(c.provider.docs_links||[]).join('\n'),'textarea');
  const messages={};for(const [k,zh,en] of [['welcome','欢迎语','Welcome'],['offtopic','离题话术','Off-topic reply'],['handoff_sent','转人工提示','Handoff notice'],['unavailable','无法接待话术','Unavailable reply'],['outside_hours','下班话术','Outside-hours reply'],['limit','限额话术','Limit reply']]){messages[k]={};for(const l of ['zh','en'])messages[k][l]=f(zh+' · '+l,en+' · '+l,typeof c.messages[k]==='string'?c.messages[k]:c.messages[k]?.[l]||'');}
  const position=f('挂件位置（br / bl）','Widget position (br / bl)',c.widget.position),accent=f('挂件颜色（#629887 / #141414 / #827dbd / #d97757）','Widget accent (#629887 / #141414 / #827dbd / #d97757)',c.widget.accent),welcome=f('挂件欢迎语（留空使用上方欢迎语）','Widget welcome (empty uses welcome above)',c.widget.welcome),quick=f('快捷问题（每行一个，最多 6 个）','Quick questions (one per line, up to 6)',c.widget.quick_replies.join('\n'),'textarea'),auto=checkbox(group,copy('自动展开挂件','Open widget on load'),c.widget.open_on_load),queue=checkbox(group,copy('等待时排队合并','Merge queued messages'),c.widget.queue_merge);
  return ()=>({safety:{provider:review.value,criteria:criteria.value},service:{after_hours:after.value},handoff:{reply_window_h:Number(handoff.value),email_enabled:email.checked},privacy:{retention_days:Number(retention.value)},memory:{history_hours:Number(hours.value),max_turns:Number(turns.value),max_chars:Number(context.value)},knowledge:{cite:cite.checked,max_chars:Number(knowledge.value),sources:JSON.parse(sources.value)},outbound:{block_commands:commands.checked,link_domains:domains.value.split(',').map(x=>x.trim()).filter(Boolean)},provider:{source:c.provider.source,...(c.provider.id?{id:c.provider.id}:{}),chain:chain.filter(e=>e.vendor.value).map(e=>({vendor:e.vendor.value,model:e.model.value})),docs_links:docs.value.split('\n').map(x=>x.trim()).filter(Boolean),thinking:'off'},messages:Object.fromEntries(Object.entries(messages).flatMap(([k,v])=>{const entries=Object.entries(v).filter(([,f])=>f.value.trim()).map(([l,f])=>[l,f.value]);return entries.length?[[k,Object.fromEntries(entries)]]:[];})),widget:{position:position.value,accent:accent.value,welcome:welcome.value,quick_replies:quick.value.split('\n').map(x=>x.trim()).filter(Boolean),open_on_load:auto.checked,queue_merge:queue.checked}});
}
function withoutId(c){const {id,...value}=c;return value;}
function paintProposal(m){
  if(!m||typeof m.id!=='string'||!m.request||typeof m.request!=='object'||m.expires<=Date.now())return;
  const main=el('bots-main');main.replaceChildren(mk('h2','',copy('Agent J 请求修改 bot','Agent J proposes a bot change')),copyable(mk('pre','',JSON.stringify(m.request,null,2).slice(0,60000)),{click:false}),button(copy('批准这次修改','Approve this change'),()=>act(async()=>{if(m.expires<=Date.now())throw new Error('expired');await write(m.request);pendingProposal=null;await refresh();})),button(copy('返回','Back'),()=>{pendingProposal=null;refresh();}));
}

export function proposal(m){if(!m?.request || typeof m.expires!=='number' || m.expires<=Date.now())return;pendingProposal=m;open();}

export function toolRequest(m){
 if(!m?.digest || typeof m.expires!=='number' || m.expires*1000<=Date.now())return;
 const req={op:'tool_approve',approval:m.id,digest:m.digest,allow:true};
 const main=el('bots-main');hooks.openPanel('bots');++generation;
 main.replaceChildren(mk('h2','',copy('批准 bot 写入操作','Approve a bot write operation')),mk('p','',text(m.tool,40)),copyable(mk('pre','',text(m.args,4000)),{click:false}),button(copy('批准这一次','Approve once'),()=>act(async()=>{await write(req);await refresh();})),button(copy('拒绝','Deny'),()=>act(async()=>{await write({...req,allow:false});await refresh();})));
}
