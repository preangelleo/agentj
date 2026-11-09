// A failed challenge is not evidence that the browser lost its credential.
export function pkTestResult(message) {
  if (message?.t !== 'pk_check_res') return {key:'p566.notSent', retry:true};
  if (message.ok === true) return {key:'p566.pk.testOk', success:true};
  if (message.why === 'no_check') return {key:'p0300.pk.testExpired', retry:true};
  if (['credential','rp','origin','signature','uv','counter','challenge'].includes(message.why)) {
    return {key:'p0300.pk.testRejected', setup:true};
  }
  return {key:'p0300.pk.testIncomplete', retry:true};
}
