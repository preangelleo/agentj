"""Voice providers execute on the owner's host. No Agent J API handles plaintext audio.
Cloud is opt-in, uses named environment variables, fixed official endpoints, bounded bodies,
and errors never echo provider responses or keys. No retries that could double-charge.
"""
import json
import io
import wave
import re
import os
import pathlib
import shutil
import subprocess
import urllib.request
import uuid
import sys
from . import preferences as p

ASR_URL='https://openrouter.ai/api/v1/audio/transcriptions'
TTS_URL='https://api.openai.com/v1/audio/speech'
ASR_OPENAI_URL='https://api.openai.com/v1/audio/transcriptions'
TTS_ELEVEN_URL='https://api.elevenlabs.io/v1/text-to-speech/'

def has_key(name): return bool(os.environ.get(name))

def validate_runtime(cfg):
    if any(x.get("type")=="telegram" for x in p.get(cfg,"channels.items",[])):
        from .telegram import configuration
        if not configuration():raise p.ConfigError("channels.items","Telegram needs owner enrollment and a local bot key; run agentj channel add telegram in your terminal",code=2)
    if p.get(cfg,"voice.wake_enabled"):
        from . import wake
        from .state import State
        wake.keyword(p.get(cfg,"voice.wake_word") or "嘿 "+(State().agent_name() or "Agent J"),p.get(cfg,"voice.wake_pronunciation",""))
    if p.get(cfg,'voice.tts.mode')=='cloud' and p.get(cfg,'voice.tts.provider')=='elevenlabs':
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',p.get(cfg,'voice.tts.voice')):raise p.ConfigError('voice.tts.voice','ElevenLabs requires a plain voice ID')
        if not p.get(cfg,'voice.tts.model').startswith('eleven_'):raise p.ConfigError('voice.tts.model','choose an ElevenLabs model ID, e.g. eleven_multilingual_v2')
        if not 0.7<=p.get(cfg,'voice.tts.rate')<=1.2:raise p.ConfigError('voice.tts.rate','ElevenLabs speed range: 0.7..1.2')
    for kind in ('asr','tts'):
        if p.get(cfg,f'voice.{kind}.mode')=='cloud':
            provider=p.get(cfg,f'voice.{kind}.provider');env=p.get(cfg,f'voice.{kind}.key_env')
            known={'OPENAI_API_KEY':'openai','OPENROUTER_API_KEY':'openrouter','ELEVENLABS_API_KEY':'elevenlabs'}
            if env in known and known[env]!=provider:raise p.ConfigError(f'voice.{kind}.key_env','named key belongs to a different provider; choose your local key variable explicitly')
        if p.get(cfg,f'voice.{kind}.mode')=='cloud' and not has_key(p.get(cfg,f'voice.{kind}.key_env')):
            raise p.ConfigError(f'voice.{kind}.key_env','selected cloud provider key is absent; set it locally, never in conversation')
    if p.get(cfg,'voice.tts.mode')=='host' and not (shutil.which('say') or shutil.which('espeak-ng')):
        raise p.ConfigError('voice.tts.mode','host speech engine not installed; choose phone or install OS speech')

def hardware():
    cpus=os.cpu_count() or 1
    try: cpus=min(cpus,len(os.sched_getaffinity(0)))
    except (AttributeError,OSError):pass
    try: mem=os.sysconf('SC_PHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')//(1024**2)
    except (ValueError,OSError):mem=0
    if sys.platform=='darwin':
        try:
            result=subprocess.run(['/usr/sbin/sysctl','-n','hw.memsize'],capture_output=True,text=True,timeout=3,check=True)
            mem=int(result.stdout.strip())//(1024**2)
        except (OSError,ValueError,subprocess.SubprocessError):pass
    # cgroup v2 limits describe the available machine when running in a container.
    if sys.platform.startswith('linux'):
        try:
            relative=next(x.split(':',2)[2] for x in pathlib.Path('/proc/self/cgroup').read_text().splitlines() if x.startswith('0::'))
            root=pathlib.Path('/sys/fs/cgroup')
            scoped=(root/relative.lstrip('/')).resolve()
            if not scoped.is_relative_to(root) or not scoped.is_dir():scoped=root
            for directory in (scoped,*scoped.parents):
                if directory==root.parent:break
                try:
                    limit=(directory/'memory.max').read_text().strip()
                    if limit!='max':mem=min(mem,int(limit)//(1024**2)) if mem else int(limit)//(1024**2)
                except (OSError,ValueError):pass
                try:
                    quota,period=(directory/'cpu.max').read_text().split()
                    if quota!='max':cpus=min(cpus,max(1,int(quota)//int(period)))
                except (OSError,ValueError,ZeroDivisionError):pass
                if directory==root:break
        except (OSError,StopIteration):pass
    disk=shutil.disk_usage(pathlib.Path.home()).free//(1024**2)
    tier='local' if cpus>=2 and mem>=4096 and disk>=1024 else 'cloud-recommended'
    return {'tier':tier,'cpu_logical':cpus,'memory_mib':mem,'free_disk_mib':disk,'gpu_required':False,'recommendation':'sensevoice CPU' if tier=='local' else 'your cloud ASR key, or phone keyboard dictation','assessment':'capacity estimate; run a real voice sample to measure latency'}

def _request(url,key,body,content_type,timeout,max_bytes,auth_header='Authorization'):
    headers={auth_header:('Bearer '+key if auth_header=='Authorization' else key),'Content-Type':content_type}
    req=urllib.request.Request(url,data=body,headers=headers,method='POST')
    # Explicitly prohibit redirects (credentials/audio must stay at the selected official provider).
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args): return None
    with urllib.request.build_opener(NoRedirect).open(req,timeout=timeout) as r:
        data=r.read(max_bytes+1)
        if len(data)>max_bytes:raise ValueError('provider response exceeds limit')
        return data

def cloud_asr(path,cfg,timeout=60):
    name=p.get(cfg,'voice.asr.key_env'); key=os.environ.get(name)
    if not key:return {'ok':False,'reason':'broken'}
    data=pathlib.Path(path).read_bytes()
    if len(data)>25*1024*1024:return {'ok':False,'reason':'bad_audio'}
    boundary='agentj'+uuid.uuid4().hex
    model=p.get(cfg,'voice.asr.model')
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()+data+f'\r\n--{boundary}--\r\n'.encode())
    try:
        url=ASR_URL if p.get(cfg,'voice.asr.provider')=='openrouter' else ASR_OPENAI_URL
        res=json.loads(_request(url,key,body,'multipart/form-data; boundary='+boundary,timeout,256*1024))
        text=res.get('text')
        if not isinstance(text,str) or len(text)>64000:return {'ok':False,'reason':'engine_failed'}
        return {'ok':True,'text':text,'engine':p.get(cfg,'voice.asr.provider')}
    except Exception:return {'ok':False,'reason':'engine_failed'}

def synthesize(text,cfg,timeout=60):
    if not isinstance(text,str) or not text.strip() or len(text)>4000: raise p.ConfigError('voice.tts','text must be 1..4000 characters')
    mode=p.get(cfg,'voice.tts.mode'); voice=p.get(cfg,'voice.tts.voice')
    if mode=='phone':raise p.ConfigError('voice.tts.mode','phone speech runs on phone; use its Read button')
    if mode=='cloud':
        validate_runtime(cfg)
        if p.get(cfg,'voice.tts.provider')=='elevenlabs':
            body=json.dumps({'text':text,'model_id':p.get(cfg,'voice.tts.model'),'voice_settings':{'speed':p.get(cfg,'voice.tts.rate')}}).encode()
            try:
                pcm=_request(TTS_ELEVEN_URL+voice+'?output_format=pcm_16000',os.environ[p.get(cfg,'voice.tts.key_env')],body,'application/json',timeout,8*1024*1024-44,auth_header='xi-api-key')
                if not pcm or len(pcm)%2:raise ValueError()
                out=io.BytesIO()
                with wave.open(out,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(pcm)
                return out.getvalue()
            except Exception:raise p.ConfigError('voice.tts','provider request failed; no retry or credentials echoed') from None
        body=json.dumps({'model':p.get(cfg,'voice.tts.model'),'voice':voice,'input':text,'response_format':'wav','speed':p.get(cfg,'voice.tts.rate')}).encode()
        try:return _request(TTS_URL,os.environ[p.get(cfg,'voice.tts.key_env')],body,'application/json',timeout,8*1024*1024)
        except Exception:raise p.ConfigError('voice.tts','provider request failed; no retry, previous settings retained') from None
    import tempfile
    with tempfile.TemporaryDirectory(prefix='agentj-speech-') as tmp:
        output=pathlib.Path(tmp)/'speech.wav'
        exe=shutil.which('espeak-ng')
        if exe:
            args=[exe,'-w',str(output),'-s',str(int(175*p.get(cfg,'voice.tts.rate')))]
            if voice:args+=['-v',voice]
            args+=['--stdin']
            res=subprocess.run(args,input=text.encode(),capture_output=True,timeout=timeout)
        else:
            exe=shutil.which('say')
            if not exe:raise p.ConfigError('voice.tts.mode','no host speech engine')
            args=[exe,'-o',str(output),'--file-format=WAVE','--data-format=LEI16@22050']
            if voice:args+=['-v',voice]
            res=subprocess.run(args,input=text.encode(),capture_output=True,timeout=timeout)
        if res.returncode or not output.exists():raise p.ConfigError('voice.tts.voice','OS engine failed; check voice ID')
        return output.read_bytes()
