"""Stand-in loaders execute the generated real local guard; no mocked Host decision."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tomllib


def codex_commands():
    commands=[]
    for arg in sys.argv[1:]:
        if arg.startswith('hooks.PreToolUse='):
            parsed=tomllib.loads(arg)
            commands.extend(hook['command'] for group in parsed['hooks']['PreToolUse'] for hook in group['hooks'])
    return commands


def codex_listing(cwds):
    return {'data':[{'cwd':cwd,'hooks':[{'command':command,'enabled':True,'handlerType':'command',
        'eventName':'preToolUse','key':'fixture-'+hashlib.sha256(command.encode()).hexdigest(),
        'currentHash':hashlib.sha256(command.encode()).hexdigest()} for command in codex_commands()]} for cwd in cwds]}


def exchange(argv,event):
    result=subprocess.run(argv,input=json.dumps(event),text=True,capture_output=True,timeout=180)
    if result.returncode:
        return False
    output=json.loads(result.stdout) if result.stdout.strip() else {}
    return output.get('hookSpecificOutput',{}).get('permissionDecision') not in ('deny','ask') and output.get('decision') != 'block'


def codex_pretool(tool,inp,session,cwd):
    return all(exchange(shlex.split(command),{'hook_event_name':'PreToolUse','tool_name':tool,
        'tool_input':inp,'session_id':session,'cwd':cwd}) for command in codex_commands())


def opencode_command(cwd):
    path=Path(cwd)/'.opencode/plugins/agentj-dispatch/index.js'
    if not path.is_file():
        return None
    line=next((line for line in path.read_text().splitlines() if line.startswith('const python=')),'')
    match=re.fullmatch(r'const python=(".*?"), hook=(".*?"), channel=(".*?");',line)
    return [json.loads(part) for part in match.groups()] if match else None


def opencode_event(cwd,name,session=None,tool=None,inp=None):
    argv=opencode_command(cwd)
    if argv is None:
        return True
    event={'hook_event_name':name,'cwd':cwd,'session_id':session}
    if tool is not None:event.update(tool_name=tool,tool_input=inp)
    return exchange(argv,event)
