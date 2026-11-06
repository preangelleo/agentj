// Registered L1 entry: use the current full terminal contract and browser fixture.
import {runP0269Term} from './pfull-terminal.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch} from './browser.mjs';
export {runP0269Term};
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
  try {await runP0269Term({B,web,fake});} finally {await B.close();await fake.stop();await web.stop();}
}
