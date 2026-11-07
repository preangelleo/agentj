// Owner-only /bots. Every read/write is inside the paired Noise channel.
// This file is never loaded in the public chat or verification frames.
import { ask1, isReady } from './session.js';
import { signedWrite } from './controls.js';
import { lang, t, both } from './t.js';
import { el, mk, toast, confirmSheet, pageCopy } from './ui.js';
import { copyable, copyNow } from './copycode.js';   // P292: every pre can be copied (⧉)
let hooks, selected=null, tab='settings', generation=0, pendingProposal=null,hostZone='UTC',vendors=[];
const copy=(zh,en)=>lang()==='en'?en:zh;
const text=(v,max=6000)=>typeof v==='string'?v.slice(0,max):'';
export const TABS=['settings','limits','knowledge','tools','conversations','statistics','embed'];
export function configure(h){hooks=h;}
export function open(){hooks.openPanel('bots');}
const button=(label,fn)=>{const b=mk('button','aj-action',label, 'aj-action');b.type='button';b.addEventListener('click',fn);return b;};
function status(message){el('bots-status').textContent=message;}
async function read(request){if(!isReady())throw new Error(t('bots.agent_j_host_is_disconnected'));const m=await ask1({t:'bots_read',request},'bots_res');if(!m?.ok)throw new Error(m?.why||'offline');return m;}
async function write(request){
  const m=await signedWrite('bots_write',{request},{t:'bots_write',request});
  if(!m.ok)throw new Error(m.why||'offline');return m;
}
// P348: a support bot serves other people, so no Coding Plan / Token Plan key — the owner adds a pay-as-you-go one
const planProblem=[...both('bots.a_support_bot_serves_other_people_so_it_cannot_use_a_coding_plan_token_plan_key_choose_owner_s_model')];
async function act(fn){try{status(t('bots.working'));await fn();status('');}catch(e){status(e.message==='coding_plan_bot'?copy(...planProblem):text(e.message,160));}}
function field(parent,label,value='',type='text'){
  const wrap=mk('label','bots-field'),title=mk('span','',label),input=mk(type==='textarea'?'textarea':'input',undefined,undefined,'aj-bots');
  if(type!=='textarea')input.type=type;input.value=value;input.setAttribute('aria-label',label);wrap.append(title,input);parent.append(wrap);return input;
}
function checkbox(parent,label,value){const w=mk('label','bots-field'),input=mk('input', undefined, undefined, 'aj-bots');input.type='checkbox';input.checked=value===true;w.append(input,document.createTextNode(label));parent.append(w);return input;}
function privacy(parent){parent.append(pageCopy(mk('p','small',t('bots.bots_use_your_model_account_directly_from_your_agent_j_host_agentsrelay_requests_pass_through_its_re'), 'aj-bots')));}
export async function refresh(){
  if(pendingProposal && pendingProposal.expires>Date.now()){paintProposal(pendingProposal);return;}
  pendingProposal=null;
  const g=++generation;await act(async()=>{
    const list=await read({op:'list'});hostZone=list.host_timezone||'UTC';vendors=Array.isArray(list.vendors)?list.vendors.slice(0,30):[];if(g!==generation)return;
    const side=el('bots-side');side.replaceChildren(button(t('bots.main_chat'),()=>hooks.openPanel('chat')),button(t('bots.create_bot'),wizard));
    for(const bot of (list.bots||[]).slice(0,100))side.append(button(text(bot.title,80)+' · '+(bot.enabled?t('bots.enabled'):t('bots.paused')),()=>{selected=bot.id;tab='settings';refresh();}));
    if(selected&&!list.bots?.some(b=>b.id===selected))selected=null;
    const main=el('bots-main');main.replaceChildren();
    if(!selected){main.append(mk('h2','',t('bots.customer_service_on_your_agent_j_host'), 'aj-bots'),pageCopy(mk('p','',t('bots.visitors_are_served_while_your_agent_j_host_is_online_one_enabled_bot_per_seat'), 'aj-bots')));privacy(main);return;}
    const detail=await read({op:'detail',id:selected});if(g!==generation)return;draw(detail,main);
  });
}
function tabs(parent){const nav=mk('nav','bots-tabs aj-actions');nav.setAttribute('aria-label',t('bots.bot_settings'));
  const labels={settings:[...both('bots.settings')],limits:[...both('bots.limits')],knowledge:[...both('bots.knowledge')],tools:[...both('bots.tools')],conversations:[...both('bots.conversations')],statistics:[...both('bots.statistics')],embed:[...both('bots.embed')]};
  for(const name of TABS){const b=button(copy(...labels[name]),()=>{tab=name;refresh();});b.className='aj-navigation-action';b.setAttribute('data-aj-owner','aj-navigation-action');b.setAttribute('aria-current',name===tab?'page':'false');nav.append(b);}parent.append(nav);
}
function wizard(){selected=null;const main=el('bots-main');main.replaceChildren();let step=1;const config={slug:'',title:'',enabled:false,terms_accepted:false,subscription_risk_accepted:false,timezone:hostZone,template:'customer_service'};
  const render=()=>{main.replaceChildren(mk('h2','',t('bots.wizard_step',{step}), 'aj-bots'));
    if(step===1){
      for(const [id,zh,en] of [['customer_service',...both('bots.customer_service_answer_from_knowledge_ask_a_human_when_unsure')],['companion',...both('bots.companion_warm_conversation_with_clear_ai_identity_and_boundaries')],['paid_qa',...both('bots.paid_q_a_show_your_payment_link_after_n_free_daily_questions')]])main.append(button(copy(zh,en),()=>{config.template=id;step=2;render();}));
    }
    if(step===2){const title=field(main,t('bots.display_name'),config.title),slug=field(main,t('bots.public_name_lowercase_letters_numbers_hyphens_3_32'),config.slug);main.append(button(t('bots.continue'),()=>{config.title=title.value;config.slug=slug.value;step=3;render();}));}
    if(step===3){main.append(pageCopy(mk('p','',t('bots.defaults_20_messages_visitor_day_100_bot_day_100_messages_and_100k_model_tokens_day_across_your_agen'), 'aj-bots')));privacy(main);const modelConfig=ownFields(main,{provider:{source:'main'},own_model:null});const risk=checkbox(main,t('bots.i_checked_provider_terms_and_accept_possible_restrictions_on_public_use_of_a_personal_subscription'),false);main.append(pageCopy(mk('p','',t('bots.after_creation_choose_typesafe_or_openrouter_and_add_a_review_key_through_the_phone_secret_card_acti'), 'aj-bots')));const terms=checkbox(main,t('bots.i_accept_public_service_and_content_responsibility_terms'),false);main.append(button(t('bots.create_paused'),()=>act(async()=>{config.terms_accepted=terms.checked;config.subscription_risk_accepted=risk.checked;Object.assign(config,modelConfig());const m=await write({op:'create',config});selected=m.bot.id;await refresh();})));}
  };render();
}
// P348: 「主人自带模型 key」 starts with the provider (it may differ from the main Agent's): each preset brings its endpoint,
// a cheap default model and its own key name; 「其他」 = the owner's own OpenAI-compatible address (key BOT_MODEL_KEY)
const vendorName=id=>{const v=vendors.find(x=>x.id===id);return v?copy(v.zh,v.en):t('bots.other_service_own_address');};
function ownFields(main,c){
  const choice=mk('select','aj-select', undefined, 'aj-bots');choice.setAttribute('aria-label',t('bots.model_billing_channel'));
  for(const [v,zh,en] of [['main',...both('bots.use_main_agent_plan')],['own',...both('bots.owner_s_model_key')],['profile',...both('bots.existing_provider_profile')]]){const o=mk('option','',copy(zh,en));o.value=v;choice.append(o);}choice.value=c.provider.source;main.append(choice);
  const vendorWrap=mk('label','bots-field'),vendor=mk('select','aj-select', undefined, 'aj-bots');vendor.setAttribute('aria-label',t('bots.which_provider_s_pay_as_you_go_key'));
  vendorWrap.append(mk('span','',t('bots.which_provider_s_pay_as_you_go_key_may_differ_from_your_main_agent_s')),vendor);main.append(vendorWrap);
  for(const id of [...vendors.map(v=>v.id),'other']){const o=mk('option','',vendorName(id));o.value=id;vendor.append(o);}
  vendor.value=c.own_model?(c.own_model.vendor||'other'):(vendors[0]?.id||'other');
  const profile=field(main,t('bots.existing_profile_name'),c.provider.id||''),url=field(main,t('bots.model_api_url_openai_compatible'),c.own_model?.base_url||''),model=field(main,t('bots.model_name'),c.own_model?.model||''),limit=field(main,t('bots.own_key_daily_model_token_limit'),String(c.own_model?.daily_tokens??100000),'number');
  const hint=pageCopy(mk('p','small', undefined, 'aj-bots'));main.append(hint);
  let last=vendor.value;
  const update=()=>{const own=choice.value==='own',v=vendors.find(x=>x.id===vendor.value);profile.parentElement.hidden=choice.value!=='profile';for(const f of [model,limit])f.parentElement.hidden=!own;vendorWrap.hidden=hint.hidden=!own;url.parentElement.hidden=!own||!!v;
    hint.textContent=v?t('bots.provider_hint',{address:v.base_url,model:v.model,key:v.key,console:v.console}):t('bots.enter_an_openai_compatible_pay_as_you_go_address_the_secret_card_stores_bot_model_key');};
  vendor.addEventListener('change',()=>{const was=vendors.find(x=>x.id===last),v=vendors.find(x=>x.id===vendor.value);if(!model.value||(was&&model.value===was.model))model.value=v?v.model:'';last=vendor.value;update();});
  if(!model.value&&vendors.find(x=>x.id===vendor.value))model.value=vendors.find(x=>x.id===vendor.value).model;
  choice.addEventListener('change',update);update();
  const own=()=>({...(vendor.value==='other'?{vendor:'other',base_url:url.value,api:!c.own_model?.vendor||c.own_model.vendor==='other'?c.own_model?.api||'chat':'chat'}:{vendor:vendor.value}),model:model.value,daily_tokens:Number(limit.value)});
  const out=()=>({provider:{...(c.provider.chain?{chain:c.provider.chain}:{}),...(c.provider.docs_links?{docs_links:c.provider.docs_links}:{}),...(c.provider.thinking?{thinking:c.provider.thinking}:{}),...(choice.value==='profile'?{source:'profile',id:profile.value}:{source:choice.value})},own_model:choice.value==='own'?own():c.own_model});
  out.vendor=()=>vendor.value;return out;
}
function draw(detail,main){const c=detail.bot;main.append(mk('h2','',text(c.title,80), 'aj-bots'));tabs(main);
  if(tab==='settings'){
    const title=field(main,t('bots.name'),c.title),desc=field(main,t('bots.public_description'),c.description),prompt=field(main,t('bots.business_scope_and_instructions'),c.prompt,'textarea'),background=field(main,t('bots.always_included_background_e_g_price_list'),c.background,'textarea');
    const zone=field(main,t('bots.service_timezone'),c.timezone),language=field(main,t('bots.language_auto_zh_en'),c.language);
    const modelConfig=ownFields(main,c);
    const commercial=commercialFields(main,c);
    const enabled=checkbox(main,t('bots.serve_visitors'),c.enabled),terms=checkbox(main,t('bots.accept_public_service_terms'),c.terms_accepted),risk=checkbox(main,t('bots.i_checked_terms_and_risks_of_public_subscription_use'),c.subscription_risk_accepted);privacy(main);
    const register=mk('a','',t('bots.review_provider_you_pay_review_costs'));register.href='https://openrouter.ai/';register.hidden=c.safety?.provider==='typesafe';register.target='_blank';register.rel='noopener noreferrer';main.append(register,pageCopy(mk('p','',t('bots.the_phone_secret_card_writes_the_key_only_to_your_agent_j_host_uses_the_jev_mode_in_settings_safety_'), 'aj-bots')),button(t('bots.add_update_review_key_save_provider_first'),()=>act(async()=>{await write({op:'audit_key',id:c.id});await refresh();})));
    // P348: the card asks for the SAVED provider's key — a provider picked but not saved yet is saved first
    const saved=c.own_model?(c.own_model.vendor||'other'):null;
    main.append(button(t('bots.model_key',{provider:vendorName(saved||modelConfig.vendor())}),()=>act(async()=>{if(c.provider.source!=='own'||saved!==modelConfig.vendor())throw new Error(t('bots.save_provider_first',{provider:vendorName(modelConfig.vendor())}));await write({op:'model_key',id:c.id});await refresh();})),button(t('bots.add_replace_telegram_token'),()=>act(async()=>{await write({op:'telegram_key',id:c.id});await refresh();})));
    const telegram=checkbox(main,t('bots.serve_on_telegram_disable_to_stop_receiving_and_sending'),c.telegram?.enabled);
    // P297: the owner's own Agent J Telegram bot — the owner's chat stays the main session; everyone else is this bot's visitor
    const guests=checkbox(main,t('bots.let_my_agent_j_telegram_bot_receive_guests'),c.telegram?.owner_bot);
    guests.addEventListener('change',()=>act(async()=>{await write({op:'attach_telegram',id:c.id,attach:guests.checked});await refresh();}));
    main.append(pageCopy(mk('p','small',t('bots.one_bot_two_roles_your_assistant_for_you_main_chat_this_customer_service_for_everyone_else_separate_'), 'aj-bots')));
    // P275: a bot without a photo gets the official Agent J one automatically; one with its own photo keeps it, unless the owner taps here.
    if(detail.telegram?.configured)main.append(button(t('bots.use_the_agent_j_avatar'),()=>act(async()=>{if(await confirmSheet(t('bots.use_the_agent_j_avatar'),t('bots.replace_this_telegram_bot_s_current_photo_with_the_official_agent_j_logo'),t('bots.use_the_agent_j_avatar'))){await write({op:'telegram_avatar',id:c.id});toast(t('bots.the_bot_now_uses_the_official_agent_j_logo'));}})),pageCopy(mk('p','small',t('bots.a_bot_with_no_photo_gets_the_official_agent_j_logo_when_bound_a_bot_that_already_has_a_photo_keeps_i'), 'aj-bots')));
    main.append(pageCopy(mk('p','small',t('bots.add_a_token_using_the_secret_card_then_enable_telegram_replacing_the_token_pauses_telegram_save_to_r'), 'aj-bots')));
    const free=field(main,t('bots.daily_free_questions_per_visitor'),String(c.paid_qa?.free_questions??3),'number'),payment=field(main,t('bots.owner_s_payment_link_https_no_payment_integration'),c.paid_qa?.payment_url||'');free.parentElement.hidden=payment.parentElement.hidden=c.template!=='paid_qa';
    const nativeProblems={
      native_executable_missing:[...both('bots.the_selected_main_agent_program_was_not_found_check_its_installation_path_on_your_agent_j_host')],
      native_executable_not_executable:[...both('bots.the_selected_main_agent_program_cannot_run_check_its_file_permissions_on_your_agent_j_host')],
      native_executable_selection_failed:[...both('bots.the_main_agent_program_path_is_unresolved_confirm_the_selected_version_on_your_agent_j_host')],
      native_spawn_failed:[...both('bots.the_bot_program_could_not_start_this_model_reservation_was_released_check_your_agent_j_host')],
      coding_plan_bot:planProblem
    };
    const problem=detail.provider_problem||detail.native_start_failure;
    if(problem)main.append(pageCopy(mk('p','',nativeProblems[problem]?copy(...nativeProblems[problem]):t('bots.check_your_main_agent_login_or_add_your_own_key_using_the_phone_secret_card'), 'aj-bots')));
    const allDay=checkbox(main,t('bots.serve_all_day'),c.hours===null),start=field(main,t('bots.start_hour_0_23'),String(c.hours?.start??9),'number'),end=field(main,t('bots.end_hour_0_23'),String(c.hours?.end??18),'number');
    const days=[];for(const [i,labels] of [[...both('bots.monday')],[...both('bots.tuesday')],[...both('bots.wednesday')],[...both('bots.thursday')],[...both('bots.friday')],[...both('bots.saturday')],[...both('bots.sunday')]].entries())days.push(checkbox(main,copy(...labels),c.hours?.days.includes(i)??true));
    const slug=field(main,t('bots.public_name_old_name_frozen_for_30_days'),c.slug);
    main.append(button(t('bots.delete_bot_name_frozen_for_30_days'),()=>act(async()=>{if(await confirmSheet(t('bots.delete_bot'),c.slug,t('bots.delete'))){await write({op:'delete',id:c.id});selected=null;await refresh();}})));
    main.append(button(t('bots.save'),()=>act(async()=>{await write({op:'save',id:c.id,config:{...withoutId(c),slug:slug.value,title:title.value,description:desc.value,prompt:prompt.value,background:background.value,timezone:zone.value,language:language.value,hours:allDay.checked?null:{start:Number(start.value),end:Number(end.value),days:days.flatMap((e,i)=>e.checked?[i]:[])},...commercial(),...modelConfig(),provider:{...modelConfig().provider,chain:commercial().provider.chain,docs_links:commercial().provider.docs_links,thinking:commercial().provider.thinking},paid_qa:{free_questions:Number(free.value),payment_url:payment.value},telegram:{enabled:telegram.checked},enabled:enabled.checked,terms_accepted:terms.checked,subscription_risk_accepted:risk.checked}});await refresh();})));
  }else if(tab==='limits'){
    const labels={visitor_messages:[...both('bots.daily_visitor_messages')],bot_messages:[...both('bots.daily_bot_messages')],host_messages:[...both('bots.daily_host_messages')],host_tokens:[...both('bots.daily_host_model_tokens_including_reviews')],concurrency:[...both('bots.concurrent_visitors')],max_chars:[...both('bots.characters_per_message_up_to_4000')],per_minute:[...both('bots.messages_per_visitor_per_minute')]},fields={};
    for(const [k,v] of Object.entries(c.limits))fields[k]=field(main,copy(...labels[k]),String(v),'number');
    main.append(button(t('bots.save_limits'),()=>act(async()=>{const limits=Object.fromEntries(Object.entries(fields).map(([k,e])=>[k,Number(e.value)]));await write({op:'save',id:c.id,config:{...withoutId(c),limits}});await refresh();})));
  }else if(tab==='knowledge'){
    main.append(pageCopy(mk('p','',t('bots.ask_your_agent_j_to_sync_a_local_folder_or_url_documents_are_retrieved_as_needed_put_a_fixed_price_l'), 'aj-bots')));
    const upload=field(main,t('bots.add_knowledge_md_txt_pdf_csv'),'','file');upload.accept='.md,.txt,.pdf,.csv';
    upload.addEventListener('change',()=>act(async()=>{const file=upload.files?.[0];if(!file)return;if(file.size>2*1024*1024)throw new Error('2 MiB maximum');const bytes=new Uint8Array(await file.arrayBuffer());let raw='';for(const byte of bytes)raw+=String.fromCharCode(byte);await write({op:'knowledge_add',id:c.id,name:file.name,data:btoa(raw)});await refresh();}));
    for(const file of detail.knowledge||[]){const row=mk('div','bots-row',text(file.name,100));row.append(button(t('bots.delete'),()=>act(async()=>{if(await confirmSheet(t('bots.delete_knowledge'),file.name,t('bots.delete'))){await write({op:'knowledge_remove',id:c.id,name:file.name});await refresh();}})));main.append(row);}
  }else if(tab==='tools'){
    main.append(pageCopy(mk('p','',t('bots.ask_agent_j_to_configure_company_apis_read_tools_run_automatically_write_tools_default_to_off_and_al'), 'aj-bots')));
    act(async()=>{const usage=await read({op:'statistics',id:c.id});if(tab!=='tools'||selected!==c.id)return;for(const tool of usage.tools||[])main.append(pageCopy(mk('p','',`${tool.day}: ${tool.tool} · ${tool.calls} ${t('bots.calls')}`, 'aj-bots')));});
    for(const tool of detail.tools||[]){const row=mk('div','bots-row');row.append(mk('h3','',text(tool.name,40)+(tool.level==='write'?t('bots.write_approval_every_time'):t('bots.read')), 'aj-bots'),pageCopy(mk('p','',text(tool.description,1000), 'aj-bots')));const definition=copyable(mk('pre','',JSON.stringify(tool,null,2), 'aj-bots'),{click:false});row.append(definition,button(tool.enabled?t('bots.disable'):t('bots.enable'),()=>act(async()=>{await write({op:'tool_save',id:c.id,tool:{...tool,enabled:!tool.enabled}});await refresh();})));main.append(row);}
  }else if(tab==='conversations')act(async()=>{
    const handoffs=await read({op:'handoffs',id:c.id});
    for(const h of handoffs.handoffs||[]){
      main.append(pageCopy(mk('p','',h.visitor.slice(0,8)+' · '+h.status, 'aj-bots')));
      if(!h.contact)continue;
      main.append(pageCopy(mk('p','',h.contact.email+' · '+(h.contact.verified?t('bots.verified'):t('bots.unverified'))+' · '+h.contact.sends+'/3', 'aj-bots')));
      const submit=async(op,text)=>{const to=h.contact.email;if(!await confirmSheet(t('bots.confirm_email_recipient_and_body'),to+'\n\n'+(text||t('bots.send_a_customer_service_verification_code_valid_for_10_minutes')),t('bots.sign_and_send')))return;await write({op,id:c.id,handoff:h.id,to,send_id:crypto.randomUUID().replaceAll('-',''),...(text?{text}:{})});await refresh();};
      if(h.contact.verified){const reply=field(main,t('bots.human_email_body'),'','textarea');main.append(button(t('bots.sign_and_send_reply'),()=>act(()=>submit('human_email_reply',reply.value))));}
      else main.append(button(t('bots.sign_and_send_verification_code'),()=>act(()=>submit('human_email_code'))));
    }
    const m=await read({op:'history',id:c.id});for(const session of m.sessions||[])main.append(button(session.visitor.slice(0,8)+' · '+session.messages,()=>act(async()=>{const m=await read({op:'history',id:c.id,visitor:session.visitor});const history=mk('div','');for(const msg of m.messages||[])history.append(pageCopy(mk('p','',msg.role+': '+text(msg.text,12000), 'aj-bots')));main.append(history);})));});
  else if(tab==='statistics')act(async()=>{const m=await read({op:'statistics',id:c.id});for(const day of m.days||[])main.append(pageCopy(mk('p','',`${day.day}: ${day.calls} ${t('bots.calls')} · ${day.charged_tokens} ${t('bots.charged_reserved_tokens')}`, 'aj-bots')));for(const tool of m.tools||[])main.append(pageCopy(mk('p','',`${tool.day}: ${tool.tool} · ${tool.calls}`, 'aj-bots')));});
  else if(tab==='embed'){
    const url='https://agentj.app/bots/'+c.slug;main.append(pageCopy(mk('p','',t('bots.free_public_url')+': '+url, 'aj-bots')));
    const code=`<script src="https://agentj.app/bots/embed.js" data-bot="${c.slug}" async></script>`;
    main.append(copyable(mk('pre','',code, 'aj-bots'),{label:'r.md.copyAria'}),button(t('bots.copy_embed_code'),()=>act(async()=>{if(!await copyNow(code))throw new Error(t('bots.copy_failed'));})));
    const domains=field(main,t('bots.tool_outbound_domains_comma_separated'),c.outbound_domains.join(',')),origins=field(main,t('bots.allowed_embedding_websites_https_origins_comma_separated'),c.embed_origins.join(','));main.append(button(t('bots.save'),()=>act(async()=>{await write({op:'save',id:c.id,config:{...withoutId(c),outbound_domains:domains.value.split(',').map(x=>x.trim()).filter(Boolean),embed_origins:origins.value.split(',').map(x=>x.trim()).filter(Boolean)}});await refresh();})));
  }
}
function commercialFields(main,c){
  c={...c,privacy:c.privacy||{retention_days:30},memory:c.memory||{history_hours:24,max_turns:40,max_chars:28000},outbound:c.outbound||{link_domains:[],block_commands:true},knowledge:c.knowledge||{cite:true,max_chars:6000},handoff:c.handoff||{reply_window_h:48},messages:c.messages||{},widget:c.widget||{position:'br',accent:'#629887',welcome:'',quick_replies:[],open_on_load:false,queue_merge:true}};
  const group=mk('details'),summary=mk('summary','',t('bots.business_settings_fallback_models_messages_and_widget'), 'aj-bots');group.append(summary);main.append(group);
  const f=(key,v,type='text')=>field(group,t(key),v,type);
  const review=f('bots.review_provider_openrouter_typesafe',c.safety?.provider||'openrouter'),criteria=f('bots.criteria_customer_service_strict',c.safety?.criteria||'customer_service'),after=f('bots.after_hours_refuse_ai_only_leave_message',c.service?.after_hours||'refuse');
  const email=checkbox(group,t('bots.allow_handoff_email_verified_address_sign_each_mail_three_including_verification'),c.handoff.email_enabled);
  const sources=f('bots.scheduled_sources_json_url_name_cite_url_resync',JSON.stringify(c.knowledge.sources||[]),'textarea');
  const handoff=f('bots.human_reply_window_hours',c.handoff.reply_window_h,'number');
  const retention=f('bots.chat_retention_days',c.privacy.retention_days,'number'),hours=f('bots.history_hours',c.memory.history_hours,'number'),turns=f('bots.history_messages',c.memory.max_turns,'number'),context=f('bots.context_byte_limit',c.memory.max_chars,'number');
  const cite=checkbox(group,t('bots.append_source_links'),c.knowledge.cite),knowledge=f('bots.retrieved_characters',c.knowledge.max_chars,'number');
  const domains=f('bots.clickable_answer_domains_comma_separated',c.outbound.link_domains.join(',')),commands=checkbox(group,t('bots.block_dangerous_commands'),c.outbound.block_commands);
  const chain=[];for(let i=0;i<2;i++){
    const e=c.provider.chain?.[i];const vendor=mk('select', undefined, undefined, 'aj-bots');vendor.setAttribute('aria-label',t('bots.fallback_provider',{number:i+1}));const off=mk('option','',t('bots.off'));off.value='';vendor.append(off);for(const v of vendors){const o=mk('option','',copy(v.zh,v.en));o.value=v.id;vendor.append(o);}vendor.value=e?.vendor||'';group.append(vendor);const model=field(group,t('bots.fallback_model',{number:i+1}),e?.model||'');chain.push({vendor,model});
    if(e)group.append(button(t('bots.fallback_key',{provider:vendorName(e.vendor)}),()=>act(async()=>{await write({op:'model_key',id:c.id,vendor:e.vendor});await refresh();})));
  }
  const docs=f('bots.https_documentation_links_if_every_model_fails_one_per_line',(c.provider.docs_links||[]).join('\n'),'textarea');
  const messages={};for(const [k,zh,en] of [['welcome',...both('bots.welcome')],['offtopic',...both('bots.off_topic_reply')],['handoff_sent',...both('bots.handoff_notice')],['unavailable',...both('bots.unavailable_reply')],['outside_hours',...both('bots.outside_hours_reply')],['limit',...both('bots.limit_reply')]]){messages[k]={};for(const l of ['zh','en'])messages[k][l]=field(group,copy(zh+' · '+l,en+' · '+l),typeof c.messages[k]==='string'?c.messages[k]:c.messages[k]?.[l]||'');}
  const position=f('bots.widget_position_br_bl',c.widget.position),accent=f('bots.widget_accent_629887_141414_827dbd_d97757',c.widget.accent),welcome=f('bots.widget_welcome_empty_uses_welcome_above',c.widget.welcome),quick=f('bots.quick_questions_one_per_line_up_to_6',c.widget.quick_replies.join('\n'),'textarea'),auto=checkbox(group,t('bots.open_widget_on_load'),c.widget.open_on_load),queue=checkbox(group,t('bots.merge_queued_messages'),c.widget.queue_merge);
  return ()=>({safety:{provider:review.value,criteria:criteria.value},service:{after_hours:after.value},handoff:{reply_window_h:Number(handoff.value),email_enabled:email.checked},privacy:{retention_days:Number(retention.value)},memory:{history_hours:Number(hours.value),max_turns:Number(turns.value),max_chars:Number(context.value)},knowledge:{cite:cite.checked,max_chars:Number(knowledge.value),sources:JSON.parse(sources.value)},outbound:{block_commands:commands.checked,link_domains:domains.value.split(',').map(x=>x.trim()).filter(Boolean)},provider:{source:c.provider.source,...(c.provider.id?{id:c.provider.id}:{}),chain:chain.filter(e=>e.vendor.value).map(e=>({vendor:e.vendor.value,model:e.model.value})),docs_links:docs.value.split('\n').map(x=>x.trim()).filter(Boolean),thinking:'off'},messages:Object.fromEntries(Object.entries(messages).flatMap(([k,v])=>{const entries=Object.entries(v).filter(([,f])=>f.value.trim()).map(([l,f])=>[l,f.value]);return entries.length?[[k,Object.fromEntries(entries)]]:[];})),widget:{position:position.value,accent:accent.value,welcome:welcome.value,quick_replies:quick.value.split('\n').map(x=>x.trim()).filter(Boolean),open_on_load:auto.checked,queue_merge:queue.checked}});
}
function withoutId(c){const {id,...value}=c;return value;}
function paintProposal(m){
  if(!m||typeof m.id!=='string'||!m.request||typeof m.request!=='object'||m.expires<=Date.now())return;
  const main=el('bots-main');main.replaceChildren(mk('h2','',t('bots.agent_j_proposes_a_bot_change'), 'aj-bots'),copyable(mk('pre','',JSON.stringify(m.request,null,2).slice(0,60000), 'aj-bots'),{click:false}),button(t('bots.approve_this_change'),()=>act(async()=>{if(m.expires<=Date.now())throw new Error('expired');await write(m.request);pendingProposal=null;await refresh();})),button(t('bots.back'),()=>{pendingProposal=null;refresh();}));
}

export function proposal(m){if(!m?.request || typeof m.expires!=='number' || m.expires<=Date.now())return;pendingProposal=m;open();}

export function toolRequest(m){
 if(!m?.digest || typeof m.expires!=='number' || m.expires*1000<=Date.now())return;
 const req={op:'tool_approve',approval:m.id,digest:m.digest,allow:true};
 const main=el('bots-main');hooks.openPanel('bots');++generation;
 main.replaceChildren(mk('h2','',t('bots.approve_a_bot_write_operation'), 'aj-bots'),pageCopy(mk('p','',text(m.tool,40), 'aj-bots')),copyable(mk('pre','',text(m.args,4000), 'aj-bots'),{click:false}),button(t('bots.approve_once'),()=>act(async()=>{await write(req);await refresh();})),button(t('bots.deny'),()=>act(async()=>{await write({...req,allow:false});await refresh();})));
}
