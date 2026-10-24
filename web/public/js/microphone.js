// Foreground-only microphone lease. Idle tracks output silence; hidden/pagehide/reset
// destroys the source. No permission request or recording starts on visibility return.
export function createMicrophone({getUserMedia, visible}) {
  let stream = null, pending = null, epoch = 0, owner = null;
  const stop = s => s?.getTracks().forEach(t => { t.enabled = false; t.stop(); });
  const live = s => s && s.getAudioTracks().length > 0 && s.getAudioTracks().every(t => t.readyState === 'live');
  const abort = () => new DOMException('Microphone request no longer active', 'AbortError');
  function close() {
    epoch++; owner = null; pending = null;
    stop(stream); stream = null;
  }
  async function acquire() {
    if (!visible()) throw abort();
    if (!live(stream)) {
      stop(stream); stream = null;
      if (!pending) {
        const generation = epoch;
        // Start inside the user's gesture. Always stop a late result, even if a
        // newer foreground request has already succeeded after a hide/cancel.
        const request = Promise.resolve(getUserMedia()).then(s => {
          if (generation !== epoch || !visible()) { stop(s); throw abort(); }
          stream = s;
          return s;
        });
        pending = request;
        request.then(() => { if (pending === request) pending = null; }, () => { if (pending === request) pending = null; });
      }
      await pending;
    }
    if (!visible() || !live(stream)) throw abort();
    const held = stream, token = owner = {};
    held.getAudioTracks().forEach(t => { t.enabled = true; });
    return {stream: held, release() {
      if (owner !== token) return;
      owner = null;
      held.getAudioTracks().forEach(t => { t.enabled = false; });
    }};
  }
  return {acquire, close};
}
