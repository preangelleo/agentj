"""Semantic aliases for preferences; all writes use the single transactional writer."""
import argparse
import json
import os
import sys
from pathlib import Path
from . import preferences as p
from . import skill_links

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
            elif group=='channel' and a.action in ('add','pair','rebind') and a.value=='telegram' and a.owner_id is None:
                return pair_telegram(a.key_env)
            elif group=='channel' and a.action=='add' and a.value=='telegram':
                from .telegram import enroll
                result=enroll(a.owner_id,a.key_env)
                if not result['ok']:print(json.dumps(result));return 2
                print(result['notice'],file=sys.stderr)
                rows=p.merge(rows,[{'id':'telegram','type':'telegram'}])
            elif group=='channel' and a.action=='remove':
                rows=[x for x in rows if x['id']!=a.value]+[{'id':a.value,'disabled':True}]
            elif group=='channel':raise p.ConfigError(key,'use agentj channel add telegram: the paired phone shows a one-time code the owner sends to the bot')
            else:raise p.ConfigError(key,'unsupported action')
            return p.command(['set',key,json.dumps(rows,ensure_ascii=False),'--json-value'])
        except p.ConfigError as e:print(json.dumps(e.result()));return e.code
    if group=='skill':return skill(a)
    return 1

def pair_telegram(key_env):
    """P283: ask the running serve for a one-time Telegram pairing code. The code appears only on the paired phone's card;
    whoever sends `/start <code>` to the bot in a private chat becomes the owner. No numeric ID to look up anywhere."""
    from .state import State
    from .names import ctl_call, ServeBusy, control_unavailable
    st=State()
    try:res=ctl_call(st,{'cmd':'telegram_pair','key_env':key_env},30) if st.exists() else None
    except ServeBusy as e:res=e.response()
    if res is None:
        print(json.dumps(control_unavailable(), ensure_ascii=False));return 2
    if not res.get('ok'):
        print(json.dumps({k:res[k] for k in ('ok','error','detail') if k in res}));return 2
    res.pop('code',None)
    bot='@'+res['bot'] if res.get('bot') else 'your bot'
    res['next']=(f'The pairing code is on your paired phone (valid {res.get("expires_in",600)//60} minutes, one use). In Telegram send {bot}: '
                 '/start <code>. That private chat becomes the owner; Agent J then says on Telegram that it is connected.')
    print(json.dumps(res,ensure_ascii=False));return 0

SKILLS=Path(__file__).with_name('skills')
HARNESS_DIRS=('.claude/skills','.codex/skills','.agents/skills','.config/opencode/skills')

def bundled():
    """Frozen built-in names. New skills belong in the plaza."""
    try:return json.loads(Path(__file__).with_name('bundled_skills.json').read_text())['skills']
    except (OSError,ValueError,KeyError):return []

def links(home=None):
    """[(skill, source, target)] for every bundled skill × the four harness skill folders."""
    home=Path(home) if home else Path.home()
    return [(n,SKILLS/n,home/h/n) for n in bundled() for h in HARNESS_DIRS]

def status(home=None):
    out=[]
    for name,source,target in links(home):
        kind,_=skill_links.classify(name,source,target)
        out.append({'skill':name,'path':str(target),'installed':kind=='installed',
                    'conflict':kind=='conflict','legacy':kind=='legacy','kept':False})
    return out

def ensure(home=None,force=False):
    """Install all bundled names automatically, with recoverable backups; never raises."""
    # Only an installed package links by itself: a source checkout (host/pyproject.toml beside the package — tests, e2e suites,
    # development) never writes into the real ~/.claude etc.; AGENTJ_SKILL_LINK=off turns it off anywhere (tests/_hermetic.py).
    if not force and (os.environ.get('AGENTJ_SKILL_LINK')=='off' or (SKILLS.parent.parent/'pyproject.toml').is_file()):
        return {'linked':0,'conflicts':0,'off':True}
    linked=conflicts=0
    for name,source,target in links(home):
        try:
            if skill_links.link(name,source,target):linked+=1
            if skill_links.classify(name,source,target)[0]=='conflict':conflicts+=1
        except (OSError,ValueError,RuntimeError) as exc:
            conflicts+=1
            try:skill_links._save(target.parent,name,{'error':'directory_permission_denied' if isinstance(exc,PermissionError) else 'update_failed','errno':getattr(exc,'errno',None)})
            except OSError:pass
    return {'linked':linked,'conflicts':conflicts}

def skill(a):
    # Package-managed entries update automatically; no owner decision queue.
    if a.action not in ('install','uninstall','list','status'):return 1
    names=bundled();errors=0
    for name,source,target in links():
        try:
            kind,_=skill_links.classify(name,source,target)
            if a.action=='install':skill_links.link(name,source,target)
            elif a.action=='uninstall' and kind=='installed':
                if target.is_symlink():target.unlink()
                else:
                    for rel in skill_links.logic_files(source):
                        path=target/rel
                        if skill_links.points_to(path,source/rel):path.unlink()
                    try:target.rmdir()
                    except OSError:pass
        except (OSError,ValueError,RuntimeError):errors+=1
    out=status()
    ok=not errors and (a.action not in ('install',) or all(r['installed'] for r in out))
    print(json.dumps({'ok':ok,'skills':names,'harnesses':out}));return 0 if ok else 1


def summary(home=None,language="zh"):
    updated=set();modified=set();folders=set()
    for name,source,target in links(home):
        row=skill_links._read(target.parent).get(name,{})
        if not isinstance(row,dict) or not row.get('updated'):continue
        updated.add(name)
        if row.get('modified') and row.get('backup'):
            modified.add(name);folders.add(str(Path(row['backup']).parent))
    if not updated:
        return 'Built-in skills are up to date' if language=='en' else '内置技能已是新版，用户配置已保留'
    where='、'.join(sorted(folders))
    if language=='en':
        return (f'Updated {len(updated)} built-in skills; {len(modified)} modified copies backed up; user configuration preserved' +
                (f'; recover them in {where}' if folders else ''))
    return (f'已更新 {len(updated)} 个内置技能，你改过的 {len(modified)} 个已另存备份，用户配置已保留' +
            (f'，可在 {where} 找回' if folders else ''))
