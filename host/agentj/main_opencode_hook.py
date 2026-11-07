"""Native OpenCode v2 execute.before bridge for #579; no model/permission edits."""
import json
import os
from pathlib import Path
import sys
from . import shared_hook

HEADER = '// Agent J native dispatch budget v1. Native permissions remain authoritative.\n'


def install(directory, channel):
    root = Path(directory)
    path = root / '.opencode/plugins/agentj-dispatch/index.js'
    if any(part.is_symlink() for part in (root/'.opencode',root/'.opencode/plugins',path.parent,path)):
        raise ValueError('dispatch_plugin_symlink')
    script = r'''
import {spawn} from 'node:child_process';
const python=PYTHON, hook=HOOK, channel=CHANNEL;
function request(event){
  return new Promise((resolve,reject)=>{
    const child=spawn(python,[hook,channel],{stdio:['pipe','pipe','ignore']});
    let out='';const timer=setTimeout(()=>{child.kill();reject(new Error('Agent J dispatch guard unavailable'));},15000);
    child.stdout.on('data',d=>{out+=d;if(out.length>32768){child.kill();reject(new Error('Agent J dispatch guard response too large'));}});
    child.on('error',()=>{clearTimeout(timer);reject(new Error('Agent J dispatch guard unavailable'));});
    child.on('exit',code=>{clearTimeout(timer);if(code!==0)return reject(new Error('Agent J dispatch guard unavailable'));try{resolve(JSON.parse(out.trim()||'{}'));}catch{reject(new Error('Agent J dispatch guard invalid'));}});
    child.stdin.end(JSON.stringify(event));
  });
}
export default {
 id:'agentj.dispatch-budget',
 async setup(ctx){
  const directory=ctx.location.directory;
  await request({hook_event_name:'SharedGuardReady',cwd:directory});
  await ctx.session.hook('prompt',async event=>{
    await request({hook_event_name:'UserPromptSubmit',session_id:event.sessionID,cwd:directory});
    await ctx.session.instructions.entry.remove({sessionID:event.sessionID,key:'agentj.dispatch-budget'});
  });
  await ctx.tool.hook('execute.before',async event=>{
    const names={shell:'Bash',bash:'Bash',read:'Read',write:'Write',edit:'Edit',glob:'Glob',grep:'Grep',webfetch:'WebFetch',task:'Task',subagent:'Task'};
    const raw=typeof event.tool==='string'?event.tool:event.tool?.id;
    const input={...(event.input||{})};if(input.filePath)input.file_path=input.filePath;
    const result=await request({hook_event_name:'PreToolUse',session_id:event.sessionID,cwd:directory,
      tool_name:names[raw]||raw||'?',tool_input:input});
    const output=result.hookSpecificOutput||{};
    if(output.additionalContext)await ctx.session.instructions.entry.put({sessionID:event.sessionID,key:'agentj.dispatch-budget',value:output.additionalContext});
    if(output.permissionDecision==='deny'||output.permissionDecision==='ask')throw new Error(output.permissionDecisionReason||'Agent J dispatch budget refused this main-thread work');
  });
 }
};
'''.replace('PYTHON',json.dumps(sys.executable)).replace('HOOK',json.dumps(str(Path(shared_hook.__file__).resolve()))).replace('CHANNEL',json.dumps(str(channel)))
    data = HEADER + script
    if path.exists() and not path.read_text().startswith(HEADER):
        raise ValueError('dispatch_plugin_owned_elsewhere')
    if not path.exists() or path.read_text() != data:
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        temporary = path.with_suffix('.js.agentj-tmp')
        fd = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as handle:
            handle.write(data)
        os.replace(temporary,path)
    return path


async def prepare(adapter):
    if adapter.cfg.get('_workflow_ceo') is True:
        return
    from . import main_thread
    main_thread.of(adapter)  # expose guard_pending before the external native server loads this plugin
    if getattr(adapter,'risk_channel',None) is None:
        adapter.risk_channel=shared_hook.Channel(adapter.risk,shared_hook.channel_path(adapter.host.st.perm_dir,'opencode'))
        await adapter.risk_channel.start()
    install(adapter.cfg['dir'],adapter.risk_channel.path)
