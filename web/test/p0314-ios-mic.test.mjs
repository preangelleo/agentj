import test from 'node:test';
import assert from 'node:assert/strict';
import {createMicrophone, microphoneErrorKey} from '../public/js/microphone.js';
import DICT from '../public/i18n.js';

const source = () => {
  const track = {readyState: 'live', enabled: true, stop(){this.readyState = 'ended'; this.enabled = false;}};
  return {track, stream: {getTracks: () => [track], getAudioTracks: () => [track]}};
};
const error = name => new DOMException(name, name);

test('transient capture failure retries once only while the original gesture is active', async () => {
  let calls = 0, gesture = true;
  const s = source();
  const mic = createMicrophone({visible: () => true, gestureActive: () => gesture,
    getUserMedia: () => { calls++; return calls === 1 ? Promise.reject(error('NotReadableError')) : Promise.resolve(s.stream); }});
  const take = await mic.acquire();
  assert.equal(calls, 2); assert.equal(take.stream, s.stream);
  take.release(); assert.equal(s.track.readyState, 'ended');
  gesture = false;
});

test('persistent transient failure never retries more than once', async () => {
  let calls = 0;
  const mic = createMicrophone({visible: () => true, gestureActive: () => true,
    getUserMedia: () => { calls++; return Promise.reject(error('AbortError')); }});
  await assert.rejects(mic.acquire(), {name: 'AbortError'});
  assert.equal(calls, 2);
});

test('denial never retries and the next owner tap can request again', async () => {
  let calls = 0;
  const mic = createMicrophone({visible: () => true, gestureActive: () => true,
    getUserMedia: () => { calls++; return calls === 1 ? Promise.reject(error('NotAllowedError')) : Promise.resolve(source().stream); }});
  await assert.rejects(mic.acquire(), {name: 'NotAllowedError'});
  assert.equal(calls, 1);
  const take = await mic.acquire(); assert.equal(calls, 2); take.release();
});

test('no retry after gesture expiry, visibility loss or cancellation; late stream is stopped', async () => {
  for (const boundary of ['gesture', 'visibility', 'cancel']) {
    let gesture = true, visible = true, rejectFirst, calls = 0;
    const mic = createMicrophone({visible: () => visible, gestureActive: () => gesture,
      getUserMedia: () => { calls++; return new Promise((_, reject) => {rejectFirst = reject;}); }});
    const attempt = mic.acquire();
    const rejected = assert.rejects(attempt, {name: 'NotReadableError'});
    if (boundary === 'gesture') gesture = false;
    if (boundary === 'visibility') visible = false;
    if (boundary === 'cancel') mic.close();
    rejectFirst(error('NotReadableError'));
    await rejected; assert.equal(calls, 1, boundary);
  }
  let resolveFirst;
  const s = source();
  const mic = createMicrophone({visible: () => true, gestureActive: () => true,
    getUserMedia: () => new Promise(resolve => {resolveFirst = resolve;})});
  const attempt = mic.acquire(); const rejected = assert.rejects(attempt, {name: 'AbortError'});
  mic.close(); resolveFirst(s.stream); await rejected;
  assert.equal(s.track.readyState, 'ended');
});

test('messages distinguish permission, busy device, interruption and timeout in both languages', () => {
  for (const language of ['zh', 'en']) {
    const messages = ['NotAllowedError', 'NotReadableError', 'AbortError', 'TimeoutError']
      .map(name => DICT[language][microphoneErrorKey({name})]);
    assert.equal(new Set(messages).size, 4);
    assert.ok(messages.every(message => !/No microphone permission|拿不到麦克风权限/.test(message)));
    assert.match(messages[0], language === 'en' ? /Settings/ : /设置/);
    assert.match(messages[1], language === 'en' ? /other apps/ : /其他 App/);
  }
});
