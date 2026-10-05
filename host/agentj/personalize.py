"""Semantic aliases for preferences; all writes use the single transactional writer."""
import argparse
import json
import os
import sys
from pathlib import Path
from . import preferences as p

def command(args):
    group=args[0]; args=args[1:]
    parser=argparse.ArgumentParser(prog='agentj '+group)
    parser.add_argument('action',nargs='?',default='list');parser.add_argument('value',nargs='?')
    parser.add_argument('--owner-id',type=int);parser.add_argument('--key-env',default='TELEGRAM_BOT_TOKEN');parser.add_argument('--tts');parser.add_argument('--gender');parser.add_argument('--lang');parser.add_argument('--text');parser.add_argument('--say');parser.add_argument('--label');parser.add_argument('--run');parser.add_argument('--json',action='store_true');parser.add_argument('--owner-confirmed',action='store_true')
    a=parser.parse_args(args)
    if group=='voice':
        if a.action=='wake':return p.command(['set','voice.wake_word',a.value or ''])
        if a.action=='set' and a.tts:return p.command(['set','voice.tts.voice',a.tts])
        if a.action=='test-wake':
            from .state import State
            expected=p.get(p.effective(State()),'voice.wake_word')
            # This is a text match; expressly not acoustic acceptance.
            normalize=lambda x:''.join(c for c in x.casefold() if c.isalnum())
            ok=normalize(a.text or '')==normalize(expected)
            print(json.dumps({'ok':ok,'expected':expected,'test':'text comparison only; use APK microphone for acoustic test'},ensure_ascii=False));return 0 if ok else 1
        if a.action=='test' and a.say:
            from .voice import synthesize
            from .state import State
            try:
                st=State();data=synthesize(a.say,p.effective(st));target=st.root/'voice-test.wav';target.parent.mkdir(parents=True,exist_ok=True)
                st.write_private(target,data);print(json.dumps({'ok':True,'audio':str(target),'needs':['listen to sample']}));return 0
            except p.ConfigError as e:print(json.dumps(e.result()));return e.code
        if a.action=='list':
            print(json.dumps({'ok':True,'voices':[],'detail':'Phone voices: speechSynthesis.getVoices().filter(v=>v.localService). Host: say -v ? or espeak-ng --voices. Cloud: selected provider voice IDs. No invented gender tags.'}));return 0
        return p.command(['validate','--json'])
    if group=='theme':return p.command(['set','appearance.theme',a.value or a.action])
    if group in ('menu','key','channel'):
        key={'menu':'menu.items','key':'keyboard.bindings','channel':'channels.items'}[group]
        try:
            rows=p.get(p.read()[1],key,[])
            if a.action=='list':print(json.dumps({'ok':True,'items':p.get(p.effective(),key)},ensure_ascii=False));return 0
            if group=='menu' and a.action=='add':
                rows=p.merge(rows,[{'id':a.value,'cmd':'/'+(a.run or a.value).removeprefix('task:').lstrip('/'),'desc':a.label or a.value}])
            elif group=='menu' and a.action=='remove':rows=[x for x in rows if x['id']!=a.value]+[{'id':a.value,'disabled':True}]
            elif group=='key' and a.action=='bind':
                if not a.value or not a.run:raise p.ConfigError(key,'use key bind <combo> --run <allowed action>')
                rows=p.merge(rows,[{'id':a.value.replace('+','-'),'combo':a.value,'action':a.run}])
            elif group=='channel' and a.action=='add' and a.value=='telegram':
                from .telegram import enroll
                result=enroll(a.owner_id,a.key_env)
                if not result['ok']:print(json.dumps(result));return 2
                print(result['notice'],file=sys.stderr)
                rows=p.merge(rows,[{'id':'telegram','type':'telegram'}])
            elif group=='channel' and a.action=='remove':
                rows=[x for x in rows if x['id']!=a.value]+[{'id':a.value,'disabled':True}]
            elif group=='channel':raise p.ConfigError(key,'use channel add telegram --owner-id <your ID>; only the owner can enroll')
            else:raise p.ConfigError(key,'unsupported action')
            return p.command(['set',key,json.dumps(rows,ensure_ascii=False),'--json-value'])
        except p.ConfigError as e:print(json.dumps(e.result()));return e.code
    if group=='skill':return skill(a)
    return 1

SKILLS=Path(__file__).with_name('skills')
HARNESS_DIRS=('.claude/skills','.codex/skills','.agents/skills','.config/opencode/skills')

def bundled():
    """Every skill shipped in the package (P57: generic — a new folder under agentj/skills/ with a SKILL.md ships itself)."""
    try:return sorted(d.name for d in SKILLS.iterdir() if d.is_dir() and (d/'SKILL.md').is_file() and not d.name.startswith(('.','_')))
    except OSError:return []

def links(home=None):
    """[(skill, source, target)] for every bundled skill × the four harness skill folders."""
    home=Path(home) if home else Path.home()
    return [(n,SKILLS/n,home/h/n) for n in bundled() for h in HARNESS_DIRS]

def status(home=None):
    out=[]
    for name,source,target in links(home):
        own=target.is_symlink() and target.resolve()==source.resolve()
        out.append({'skill':name,'path':str(target),'installed':own,'conflict':os.path.lexists(target) and not own})
    return out

def ensure(home=None,force=False):
    """P57: `serve` links every bundled skill where nothing is in the way yet, before the Agent starts — the main Agent's
    skills (agentj-config, agentj-recall, agentj-manual) work without anyone running `agentj skill install`. A foreign file or
    link at a target is never replaced; `agentj skill uninstall` removes ours (they come back at the next serve start only
    where the target is free again). → {"linked": n, "conflicts": n}; never raises."""
    # Only an installed package links by itself: a source checkout (host/pyproject.toml beside the package — tests, e2e suites,
    # development) never writes into the real ~/.claude etc.; AGENTJ_SKILL_LINK=off turns it off anywhere (tests/_hermetic.py).
    if not force and (os.environ.get('AGENTJ_SKILL_LINK')=='off' or (SKILLS.parent.parent/'pyproject.toml').is_file()):
        return {'linked':0,'conflicts':0,'off':True}
    linked=conflicts=0
    for _name,source,target in links(home):
        try:
            if target.is_symlink() and target.resolve()==source.resolve():continue
            if os.path.lexists(target):conflicts+=1;continue
            target.parent.mkdir(parents=True,exist_ok=True);target.symlink_to(source);linked+=1
        except OSError:conflicts+=1
    return {'linked':linked,'conflicts':conflicts}

def skill(a):
    # F14: no owner-confirmed / terminal gate (--owner-confirmed still accepted). Each link must point at this package's own
    # skill folder; a foreign file or link at a target is a conflict, never replaced. P57: every bundled skill, not only
    # agentj-config (agentj-recall, agentj-manual, …).
    if a.action not in ('install','uninstall','list','status'):return 1
    out=[]
    for name,source,target in links():
        own=target.is_symlink() and target.resolve()==source.resolve()
        if a.action=='install':
            if not os.path.lexists(target):target.parent.mkdir(parents=True,exist_ok=True);target.symlink_to(source);own=True
        elif a.action=='uninstall' and own:target.unlink();own=False
        out.append({'skill':name,'path':str(target),'installed':own,'conflict':os.path.lexists(target) and not own})
    print(json.dumps({'ok':not any(x['conflict'] for x in out),'skills':bundled(),'harnesses':out}));return 1 if any(x['conflict'] for x in out) else 0
