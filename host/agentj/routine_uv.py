"""Narrow data/dispatch adapters for the real 2026-11-06 false-UV shapes.

No execution, source admission, signature, explicit host UV, or native-execution
registry changes. Unknown programs and unsupported shell forms abstain.
"""
from __future__ import annotations
import os
import re

_DATA = frozenset(('cat','head','tail','grep','egrep','fgrep','rg','sed','awk','wc','cut','sort','uniq','tee','echo','printf','pwd','cd','true','false','test','[',':','mkdir','ls','stat','find','date','sleep','which','command','realpath','readlink'))
_HARNESS = frozenset(('codex','claude','opencode'))
_NORMAL = frozenset(('git','uv','uvx','npm','npx','pytest','olares-run','rsync','ssh','cp','mv','chmod','touch','rm','rmdir','trash-put','trash','gio','agentj'))
_BYPASS = ('--dangerously-bypass-approvals-and-sandbox','--dangerously-skip-permissions','--yolo')


def _scope_nodes(text):
    """Only detect rebinding in decoded words; never prove new code safe."""
    from . import danger
    pending=[(text,0)];seen=set();nodes=[];size=0
    while pending:
        source,depth=pending.pop()
        if source in seen:continue
        seen.add(source);size+=len(source)
        if depth>6 or size>262144:return None
        batch=danger.lex(source);nodes.extend(batch)
        if len(nodes)>512:return None
        for command in batch:
            parts=[*command.words,*[d[2] for d in command.docs]]
            pending.extend((part,depth+1) for part in parts if part!=source and any(c in part for c in (';','\n',' ')) and ('=' in part or '$' in part))
    return nodes


def _variable_writes(nodes,key):
    if nodes is None:return None
    from . import danger
    writes=sum(bool(re.fullmatch(re.escape(key)+r'\+?=.*',word,re.S)) for c in nodes for word in c.words)
    variable_printf=any(os.path.basename(s.name)=='printf' and any(a.startswith('-v') for a in s.args) for c in nodes for s in danger.unwrap(c))
    return None if variable_printf else writes


def _home_view(text):
    from . import danger
    if any(s.flags & {'eval','remote','piped_shell'} for c in danger.lex(text) for s in danger.unwrap(c)):return text
    writes=_variable_writes(_scope_nodes(text),'HOME')
    if writes is None or writes:return text
    home=os.path.expanduser('~')
    return re.sub(r'\$(?:HOME\b|\{HOME\})',lambda _:home,text) if re.fullmatch(r'[A-Za-z0-9_./~-]+',home) else text


def _literal_path_bindings(text,cwd):
    """Resolve only single, unconditional path bindings, never variable-selected data."""
    from . import danger,uv_effect,approval_policy
    commands=danger.lex(text)
    # A new language/shell scope can shadow a parent's variable. Do not replace
    # references inside code/document operands using a parent's binding.
    if any(c.docs for c in commands):return text
    for command in commands:
        for simple in danger.unwrap(command):
            if simple.flags & {'eval','remote','piped_shell'} or re.fullmatch(r'python(?:[0-9.]+)?|node|ruby|perl|php',os.path.basename(simple.name)):return text
    writes={};eligible={};prefix_only=True
    for command in commands:
        prefix=[]
        for word in command.words:
            if not re.match(r'[A-Za-z_]\w*=',word):break
            prefix.append(word)
        for word in prefix:
            key,value=word.split('=',1)
            writes[key]=writes.get(key,0)+1
            if prefix_only and len(prefix)==len(command.words) and command.owner is None and not command.subst:
                eligible[key]=value
        if command.owner is None and len(prefix)!=len(command.words):prefix_only=False
        if command.words[:1]==['for'] and len(command.words)>1:
            key=command.words[1];writes[key]=writes.get(key,0)+1
    for key,value in eligible.items():
        if _variable_writes(_scope_nodes(text),key)!=1:continue
        if writes[key]!=1 or approval_policy.secret_name(key) or key in ('PATH','HOME','BASH_ENV','ENV','PYTHONPATH','PYTHONSTARTUP','NODE_OPTIONS','SHELLOPTS','BASHOPTS','IFS','CDPATH','GLOBIGNORE','PS4') or key.startswith('LD_'):continue
        if not re.fullmatch(r'[A-Za-z0-9_./:@+~-]+',value) or not value.startswith(('/','~/')):continue
        path=uv_effect._local_operand(value,cwd)
        if path is None or danger.cred_path(path):continue
        pattern=re.compile(r'\$(?:'+re.escape(key)+r'\b|\{'+re.escape(key)+r'(?::\?)?\})')
        first=pattern.search(text)
        assignment=text.find(key+'='+value)
        # Quoted assignment formatting cannot be located precisely: abstain.
        if first is None or assignment<0 or first.start()<assignment:continue
        text=pattern.sub(lambda _:value,text)
    return text


def _literal_unit_loops(text):
    # Parse executable nodes, never prose supplied to a harness or a quoted doc.
    from . import danger
    loops=[]
    for command in danger.lex(text):
        if command.words[:1] != ['for']:
            continue
        words=command.words
        if len(words)<4 or words[2]!='in' or not re.fullmatch(r'[A-Za-z_]\w*',words[1]):
            return False
        domain=words[3:]
        if len(domain)>16 or not all(re.fullmatch(r'[A-Za-z0-9_./@:+~-]+',word) for word in domain):
            return False
        loops.append((words[1],domain))
    return bool(loops)


def _loop_views(text):
    from . import danger
    views=[text]
    for command in danger.lex(text):
        if command.words[:1]!=['for']:
            continue
        if not _literal_unit_loops(text):
            return None
        variable=command.words[1]
        nodes=danger.lex(text)
        if sum(c.words[:2]==['for',variable] for c in nodes)!=1:return None
        if any(any(re.match(re.escape(variable)+r'=',word) for word in c.words) for c in nodes):return None
        if any(s.flags & {'eval','remote','piped_shell'} for c in nodes for s in danger.unwrap(c)):return None
        if _variable_writes(_scope_nodes(text),variable)!=0:return None
        if any(re.search(r'\$(?:'+re.escape(variable)+r'\b|\{'+re.escape(variable)+r'\})',d[2]) for c in nodes for d in c.docs):return None
        pattern=re.compile(r'\$(?:'+re.escape(variable)+r'\b|\{'+re.escape(variable)+r'\})')
        views=[pattern.sub(lambda _:word,view) for view in views for word in command.words[3:]]
        if len(views)>16:return None
    return views


def _metadata_substitutions(commands):
    from . import danger
    children=[c for c in commands if c.owner is not None]
    if not children:return False
    for command in children:
        if command.docs or command.redirs:return False
        simple=danger.unwrap(command)
        if len(simple)!=1:return False
        node=simple[0];name=os.path.basename(node.name)
        if name not in ('date','printf'):return False
        if any('$' in a or '`' in a for a in node.args):return False
        if name=='date' and any(a in ('-f','--file','-s','--set') or a.startswith(('--file=','--set=')) for a in node.args):return False
        if name=='printf' and node.args[:1]==['-v']:return False
    return True


def _script_body(path,cwd,commands):
    # A pathname does not bind the bytes later executed by the harness. Even a
    # literal heredoc producer leaves a same-UID writer/replacement race. Only
    # scripts carried directly in argv/stdin may qualify for routine projection.
    return None


def _python_program(simple,command,commands,cwd):
    import shlex
    program=shlex.join(simple.raw)
    if len(command.docs)==1 and simple.raw[:1]==command.words[:1]:
        _,quoted,body,*_=command.docs[0]
        if not quoted and any(c in body for c in '$`'):return None
        program+=" <<'__AGENTJ_LITERAL_PY__'\n"+body+"\n__AGENTJ_LITERAL_PY__"
    elif len(simple.args)==1 and not simple.args[0].startswith('-'):
        body=_script_body(simple.args[0],cwd,commands)
        if body is None:return None
        program=shlex.quote(simple.name)+" - <<'__AGENTJ_LITERAL_PY__'\n"+body+"\n__AGENTJ_LITERAL_PY__"
    return program


def _script_views(text,cwd,depth=0):
    from . import danger,uv_effect
    if depth>6:return None
    text=_home_view(text)
    bodies=[];commands=danger.lex(text)
    for command in commands:
        for simple in danger.unwrap(command):
            if os.path.basename(simple.name)=='cd':
                if len(simple.args)!=1:return None
                target=uv_effect._local_operand(simple.args[0],cwd)
                if target is None or not os.path.isdir(target):return None
                cwd=str(target)
            if re.fullmatch(r'python(?:[0-9.]+)?',os.path.basename(simple.name)) and len(simple.args)==1 and not simple.args[0].startswith('-'):
                program=_python_program(simple,command,commands,cwd)
                if program is None:return None
                bodies.append(program)
                continue
            if os.path.basename(simple.name) not in ('bash','sh') or any(re.fullmatch(r'-[A-Za-z]*c[A-Za-z]*',a) for a in simple.args):continue
            if len(simple.args)!=1:return None
            body=_script_body(simple.args[0],cwd,commands)
            if body is None or not _closed(body,cwd,depth+1):return None
            children=_script_views(body,cwd,depth+1)
            if children is None:return None
            bodies.extend([body,*children])
    return bodies


def _closed(text, cwd=None, depth=0):
    from . import danger, uv_effect
    if depth > 6:
        return False
    text=_literal_path_bindings(_home_view(text),cwd)
    saw = False
    # Startup and name-resolution mutations are executable scope, never routine
    # by a transport or a copied program name alone.
    commands=danger.lex(text)
    if any(re.match(r'(?:PATH|HOME|BASH_ENV|ENV|LD_[A-Z_]+|PYTHONPATH|PYTHONSTARTUP|NODE_OPTIONS|SHELLOPTS|BASHOPTS|IFS|CDPATH|GLOBIGNORE|PS4)=', word) for command in commands for word in command.words):
        return False
    metadata=_metadata_substitutions(commands)
    for command in commands:
        if command.subst and not metadata:
            return False
        for simple in danger.unwrap(command):
            saw = True
            name = os.path.basename(simple.name)
            if name=='cd':
                if len(simple.args)!=1:return False
                target=uv_effect._local_operand(simple.args[0],cwd)
                if target is None or not os.path.isdir(target):return False
                cwd=str(target)
            if any(re.search(r'\$(?:[A-Za-z_]|\{)',a) for a in simple.args) and name not in _HARNESS and name not in ('echo','printf','systemctl','systemd-run','bash','sh','for','while','until','if','do') and not re.fullmatch(r'python(?:[0-9.]+)?',name):
                if name!='cp' or not _literal_unit_loops(text):return False
            if name in ('for','do','done','in','while','until','if','then','else','elif','fi'):
                loop=_literal_unit_loops(text)
                if not loop:
                    code=danger.executable_text(text)
                    # Literal metadata polling has no variable/path binding.
                    # Conditions and bodies are still independently traversed.
                    if name=='for' or not re.search(r'\b(?:while|until|if)\b',code):
                        return False
                continue
            if name == 'systemd-run':
                if '--user' not in simple.args or any(x in ('-E','--setenv','--system') or x.startswith(('--setenv=','--property=Environment')) for x in simple.args):
                    return False
                continue
            if name == 'systemctl':
                if any(a in ('start','stop','restart','try-restart','reload','reload-or-restart','kill','reset-failed') for a in simple.args):
                    return False  # mutable service control needs its live Host proof
                if '--user' not in simple.args or any(x in ('enable','disable','mask','unmask','link','edit','daemon-reload','set-environment','import-environment') for x in simple.args):
                    return False
                continue
            if re.fullmatch(r'python(?:[0-9.]+)?',name):
                # Literal file/text AST only; arbitrary Python remains the prior policy's responsibility.
                program=_python_program(simple,command,commands,cwd)
                if program is None or uv_effect._python_file_program(program,cwd) is None:return False
                continue
            if name in ('bash','sh'):
                flag=next((i for i,a in enumerate(simple.args) if re.fullmatch(r'-[A-Za-z]*c[A-Za-z]*',a)),None)
                if flag is None:
                    if len(simple.args)!=1:return False
                    body=None if '>>' in text else _script_body(simple.args[0],cwd,commands)
                    if body is None or not _closed(body,cwd,depth+1):return False
                elif flag+1>=len(simple.args) or not _closed(simple.args[flag+1],cwd,depth+1):return False
                continue
            if name=='set' and all(a in ('-e','-u','-eu','-ue','-o','pipefail') for a in simple.args):continue
            if name=='exit' and (not simple.args or len(simple.args)==1 and simple.args[0].isdigit()):continue
            if name in _DATA or name in _HARNESS or name in _NORMAL:
                continue
            return False
    return saw


def proven_routine(tool, inp, *, env=None, cwd=None):
    """An ordinary proof or False; never infer permission from a supplied role/name."""
    from . import danger, uv_effect
    if not isinstance(inp,dict):
        return False
    op = tool.split('__')[-1] if isinstance(tool,str) else ''
    if op in ('Write','Edit','MultiEdit','NotebookEdit'):
        path=inp.get('file_path',inp.get('path',inp.get('notebook_path')))
        if not isinstance(path,str) or uv_effect._local_operand(path,cwd) is None:
            return False
        return uv_effect._file_operand_effect(op,inp,'',cwd) is None and not uv_effect._state_write(op,inp,'',cwd)
    if op not in ('Bash','exec_command','shell_command','execute_command','bash','Monitor'):
        return False
    text=inp.get('command',inp.get('cmd'))
    if not isinstance(text,str):
        return False
    text=_literal_path_bindings(_home_view(text),cwd)
    direct=uv_effect._python_file_program(text,cwd)
    if direct is not None:
        return direct.reason is None
    if not _closed(text,cwd):
        return False
    # A transient child's requested harness mode does not rewrite the parent's
    # permission root. Only this transport's executable flags are ignored here;
    # adjacent reads, writes, expansion, delivery and destruction are still assessed.
    bodies=_script_views(text,cwd)
    if bodies is None:return False
    normalized=_home_view('\n'.join([text,*bodies]))
    if any(os.path.basename(s.name)=='systemd-run' for c in danger.lex(text) for s in danger.unwrap(c)):
        for flag in _BYPASS:
            normalized=normalized.replace(flag,'')
    # Restart authority belongs to restart_guard's live proof, not this syntax
    # classifier. Unproven restarts retain the existing approval floor.
    try:
        views=_loop_views(normalized)
        if views is None:return False
        return all(uv_effect.assess('Bash',{'command':view},env=env,cwd=cwd).reason is None for view in views)
    except Exception:
        return False
