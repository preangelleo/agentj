// Olares-only integration helper. Existing relay helper owns/reaps its workerd process group.
import {startLocalRelay} from '../../../relay/test/local_relay.mjs';
import {createInterface} from 'node:readline';
const relay=await startLocalRelay({seatApi:true});
try {
 process.stdout.write(JSON.stringify({url:relay.url,httpUrl:relay.httpUrl})+'\n');
 for await(const line of createInterface({input:process.stdin,terminal:false}))if(line==='stop')break;
} finally {await relay.stop();}
