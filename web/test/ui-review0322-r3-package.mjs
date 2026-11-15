// Publish review copies only after every captured scene and layout assertion passes.
import {readFileSync,writeFileSync,mkdirSync,copyFileSync} from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root=process.env.OLARES_RUN_OUT,shots=join(root,'shots'),out=join(root,'review-r3');
const index=JSON.parse(readFileSync(join(shots,'index.json'))),audit=JSON.parse(readFileSync(join(shots,'assertions.json')));
if(audit.failed||index.rows.some(r=>r.error||r.problems.length))throw new Error('Captured matrix must pass before review export');
const items=[
 ['U01','account-links','账号后台说明自动链接至账户页。'],
 ['U02','update-status','升级方式显示主机当前状态。'],
 ['U03','auth-checking','正在检查登录与 Relay 双密钥卡之间保留24px段落间距。'],
 ['U04','secret-entry','原密钥掩码输入保留，复制按钮有独立圆角和内距，不提供与保存等宽。'],
 ['U05','bot-api','客服机器人显示已放行的API页签。'],
 ['U06','friend-card','好友审批标题与收起在卡内同一行，顶部留白加大，引用信息恢复旧灰。'],
 ['U07','uninstall-card','广场卸载审批显示专用标题及备份说明。'],
 ['U08','kimi','Kimi授权指引显示已放行的新说明。'],
 ['U09','notices','即时操作通知保留，普通通知按队列显示。'],
 ['U10','popup-notice','通知避让当前密钥卡，保存保持Lotion底Noir字。'],
 ['U11','session-note','重启Agent说明包含额度恢复和更换模型提示。'],
 ['U12','host-reason','重置失败显示主机固定原因。'],
 ['U13','passkey','不合格验证密钥显示新说明与两色实底按钮。'],
 ['U14','eviction','替换遥控器成功回执在2.9秒后仍保留等待确认。'],
 ['U15','media-punct','附件拒绝卡清理重复标点。'],
 ['U16','form-invalid','表单无效输入启用aria-invalid，保持既有错误呈现。'],
 ['U03a','models-keys','模型页 Relay 双密钥专名无重复。'],
 ['U03b','relay-keys','备用账号 Relay 卡按现有间距体系分隔。'],
 ['U08b','kimi-timeout','登录检查90秒后给出超时说明。'],
 ['U11b','rescue-notes','重置连接说明包含手机离线时的处理方式。'],
 ['G01','plain-approval','普通审批收起完整落在标题行内，信息块恢复基线灰色。'],
 ['G02','choice-card','选择卡使用两种实底按钮。'],
 ['G03','main','主页面无新增可见行或控件。'],
];
mkdirSync(out,{recursive:true});const files=[];
for(const [id,name,note] of items){
 for(const theme of ['light','dark']){
  const src='compare-'+theme+'-390-'+name+'.png',dst=id+'-对比-'+theme+'-390.png';
  copyFileSync(join(shots,src),join(out,dst));
  files.push({file:dst,description:note+' 左0.30.21，右r3；'+theme+'，390px。',kind:'comparison',before_ref:index.refs.before,after_ref:index.refs.after,sha256:createHash('sha256').update(readFileSync(join(out,dst))).digest('hex')});
 }
 if(/^U\d\d$/.test(id)){
  const dst=id+'-最终-light-390.png',src='after-light-390-'+name+'.png';copyFileSync(join(shots,src),join(out,dst));
  files.push({file:dst,description:note+' r3最终状态；light，390px。',kind:'final',source_ref:index.refs.after,sha256:createHash('sha256').update(readFileSync(join(out,dst))).digest('hex')});
 }
}
const public_tree=execFileSync('git',['rev-parse',index.refs.after+':web/public'],{encoding:'utf8'}).trim();
writeFileSync(join(out,'index.json'),JSON.stringify({refs:index.refs,public_tree,assertions:{passed:audit.passed,failed:audit.failed},files},null,2));
writeFileSync(join(out,'README.md'),'r3界面审核：实际0.30.21改前 / r3改后，390px明暗对比及U01至U16最终light图。\n\n'+files.map(f=>'- '+f.file+' — '+f.description).join('\n')+'\n');
console.log('REVIEW EXPORT',files.length,'PNG');
