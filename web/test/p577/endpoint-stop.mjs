// The synthetic host's HTTP close callback can precede upgraded sockets' close callbacks.
// Await the real connection ledger; never zero it or treat an unresolved close as success.
export async function stopOwnedEndpoints(fake,web,{timeout=2000}={}){
 const started=performance.now();await fake.stop();await web.stop();let checks=0,last;
 for(;;){checks++;last={fakeConnectionsRemaining:fake.conns,sourceServerListening:web.server.listening};
  if(last.fakeConnectionsRemaining===0&&last.sourceServerListening===false)return {...last,stopCompleted:true,observations:checks,elapsedMilliseconds:performance.now()-started};
  if(performance.now()-started>=timeout)throw Object.assign(Error('Old phase fixture endpoint remained active: '+JSON.stringify(last)),{endpointState:last});
  await new Promise(resolve=>setImmediate(resolve));
 }
}
