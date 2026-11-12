// Foreground-only microphone lease. Every completed take stops its tracks; hidden/pagehide/reset
// destroys the source. No permission request or recording starts on visibility return.
// P496 exact rule (0.30.8 int1 security r1 L3): a grant landing while the page is hidden is always stopped here. A take
// (or QR scan / camera sheet) still waiting for its permission when the page went hidden survives the return only if the
// page was hidden for at most PROMPT_HIDE_MAX_MS; longer, the caller closes it on return (no recording, no camera, no
// 「no microphone」 notice) and a grant that lands afterwards is stopped. Android Chrome shows its permission prompt
// in-page (no hide at all); a home-screen app's system permission dialog hides the page only for the activity switch
// around it, well under a second. 3 s covers a slow device's switch; a page away longer is the owner leaving, and a
// return from that must never turn into capture without another tap (the permission, if granted, makes that tap instant).
export const PROMPT_HIDE_MAX_MS = 3000;
export const hiddenTooLong = (since, now = Date.now()) => since > 0 && now - since > PROMPT_HIDE_MAX_MS;
export function microphoneErrorKey(error) {
  return 'p0314.mic.' + ({ NotAllowedError: 'denied', NotReadableError: 'busy', AbortError: 'interrupted', TimeoutError: 'timeout' }[error?.name] || 'default');
}
export function createMicrophone({getUserMedia, visible, gestureActive = () => navigator.userActivation?.isActive === true}) {
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
        // A native transient failure may be retried once only while the original
        // gesture is still active. Never retry a permission denial, a stale take,
        // or on foreground return (which requires a fresh owner tap).
        let first;
        try { first = getUserMedia(); } catch (err) { first = Promise.reject(err); }
        const request = Promise.resolve(first).catch(err => {
          if ((err?.name !== 'NotReadableError' && err?.name !== 'AbortError') ||
              generation !== epoch || !visible() || !gestureActive()) throw err;
          return getUserMedia();
        }).then(s => {
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
      stop(held);
      if (stream === held) stream = null;
    }};
  }
  return {acquire, close};
}
