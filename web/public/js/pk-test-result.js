// A failed test can be retried through the current pairing unless the Host rejected the actual credential.
export function pkTestResult(message) {
  if (message?.t !== 'pk_check_res') return {key:'p566.notSent', retry:true};
  if (message.ok === true) return {key:'p566.pk.testOk', success:true};
  switch (message.why) {
    case 'no_check':
    case 'challenge':
      return {key:'p0300.pk.testExpired', retry:true};
    case 'uv':
      return {key:'p0378.pk.testUv', retry:true};
    case 'origin':
    case 'rp':
      return {key:'p0378.pk.testOrigin'};
    case 'credential':
    case 'signature':
    case 'counter':
      return {key:'p0300.pk.testRejected', setup:true};
    default:
      return {key:'p0300.pk.testIncomplete', retry:true};
  }
}

export function pkTestBrowserError(name) {
  if (name === 'WrongSeatKey') return { key: 'p566.pk.wrongSeat', retry: true };
  if (name === 'SecurityError') return { key: 'p0378.pk.testOrigin' };
  return { key: name === 'AbortError' ? 'p539.fail.abort' : 'p539.fail.cancel', retry: true };
}
