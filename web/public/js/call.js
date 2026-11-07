// #441 laboratory prototype; release activation awaits the owner’s boundary decision.
export const VOICE_PROTOTYPE_RELEASED = false;
// send() MUST be the current paired Noise session.
export class VoiceCall {
  constructor({send, ready, speak, cancelSpeech, show = () => {}, now = () => crypto.randomUUID().replaceAll('-', '').slice(0,22)}) {
    Object.assign(this, {send, ready, speak, cancelSpeech, show, now});
    this.enabled = false; this.id = null; this.seq = 0; this.pending = 0; this.generation = 0;
  }
  capability(m) { this.enabled = m.enabled === true; if (!this.enabled) this.stop(); }
  async start() {
    if (!this.enabled || !this.ready()) throw Error('stream_unavailable');
    await this.stop();
    this.id = this.now(); this.seq = 0; this.sentence = 0; this.generation++;
    await this.send({t:'call_start', id:this.id});
  }
  async pcm(samples) {
    if (!this.id || !this.ready() || this.finishing) return;
    if (!(samples instanceof Int16Array) || samples.length > 3200 || !samples.length || this.pending >= 8) {
      await this.stop(); throw Error('backpressure');
    }
    const id = this.id, generation = this.generation;
    const bytes = new Uint8Array(samples.length*2), view = new DataView(bytes.buffer);
    samples.forEach((v,i)=>view.setInt16(i*2,v,true));
    const pcm = btoa(String.fromCharCode(...bytes));
    this.pending++;
    try { if (generation === this.generation) await this.send({t:'call_pcm',id,seq:this.seq++,pcm}); }
    finally { this.pending--; }
  }
  async finish() {
    if (!this.id || this.finishing) return;
    await this.releaseMic?.(true);
    if (!this.id) return;
    this.finishing = true;
    await this.send({t:'call_finish',id:this.id});
  }
  async stop(notify=true) {
    const id = this.id;
    this.id = null; this.generation++; this.finishing = false;
    this.cancelSpeech(); await this.releaseMic?.();
    if (id && notify && this.ready()) await this.send({t:'call_stop',id}).catch(()=>{});
  }
  event(m) {
    if (m.id !== this.id) return;
    if (m.kind === 'input_done') { this.finishing=true; void this.releaseMic?.(); return; }
    if (m.kind === 'error' || m.kind === 'stopped') { this.show(m.reason, true); void this.stop(false); return; }
    if (m.kind === 'partial' || m.kind === 'final') this.show(m.text);
    if (m.kind === 'sentence' && m.seq === this.sentence+1 && typeof m.text === 'string' && m.text.length <= 8000) {
      this.sentence = m.seq; this.speak(m.text);
    }
  }
}

// 200ms true PCM chunks; never MediaRecorder/whole-file transcription.
export async function microphone(call) {
  const generation = call.generation;
  const stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1},video:false});
  if (generation !== call.generation || !call.id || call.finishing) { stream.getTracks().forEach(t=>t.stop()); return; }
  const ctx = new AudioContext(), source = ctx.createMediaStreamSource(stream);
  const processor = ctx.createScriptProcessor(2048,1,1); // prototype fallback supported on phone browsers
  const mute = ctx.createGain(); mute.gain.value=0;
  source.connect(processor); processor.connect(mute); mute.connect(ctx.destination);
  let input = [], position = 0, pcm = [];
  call.releaseMic = async (flush=false) => {
    call.releaseMic = null;
    processor.onaudioprocess = null; processor.disconnect(); source.disconnect(); mute.disconnect();
    stream.getTracks().forEach(t=>t.stop());
    if (flush && pcm.length) { await call.pcm(Int16Array.from(pcm)); pcm=[]; }
    await ctx.close();
  };
  processor.onaudioprocess = e => {
    if (generation !== call.generation) return;
    input.push(...e.inputBuffer.getChannelData(0));
    const step = ctx.sampleRate/16000;
    while (position+1 < input.length) {
      const i=Math.floor(position), f=position-i;
      const value=input[i]*(1-f)+input[i+1]*f;
      pcm.push(Math.round(Math.max(-1,Math.min(1,value))*32767)); position+=step;
      if (pcm.length===3200) { void call.pcm(Int16Array.from(pcm)).catch(()=>call.stop()); pcm=[]; }
    }
    const used=Math.floor(position); input=input.slice(used); position-=used;
  };
  await ctx.resume();
}

export function mountCall({send,ready,lang}) {
  if (!VOICE_PROTOTYPE_RELEASED) return {capability:()=>{},event:()=>{},reset:()=>{}};
  let panel;
  const call = new VoiceCall({send,ready,
    speak: text => {
      // Only an explicitly local browser voice; never invoke a network TTS service.
      const voices=speechSynthesis.getVoices().filter(v=>v.localService);
      const voice=voices.find(v=>v.lang.startsWith(lang())) || voices[0];
      if (!voice) { panel.status.textContent=lang()==='en'?'Local phone TTS unavailable':'手机本地朗读不可用'; return; }
      const u=new SpeechSynthesisUtterance(text); u.voice=voice; speechSynthesis.speak(u);
    }, cancelSpeech:()=>globalThis.speechSynthesis?.cancel(),
    show:(text,error=false)=>{ if(panel)panel.status.textContent=error
      ? (lang()==='en'?'Call stopped or streaming unavailable':'通话已停止或流式语音不可用') : text; }
  });
  const capability=m=>{
    call.capability(m);
    if (!panel) {
      const root=document.createElement('section'), start=document.createElement('button'), hold=document.createElement('button');
      const end=document.createElement('button'), status=document.createElement('output');
      root.id='voice-call';start.id='voice-call-start';hold.id='voice-call-hold';end.id='voice-call-end';status.id='voice-call-status';
      start.textContent=lang()==='en'?'Start voice prototype':'开始语音原型';
      hold.textContent=lang()==='en'?'Hold to talk':'按住说话'; hold.disabled=true;
      end.textContent=lang()==='en'?'End call':'结束通话';
      root.append(start,hold,end,status); document.body.append(root); panel={root,status};
      start.onclick=()=>{hold.disabled=false;status.textContent=lang()==='en'?'Ready · local ASR prototype':'就绪 · 本地 ASR 原型';};
      hold.onpointerdown=async e=>{
        hold.setPointerCapture(e.pointerId);
        try { await call.start(); await microphone(call); }
        catch { status.textContent=lang()==='en'?'Streaming unavailable':'流式语音不可用'; await call.stop(); }
      };
      hold.onpointerup=()=>void call.finish(); hold.onpointercancel=()=>void call.stop();
      end.onclick=()=>{hold.disabled=true;void call.stop();};
    }
    panel.root.hidden=!m.enabled || !navigator.mediaDevices?.getUserMedia || !globalThis.speechSynthesis;
  };
  setInterval(()=>{if(call.id && !ready()) {void call.stop(false);if(panel)panel.root.hidden=true;}},100);
  globalThis.addEventListener('pagehide',()=>void call.stop());
  return {capability,event:m=>call.event(m),reset:()=>{void call.stop(false);if(panel)panel.root.hidden=true;call.enabled=false;}};
}
