// Display only: a quoted operation or arbitrary command can never select this UI.
export function friendsTellCard(e,p){
 if(p?.envelope_verified!==true||p.tool!=='FriendsTellOnce'||p.passkey_required!==true||p.envelope!==e||p.summary!==e?.details)return null;
 try{
  const prefix='FriendsTellOnce\n';if(e.action!=='FriendsTellOnce'||!e.details.startsWith(prefix))return null;
  let body=e.details.slice(prefix.length);
  if(body.startsWith('Working directory: ')){
   const native=/^Working directory: (\/[^\r\n\u0000]{0,4095})\n(\{[\s\S]*\})$/.exec(body);
   if(!native)return null;body=native[2];
  }
  const o=JSON.parse(body),hash=x=>typeof x==='string'&&/^[0-9a-f]{64}$/.test(x);
  const keys=new Set(['schema','operation','scope','friend_id','friend_name','text','reply_to','friend_binding_sha256','text_sha256','request_nonce','policy','description','effect']);
  if(Object.keys(o).some(k=>!keys.has(k))||o.schema!==1||o.operation!=='friends_tell_once'||o.scope!=='one_message_one_friend'||o.policy!=='no_shell_no_permission_change'
    ||typeof o.friend_id!=='string'||!o.friend_id||o.friend_id.length>128||typeof o.friend_name!=='string'||o.friend_name.length>160
    ||typeof o.text!=='string'||!o.text||o.text.length>6000||!(o.reply_to===null||typeof o.reply_to==='string'&&/^[0-9a-f]{16}$/.test(o.reply_to))
    ||!hash(o.friend_binding_sha256)||!hash(o.text_sha256)||!(/^[0-9a-f]{32}$/.test(o.request_nonce)))return null;
  return o;
 }catch{return null;}
}
