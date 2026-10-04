"""Native OpenCode tool-before wrapper around the shared danger classifier.

No rules, tool arguments or native permission answers are changed by this plugin.
The existing permission.asked path still forwards the owner's native approvals.
"""
import json
import os
from pathlib import Path
import sys
from . import shared_hook


def install(directory, channel):
    path=Path(directory)/'.opencode/plugins/agentj-shared.js'
    if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
        raise ValueError('OpenCode plugin symlink refused')
    data='''// Agent J owned shared pre-execution hook. Native permissions remain authoritative.
import { spawn } from 'node:child_process';
const python=PYTHON, hook=HOOK, channel=CHANNEL;
function request(event) {
  return new Promise((resolve,reject)=>{
    const child=spawn(python,[hook,channel],{stdio:['pipe','pipe','ignore']});
    let out='';
    child.stdout.on('data',d=>{out+=d;if(out.length>2097152)child.kill();});
    child.on('error',()=>reject(new Error('Agent J guard unavailable')));
    child.on('exit',code=>{
      if(code!==0)return reject(new Error('Agent J guard unavailable'));
      try{resolve(JSON.parse(out.trim()||'{}'));}catch{reject(new Error('Agent J guard invalid'));}
    });
    child.stdin.end(JSON.stringify(event));
  });
}
export const AgentJShared = async ({directory}) => {
  await request({hook_event_name:'SharedGuardReady',cwd:directory});
  return {'tool.execute.before':async(input,output)=>{
    const names={bash:'Bash',read:'Read',write:'Write',edit:'Edit',glob:'Glob',grep:'Grep',webfetch:'WebFetch'};
    const args={...output.args};if(args.filePath)args.file_path=args.filePath;
    const result=await request({hook_event_name:'PreToolUse',session_id:input.sessionID,
      cwd:directory,tool_name:names[input.tool]||('mcp__'+input.tool),tool_input:args});
    if(result.hookSpecificOutput?.permissionDecision==='deny')throw new Error('Denied by paired phone');
  }};
};
'''.replace('PYTHON',json.dumps(sys.executable)).replace('HOOK',json.dumps(str(Path(shared_hook.__file__).resolve()))).replace('CHANNEL',json.dumps(str(channel)))
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.exists() and not path.read_text().startswith('// Agent J owned shared pre-execution hook.'):
        raise ValueError('OpenCode plugin name is owned by another file')
    if not path.exists() or path.read_text()!=data:
        temp=path.with_suffix('.js.agentj-tmp')
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(data)
        os.replace(temp,path)
    return path
